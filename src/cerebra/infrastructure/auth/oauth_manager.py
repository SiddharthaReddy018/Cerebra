"""OAuth 2.0 manager for the installed-app flow (FR-5.1 to FR-5.5, CON-3, ADR-D).

Responsibilities
  * Start with ``youtube.readonly``; upgrade incrementally to ``youtube`` only when
    ``create_playlist`` needs it (FR-5.1).
  * Store tokens via a TokenStore (keyring → owner-only file; NFR-SE-2/3).
  * Refresh tokens and detect the 7-day Testing-mode refresh-token expiry (CON-3).
  * Revoke on request and delete the local token (FR-5.5).

Nothing here prints to stdout: stdout carries MCP JSON-RPC. Human-facing text goes to stderr.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from collections.abc import Callable
from typing import Any, TextIO, cast

from cerebra.domain.errors import (
    CerebraError,
    auth_expired,
    auth_required,
    insufficient_scope,
    transient_error,
)
from cerebra.ports.interfaces import Clock, TokenStore

SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
SCOPE_WRITE = "https://www.googleapis.com/auth/youtube"
# Equivalent-to-`youtube` scope; also satisfies both requirements.
SCOPE_FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint, not a secret
GOOGLE_REVOKE_URI = "https://oauth2.googleapis.com/revoke"

# CON-3: refresh tokens of apps in "Testing" publishing status expire after 7 days.
TESTING_MODE_REFRESH_LIFETIME_S = 7 * 24 * 3600
# Warn this long before the expected expiry.
EXPIRY_WARNING_WINDOW_S = 24 * 3600


def scope_satisfies(granted: set[str], required: str) -> bool:
    """`youtube` (and force-ssl) are supersets of `youtube.readonly` for our purposes."""
    if required in granted:
        return True
    if required == SCOPE_READONLY:
        return SCOPE_WRITE in granted or SCOPE_FORCE_SSL in granted
    if required == SCOPE_WRITE:
        return SCOPE_FORCE_SSL in granted
    return False


class OAuthManager:
    """Owns the credential lifecycle. Constructed once at the composition root (no singletons)."""

    def __init__(
        self,
        token_store: TokenStore,
        client_id: str,
        client_secret: str,
        clock: Clock,
        *,
        interactive: bool = True,
        flow_factory: Callable[[dict[str, Any], list[str]], Any] | None = None,
        refresh_fn: Callable[[Any], None] | None = None,
        http_post: Callable[..., Any] | None = None,
        stderr: TextIO | None = None,
        env_refresh_token: str = "",
        env_refresh_token_scopes: str = "",
    ) -> None:
        self._store = token_store
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._interactive = interactive
        self._flow_factory = flow_factory or _default_flow_factory
        self._refresh_fn = refresh_fn or _default_refresh
        self._http_post = http_post
        self._err = stderr or sys.stderr
        # Headless mode (CEREBRA_REFRESH_TOKEN): held in memory only, never persisted.
        self._env_refresh_token = env_refresh_token.strip()
        scopes = [s for s in re.split(r"[\s,]+", env_refresh_token_scopes) if s]
        self._env_scopes = scopes or [SCOPE_READONLY]
        self._headless_creds: Any = None  # in-memory access-token cache (headless only)

    # ── Credential record: env refresh token (headless) wins over the store ──

    @property
    def headless(self) -> bool:
        """True when credentials come from CEREBRA_REFRESH_TOKEN rather than the store."""
        return bool(self._env_refresh_token)

    def _load_record(self) -> dict[str, object] | None:
        if self._env_refresh_token:
            return {
                "credentials": {
                    "refresh_token": self._env_refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "token_uri": GOOGLE_TOKEN_URI,
                    "scopes": list(self._env_scopes),
                },
                "scopes": list(self._env_scopes),
            }
        return self._store.load_token()

    # ── Introspection (never returns token material) ─────────────────────────

    def is_authenticated(self) -> bool:
        data = self._load_record()
        if not data:
            return False
        info = data.get("credentials")
        return isinstance(info, dict) and bool(info.get("refresh_token") or info.get("token"))

    def granted_scopes(self) -> set[str]:
        data = self._load_record()
        if not data:
            return set()
        return {str(s) for s in cast(list[object], data.get("scopes", []))}

    def has_scope(self, scope: str) -> bool:
        return scope_satisfies(self.granted_scopes(), scope)

    def authorised_at(self) -> float | None:
        data = self._load_record()
        if not data:
            return None
        value = data.get("authorised_at")
        return float(value) if isinstance(value, int | float) else None

    def seconds_until_testing_expiry(self) -> float | None:
        """Estimated seconds before a Testing-mode refresh token expires (CON-3), else None."""
        issued = self.authorised_at()
        if issued is None:
            return None
        return issued + TESTING_MODE_REFRESH_LIFETIME_S - self._clock.utcnow_timestamp()

    def status(self) -> dict[str, object]:
        remaining = self.seconds_until_testing_expiry()
        warn = remaining is not None and remaining < EXPIRY_WARNING_WINDOW_S
        return {
            "authenticated": self.is_authenticated(),
            "scopes": sorted(self.granted_scopes()),
            "write_access": self.has_scope(SCOPE_WRITE),
            "storage": getattr(self._store, "backend_name", "unknown"),
            "testing_mode_expiry_hint_hours": (
                None if remaining is None else round(max(0.0, remaining) / 3600, 1)
            ),
            "reauthorise_soon": bool(warn),
            "note": (
                "If this Google Cloud app is in 'Testing' status, refresh tokens expire "
                "after 7 days (CON-3); run `cerebra-cli login` again when warned."
            ),
        }

    # ── Credentials for API calls ────────────────────────────────────────────

    def get_credentials(self, required_scope: str = SCOPE_READONLY) -> Any:
        """Return refreshed credentials covering ``required_scope`` or raise CerebraError.

        Never opens a browser. Use ``ensure_scope`` for the incremental-upgrade path.
        """
        if not self.is_authenticated():
            raise auth_required()
        if not self.has_scope(required_scope):
            raise insufficient_scope(required_scope)
        return self.ensure_valid_token()

    def ensure_valid_token(self) -> Any:
        """Load + refresh credentials if needed (called before every API request, ADR-D §4)."""
        if self.headless and getattr(self._headless_creds, "valid", False):
            return self._headless_creds
        data = self._load_record()
        if not data or not isinstance(data.get("credentials"), dict):
            raise auth_required()
        try:
            creds = _credentials_from_info(data["credentials"])  # type: ignore[arg-type]
        except (ValueError, KeyError):
            # Stored token is incomplete/corrupt (e.g. no refresh_token): need a fresh login.
            raise auth_expired() from None
        if getattr(creds, "valid", False):
            self._headless_creds = creds if self.headless else None
            return creds
        if not getattr(creds, "refresh_token", None):
            raise auth_expired()
        try:
            self._refresh_fn(creds)
        except CerebraError:
            raise
        except Exception as exc:
            raise self._classify_refresh_failure(exc) from exc
        if self.headless:  # headless tokens are never persisted (NFR-SE-2)
            self._headless_creds = creds
        else:
            data["credentials"] = json.loads(creds.to_json())
            self._store.save_token(data)
        return creds

    def _classify_refresh_failure(self, exc: Exception) -> CerebraError:
        from google.auth.exceptions import RefreshError, TransportError

        if isinstance(exc, TransportError | ConnectionError | TimeoutError):
            # Network problem, NOT an expired grant: do not tell the user to re-login.
            return transient_error("could not reach Google to refresh the access token")
        if isinstance(exc, RefreshError):
            remaining = self.seconds_until_testing_expiry()
            err = auth_expired()
            if remaining is not None and remaining <= 0:
                return CerebraError(
                    err.code,
                    "Your Google refresh token has expired. Apps in Google's 'Testing' "
                    "publishing status expire refresh tokens after 7 days (CON-3).",
                    retryable=False,
                    suggested_action=(
                        "Run `cerebra-cli login` to re-authorise. Pending proposals are kept. "
                        "Publishing the OAuth app removes the 7-day limit."
                    ),
                )
            return CerebraError(
                err.code,
                "Google rejected the refresh token (revoked, or the app's Testing-mode "
                "7-day limit was reached).",
                retryable=False,
                suggested_action=(
                    "Run `cerebra-cli login` to re-authorise. Pending proposals are kept."
                ),
            )
        return transient_error(
            f"unexpected error while refreshing credentials: {type(exc).__name__}"
        )

    # ── Interactive flows (human at the keyboard) ────────────────────────────

    def login(self, scopes: list[str] | None = None) -> dict[str, object]:
        """Run the browser consent flow for ``scopes`` (default: readonly)."""
        wanted = list(dict.fromkeys(scopes or [SCOPE_READONLY]))
        return self._run_flow(wanted)

    def request_additional_scope(self, scope: str) -> dict[str, object]:
        """Incremental authorisation (FR-5.1, ADR-D §2): add ``scope`` to what is granted.

        Existing scopes are retained (``include_granted_scopes``), so proposals and the
        readonly token keep working throughout.
        """
        wanted = list(dict.fromkeys([*sorted(self.granted_scopes()), SCOPE_READONLY, scope]))
        return self._run_flow(wanted)

    def ensure_scope(self, scope: str) -> Any:
        """Return valid credentials covering ``scope``, upgrading interactively if allowed."""
        if self.headless and not self.has_scope(scope):
            raise insufficient_scope(scope)  # never open a browser in headless runs
        if not self.is_authenticated():
            if not self._interactive:
                raise auth_required()
            self.login([SCOPE_READONLY, scope] if scope != SCOPE_READONLY else [scope])
        elif not self.has_scope(scope):
            if not self._interactive:
                raise insufficient_scope(scope)
            self.request_additional_scope(scope)
        return self.ensure_valid_token()

    def _run_flow(self, scopes: list[str]) -> dict[str, object]:
        if not self._client_id or not self._client_secret:
            raise CerebraError(
                auth_required().code,
                "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set, "
                "so authorisation cannot start.",
                suggested_action=(
                    "Create an OAuth 'Desktop app' client in Google Cloud Console and export "
                    "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (never commit them)."
                ),
            )
        client_config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": GOOGLE_AUTH_URI,
                "token_uri": GOOGLE_TOKEN_URI,
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = self._flow_factory(client_config, scopes)
        # stdout is the MCP channel: anything the library prints must go to stderr instead.
        with contextlib.redirect_stdout(self._err):
            creds = flow.run_local_server(
                port=0,
                open_browser=True,
                access_type="offline",
                prompt="consent",
                include_granted_scopes="true",
                authorization_prompt_message="Open this URL to authorise Cerebra: {url}",
                success_message="Cerebra is authorised. You can close this tab.",
            )
        granted = list(getattr(creds, "scopes", None) or scopes)
        record: dict[str, object] = {
            "credentials": json.loads(creds.to_json()),
            "scopes": sorted(set(granted)),
            "authorised_at": self._clock.utcnow_timestamp(),
        }
        self._store.save_token(record)
        return self.status()

    # ── Revocation (FR-5.5) ──────────────────────────────────────────────────

    def revoke(self) -> dict[str, object]:
        """Revoke at Google (best effort) and always delete the local token."""
        data = self._load_record()
        remote_revoked = False
        if data and isinstance(data.get("credentials"), dict):
            info: dict[str, Any] = data["credentials"]  # type: ignore[assignment]
            token = info.get("refresh_token") or info.get("token")
            if token:
                try:
                    post = self._http_post or _default_http_post
                    resp = post(GOOGLE_REVOKE_URI, data={"token": token}, timeout=10.0)
                    remote_revoked = 200 <= int(resp.status_code) < 300
                except Exception:
                    remote_revoked = False
        self._store.delete_token()
        return {"revoked_remotely": remote_revoked, "local_token_deleted": True}


# ── Library glue (isolated so tests inject fakes; no network at import) ──────


def _credentials_from_info(info: dict[str, Any]) -> Any:
    from google.oauth2.credentials import Credentials

    return Credentials.from_authorized_user_info(info)  # type: ignore[no-untyped-call]


def _default_refresh(creds: Any) -> None:
    from google.auth.transport.requests import Request

    creds.refresh(Request())


def _default_flow_factory(client_config: dict[str, Any], scopes: list[str]) -> Any:
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_config(client_config, scopes=scopes)


def _default_http_post(url: str, **kwargs: Any) -> Any:
    import httpx

    return httpx.post(url, **kwargs)

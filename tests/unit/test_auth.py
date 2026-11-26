"""Token storage + OAuth manager (FR-5.1–5.5, CON-3, NFR-SE-2/3, ADR-D)."""

# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2
from __future__ import annotations

import io
import json
import os
import stat
from types import SimpleNamespace

import pytest
from google.auth.exceptions import RefreshError, TransportError

from cerebra.domain.errors import CerebraError, ErrorCode
from cerebra.infrastructure.auth.oauth_manager import (
    SCOPE_FORCE_SSL,
    SCOPE_READONLY,
    SCOPE_WRITE,
    TESTING_MODE_REFRESH_LIFETIME_S,
    OAuthManager,
    scope_satisfies,
)
from cerebra.infrastructure.auth.token_store import (
    FallbackTokenStore,
    FileTokenStore,
    InMemoryTokenStore,
    KeyringTokenStore,
    build_token_store,
    keyring_available,
)
from cerebra.infrastructure.clock import FakeClock

NOW = 1_800_000_000.0


def token_record(scopes=(SCOPE_READONLY,), authorised_at=NOW, valid=True, refresh=True):
    info = {
        "token": "ya29.fake",
        "client_id": "cid",
        "client_secret": "csecret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": list(scopes),
        "expiry": "2099-01-01T00:00:00Z" if valid else "2001-01-01T00:00:00Z",
    }
    if refresh:
        info["refresh_token"] = "1//refresh"
    return {"credentials": info, "scopes": list(scopes), "authorised_at": authorised_at}


class FakeKeyring:
    def __init__(self, fail=False):
        self.d, self.fail = {}, fail

    def set_password(self, s, u, p):
        if self.fail:
            raise RuntimeError("locked")
        self.d[(s, u)] = p

    def get_password(self, s, u):
        if self.fail:
            raise RuntimeError("locked")
        return self.d.get((s, u))

    def delete_password(self, s, u):
        if (s, u) not in self.d:
            raise RuntimeError("nope")
        del self.d[(s, u)]


# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2, NFR-SE-3
class TestFileTokenStore:
    def test_roundtrip_and_owner_only_modes(self, tmp_path):
        path = tmp_path / "cfg" / "cerebra" / "token.json"
        st = FileTokenStore(path)
        st.save_token({"a": 1})
        assert st.load_token() == {"a": 1}
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_no_temp_files_left(self, tmp_path):
        st = FileTokenStore(tmp_path / "t.json")
        st.save_token({"a": 1})
        st.save_token({"a": 2})
        assert [p.name for p in tmp_path.iterdir()] == ["t.json"]

    def test_loose_permissions_are_tightened(self, tmp_path, capsys):
        path = tmp_path / "t.json"
        st = FileTokenStore(path)
        st.save_token({"a": 1})
        os.chmod(path, 0o644)
        assert st.load_token() == {"a": 1}
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "tightened" in capsys.readouterr().err

    def test_missing_corrupt_and_non_dict(self, tmp_path):
        path = tmp_path / "t.json"
        st = FileTokenStore(path)
        assert st.load_token() is None
        path.write_text("{not json")
        assert st.load_token() is None
        path.write_text("[1,2]")
        assert st.load_token() is None

    def test_delete_idempotent(self, tmp_path):
        st = FileTokenStore(tmp_path / "t.json")
        st.delete_token()
        st.save_token({"a": 1})
        st.delete_token()
        assert st.load_token() is None

    def test_failed_write_cleans_up(self, tmp_path):
        st = FileTokenStore(tmp_path / "t.json")
        with pytest.raises(TypeError):
            st.save_token({"a": object()})
        assert list(tmp_path.iterdir()) == []


# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2, NFR-SE-3
class TestKeyringAndFallback:
    def test_keyring_roundtrip(self):
        kr = KeyringTokenStore(keyring_module=FakeKeyring())
        assert kr.load_token() is None
        kr.save_token({"a": 1})
        assert kr.load_token() == {"a": 1}
        kr.delete_token()
        kr.delete_token()  # second delete must not raise
        assert kr.load_token() is None and kr.backend_name == "keyring"

    def test_keyring_garbage_value(self):
        fk = FakeKeyring()
        fk.d[("cerebra", "youtube-oauth-token")] = "not-json"
        assert KeyringTokenStore(keyring_module=fk).load_token() is None
        fk.d[("cerebra", "youtube-oauth-token")] = "[1]"
        assert KeyringTokenStore(keyring_module=fk).load_token() is None

    def test_falls_back_to_file_when_keyring_errors(self, tmp_path):
        file = FileTokenStore(tmp_path / "t.json")
        st = FallbackTokenStore(KeyringTokenStore(keyring_module=FakeKeyring(fail=True)), file)
        st.save_token({"a": 1})
        assert file.load_token() == {"a": 1}
        assert st.load_token() == {"a": 1}
        assert st.backend_name.startswith("file:")
        st.delete_token()
        assert file.load_token() is None

    def test_keyring_preferred_and_stale_file_removed(self, tmp_path):
        file = FileTokenStore(tmp_path / "t.json")
        file.save_token({"old": 1})
        st = FallbackTokenStore(KeyringTokenStore(keyring_module=FakeKeyring()), file)
        st.save_token({"a": 1})
        assert file.load_token() is None and st.load_token() == {"a": 1}
        assert st.backend_name == "keyring"

    def test_build_token_store_without_keyring_uses_file(self, tmp_path):
        st = build_token_store(tmp_path / "t.json", use_keyring=False)
        assert isinstance(st, FileTokenStore)

    def test_keyring_available_returns_bool(self):
        assert isinstance(keyring_available(), bool)

    def test_in_memory_isolated_copy(self):
        st = InMemoryTokenStore()
        d = {"a": [1]}
        st.save_token(d)
        d["a"].append(2)
        assert st.load_token() == {"a": [1]} and st.backend_name == "memory"
        st.delete_token()
        assert st.load_token() is None


def manager(
    store=None,
    *,
    refresh=None,
    flow=None,
    post=None,
    interactive=True,
    cid="cid",
    secret="csecret",
    now=NOW,
):
    store = store or InMemoryTokenStore()
    return OAuthManager(
        store,
        cid,
        secret,
        FakeClock(now),
        interactive=interactive,
        refresh_fn=refresh,
        flow_factory=flow,
        http_post=post,
        stderr=io.StringIO(),
    ), store


class FakeFlowCreds:
    def __init__(self, scopes):
        self.scopes = scopes

    def to_json(self):
        return json.dumps(
            {
                "token": "new",
                "refresh_token": "r2",
                "client_id": "cid",
                "client_secret": "csecret",
                "scopes": self.scopes,
                "expiry": "2099-01-01T00:00:00Z",
            }
        )


class FakeFlow:
    instances = []

    def __init__(self, config, scopes):
        self.config, self.scopes, self.kwargs = config, scopes, None
        FakeFlow.instances.append(self)

    def run_local_server(self, **kw):
        self.kwargs = kw
        print("PROMPT-THAT-MUST-NOT-REACH-STDOUT")
        return FakeFlowCreds(self.scopes)


# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2, NFR-SE-1, TR-7
class TestScopes:
    def test_hierarchy(self):
        assert scope_satisfies({SCOPE_WRITE}, SCOPE_READONLY)
        assert scope_satisfies({SCOPE_FORCE_SSL}, SCOPE_READONLY)
        assert scope_satisfies({SCOPE_FORCE_SSL}, SCOPE_WRITE)
        assert not scope_satisfies({SCOPE_READONLY}, SCOPE_WRITE)
        assert scope_satisfies({"x"}, "x") and not scope_satisfies(set(), SCOPE_READONLY)


# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2
class TestTokenLifecycle:
    def test_unauthenticated(self):
        m, _ = manager()
        assert not m.is_authenticated() and m.granted_scopes() == set()
        assert m.authorised_at() is None and m.seconds_until_testing_expiry() is None
        with pytest.raises(CerebraError) as e:
            m.get_credentials()
        assert e.value.code == ErrorCode.AUTH_REQUIRED

    def test_valid_token_returned_without_refresh(self):
        m, st = manager(refresh=lambda c: pytest.fail("must not refresh"))
        st.save_token(token_record())
        assert m.get_credentials().token == "ya29.fake"

    def test_insufficient_scope_does_not_open_browser(self):
        m, st = manager(flow=lambda *a: pytest.fail("no browser"))
        st.save_token(token_record())
        with pytest.raises(CerebraError) as e:
            m.get_credentials(SCOPE_WRITE)
        assert e.value.code == ErrorCode.INSUFFICIENT_SCOPE

    def test_expired_token_refreshed_and_saved(self):
        def refresh(c):
            c.token = "refreshed"
            from datetime import datetime

            c.expiry = datetime(2099, 1, 1)

        m, st = manager(refresh=refresh)
        st.save_token(token_record(valid=False))
        assert m.ensure_valid_token().token == "refreshed"
        assert st.load_token()["credentials"]["token"] == "refreshed"
        assert st.load_token()["authorised_at"] == NOW  # preserved for the 7-day clock

    def test_no_refresh_token_means_reauth(self):
        m, st = manager()
        st.save_token(token_record(valid=False, refresh=False))
        with pytest.raises(CerebraError) as e:
            m.ensure_valid_token()
        assert e.value.code == ErrorCode.AUTH_EXPIRED

    def test_corrupt_credentials_means_reauth(self):
        m, st = manager()
        st.save_token({"credentials": {"token": "x"}, "scopes": [], "authorised_at": NOW})
        with pytest.raises(CerebraError) as e:
            m.ensure_valid_token()
        assert e.value.code == ErrorCode.AUTH_EXPIRED

    def test_network_failure_during_refresh_is_transient_not_expired(self):
        def refresh(c):
            raise TransportError("offline")

        m, st = manager(refresh=refresh)
        st.save_token(token_record(valid=False))
        with pytest.raises(CerebraError) as e:
            m.ensure_valid_token()
        assert e.value.code == ErrorCode.TRANSIENT_ERROR and e.value.retryable

    def test_unexpected_refresh_error_is_transient(self):
        def refresh(c):
            raise ValueError("weird")

        m, st = manager(refresh=refresh)
        st.save_token(token_record(valid=False))
        with pytest.raises(CerebraError) as e:
            m.ensure_valid_token()
        assert e.value.code == ErrorCode.TRANSIENT_ERROR


# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2, TR-4, FR-5.4
class TestTestingModeExpiry:
    """CON-3: refresh tokens of 'Testing' apps die after 7 days."""

    def refresh_fails(self, c):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    def test_refresh_failure_after_7_days_names_the_testing_limit(self):
        now = NOW + TESTING_MODE_REFRESH_LIFETIME_S + 60
        m, st = manager(refresh=self.refresh_fails, now=now)
        st.save_token(token_record(valid=False))
        with pytest.raises(CerebraError) as e:
            m.ensure_valid_token()
        assert e.value.code == ErrorCode.AUTH_EXPIRED
        assert "7 days" in e.value.message and "cerebra-cli login" in e.value.suggested_action

    def test_refresh_failure_before_7_days_is_generic_revocation(self):
        m, st = manager(refresh=self.refresh_fails, now=NOW + 3600)
        st.save_token(token_record(valid=False))
        with pytest.raises(CerebraError) as e:
            m.ensure_valid_token()
        assert e.value.code == ErrorCode.AUTH_EXPIRED and "revoked" in e.value.message

    def test_status_warns_inside_last_24h(self):
        m, st = manager(now=NOW + TESTING_MODE_REFRESH_LIFETIME_S - 3600)
        st.save_token(token_record())
        s = m.status()
        assert s["reauthorise_soon"] is True and s["testing_mode_expiry_hint_hours"] == 1.0

    def test_status_no_warning_when_fresh_and_never_leaks_tokens(self):
        m, st = manager(now=NOW + 60)
        st.save_token(token_record())
        s = m.status()
        assert s["reauthorise_soon"] is False and s["authenticated"] and not s["write_access"]
        blob = json.dumps(s)
        assert "ya29" not in blob and "refresh" not in blob.replace("refresh tokens", "")
        assert "csecret" not in blob


# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2
class TestInteractiveFlows:
    def setup_method(self):
        FakeFlow.instances.clear()

    def test_login_requests_scopes_and_keeps_stdout_clean(self, capsys):
        m, st = manager(flow=FakeFlow)
        out = m.login([SCOPE_READONLY])
        cap = capsys.readouterr()
        assert "PROMPT-THAT-MUST-NOT-REACH-STDOUT" not in cap.out  # stdout = MCP channel
        assert FakeFlow.instances[0].scopes == [SCOPE_READONLY]
        kw = FakeFlow.instances[0].kwargs
        assert kw["include_granted_scopes"] == "true" and kw["access_type"] == "offline"
        assert out["authenticated"] and st.load_token()["authorised_at"] == NOW

    def test_incremental_upgrade_keeps_existing_scope(self):
        m, st = manager(flow=FakeFlow)
        st.save_token(token_record())
        m.request_additional_scope(SCOPE_WRITE)
        assert set(FakeFlow.instances[0].scopes) == {SCOPE_READONLY, SCOPE_WRITE}
        assert m.has_scope(SCOPE_WRITE) and m.status()["write_access"]

    def test_ensure_scope_upgrades_when_interactive(self):
        m, st = manager(flow=FakeFlow)
        st.save_token(token_record())
        assert m.ensure_scope(SCOPE_WRITE).token == "new"
        assert len(FakeFlow.instances) == 1

    def test_ensure_scope_noninteractive_fails_closed(self):
        m, st = manager(flow=FakeFlow, interactive=False)
        st.save_token(token_record())
        with pytest.raises(CerebraError) as e:
            m.ensure_scope(SCOPE_WRITE)
        assert e.value.code == ErrorCode.INSUFFICIENT_SCOPE and not FakeFlow.instances

    def test_ensure_scope_no_token_noninteractive(self):
        m, _ = manager(flow=FakeFlow, interactive=False)
        with pytest.raises(CerebraError) as e:
            m.ensure_scope(SCOPE_READONLY)
        assert e.value.code == ErrorCode.AUTH_REQUIRED

    def test_ensure_scope_no_token_interactive_logs_in(self):
        m, _ = manager(flow=FakeFlow)
        m.ensure_scope(SCOPE_WRITE)
        assert set(FakeFlow.instances[0].scopes) == {SCOPE_READONLY, SCOPE_WRITE}

    def test_already_granted_scope_needs_no_flow(self):
        m, st = manager(flow=lambda *a: pytest.fail("no flow"))
        st.save_token(token_record(scopes=(SCOPE_WRITE,)))
        assert m.ensure_scope(SCOPE_READONLY).token == "ya29.fake"

    def test_missing_client_credentials(self):
        m, _ = manager(flow=FakeFlow, cid="", secret="")
        with pytest.raises(CerebraError) as e:
            m.login()
        assert "GOOGLE_CLIENT_ID" in e.value.message and not FakeFlow.instances


# mark: FR-5.1, FR-5.2, FR-5.5, CON-3, NFR-SE-2
class TestRevoke:
    def test_revoke_posts_refresh_token_and_deletes_local(self):
        calls = []

        def post(url, **kw):
            calls.append((url, kw))
            return SimpleNamespace(status_code=200)

        m, st = manager(post=post)
        st.save_token(token_record())
        assert m.revoke() == {"revoked_remotely": True, "local_token_deleted": True}
        assert calls[0][0] == "https://oauth2.googleapis.com/revoke"
        assert calls[0][1]["data"] == {"token": "1//refresh"}
        assert st.load_token() is None and not m.is_authenticated()

    def test_revoke_offline_still_deletes_locally(self):
        def post(url, **kw):
            raise ConnectionError()

        m, st = manager(post=post)
        st.save_token(token_record())
        assert m.revoke() == {"revoked_remotely": False, "local_token_deleted": True}
        assert st.load_token() is None

    def test_revoke_without_token(self):
        m, _ = manager()
        assert m.revoke()["local_token_deleted"] is True

    def test_revoke_http_error_status(self):
        m, st = manager(post=lambda u, **k: SimpleNamespace(status_code=400))
        st.save_token(token_record())
        assert m.revoke()["revoked_remotely"] is False


def headless_manager(*, scopes="", refresh=None, store=None, interactive=True, token="1//env-rt"):
    store = store or InMemoryTokenStore()
    return OAuthManager(
        store,
        "cid",
        "csecret",
        FakeClock(NOW),
        interactive=interactive,
        flow_factory=lambda *a: pytest.fail("headless runs must never open a browser"),
        refresh_fn=refresh,
        stderr=io.StringIO(),
        env_refresh_token=token,
        env_refresh_token_scopes=scopes,
    ), store


def _refresh_ok(counter):
    def refresh(c):
        from datetime import datetime

        counter.append(1)
        c.token = "headless-access"
        c.expiry = datetime(2099, 1, 1)

    return refresh


# mark: FR-5.2, FR-5.3, NFR-SE-2, TR-5
class TestHeadlessRefreshToken:
    def test_default_scope_is_readonly_and_authenticated(self):
        m, _ = headless_manager()
        assert m.headless and m.is_authenticated()
        assert m.granted_scopes() == {SCOPE_READONLY}
        assert not m.has_scope(SCOPE_WRITE)

    def test_declared_scopes_split_on_space_and_comma(self):
        m, _ = headless_manager(scopes=f"{SCOPE_READONLY}, {SCOPE_WRITE}")
        assert m.granted_scopes() == {SCOPE_READONLY, SCOPE_WRITE}
        assert m.has_scope(SCOPE_WRITE)

    def test_refreshes_in_memory_and_never_persists(self):
        calls: list[int] = []
        m, store = headless_manager(refresh=_refresh_ok(calls))
        assert m.get_credentials().token == "headless-access"
        assert store.load_token() is None  # NFR-SE-2: nothing written
        assert calls == [1]

    def test_access_token_cached_between_calls(self):
        calls: list[int] = []
        m, _ = headless_manager(refresh=_refresh_ok(calls))
        m.get_credentials()
        m.get_credentials()
        m.ensure_valid_token()
        assert calls == [1]  # one refresh, not one per API call

    def test_env_token_wins_over_stored_token(self):
        m, store = headless_manager(refresh=_refresh_ok([]))
        store.save_token(token_record(scopes=(SCOPE_WRITE,)))
        assert m.granted_scopes() == {SCOPE_READONLY}

    def test_missing_scope_never_opens_browser(self):
        m, _ = headless_manager()  # flow_factory fails the test if called
        with pytest.raises(CerebraError) as e:
            m.ensure_scope(SCOPE_WRITE)
        assert e.value.code == ErrorCode.INSUFFICIENT_SCOPE

    def test_revoked_env_token_is_auth_expired(self):
        def refresh(c):
            raise RefreshError("invalid_grant")

        m, _ = headless_manager(refresh=refresh)
        with pytest.raises(CerebraError) as e:
            m.get_credentials()
        assert e.value.code == ErrorCode.AUTH_EXPIRED

    def test_status_and_repr_never_leak_the_token(self):
        m, _ = headless_manager()
        assert "1//env-rt" not in json.dumps(m.status())
        assert m.status()["testing_mode_expiry_hint_hours"] is None  # issue time unknown

    def test_empty_env_token_means_store_mode(self):
        m, store = headless_manager(token="   ")
        assert not m.headless and not m.is_authenticated()
        store.save_token(token_record())
        assert m.is_authenticated()


# mark: FR-5.2, NFR-SE-2
class TestConfigSecretsHidden:
    def test_secrets_not_in_repr(self, monkeypatch):
        from cerebra.config import Config

        monkeypatch.setenv("CEREBRA_REFRESH_TOKEN", "1//super-secret-rt")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "super-secret-cs")
        monkeypatch.setenv("CEREBRA_YOUTUBE_API_KEY", "AIza-super-secret")
        cfg = Config()
        assert cfg.refresh_token == "1//super-secret-rt"
        text = repr(cfg)
        assert "super-secret" not in text

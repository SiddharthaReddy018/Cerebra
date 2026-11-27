"""Application use case: propose_playlist + create_playlist (FR-4).

SAFETY CRITICAL: No write can happen without human-originated confirmation
bound to the proposal_id, digest, account, expiry, and single-use (FR-4.6, ADR-A).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import uuid
from collections.abc import Awaitable, Callable

from cerebra.config import Config
from cerebra.domain.entities import (
    PlaylistItem,
    PlaylistProposal,
    PrivacyStatus,
    ProposalState,
)
from cerebra.domain.errors import (
    CerebraError,
    ErrorCode,
    approval_rejected,
    auth_required,
    missing_constraint,
    proposal_already_used,
    proposal_expired,
    proposal_not_found,
    quota_budget_exceeded,
)
from cerebra.domain.playlist_solver import solve_playlist
from cerebra.domain.quota import QUOTA_COSTS, QuotaLedger
from cerebra.domain.state_machine import transition
from cerebra.ports.interfaces import (
    ApprovalStore,
    AuditLog,
    CacheStore,
    Clock,
    PlaylistWriter,
    ProposalStore,
    PublicVideoReader,
)

AccountIdProvider = Callable[[], Awaitable[str]]


def compute_digest(proposal_id: str, topic: str, video_ids: list[str], account_id: str = "") -> str:
    """SHA-256 over the proposal content (ADR-A §1). ``account_id`` binds it to the OAuth
    account when known (ADR-A §2); omitted from the payload when empty so fixture-mode
    digests are unchanged."""
    payload: dict[str, object] = {
        "proposal_id": proposal_id,
        "topic": topic,
        "item_ids": video_ids,
    }
    if account_id:
        payload["account_id"] = account_id
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ProposePlaylistUseCase:
    """FR-4.1 to FR-4.5: propose a playlist without creating anything."""

    def __init__(
        self,
        video_reader: PublicVideoReader,
        quota: QuotaLedger,
        proposal_store: ProposalStore,
        cache: CacheStore,
        clock: Clock,
        config: Config,
        is_authenticated: bool = False,
        account_id_provider: AccountIdProvider | None = None,
    ) -> None:
        self._video = video_reader
        self._quota = quota
        self._proposal_store = proposal_store
        self._cache = cache
        self._clock = clock
        self._cfg = config
        self._authenticated = is_authenticated
        self._account_provider = account_id_provider

    async def propose(
        self,
        topic: str | None,
        total_duration_seconds: int | None,
        max_video_duration_seconds: int | None = None,
        language: str | None = None,
    ) -> dict[str, object]:
        """FR-4.1 to FR-4.5."""
        if not self._authenticated:
            raise auth_required()

        # FR-4.1: check mandatory constraints
        missing = []
        if not topic:
            missing.append("topic")
        if total_duration_seconds is None:
            missing.append("total_duration_seconds")
        if missing:
            raise missing_constraint(missing)

        assert topic is not None
        assert total_duration_seconds is not None

        # FR-4.11: estimate WHOLE workflow cost before any API call
        max_items = min(50, total_duration_seconds // 60)  # rough upper bound
        est = self._quota.estimate_playlist_workflow(num_items=max_items)
        if not self._quota.can_afford(general=est["general"], search=est["search"]):
            raise quota_budget_exceeded(est["general"], self._quota.general_remaining)

        # Reserve quota atomically (ADR-B)
        reserved = self._quota.reserve(general=est["general"], search=est["search"])
        if not reserved:
            raise quota_budget_exceeded(est["general"], self._quota.general_remaining)

        try:
            # ADR-A §2: bind the proposal to the authorised account (live adapter only)
            account_id = await self._account_provider() if self._account_provider else ""

            # Build candidates
            candidates = await self._video.search_videos(
                query=topic, max_results=self._cfg.max_candidates, language=language
            )
            self._quota.consume("search.list", 1)

            # Apply language + max-duration hard constraints, then solve
            solver_result = solve_playlist(
                topic=topic,
                total_duration_seconds=total_duration_seconds,
                candidates=candidates,
                max_video_duration_seconds=max_video_duration_seconds,
                language=language,
                fill_ratio=self._cfg.playlist_fill_ratio,
            )

            # Create proposal
            now_iso = self._clock.utcnow_iso()
            expires_at = self._clock.utcnow_timestamp() + self._cfg.proposal_expiry_seconds

            expires_iso = datetime.datetime.fromtimestamp(expires_at, tz=datetime.UTC).isoformat()

            proposal_id = str(uuid.uuid4())
            digest = compute_digest(
                proposal_id, topic, [i.video_id for i in solver_result.items], account_id
            )

            proposal = PlaylistProposal(
                proposal_id=proposal_id,
                topic=topic,
                total_duration_seconds=total_duration_seconds,
                max_video_duration_seconds=max_video_duration_seconds,
                language=language,
                items=solver_result.items,
                created_at=now_iso,
                expires_at=expires_iso,
                state=ProposalState.PROPOSED,
                digest=digest,
                privacy_status=PrivacyStatus.PRIVATE,
                quota_reservation_general=est["general"],
                quota_reservation_write=est.get("write", 0),
                account_id=account_id,
            )

            # FR-4.5: persist proposal BEFORE returning (no insert while PROPOSED)
            self._proposal_store.save(proposal)

            # Build output for FR-4.4
            return {
                "proposal_id": proposal_id,
                "digest": digest,
                "topic": f"[DATA] {topic}",
                "feasible": solver_result.feasible,
                "infeasible_reason": solver_result.infeasible_reason
                if not solver_result.feasible
                else "",
                "total_duration": proposal.total_duration_human(),
                "total_duration_seconds": proposal.total_actual_seconds,
                "requested_duration_seconds": total_duration_seconds,
                "video_count": len(solver_result.items),
                "items": [
                    {
                        "video_id": item.video_id,
                        "title": f"[DATA] {item.title}",
                        "channel": f"[DATA] {item.channel_title}",
                        "duration": item.duration_human(),
                        "duration_seconds": item.duration_seconds,
                        "url": item.url,
                    }
                    for item in solver_result.items
                ],
                "expires_at": expires_iso,
                "privacy_status": "private (default, FR-4.9)",
                "constraint_check": {
                    "language": language or "any",
                    "max_video_duration_seconds": max_video_duration_seconds or "none",
                    "fill_ratio_met": solver_result.feasible,
                },
                "confirmation_instructions": (
                    "To create this playlist, call create_playlist with the proposal_id "
                    f"({proposal_id!r}) and digest ({digest!r}). "
                    "This requires explicit human approval — the AI client cannot approve on your behalf."
                ),
            }
        except Exception:
            # Release reservation on failure
            self._quota.release(general=est["general"], search=est["search"])
            raise

    def _compute_digest(self, proposal_id: str, topic: str, items: list[PlaylistItem]) -> str:
        return compute_digest(proposal_id, topic, [i.video_id for i in items])


class CreatePlaylistUseCase:
    """FR-4.6 to FR-4.13: create playlist after verified human confirmation.

    SAFETY: No playlist write without human-originated confirmation
    bound to proposal_id + digest + expiry + single-use (FR-4.6, ADR-A).

    Write protocol (ADR-C): state is persisted BEFORE each write, per-item state is tracked,
    and a failure is never blindly retried here — ``RecoverPlaylistUseCase`` reconciles
    against remote state first.
    """

    #: Error codes after which continuing to insert items is pointless.
    _STOP_CODES = frozenset(
        {
            ErrorCode.QUOTA_EXHAUSTED,
            ErrorCode.QUOTA_BUDGET_EXCEEDED,
            ErrorCode.AUTH_REQUIRED,
            ErrorCode.AUTH_EXPIRED,
            ErrorCode.INSUFFICIENT_SCOPE,
        }
    )

    def __init__(
        self,
        playlist_writer: PlaylistWriter,
        quota: QuotaLedger,
        proposal_store: ProposalStore,
        audit_log: AuditLog,
        clock: Clock,
        config: Config,
        approval_store: ApprovalStore | None = None,
        account_id_provider: AccountIdProvider | None = None,
        require_approval: bool = False,
    ) -> None:
        self._writer = playlist_writer
        self._quota = quota
        self._proposal_store = proposal_store
        self._audit = audit_log
        self._clock = clock
        self._cfg = config
        self._approvals = approval_store
        self._account_provider = account_id_provider
        self._require_approval = require_approval
        if require_approval and approval_store is None:
            raise ValueError("require_approval=True needs an approval_store (ADR-A §3)")

    async def create(
        self,
        proposal_id: str,
        confirmation_digest: str,
        privacy_status: str = "private",
    ) -> dict[str, object]:
        """FR-4.6 to FR-4.13: validate approval and create playlist."""
        proposal = self._proposal_store.load(proposal_id)
        if proposal is None:
            raise proposal_not_found(proposal_id)

        # FR-4.7: reject expired
        now_ts = self._clock.utcnow_timestamp()
        expires_ts = datetime.datetime.fromisoformat(proposal.expires_at).timestamp()
        if now_ts > expires_ts:
            self._proposal_store.update_state(proposal_id, state=ProposalState.EXPIRED)
            self._quota.release(general=proposal.quota_reservation_general, search=0)
            raise proposal_expired(proposal_id)

        # FR-4.7: reject already-used
        if proposal.state != ProposalState.PROPOSED:
            raise proposal_already_used(proposal_id)

        # ADR-A §1/§5: digest must match; constant-time comparison
        if not hmac.compare_digest(confirmation_digest.encode(), proposal.digest.encode()):
            raise approval_rejected(
                f"Digest mismatch: provided {confirmation_digest[:8]!r}... "
                f"does not match proposal {proposal.digest[:8]!r}..."
            )

        # ADR-A §2: account binding (live adapter only)
        if self._account_provider is not None:
            current = await self._account_provider()
            if not hmac.compare_digest(current.encode(), proposal.account_id.encode()):
                raise approval_rejected("proposal was created for a different YouTube account")
            recomputed = compute_digest(
                proposal_id, proposal.topic, [i.video_id for i in proposal.items], current
            )
            if not hmac.compare_digest(recomputed.encode(), proposal.digest.encode()):
                raise approval_rejected("stored proposal content no longer matches its digest")

        # ADR-A §3: the human must have approved out-of-band (cerebra-cli approve)
        if self._require_approval:
            self._verify_human_approval(proposal)

        try:
            ps = PrivacyStatus(privacy_status)
        except ValueError:
            ps = PrivacyStatus.PRIVATE

        # FR-5.8 / BR-5: refuse BEFORE the first write API call if the whole write won't fit
        needed = (
            QUOTA_COSTS["playlists.insert"]
            + len(proposal.items) * QUOTA_COSTS["playlistItems.insert"]
        )
        held = proposal.quota_reservation_general if self._quota.holds_reservations else 0
        extra = max(0, needed - held)
        if extra and not self._quota.can_afford(general=extra):
            raise quota_budget_exceeded(needed, self._quota.general_remaining)

        # FR-5.1 / ADR-D: upgrade to the write scope BEFORE anything is persisted or written.
        # A failure here leaves the proposal PROPOSED so the human can fix auth and retry.
        await self._writer.ensure_write_access()

        # Single-use approval is burned only now that every pre-write check has passed.
        if self._require_approval:
            assert self._approvals is not None
            if not self._approvals.consume(proposal_id):
                raise proposal_already_used(proposal_id)

        # FR-4.5: no insert while PROPOSED. PROPOSED → CONFIRMED → PARTIAL (ADR-C §2 step 1-2)
        state = transition(proposal_id, proposal.state, ProposalState.CONFIRMED)
        self._proposal_store.update_state(proposal_id, state=state, privacy_status=ps)
        state = transition(proposal_id, state, ProposalState.PARTIAL)
        item_states = {i.video_id: "PENDING" for i in proposal.items}
        self._proposal_store.update_state(
            proposal_id, state=state, playlist_id=None, item_states=item_states
        )

        # playlists.insert (never retried here)
        try:
            playlist_id = await self._writer.create_playlist(
                title=proposal.topic,
                description=(
                    f"Created by Cerebra from proposal {proposal_id}. Topic: {proposal.topic}"
                ),
                privacy_status=ps.value,
            )
        except Exception as exc:
            await self._on_playlist_create_failure(proposal_id, proposal, exc)
            raise  # pragma: no cover - _on_playlist_create_failure always raises

        self._quota.consume("playlists.insert", 1)
        self._proposal_store.update_state(proposal_id, playlist_id=playlist_id)

        # playlistItems.insert, one persisted step at a time
        item_states = await self._insert_items(
            proposal_id, playlist_id, proposal.items, item_states, start_position=0
        )
        return self._finish(proposal_id, proposal, playlist_id, item_states, ps)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _verify_human_approval(self, proposal: PlaylistProposal) -> None:
        assert self._approvals is not None
        rec = self._approvals.get(proposal.proposal_id)
        if rec is None:
            raise approval_rejected(
                "no human approval is recorded for this proposal. Review it in your terminal "
                f"and run: cerebra-cli approve {proposal.proposal_id}"
            )
        if rec.get("used") != "0":
            raise proposal_already_used(proposal.proposal_id)
        if not hmac.compare_digest(rec["digest"].encode(), proposal.digest.encode()):
            raise approval_rejected("the recorded approval does not match this proposal's digest")
        if not hmac.compare_digest(rec["account_id"].encode(), proposal.account_id.encode()):
            raise approval_rejected("the recorded approval was given for a different account")

    async def _on_playlist_create_failure(
        self, proposal_id: str, proposal: PlaylistProposal, exc: Exception
    ) -> None:
        """ADR-C §6: ambiguous failures stay PARTIAL; definitive failures become FAILED."""
        ambiguous = not isinstance(exc, CerebraError) or exc.code == ErrorCode.TRANSIENT_ERROR
        if ambiguous:
            self._proposal_store.update_state(proposal_id, ambiguous=True)
            self._audit.log_event(
                proposal_id, "", "ambiguous_failure:playlists.insert", self._clock.utcnow_iso()
            )
            # ADR-B §5: do NOT release the reservation; it may have been billed.
            raise CerebraError(
                ErrorCode.PARTIAL_CREATION,
                "The playlist request did not return a confirmed result, so the playlist may "
                "or may not exist on YouTube. Nothing was retried.",
                retryable=False,
                suggested_action=f"Run `cerebra-cli recover {proposal_id}` to reconcile safely.",
            ) from exc
        self._proposal_store.update_state(proposal_id, state=ProposalState.FAILED)
        self._quota.release(general=proposal.quota_reservation_general)

    async def _insert_items(
        self,
        proposal_id: str,
        playlist_id: str,
        items: list[PlaylistItem],
        item_states: dict[str, str],
        start_position: int,
    ) -> dict[str, str]:
        position = start_position
        for item in items:
            if item_states.get(item.video_id) == "INSERTED":
                continue
            try:
                # position == current length, so earlier failures never leave gaps
                await self._writer.add_playlist_item(playlist_id, item.video_id, position)
            except Exception as exc:
                item_states[item.video_id] = "FAILED"
                self._proposal_store.update_state(proposal_id, item_states=dict(item_states))
                if isinstance(exc, CerebraError) and exc.code in self._STOP_CODES:
                    break
                if not isinstance(exc, CerebraError) or exc.code == ErrorCode.TRANSIENT_ERROR:
                    self._audit.log_event(
                        proposal_id,
                        playlist_id,
                        f"ambiguous_failure:playlistItems.insert:{item.video_id}",
                        self._clock.utcnow_iso(),
                    )
                continue
            self._quota.consume("playlistItems.insert", 1)
            item_states[item.video_id] = "INSERTED"
            self._proposal_store.update_state(proposal_id, item_states=dict(item_states))
            position += 1
        return item_states

    def _finish(
        self,
        proposal_id: str,
        proposal: PlaylistProposal,
        playlist_id: str,
        item_states: dict[str, str],
        ps: PrivacyStatus,
    ) -> dict[str, object]:
        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        failed_items = [
            i.video_id for i in proposal.items if item_states.get(i.video_id) != "INSERTED"
        ]
        if failed_items:
            # FR-4.13: partial creation — stays PARTIAL, report, allow reconciliation
            self._proposal_store.update_state(
                proposal_id,
                state=ProposalState.PARTIAL,
                failed_item_ids=failed_items,
                playlist_url=playlist_url,
            )
            self._audit.log_creation(proposal_id, playlist_id, self._clock.utcnow_iso())
            return {
                "status": "PARTIAL",
                "playlist_id": playlist_id,
                "playlist_url": playlist_url,
                "failed_video_ids": failed_items,
                "message": (
                    f"Playlist created but {len(failed_items)} video(s) could not be added. "
                    f"Run `cerebra-cli recover {proposal_id}` to add the missing items safely."
                ),
            }

        self._proposal_store.update_state(
            proposal_id,
            state=ProposalState.CREATED,
            playlist_url=playlist_url,
            failed_item_ids=[],
        )
        self._quota.release(
            general=max(
                0,
                proposal.quota_reservation_general
                - QUOTA_COSTS["playlists.insert"]
                - len(proposal.items) * QUOTA_COSTS["playlistItems.insert"],
            )
        )
        # FR-4.8: audit log (time, proposal_id, playlist_id only)
        self._audit.log_creation(proposal_id, playlist_id, self._clock.utcnow_iso())
        return {
            "status": "CREATED",
            "playlist_id": playlist_id,
            "playlist_url": playlist_url,
            "video_count": len(proposal.items),
            "total_duration": proposal.total_duration_human(),
            "privacy_status": ps.value,
        }

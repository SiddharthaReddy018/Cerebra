"""Composition root – the ONLY place concrete dependencies are built (DIP).

No other module imports concrete infrastructure classes directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cerebra.adapters.fixtures.fixture_adapter import (
    FixtureChannelReader,
    FixturePlaylistWriter,
    FixtureUserLibraryReader,
    FixtureVideoReader,
)
from cerebra.adapters.youtube.live_adapter import LiveYouTubeAdapter
from cerebra.application.analyse import AnalyseEngagementUseCase
from cerebra.application.find_similar import FindSimilarUseCase
from cerebra.application.playlist import CreatePlaylistUseCase, ProposePlaylistUseCase
from cerebra.application.recommend import RecommendVideosUseCase
from cerebra.application.recover import RecoverPlaylistUseCase
from cerebra.config import Config
from cerebra.domain.quota import QuotaLedger
from cerebra.infrastructure.auth.oauth_manager import OAuthManager
from cerebra.infrastructure.auth.token_store import build_token_store
from cerebra.infrastructure.clock import RealClock
from cerebra.infrastructure.persistence.sqlite_stores import (
    SQLiteApprovalStore,
    SQLiteAuditLog,
    SQLiteCacheStore,
    SQLiteDB,
    SQLiteProposalStore,
    SQLiteQuotaStore,
)
from cerebra.infrastructure.quota.adapter_metered import AdapterMeteredQuota
from cerebra.infrastructure.quota.persistent_ledger import PersistentQuotaLedger
from cerebra.ports.interfaces import (
    ApprovalStore,
    CacheStore,
    Clock,
    ProposalStore,
)


class _LiveAuthFlag:
    """Truthy iff an OAuth token exists *now* (re-evaluated per use, so a ``cerebra-cli login``
    in another terminal takes effect without restarting the server)."""

    def __init__(self, oauth: OAuthManager) -> None:
        self._oauth = oauth

    def __bool__(self) -> bool:
        return self._oauth.is_authenticated()


@dataclass
class Container:
    """All assembled use cases and shared infrastructure."""

    quota: QuotaLedger
    find_similar: FindSimilarUseCase
    recommend: RecommendVideosUseCase
    analyse: AnalyseEngagementUseCase
    propose: ProposePlaylistUseCase
    create_pl: CreatePlaylistUseCase
    is_authenticated: Any  # bool in fixture mode; a live flag object (bool()-able) otherwise
    # Added in Phase 3/4 (defaults keep older call sites working)
    recover: RecoverPlaylistUseCase | None = None
    proposal_store: ProposalStore | None = None
    approvals: ApprovalStore | None = None
    oauth: OAuthManager | None = None
    clock: Clock | None = None
    live_adapter: LiveYouTubeAdapter | None = None
    live: bool = False
    cache: CacheStore | None = None  # NFR-PV-2: `cerebra-cli clear-cache`


def build_container(
    cfg: Config,
    use_fixtures: bool = True,
    *,
    oauth: OAuthManager | None = None,
    service_factory: Callable[[Any, str], Any] | None = None,
    clock: Clock | None = None,
) -> Container:
    """Build the dependency graph.

    ``use_fixtures=True``  → fixture adapter, no network, no OAuth (CI, NFR-TE-1).
    ``use_fixtures=False`` → live YouTube adapter behind the SAME ports (CON-6, LSP).

    ``oauth`` / ``service_factory`` / ``clock`` are injection points for tests only.
    """
    cfg.ensure_data_dir()
    db = SQLiteDB(cfg.db_path)
    cache = SQLiteCacheStore(
        db,
        max_size=cfg.cache_max_size,
        max_ttl_seconds=cfg.cache_retention_days * 86400,
    )
    proposal_store = SQLiteProposalStore(db)
    audit_log = SQLiteAuditLog(db)
    approvals = SQLiteApprovalStore(db)
    quota_store = SQLiteQuotaStore(db)
    clock = clock or RealClock()

    if use_fixtures:
        quota: QuotaLedger = QuotaLedger(
            general_budget=cfg.daily_quota_budget,
            search_budget=cfg.search_quota_budget,
        )
        uc_quota: Any = quota
        video_reader: Any = FixtureVideoReader()
        channel_reader: Any = FixtureChannelReader()
        user_library: Any = FixtureUserLibraryReader()
        playlist_writer: Any = FixturePlaylistWriter()
        is_authenticated: Any = True  # fixture mode: always authenticated
        account_provider = None
        require_approval = False
        live_adapter = None
    else:
        # Quota survives restarts and resets at midnight Pacific (ADR-B).
        quota = PersistentQuotaLedger(
            quota_store,
            clock,
            general_budget=cfg.daily_quota_budget,
            search_budget=cfg.search_quota_budget,
        )
        oauth = oauth or OAuthManager(
            build_token_store(cfg.token_file, cfg.use_keyring),
            cfg.google_client_id,
            cfg.google_client_secret,
            clock,
            interactive=cfg.interactive_auth,
            env_refresh_token=cfg.refresh_token,
            env_refresh_token_scopes=cfg.refresh_token_scopes,
        )
        live_adapter = LiveYouTubeAdapter(
            quota, clock, oauth, api_key=cfg.youtube_api_key, service_factory=service_factory
        )
        # The adapter meters each call; use cases only make the FR-5.8 up-front decision.
        uc_quota = AdapterMeteredQuota(quota)
        video_reader = channel_reader = user_library = playlist_writer = live_adapter
        is_authenticated = _LiveAuthFlag(oauth)
        account_provider = live_adapter.get_account_id
        require_approval = True  # NFR-SA-1 / ADR-A: live writes ALWAYS need CLI approval

    find_similar = FindSimilarUseCase(
        video_reader=video_reader,
        channel_reader=channel_reader,
        quota=uc_quota,
        cache=cache,
        config=cfg,
    )
    recommend = RecommendVideosUseCase(
        video_reader=video_reader,
        user_library=user_library,
        quota=uc_quota,
        cache=cache,
        config=cfg,
        is_authenticated=is_authenticated,
    )
    analyse = AnalyseEngagementUseCase(
        user_library=user_library,
        quota=uc_quota,
        cache=cache,
        config=cfg,
        is_authenticated=is_authenticated,
    )
    propose = ProposePlaylistUseCase(
        video_reader=video_reader,
        quota=uc_quota,
        proposal_store=proposal_store,
        cache=cache,
        clock=clock,
        config=cfg,
        is_authenticated=is_authenticated,
        account_id_provider=account_provider,
    )
    create_pl = CreatePlaylistUseCase(
        playlist_writer=playlist_writer,
        quota=uc_quota,
        proposal_store=proposal_store,
        audit_log=audit_log,
        clock=clock,
        config=cfg,
        approval_store=approvals,
        account_id_provider=account_provider,
        require_approval=require_approval,
    )
    recover = RecoverPlaylistUseCase(
        playlist_writer=playlist_writer,
        user_library=user_library,
        quota=uc_quota,
        proposal_store=proposal_store,
        audit_log=audit_log,
        clock=clock,
        config=cfg,
        account_id_provider=account_provider,
    )

    return Container(
        quota=quota,
        find_similar=find_similar,
        recommend=recommend,
        analyse=analyse,
        propose=propose,
        create_pl=create_pl,
        is_authenticated=is_authenticated,
        recover=recover,
        proposal_store=proposal_store,
        approvals=approvals,
        oauth=oauth,
        clock=clock,
        live_adapter=live_adapter,
        live=not use_fixtures,
        cache=cache,
    )

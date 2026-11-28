"""Ports – narrow Protocol interfaces (ISP, DIP).

Each use case depends only on the port(s) it needs.
Both the live YouTube adapter and the fixture adapter satisfy these contracts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cerebra.domain.entities import (
    ChannelMetadata,
    PlaylistProposal,
    VideoMetadata,
)

# ── Video / Channel reading ──────────────────────────────────────────────────


@runtime_checkable
class PublicVideoReader(Protocol):
    """Read public video metadata without user auth."""

    async def get_video(self, video_id: str) -> VideoMetadata:
        """Fetch a single video's metadata. Raises CerebraError on failure."""
        ...

    async def get_videos(self, video_ids: list[str]) -> list[VideoMetadata]:
        """Fetch multiple videos by ID (batched). Missing IDs are omitted."""
        ...

    async def search_videos(
        self,
        query: str,
        max_results: int = 25,
        language: str | None = None,
    ) -> list[VideoMetadata]:
        """Search for videos matching query. Uses search.list (costly: 100 units)."""
        ...

    async def get_channel_uploads(
        self, channel_id: str, max_results: int = 50
    ) -> list[VideoMetadata]:
        """Fetch recent uploads for a channel via uploads playlist (cheap)."""
        ...


@runtime_checkable
class PublicChannelReader(Protocol):
    """Read public channel metadata without user auth."""

    async def get_channel(self, channel_id: str) -> ChannelMetadata:
        """Fetch channel metadata by ID."""
        ...

    async def resolve_channel(self, kind: str, value: str) -> str:
        """Resolve handle/custom/user to a channel_id. Returns channel_id."""
        ...

    async def search_channels(self, query: str, max_results: int = 25) -> list[ChannelMetadata]:
        """Search for channels matching query."""
        ...


@runtime_checkable
class UserLibraryReader(Protocol):
    """Read the authenticated user's YouTube library."""

    async def get_liked_videos(self, max_results: int = 1000) -> list[VideoMetadata]:
        """Return up to max_results liked videos.

        IMPORTANT: YouTube API caps liked video retrieval at ~1,000 items.
        This is NOT complete like history — the API does not provide full history.
        Like timestamps are unavailable; publishedAt is the video's publish date.
        """
        ...

    async def get_subscriptions(self, max_results: int = 500) -> list[ChannelMetadata]:
        """Return the user's subscribed channels."""
        ...

    async def get_playlists(self, max_results: int = 200) -> list[dict[str, object]]:
        """Return the user's playlists as raw dicts (id, title, item_count)."""
        ...

    async def get_playlist_items(
        self, playlist_id: str, max_results: int = 500
    ) -> list[VideoMetadata]:
        """Return videos in a playlist. snippet.publishedAt = add date (available)."""
        ...


@runtime_checkable
class PlaylistWriter(Protocol):
    """Write operations — playlists.insert + playlistItems.insert ONLY (NFR-SA-2)."""

    async def ensure_write_access(self) -> None:
        """Make sure write credentials are available BEFORE any write (FR-5.1, ADR-D).

        The live adapter upgrades ``youtube.readonly`` to ``youtube`` here (incremental
        authorisation). Raises CerebraError (AUTH_REQUIRED / INSUFFICIENT_SCOPE /
        AUTH_EXPIRED) without having made any write call.
        """
        ...

    async def create_playlist(
        self,
        title: str,
        description: str,
        privacy_status: str,
    ) -> str:
        """Create a playlist and return its playlist_id."""
        ...

    async def add_playlist_item(
        self,
        playlist_id: str,
        video_id: str,
        position: int,
    ) -> None:
        """Add a single video to a playlist at given position."""
        ...


# ── Infrastructure ports ─────────────────────────────────────────────────────


@runtime_checkable
class Clock(Protocol):
    """Injected clock for deterministic time (testability)."""

    def utcnow_iso(self) -> str:
        """Return current UTC time as ISO 8601 string."""
        ...

    def utcnow_timestamp(self) -> float:
        """Return current UTC time as POSIX timestamp."""
        ...


@runtime_checkable
class QuotaStore(Protocol):
    """Persistence for quota ledger state (surviving restarts)."""

    def save_state(self, state: dict[str, object]) -> None: ...
    def load_state(self) -> dict[str, object]: ...


@runtime_checkable
class ProposalStore(Protocol):
    """Persistent storage for playlist proposals (FR-4.5, ADR-C)."""

    def save(self, proposal: PlaylistProposal) -> None: ...
    def load(self, proposal_id: str) -> PlaylistProposal | None: ...
    def update_state(self, proposal_id: str, **kwargs: object) -> None: ...
    def list_active(self) -> list[PlaylistProposal]: ...


@runtime_checkable
class CacheStore(Protocol):
    """Cache for YouTube API responses (FR-5.9, NFR-PV-2)."""

    def get(self, key: str) -> object | None: ...
    def set(self, key: str, value: object, ttl_seconds: int) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear_all(self) -> None: ...
    def stats(self) -> dict[str, object]: ...


@runtime_checkable
class TokenStore(Protocol):
    """Secure token storage (FR-5.2, NFR-SE-3)."""

    def save_token(self, token_data: dict[str, object]) -> None: ...
    def load_token(self) -> dict[str, object] | None: ...
    def delete_token(self) -> None: ...


@runtime_checkable
class AuditLog(Protocol):
    """Audit log for playlist creation (FR-4.8).

    Stores: time, proposal_id, playlist_id — no personal data beyond IDs.
    """

    def log_creation(
        self,
        proposal_id: str,
        playlist_id: str,
        timestamp: str,
    ) -> None: ...

    def log_event(
        self,
        proposal_id: str,
        playlist_id: str,
        event: str,
        timestamp: str,
    ) -> None:
        """Log a write-lifecycle event (e.g. ``ambiguous_failure``, ``recovered``; ADR-C §6)."""
        ...


@runtime_checkable
class ApprovalStore(Protocol):
    """Out-of-band human approvals written by ``cerebra-cli approve`` (ADR-A §3).

    The MCP client can never write here: only the local CLI, which requires an
    interactive terminal, records approvals.
    """

    def record(self, proposal_id: str, digest: str, account_id: str, approved_at: str) -> None: ...
    def get(self, proposal_id: str) -> dict[str, str] | None: ...
    def consume(self, proposal_id: str) -> bool:
        """Atomically mark an approval as used. Returns False if absent/already used."""
        ...


# ── Aggregate VideoProvider (for SRS traceability, FR-5.17) ──────────────────


class VideoProvider:
    """Aggregate provider holding all sub-readers and writer.

    Composition layer builds this once; use cases receive only the ports they need.
    """

    def __init__(
        self,
        public_video: PublicVideoReader,
        public_channel: PublicChannelReader,
        user_library: UserLibraryReader,
        playlist_writer: PlaylistWriter,
    ) -> None:
        self.public_video = public_video
        self.public_channel = public_channel
        self.user_library = user_library
        self.playlist_writer = playlist_writer

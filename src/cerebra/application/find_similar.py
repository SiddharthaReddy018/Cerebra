"""Application use case: find_similar_videos / find_similar_channels (FR-1)."""

from __future__ import annotations

from cerebra.config import Config
from cerebra.domain.entities import SimilarChannel, SimilarVideo
from cerebra.domain.errors import quota_budget_exceeded
from cerebra.domain.normalise import normalise_channel_ref, normalise_video_ref
from cerebra.domain.quota import QuotaLedger
from cerebra.domain.scoring import rank_channels, rank_videos
from cerebra.ports.interfaces import CacheStore, PublicChannelReader, PublicVideoReader


class FindSimilarUseCase:
    """FR-1: Similarity Discovery.

    Fetches seed metadata, builds candidate set, scores and ranks.
    Checks quota budget before ANY API call (FR-5.8).
    Uses cache (FR-5.9).
    """

    def __init__(
        self,
        video_reader: PublicVideoReader,
        channel_reader: PublicChannelReader,
        quota: QuotaLedger,
        cache: CacheStore,
        config: Config,
    ) -> None:
        self._video = video_reader
        self._channel = channel_reader
        self._quota = quota
        self._cache = cache
        self._cfg = config

    async def find_similar_videos(
        self,
        reference: str,
        limit: int = 10,
    ) -> list[SimilarVideo]:
        """FR-1.1 to FR-1.9: find similar videos for a video reference."""
        # FR-1.1: normalise (raises CerebraError for bad refs — no API call yet)
        video_id = normalise_video_ref(reference)

        # Cache check (FR-5.9)
        cache_key = f"similar_videos:{video_id}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        # Quota check: estimate before any I/O (FR-5.8, FR-4.11)
        est = self._quota.estimate_search_workflow(
            search_calls=1, video_fetch_count=self._cfg.max_candidates
        )
        if not self._quota.can_afford(general=est["general"], search=est["search"]):
            rem = self._quota.general_remaining
            raise quota_budget_exceeded(est["general"], rem)

        # Reserve quota atomically
        reserved = self._quota.reserve(general=est["general"], search=est["search"])
        if not reserved:
            raise quota_budget_exceeded(est["general"], self._quota.general_remaining)

        try:
            # Fetch seed
            seed = await self._video.get_video(video_id)
            self._quota.consume("videos.list", 1)

            # Build candidate set: channel uploads (cheap) + search (costly, scarce)
            candidates = []

            # 1. Same-channel uploads (FR-1.4 — prefer cheap reads)
            channel_uploads = await self._video.get_channel_uploads(seed.channel_id, max_results=50)
            self._quota.consume("playlistItems.list", 1)
            candidates.extend(channel_uploads)

            # 2. Search for related videos (uses search.list — budgeted)
            search_results = await self._video.search_videos(
                query=f"{seed.title} {' '.join(seed.tags[:5])}",
                max_results=min(50, self._cfg.max_candidates),
            )
            self._quota.consume("search.list", 1)
            candidates.extend(search_results)

            # Deduplicate by video_id
            seen: set[str] = set()
            unique_candidates = []
            for c in candidates:
                if c.video_id not in seen:
                    seen.add(c.video_id)
                    unique_candidates.append(c)

            # Score and rank (FR-1.5, FR-1.9)
            scored = rank_videos(seed, unique_candidates)

            # Apply result limit (FR-1.7)
            limit = max(1, min(limit, self._cfg.max_result_limit))
            top = scored[:limit]

            # Build output (FR-1.6)
            result = []
            for rank_i, sr in enumerate(top, start=1):
                # Find original metadata
                orig = next((c for c in unique_candidates if c.video_id == sr.video_id), None)
                if orig is None:
                    continue
                result.append(
                    SimilarVideo(
                        rank=rank_i,
                        video_id=sr.video_id,
                        title=f"[DATA] {orig.title}",
                        channel_title=f"[DATA] {orig.channel_title}",
                        duration_seconds=orig.duration_seconds,
                        score=sr.score,
                        tags_missing=sr.tags_missing,
                        url=orig.url,
                    )
                )

            # Cache result
            self._cache.set(cache_key, result, ttl_seconds=self._cfg.cache_ttl_seconds)
            return result

        finally:
            # Release unused reservation
            self._quota.release(general=est["general"], search=est["search"])

    async def find_similar_channels(
        self,
        reference: str,
        limit: int = 10,
    ) -> list[SimilarChannel]:
        """FR-1.1 to FR-1.9: find similar channels for a channel reference."""
        kind, value = normalise_channel_ref(reference)

        cache_key = f"similar_channels:{kind}:{value}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        est = self._quota.estimate_search_workflow(search_calls=1, channel_fetch_count=50)
        if not self._quota.can_afford(general=est["general"], search=est["search"]):
            raise quota_budget_exceeded(est["general"], self._quota.general_remaining)

        reserved = self._quota.reserve(general=est["general"], search=est["search"])
        if not reserved:
            raise quota_budget_exceeded(est["general"], self._quota.general_remaining)

        try:
            # Resolve to channel_id
            channel_id = await self._channel.resolve_channel(kind, value)
            self._quota.consume("channels.list", 1)

            # Fetch seed channel
            seed = await self._channel.get_channel(channel_id)
            self._quota.consume("channels.list", 1)

            # Find candidates via search
            search_results = await self._channel.search_channels(
                query=f"{seed.title} {' '.join(seed.keywords[:5])}",
                max_results=min(50, self._cfg.max_candidates),
            )
            self._quota.consume("search.list", 1)

            limit = max(1, min(limit, self._cfg.max_result_limit))
            scored = rank_channels(seed, search_results)[:limit]

            result = []
            for rank_i, sr in enumerate(scored, start=1):
                orig = next((c for c in search_results if c.channel_id == sr.channel_id), None)
                if orig is None:
                    continue
                result.append(
                    SimilarChannel(
                        rank=rank_i,
                        channel_id=sr.channel_id,
                        title=f"[DATA] {orig.title}",
                        score=sr.score,
                        subscriber_count=orig.subscriber_count,
                        url=orig.url,
                    )
                )

            self._cache.set(cache_key, result, ttl_seconds=self._cfg.cache_ttl_seconds)
            return result

        finally:
            self._quota.release(general=est["general"], search=est["search"])

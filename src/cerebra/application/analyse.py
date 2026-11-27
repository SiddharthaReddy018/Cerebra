"""Application use case: analyse_engagement (FR-3)."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from cerebra.config import Config
from cerebra.domain.entities import DurationBand, VideoMetadata
from cerebra.domain.errors import CerebraError, ErrorCode, auth_required
from cerebra.domain.quota import QuotaLedger
from cerebra.ports.interfaces import CacheStore, UserLibraryReader

NOT_WATCH_HISTORY = (
    "Results are based on liked videos, subscriptions and playlists — "
    "NOT on watch history. Watch history is not available through the YouTube API (CON-5, BR-3, FR-3.7)."
)
LIKE_DATE_NOTE = (
    "Like timestamps are not available via the YouTube API. "
    "Trend dates shown are based on playlist-add dates (snippet.publishedAt) "
    "and video publication dates — not like dates (FR-3.4 limitation)."
)
LIKED_CAP_NOTE = (
    "Based on up to 1,000 most recently liked videos accessible via the YouTube API; "
    "complete like history is not available."
)


class AnalyseEngagementUseCase:
    """FR-3: Engagement Analysis.

    FR-3.7: Every report must state results are NOT from watch history.
    FR-3.8: Return empty sections + explanation when no data.
    """

    def __init__(
        self,
        user_library: UserLibraryReader,
        quota: QuotaLedger,
        cache: CacheStore,
        config: Config,
        is_authenticated: bool = False,
    ) -> None:
        self._library = user_library
        self._quota = quota
        self._cache = cache
        self._cfg = config
        self._authenticated = is_authenticated

    async def analyse(
        self,
        top_k: int = 5,
        period_a: tuple[str, str] | None = None,
        period_b: tuple[str, str] | None = None,
    ) -> dict[str, object]:
        """FR-3.1 to FR-3.8: engagement analysis."""
        # FR-3.1
        if not self._authenticated:
            raise auth_required()

        liked = await self._library.get_liked_videos(max_results=1000)
        self._quota.consume("videos.list", max(1, len(liked) // 50 + 1))

        # FR-3.8: account may have no YouTube channel (channelNotFound / NO_YOUTUBE_CHANNEL);
        # treat as empty playlists rather than surfacing an internal API error.
        try:
            playlists = await self._library.get_playlists()
            self._quota.consume("playlists.list", 1)
        except CerebraError as _exc:
            if _exc.code == ErrorCode.NO_YOUTUBE_CHANNEL:
                playlists = []
            else:
                raise
        playlist_videos: list[VideoMetadata] = []
        playlist_add_dates: dict[str, str] = {}  # video_id -> add date
        for pl in playlists:
            pid = str(pl.get("id", ""))
            if pid:
                items = await self._library.get_playlist_items(pid, max_results=200)
                self._quota.consume("playlistItems.list", 1)
                for item in items:
                    playlist_videos.append(item)
                    if item.published_at:
                        playlist_add_dates[item.video_id] = item.published_at

        subscriptions = await self._library.get_subscriptions()
        self._quota.consume("subscriptions.list", 1)
        sub_channel_ids = {c.channel_id for c in subscriptions}

        all_videos = liked + playlist_videos
        if not all_videos:
            return self._empty_result()

        # FR-3.2: top K channels
        channel_liked_counts: Counter[str] = Counter()
        channel_titles: dict[str, str] = {}
        for v in liked:
            channel_liked_counts[v.channel_id] += 1
            channel_titles[v.channel_id] = v.channel_title

        channel_playlist_counts: Counter[str] = Counter()
        for v in playlist_videos:
            channel_playlist_counts[v.channel_id] += 1
            channel_titles[v.channel_id] = v.channel_title

        all_channel_ids = set(channel_liked_counts) | set(channel_playlist_counts)
        channel_scores = {
            cid: channel_liked_counts[cid] + channel_playlist_counts[cid] for cid in all_channel_ids
        }
        top_channels = sorted(channel_scores.items(), key=lambda x: -x[1])[:top_k]

        top_channels_result = [
            {
                "channel_id": cid,
                "title": f"[DATA] {channel_titles.get(cid, cid)}",
                "liked_count": channel_liked_counts[cid],
                "playlist_item_count": channel_playlist_counts[cid],
                "is_subscribed": cid in sub_channel_ids,
                "composite_score": score,
            }
            for cid, score in top_channels
        ]

        # FR-3.3: top K categories
        cat_counts: Counter[str] = Counter()
        for v in all_videos:
            if v.category_id:
                cat_counts[v.category_id] += 1
        top_categories = [
            {"category_id": cid, "count": cnt} for cid, cnt in cat_counts.most_common(top_k)
        ]

        # FR-3.5: duration preferences
        durations = [v.duration_seconds for v in all_videos]
        band_counts: Counter[str] = Counter()
        for d in durations:
            band_counts[DurationBand.from_seconds(d).value] += 1

        duration_stats: dict[str, object] = {}
        if durations:
            duration_stats = {
                "mean_seconds": round(statistics.mean(durations)),
                "median_seconds": round(statistics.median(durations)),
                "distribution": {
                    "short_lt_4min": band_counts["short"],
                    "medium_4_to_20min": band_counts["medium"],
                    "long_gt_20min": band_counts["long"],
                },
            }

        # FR-3.4: trends per month (using add dates / publish dates)
        monthly_trends: dict[str, int] = defaultdict(int)
        for v in playlist_videos:
            date = playlist_add_dates.get(v.video_id, v.published_at)
            if date and len(date) >= 7:
                month_key = date[:7]  # YYYY-MM
                monthly_trends[month_key] += 1

        result: dict[str, object] = {
            "top_channels": top_channels_result or [],
            "top_categories": top_categories or [],
            "duration_preferences": duration_stats or {},
            "monthly_trends": dict(sorted(monthly_trends.items())),
            "not_watch_history_statement": NOT_WATCH_HISTORY,
            "like_date_note": LIKE_DATE_NOTE,
            "cap_note": LIKED_CAP_NOTE,
        }

        # FR-3.6: two-period comparison
        if period_a and period_b:
            result["period_comparison"] = self._compare_periods(
                all_videos, playlist_add_dates, period_a, period_b, top_k
            )

        return result

    def _empty_result(self) -> dict[str, object]:
        """FR-3.8: empty sections with explanation."""
        return {
            "top_channels": [],
            "top_categories": [],
            "duration_preferences": {},
            "monthly_trends": {},
            "empty_explanation": (
                "No liked videos, playlists or subscription data was found for the requested period. "
                "Try liking videos or adding them to playlists first."
            ),
            "not_watch_history_statement": NOT_WATCH_HISTORY,
            "like_date_note": LIKE_DATE_NOTE,
            "cap_note": LIKED_CAP_NOTE,
        }

    def _compare_periods(
        self,
        all_videos: list[VideoMetadata],
        add_dates: dict[str, str],
        period_a: tuple[str, str],
        period_b: tuple[str, str],
        top_k: int,
    ) -> dict[str, object]:
        """FR-3.6: compare two periods."""

        def in_period(v: VideoMetadata, start: str, end: str) -> bool:
            date = add_dates.get(v.video_id, v.published_at)
            if not date:
                return False
            return start <= date[:10] <= end

        a_videos = [v for v in all_videos if in_period(v, period_a[0], period_a[1])]
        b_videos = [v for v in all_videos if in_period(v, period_b[0], period_b[1])]

        def period_summary(videos: list[VideoMetadata]) -> dict[str, object]:
            if not videos:
                return {"count": 0, "top_channels": [], "top_categories": [], "duration_stats": {}}
            ch_counts: Counter[str] = Counter(v.channel_id for v in videos)
            cat_counts: Counter[str] = Counter(v.category_id for v in videos if v.category_id)
            durations = [v.duration_seconds for v in videos]
            return {
                "count": len(videos),
                "top_channels": [
                    {"channel_id": c, "count": n} for c, n in ch_counts.most_common(top_k)
                ],
                "top_categories": [
                    {"category_id": c, "count": n} for c, n in cat_counts.most_common(top_k)
                ],
                "duration_stats": {
                    "mean_seconds": round(statistics.mean(durations)) if durations else 0,
                    "median_seconds": round(statistics.median(durations)) if durations else 0,
                },
            }

        return {
            "period_a": {"range": f"{period_a[0]} to {period_a[1]}", **period_summary(a_videos)},
            "period_b": {"range": f"{period_b[0]} to {period_b[1]}", **period_summary(b_videos)},
        }

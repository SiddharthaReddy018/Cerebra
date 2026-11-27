"""Live YouTube Data API v3 adapter (CON-1, CON-6, FR-5.17).

The ONLY module that talks to YouTube. It satisfies the same ports as the fixture adapter
(PublicVideoReader, PublicChannelReader, UserLibraryReader, PlaylistWriter) so the two are
interchangeable (LSP).

Hard rules enforced here
  * Official API only: every request is built from google-api-python-client's bundled
    discovery document for youtube/v3 - no scraping, no unofficial endpoints (CON-1).
  * Only these methods are ever invoked; anything else is refused in ``_execute``:
      reads : videos.list, channels.list, search.list, playlistItems.list,
              playlists.list, subscriptions.list
      writes: playlists.insert, playlistItems.insert            (NFR-SA-2)
  * Quota is reserved BEFORE every API call and consumed after it (FR-5.8, ADR-B).
  * Writes are NEVER retried by this layer (ADR-C §4); reads retry with backoff on 5xx.
  * Reads need ``youtube.readonly``; writes need ``youtube`` (ADR-D).
  * Video/channel text is returned as data; nothing is interpreted (FR-4.10).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import socket
import ssl
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from typing import Any, Protocol

from cerebra.domain.duration import parse_duration
from cerebra.domain.entities import ChannelMetadata, VideoMetadata
from cerebra.domain.errors import (
    CerebraError,
    auth_expired,
    auth_required,
    channel_not_found,
    insufficient_scope,
    no_youtube_channel,
    quota_budget_exceeded,
    quota_exhausted,
    transient_error,
    video_not_found,
    youtube_api_error,
)
from cerebra.domain.quota import QUOTA_COSTS, QuotaLedger
from cerebra.infrastructure.auth.oauth_manager import SCOPE_READONLY, SCOPE_WRITE
from cerebra.ports.interfaces import Clock

READ_METHODS = frozenset(
    {
        "videos.list",
        "channels.list",
        "search.list",
        "playlistItems.list",
        "playlists.list",
        "subscriptions.list",
    }
)
WRITE_METHODS = frozenset({"playlists.insert", "playlistItems.insert"})
ALLOWED_METHODS = READ_METHODS | WRITE_METHODS

PAGE_SIZE = 50  # API maximum for maxResults on list endpoints
MAX_SEARCH_PAGES = 2  # matches ADR-B / estimate_playlist_workflow (2 search pages)
LIKED_VIDEO_API_CAP = 1000  # the API does not expose more than ~1,000 liked videos
MAX_QUERY_LEN = 500

_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PRIVACY = frozenset({"private", "public", "unlisted"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_SCOPE_REASONS = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})

# YouTube rejects '<' and '>' in titles/descriptions; titles are capped at 150 chars.
_MAX_TITLE = 150
_MAX_DESCRIPTION = 5000


class AuthProvider(Protocol):
    """What the adapter needs from the OAuth manager (satisfied by OAuthManager)."""

    def is_authenticated(self) -> bool: ...
    def get_credentials(self, required_scope: str = ...) -> Any: ...
    def ensure_scope(self, scope: str) -> Any: ...


ServiceFactory = Callable[[Any, str], Any]
Sleep = Callable[[float], Awaitable[None]]


def default_service_factory(credentials: Any, api_key: str) -> Any:
    """Build the official YouTube Data API v3 client from the bundled discovery document."""
    from googleapiclient.discovery import build

    if credentials is not None:
        return build(
            "youtube", "v3", credentials=credentials, cache_discovery=False, static_discovery=True
        )
    return build(
        "youtube", "v3", developerKey=api_key, cache_discovery=False, static_discovery=True
    )


class LiveYouTubeAdapter:
    """Concrete adapter for all four YouTube ports."""

    def __init__(
        self,
        quota: QuotaLedger,
        clock: Clock,
        auth: AuthProvider | None = None,
        *,
        api_key: str = "",
        service_factory: ServiceFactory | None = None,
        sleep: Sleep | None = None,
        max_attempts: int = 3,
        backoff_base_s: float = 0.5,
    ) -> None:
        self._quota = quota
        self._clock = clock
        self._auth = auth
        self._api_key = api_key
        self._factory = service_factory or default_service_factory
        self._sleep: Sleep = sleep or asyncio.sleep
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff_base_s
        self._svc_key: tuple[str, str] | None = None
        self._svc: Any = None
        self._account_id: str | None = None

    # ═════════════════════════ service / execution core ═════════════════════════

    def _service(self, scope: str, *, need_user: bool) -> Any:
        """Return a service object whose credentials cover ``scope`` (never opens a browser)."""
        creds: Any = None
        if self._auth is not None and self._auth.is_authenticated():
            creds = self._auth.get_credentials(scope)
        elif need_user or not self._api_key:
            raise auth_required()
        key = (scope, getattr(creds, "token", None) or self._api_key or "")
        if self._svc is None or self._svc_key != key:
            self._svc = self._factory(creds, self._api_key)
            self._svc_key = key
        return self._svc

    async def _execute(
        self,
        method: str,
        build_request: Callable[[Any], Any],
        *,
        scope: str = SCOPE_READONLY,
        need_user: bool = False,
        retry: bool = True,
    ) -> dict[str, Any]:
        """Reserve quota → call → consume. One API call per attempt (FR-5.8)."""
        if method not in ALLOWED_METHODS:  # NFR-SA-2 / CON-1 guard
            raise CerebraError(
                code=youtube_api_error(0, "").code,
                message=f"Refusing to call unsupported YouTube method {method!r}.",
            )
        is_write = method in WRITE_METHODS
        cost = QUOTA_COSTS[method]
        search_units = 1 if method == "search.list" else 0
        attempts = self._max_attempts if (retry and not is_write) else 1

        for attempt in range(1, attempts + 1):
            if not self._quota.reserve(general=cost, search=search_units):
                raise quota_budget_exceeded(cost, self._quota.general_remaining)
            try:
                request = build_request(self._service(scope, need_user=need_user))
                response = await asyncio.to_thread(request.execute)
            except CerebraError:
                self._quota.release(general=cost, search=search_units)
                raise
            except Exception as exc:
                err, charged = self._classify(exc, scope)
                if charged:
                    # Failed/ambiguous calls may still be billed (ADR-B §5): assume consumed.
                    self._quota.consume(method)
                else:
                    self._quota.release(general=cost, search=search_units)
                if err.retryable and attempt < attempts:
                    await self._sleep(self._backoff * (2 ** (attempt - 1)))
                    continue
                raise err from exc
            self._quota.consume(method)
            return response if isinstance(response, dict) else {}
        raise transient_error("retries exhausted")  # pragma: no cover - loop always returns/raises

    def _classify(self, exc: Exception, scope: str) -> tuple[CerebraError, bool]:
        """Map a library/network exception to (CerebraError, quota_assumed_consumed)."""
        status, reason = _http_status_reason(exc)
        if status is not None:
            if status == 401:
                return auth_expired(), False
            if status == 403 and reason in _QUOTA_REASONS:
                return quota_exhausted(self._next_pacific_midnight()), False
            if status == 403 and reason in _SCOPE_REASONS:
                return insufficient_scope(scope), False
            if status in (400, 403) and reason == "youtubeSignupRequired":
                return no_youtube_channel(), False
            if status == 404 and reason == "channelNotFound":
                return no_youtube_channel(), False
            if status == 429 or (status == 403 and reason in _RATE_REASONS) or status >= 500:
                return transient_error(f"HTTP {status} {reason}".strip()), True
            return youtube_api_error(status, reason), True
        if isinstance(
            exc, TimeoutError | socket.timeout | ConnectionError | ssl.SSLError | OSError
        ) or type(exc).__name__ in ("HttpLib2Error", "ServerNotFoundError"):
            return transient_error(f"network failure ({type(exc).__name__})"), True
        return transient_error(f"unexpected {type(exc).__name__}"), True

    def _next_pacific_midnight(self) -> str:
        """Quota resets at midnight Pacific Time (ADR-B §6)."""
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo("America/Los_Angeles")
            now = datetime.datetime.fromtimestamp(self._clock.utcnow_timestamp(), tz=tz)
            nxt = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return nxt.isoformat()
        except Exception:
            return "midnight"

    # ═════════════════════════ PublicVideoReader ═════════════════════════

    async def get_video(self, video_id: str) -> VideoMetadata:
        videos = await self.get_videos([video_id])
        if not videos:
            raise video_not_found(video_id)
        return videos[0]

    async def get_videos(self, video_ids: list[str]) -> list[VideoMetadata]:
        ids = list(dict.fromkeys(v for v in video_ids if v))
        found: dict[str, VideoMetadata] = {}
        for batch in _chunks(ids, PAGE_SIZE):
            resp = await self._execute(
                "videos.list",
                lambda s, b=batch: s.videos().list(  # type: ignore[misc]
                    part="snippet,contentDetails", id=",".join(b), maxResults=PAGE_SIZE
                ),
            )
            for item in resp.get("items", []):
                v = video_from_resource(item)
                found[v.video_id] = v
        return [found[i] for i in ids if i in found]  # missing IDs omitted

    async def search_videos(
        self, query: str, max_results: int = 25, language: str | None = None
    ) -> list[VideoMetadata]:
        q = query.strip()[:MAX_QUERY_LEN]
        if not q or max_results < 1:
            return []
        ids: list[str] = []
        token: str | None = None
        pages = min(MAX_SEARCH_PAGES, -(-max_results // PAGE_SIZE))
        for _ in range(pages):
            kwargs: dict[str, Any] = {
                "part": "snippet",
                "type": "video",
                "q": q,
                "maxResults": min(PAGE_SIZE, max_results - len(ids)),
            }
            if language:
                kwargs["relevanceLanguage"] = language.split("-")[0].lower()
            if token:
                kwargs["pageToken"] = token
            resp = await self._execute("search.list", lambda s, kw=kwargs: s.search().list(**kw))  # type: ignore[misc]
            ids.extend(
                i["id"]["videoId"]
                for i in resp.get("items", [])
                if isinstance(i.get("id"), dict) and i["id"].get("videoId")
            )
            token = resp.get("nextPageToken")
            if not token or len(ids) >= max_results:
                break
        return await self.get_videos(ids[:max_results])

    async def get_channel_uploads(
        self, channel_id: str, max_results: int = 50
    ) -> list[VideoMetadata]:
        uploads = await self._uploads_playlist_id(channel_id)
        ids = [vid for vid, _ in await self._playlist_entries(uploads, max_results)]
        return await self.get_videos(ids)

    # ═════════════════════════ PublicChannelReader ═════════════════════════

    async def get_channel(self, channel_id: str) -> ChannelMetadata:
        resp = await self._execute(
            "channels.list",
            lambda s: s.channels().list(
                part="snippet,statistics,topicDetails,brandingSettings,contentDetails",
                id=channel_id,
                maxResults=1,
            ),
        )
        items = resp.get("items", [])
        if not items:
            raise channel_not_found(channel_id)
        base = channel_from_resource(items[0])
        uploads = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        titles: list[str] = []
        stamps: list[str] = []
        if uploads:
            try:
                resp2 = await self._execute(
                    "playlistItems.list",
                    lambda s: s.playlistItems().list(
                        part="snippet", playlistId=uploads, maxResults=10
                    ),
                )
                for it in resp2.get("items", []):
                    sn = it.get("snippet", {})
                    titles.append(str(sn.get("title", "")))
                    stamps.append(str(sn.get("publishedAt", "")))
            except CerebraError as exc:
                if exc.code.value in (
                    "QUOTA_BUDGET_EXCEEDED",
                    "QUOTA_EXHAUSTED",
                    "AUTH_REQUIRED",
                    "AUTH_EXPIRED",
                ):
                    raise
                # Channel without a readable uploads playlist: metadata is still valid.
        return replace(
            base,
            recent_upload_titles=titles,
            upload_cadence_per_month=_cadence_per_month(stamps),
        )

    async def resolve_channel(self, kind: str, value: str) -> str:
        """Resolve a normalised reference to a channel_id using the cheapest official call."""
        if kind == "id":
            resp = await self._execute(
                "channels.list",
                lambda s: s.channels().list(part="id", id=value, maxResults=1),
            )
            return _first_channel_id(resp, value)
        if kind == "user":
            resp = await self._execute(
                "channels.list",
                lambda s: s.channels().list(part="id", forUsername=value, maxResults=1),
            )
            return _first_channel_id(resp, value)
        if kind in ("handle", "custom"):
            resp = await self._execute(
                "channels.list",
                lambda s: s.channels().list(part="id", forHandle=value, maxResults=1),
            )
            if resp.get("items"):
                return _first_channel_id(resp, value)
            if kind == "handle":
                raise channel_not_found(value)
            # Legacy /c/ custom URLs have no direct lookup: fall back to one search.list.
            found = await self._search_channel_ids(value, 1)
            if not found:
                raise channel_not_found(value)
            return found[0]
        raise channel_not_found(value)

    async def search_channels(self, query: str, max_results: int = 25) -> list[ChannelMetadata]:
        ids = await self._search_channel_ids(query, max_results)
        out: list[ChannelMetadata] = []
        for batch in _chunks(ids, PAGE_SIZE):
            resp = await self._execute(
                "channels.list",
                lambda s, b=batch: s.channels().list(  # type: ignore[misc]
                    part="snippet,statistics,topicDetails,brandingSettings",
                    id=",".join(b),
                    maxResults=PAGE_SIZE,
                ),
            )
            by_id = {c.channel_id: c for c in map(channel_from_resource, resp.get("items", []))}
            out.extend(by_id[i] for i in batch if i in by_id)
        return out

    async def _search_channel_ids(self, query: str, max_results: int) -> list[str]:
        q = query.strip()[:MAX_QUERY_LEN]
        if not q or max_results < 1:
            return []
        resp = await self._execute(
            "search.list",
            lambda s: s.search().list(
                part="snippet", type="channel", q=q, maxResults=min(PAGE_SIZE, max_results)
            ),
        )
        return [
            i["id"]["channelId"]
            for i in resp.get("items", [])
            if isinstance(i.get("id"), dict) and i["id"].get("channelId")
        ][:max_results]

    # ═════════════════════════ UserLibraryReader ═════════════════════════

    async def get_liked_videos(self, max_results: int = 1000) -> list[VideoMetadata]:
        """Up to ~1,000 liked videos. NOT complete history; no like timestamps (CON-5)."""
        limit = min(max_results, LIKED_VIDEO_API_CAP)
        out: list[VideoMetadata] = []
        token: str | None = None
        while len(out) < limit:
            kwargs: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "myRating": "like",
                "maxResults": min(PAGE_SIZE, limit - len(out)),
            }
            if token:
                kwargs["pageToken"] = token
            resp = await self._execute(
                "videos.list",
                lambda s, kw=kwargs: s.videos().list(**kw),  # type: ignore[misc]
                need_user=True,
            )
            out.extend(video_from_resource(i) for i in resp.get("items", []))
            token = resp.get("nextPageToken")
            if not token:
                break
        return out[:limit]

    async def get_subscriptions(self, max_results: int = 500) -> list[ChannelMetadata]:
        out: list[ChannelMetadata] = []
        token: str | None = None
        while len(out) < max_results:
            kwargs: dict[str, Any] = {
                "part": "snippet",
                "mine": True,
                "maxResults": min(PAGE_SIZE, max_results - len(out)),
            }
            if token:
                kwargs["pageToken"] = token
            resp = await self._execute(
                "subscriptions.list",
                lambda s, kw=kwargs: s.subscriptions().list(**kw),  # type: ignore[misc]
                need_user=True,
            )
            for it in resp.get("items", []):
                sn = it.get("snippet", {})
                cid = sn.get("resourceId", {}).get("channelId", "")
                if cid:
                    out.append(
                        ChannelMetadata(
                            channel_id=cid,
                            title=str(sn.get("title", "")),
                            description=str(sn.get("description", "")),
                        )
                    )
            token = resp.get("nextPageToken")
            if not token:
                break
        return out[:max_results]

    async def get_playlists(self, max_results: int = 200) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        token: str | None = None
        while len(out) < max_results:
            kwargs: dict[str, Any] = {
                "part": "snippet,contentDetails,status",
                "mine": True,
                "maxResults": min(PAGE_SIZE, max_results - len(out)),
            }
            if token:
                kwargs["pageToken"] = token
            resp = await self._execute(
                "playlists.list",
                lambda s, kw=kwargs: s.playlists().list(**kw),  # type: ignore[misc]
                need_user=True,
            )
            for it in resp.get("items", []):
                sn = it.get("snippet", {})
                out.append(
                    {
                        "id": it.get("id", ""),
                        "title": sn.get("title", ""),
                        "description": sn.get("description", ""),
                        "item_count": it.get("contentDetails", {}).get("itemCount", 0),
                        "published_at": sn.get("publishedAt", ""),
                        "privacy_status": it.get("status", {}).get("privacyStatus", ""),
                    }
                )
            token = resp.get("nextPageToken")
            if not token:
                break
        return out[:max_results]

    async def get_playlist_items(
        self, playlist_id: str, max_results: int = 500
    ) -> list[VideoMetadata]:
        """Videos in a playlist; ``published_at`` is the date the item was ADDED (port contract)."""
        entries = await self._playlist_entries(playlist_id, max_results, need_user=True)
        added = dict(entries)
        videos = await self.get_videos([vid for vid, _ in entries])
        return [replace(v, published_at=added.get(v.video_id, v.published_at)) for v in videos]

    # ═════════════════════════ PlaylistWriter (NFR-SA-2) ═════════════════════════

    async def ensure_write_access(self) -> None:
        """Incremental authorisation youtube.readonly → youtube (FR-5.1, ADR-D §2)."""
        if self._auth is None:
            raise auth_required()
        # May open a browser consent flow: run off the event loop.
        await asyncio.to_thread(self._auth.ensure_scope, SCOPE_WRITE)

    async def create_playlist(self, title: str, description: str, privacy_status: str) -> str:
        """playlists.insert. Never retried here (ADR-C §4)."""
        if privacy_status not in _PRIVACY:
            privacy_status = "private"  # fail closed to the least-visible option (FR-4.9)
        body = {
            "snippet": {
                "title": _clean_text(title, _MAX_TITLE) or "Cerebra playlist",
                "description": _clean_text(description, _MAX_DESCRIPTION),
            },
            "status": {"privacyStatus": privacy_status},
        }
        resp = await self._execute(
            "playlists.insert",
            lambda s: s.playlists().insert(part="snippet,status", body=body),
            scope=SCOPE_WRITE,
            need_user=True,
            retry=False,
        )
        playlist_id = str(resp.get("id", ""))
        if not playlist_id:
            raise transient_error("playlists.insert returned no playlist id")
        return playlist_id

    async def add_playlist_item(self, playlist_id: str, video_id: str, position: int) -> None:
        """playlistItems.insert. Never retried here (ADR-C §4)."""
        if not _PLAYLIST_ID_RE.match(playlist_id) or not _VIDEO_ID_RE.match(video_id):
            raise youtube_api_error(400, "invalid playlist or video id")
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "position": max(0, int(position)),
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        await self._execute(
            "playlistItems.insert",
            lambda s: s.playlistItems().insert(part="snippet", body=body),
            scope=SCOPE_WRITE,
            need_user=True,
            retry=False,
        )

    # ═════════════════════════ account binding (ADR-A §2) ═════════════════════════

    async def get_account_id(self) -> str:
        """Channel ID of the authorised account (1 unit). Raises NO_YOUTUBE_CHANNEL if none."""
        if self._account_id:
            return self._account_id
        resp = await self._execute(
            "channels.list",
            lambda s: s.channels().list(part="id", mine=True, maxResults=1),
            need_user=True,
        )
        items = resp.get("items", [])
        if not items:
            raise no_youtube_channel()
        self._account_id = str(items[0]["id"])
        return self._account_id

    # ═════════════════════════ shared helpers ═════════════════════════

    async def _uploads_playlist_id(self, channel_id: str) -> str:
        resp = await self._execute(
            "channels.list",
            lambda s: s.channels().list(part="contentDetails", id=channel_id, maxResults=1),
        )
        items = resp.get("items", [])
        if not items:
            raise channel_not_found(channel_id)
        uploads = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads:
            raise channel_not_found(channel_id)
        return str(uploads)

    async def _playlist_entries(
        self, playlist_id: str, max_results: int, *, need_user: bool = False
    ) -> list[tuple[str, str]]:
        """(video_id, added_at) pairs for a playlist, paginated."""
        out: list[tuple[str, str]] = []
        token: str | None = None
        while len(out) < max_results:
            kwargs: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(PAGE_SIZE, max_results - len(out)),
            }
            if token:
                kwargs["pageToken"] = token
            resp = await self._execute(
                "playlistItems.list",
                lambda s, kw=kwargs: s.playlistItems().list(**kw),  # type: ignore[misc]
                need_user=need_user,
            )
            for it in resp.get("items", []):
                vid = it.get("contentDetails", {}).get("videoId") or it.get("snippet", {}).get(
                    "resourceId", {}
                ).get("videoId", "")
                if vid:
                    out.append((str(vid), str(it.get("snippet", {}).get("publishedAt", ""))))
            token = resp.get("nextPageToken")
            if not token:
                break
        return out[:max_results]


# ═════════════════════════ mapping functions (pure) ═════════════════════════


def video_from_resource(item: dict[str, Any]) -> VideoMetadata:
    sn = item.get("snippet", {})
    try:
        seconds = parse_duration(str(item.get("contentDetails", {}).get("duration", "")))
    except ValueError:
        seconds = 0
    tags = [str(t) for t in sn.get("tags", []) if isinstance(t, str)]
    lang = str(sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "")
    thumbs = sn.get("thumbnails", {})
    thumb = next((thumbs[k]["url"] for k in ("medium", "high", "default") if k in thumbs), "")
    return VideoMetadata(
        video_id=str(item.get("id", "")),
        title=str(sn.get("title", "")),
        description=str(sn.get("description", "")),
        channel_id=str(sn.get("channelId", "")),
        channel_title=str(sn.get("channelTitle", "")),
        duration_seconds=seconds,
        category_id=str(sn.get("categoryId", "")),
        language=lang.split("-")[0].lower(),
        tags=tags,
        tags_missing=not tags,
        thumbnail_url=str(thumb),
        published_at=str(sn.get("publishedAt", "")),
    )


def channel_from_resource(item: dict[str, Any]) -> ChannelMetadata:
    sn = item.get("snippet", {})
    stats = item.get("statistics", {})
    count: int | None = None
    if not stats.get("hiddenSubscriberCount") and "subscriberCount" in stats:
        try:
            count = int(stats["subscriberCount"])
        except (TypeError, ValueError):
            count = None
    branding = item.get("brandingSettings", {}).get("channel", {})
    topics = [
        str(u).rstrip("/").rsplit("/", 1)[-1].replace("_", " ")
        for u in item.get("topicDetails", {}).get("topicCategories", [])
    ]
    return ChannelMetadata(
        channel_id=str(item.get("id", "")),
        title=str(sn.get("title", "")),
        description=str(sn.get("description", "")),
        keywords=_parse_keywords(str(branding.get("keywords", ""))),
        topic_categories=topics,
        language=str(sn.get("defaultLanguage", "")).split("-")[0].lower(),
        subscriber_count=count,
    )


def _parse_keywords(raw: str) -> list[str]:
    """brandingSettings keywords: space separated, multi-word phrases in double quotes."""
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', raw)]


def _cadence_per_month(stamps: Iterable[str]) -> float:
    times: list[datetime.datetime] = []
    for s in stamps:
        try:
            times.append(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(times) < 2:
        return 0.0
    span_days = (max(times) - min(times)).total_seconds() / 86400
    if span_days <= 0:
        return 0.0
    return round((len(times) - 1) / span_days * 30.44, 2)


def _first_channel_id(resp: dict[str, Any], ref: str) -> str:
    items = resp.get("items", [])
    if not items:
        raise channel_not_found(ref)
    return str(items[0]["id"])


def _clean_text(text: str, limit: int) -> str:
    cleaned = text.replace("<", "").replace(">", "").replace("\x00", "")
    return cleaned.strip()[:limit]


def _chunks(seq: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _http_status_reason(exc: Exception) -> tuple[int | None, str]:
    """Extract (status, reason) from googleapiclient.errors.HttpError without importing it."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is None:
        return None, ""
    reason = ""
    content = getattr(exc, "content", b"")
    try:
        payload = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
        errors = payload.get("error", {}).get("errors", [])
        if errors:
            reason = str(errors[0].get("reason", ""))
        elif isinstance(payload.get("error"), dict):
            reason = str(payload["error"].get("status", ""))
    except (ValueError, AttributeError, UnicodeDecodeError):
        pass
    try:
        return int(status), reason
    except (TypeError, ValueError):
        return None, ""

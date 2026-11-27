"""LiveYouTubeAdapter behaviour: quota, errors, writes, CON-1/NFR-SA-2 (no network used)."""

# mark: CON-1, FR-5.8, NFR-SA-2, CON-5
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cerebra.adapters.youtube import live_adapter as la
from cerebra.domain.errors import CerebraError, ErrorCode
from cerebra.domain.quota import QuotaLedger
from cerebra.infrastructure.auth.oauth_manager import SCOPE_READONLY, SCOPE_WRITE
from tests.support.fake_youtube import FakeYouTube, fixture_backed_fake, http_error
from tests.support.helpers import FakeAuth, make_live, run

RICK = "dQw4w9WgXcQ"
CH = "UCuAXFkgsw1L7xaCfnd5JJOw"


def code_of(coro):
    with pytest.raises(CerebraError) as exc:
        run(coro)
    return exc.value.code


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5, BR-5, FR-4.11, FR-5.11, TR-6
class TestQuotaBeforeEveryCall:
    def test_over_budget_refused_with_zero_api_calls(self):
        fake = fixture_backed_fake()
        adapter, q, _, _ = make_live(fake, quota=QuotaLedger(general_budget=0, search_budget=100))
        assert code_of(adapter.get_video(RICK)) == ErrorCode.QUOTA_BUDGET_EXCEEDED
        assert fake.calls == []

    def test_search_pool_exhausted_refuses_search_only(self):
        fake = fixture_backed_fake()
        adapter, q, _, _ = make_live(fake, quota=QuotaLedger(10_000, search_budget=0))
        assert code_of(adapter.search_videos("rick")) == ErrorCode.QUOTA_BUDGET_EXCEEDED
        assert fake.calls == []
        assert run(adapter.get_video(RICK)).video_id == RICK  # non-search still fine

    def test_consume_after_success(self):
        fake = fixture_backed_fake()
        adapter, q, _, _ = make_live(fake)
        run(adapter.get_video(RICK))
        st = q.status()
        assert st["general"]["used"] == 1 and st["general"]["reserved"] == 0

    def test_search_costs_100_and_one_search_call(self):
        fake = fixture_backed_fake()
        adapter, q, _, _ = make_live(fake)
        run(adapter.search_videos("rick", max_results=5))
        st = q.status()
        assert st["search"]["used"] == 1
        assert st["general"]["used"] == 101  # 100 search.list + 1 videos.list

    def test_auth_failure_releases_reservation(self):
        fake = fixture_backed_fake()
        adapter, q, _, _ = make_live(fake, auth=FakeAuth(authenticated=False))
        assert code_of(adapter.get_liked_videos()) == ErrorCode.AUTH_REQUIRED
        st = q.status()
        assert st["general"]["used"] == 0 and st["general"]["reserved"] == 0


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5, FR-4.12, FR-5.10, NFR-RE-1, NFR-RE-2, TR-4, TR-2
class TestErrorMapping:
    @pytest.mark.parametrize(
        "status,reason,expected",
        [
            (401, "authError", ErrorCode.AUTH_EXPIRED),
            (403, "quotaExceeded", ErrorCode.QUOTA_EXHAUSTED),
            (403, "dailyLimitExceeded", ErrorCode.QUOTA_EXHAUSTED),
            (403, "insufficientPermissions", ErrorCode.INSUFFICIENT_SCOPE),
            (400, "badRequest", ErrorCode.YOUTUBE_API_ERROR),
            (404, "notFound", ErrorCode.YOUTUBE_API_ERROR),
        ],
    )
    def test_definitive_errors_not_retried(self, status, reason, expected):
        fake = fixture_backed_fake()
        fake.errors["videos.list"] = [http_error(status, reason)]
        adapter, q, _, sleep = make_live(fake)
        assert code_of(adapter.get_video(RICK)) == expected
        assert fake.method_names() == ["videos.list"] and sleep.delays == []

    def test_quota_exhausted_does_not_charge_ledger(self):
        fake = fixture_backed_fake()
        fake.errors["videos.list"] = [http_error(403, "quotaExceeded")]
        adapter, q, _, _ = make_live(fake)
        code_of(adapter.get_video(RICK))
        assert q.status()["general"]["used"] == 0

    def test_5xx_retried_with_backoff_then_succeeds(self):
        fake = fixture_backed_fake()
        fake.errors["videos.list"] = [http_error(503, "backendError"), http_error(500)]
        adapter, q, _, sleep = make_live(fake)
        assert run(adapter.get_video(RICK)).video_id == RICK
        assert fake.method_names().count("videos.list") == 3
        assert sleep.delays == [0.5, 1.0]
        assert q.status()["general"]["used"] == 3  # failed attempts assumed billed (ADR-B §5)

    def test_5xx_exhausts_retries_to_transient(self):
        fake = fixture_backed_fake()
        fake.errors["videos.list"] = [http_error(503)] * 5
        adapter, _, _, _ = make_live(fake)
        with pytest.raises(CerebraError) as exc:
            run(adapter.get_video(RICK))
        assert exc.value.code == ErrorCode.TRANSIENT_ERROR and exc.value.retryable
        assert fake.method_names().count("videos.list") == 3

    def test_network_error_is_transient(self):
        fake = fixture_backed_fake()
        fake.errors["videos.list"] = [ConnectionResetError("boom")] * 3
        adapter, _, _, _ = make_live(fake)
        assert code_of(adapter.get_video(RICK)) == ErrorCode.TRANSIENT_ERROR

    def test_signup_required_maps_to_no_channel(self):
        fake = fixture_backed_fake()
        fake.errors["playlists.insert"] = [http_error(403, "youtubeSignupRequired")]
        adapter, _, _, _ = make_live(fake)
        assert code_of(adapter.create_playlist("t", "d", "private")) == ErrorCode.NO_YOUTUBE_CHANNEL

    def test_404_channel_not_found_maps_to_no_channel(self):
        # 404 channelNotFound on playlists.list (mine=True) means no YouTube channel (FR-3.8, NFR-RE-1)
        fake = fixture_backed_fake()
        fake.errors["playlists.list"] = [http_error(404, "channelNotFound")]
        adapter, _, _, _ = make_live(fake)
        assert code_of(adapter.get_playlists()) == ErrorCode.NO_YOUTUBE_CHANNEL

    def test_unsupported_method_refused(self):
        adapter, _, _, _ = make_live(fixture_backed_fake())
        with pytest.raises(CerebraError):
            run(adapter._execute("videos.delete", lambda s: s))


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5, NFR-RE-2, TR-4
class TestWritesNeverRetried:
    def test_playlist_insert_single_attempt_on_5xx(self):
        fake = fixture_backed_fake()
        fake.errors["playlists.insert"] = [http_error(503)] * 3
        adapter, _, _, sleep = make_live(fake)
        assert code_of(adapter.create_playlist("t", "d", "private")) == ErrorCode.TRANSIENT_ERROR
        assert fake.method_names() == ["playlists.insert"] and sleep.delays == []

    def test_item_insert_single_attempt_on_timeout(self):
        fake = fixture_backed_fake()
        fake.errors["playlistItems.insert"] = [TimeoutError()] * 3
        adapter, _, _, _ = make_live(fake)
        assert (
            code_of(adapter.add_playlist_item("PL0123456789", RICK, 0)) == ErrorCode.TRANSIENT_ERROR
        )
        assert fake.method_names() == ["playlistItems.insert"]


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5
class TestWriteRequests:
    def test_playlist_insert_body_and_scope(self):
        fake = fixture_backed_fake()
        auth = FakeAuth()
        adapter, q, auth, _ = make_live(fake, auth=auth)
        pid = run(adapter.create_playlist("My <b>list</b>", "desc <x>", "unlisted"))
        name, kw = fake.calls[-1]
        assert name == "playlists.insert" and kw["part"] == "snippet,status"
        assert kw["body"]["snippet"]["title"] == "My blist/b"  # '<' '>' stripped
        assert kw["body"]["status"]["privacyStatus"] == "unlisted"
        assert pid in fake.pl_store
        assert auth.scopes_requested[-1] == SCOPE_WRITE
        assert q.status()["write_units_used"] == 50

    def test_bad_privacy_falls_back_to_private(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        run(adapter.create_playlist("t", "d", "world-readable"))
        assert fake.calls[-1][1]["body"]["status"]["privacyStatus"] == "private"

    def test_title_truncated(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        run(adapter.create_playlist("x" * 400, "", "private"))
        assert len(fake.calls[-1][1]["body"]["snippet"]["title"]) == 150

    def test_item_insert_body(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        pid = run(adapter.create_playlist("t", "d", "private"))
        run(adapter.add_playlist_item(pid, RICK, 0))
        sn = fake.calls[-1][1]["body"]["snippet"]
        assert sn == {
            "playlistId": pid,
            "position": 0,
            "resourceId": {"kind": "youtube#video", "videoId": RICK},
        }

    def test_invalid_ids_rejected_before_any_call(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        assert (
            code_of(adapter.add_playlist_item("PL0123456789", "bad id!", 0))
            == ErrorCode.YOUTUBE_API_ERROR
        )
        assert fake.calls == []

    def test_ensure_write_access_requests_write_scope(self):
        adapter, _, auth, _ = make_live(fixture_backed_fake())
        run(adapter.ensure_write_access())
        assert auth.ensure_scope_calls == [SCOPE_WRITE]

    def test_ensure_write_access_without_auth(self):
        from cerebra.adapters.youtube.live_adapter import LiveYouTubeAdapter
        from cerebra.infrastructure.clock import FakeClock

        adapter = LiveYouTubeAdapter(QuotaLedger(), FakeClock(0.0), None)
        assert code_of(adapter.ensure_write_access()) == ErrorCode.AUTH_REQUIRED


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5, NFR-SE-1
class TestAuthModes:
    def test_reads_use_readonly_scope(self):
        adapter, _, auth, _ = make_live(fixture_backed_fake())
        run(adapter.get_video(RICK))
        assert set(auth.scopes_requested) == {SCOPE_READONLY}

    def test_no_auth_no_key_is_auth_required(self):
        adapter, _, _, _ = make_live(fixture_backed_fake(), auth=FakeAuth(False))
        assert code_of(adapter.get_video(RICK)) == ErrorCode.AUTH_REQUIRED

    def test_api_key_allows_public_reads_but_not_library(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake, auth=FakeAuth(False), api_key="k")
        assert run(adapter.get_video(RICK)).video_id == RICK
        assert code_of(adapter.get_liked_videos()) == ErrorCode.AUTH_REQUIRED


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5, TR-2
class TestReadBehaviour:
    def test_batches_of_50(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        run(adapter.get_videos([f"{i:011d}" for i in range(120)]))
        assert fake.method_names() == ["videos.list"] * 3

    def test_search_pagination_capped_at_two_pages(self):
        from cerebra.domain.entities import VideoMetadata

        vids = [
            VideoMetadata(f"vid{i:08d}", "python tutorial", "", CH, "c", 60, "27", "en")
            for i in range(200)
        ]
        fake = FakeYouTube(vids, [])
        adapter, q, _, _ = make_live(fake)
        res = run(adapter.search_videos("python", max_results=200))
        assert fake.method_names().count("search.list") == 2
        assert len(res) == 100
        assert q.status()["search"]["used"] == 2

    def test_search_passes_relevance_language_primary_subtag(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        run(adapter.search_videos("rick", language="en-US"))
        assert fake.calls[0][1]["relevanceLanguage"] == "en"

    def test_empty_query_makes_no_call(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        assert run(adapter.search_videos("   ")) == []
        assert fake.calls == []

    def test_channel_uploads_via_uploads_playlist(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        res = run(adapter.get_channel_uploads(CH, max_results=10))
        assert fake.method_names()[:2] == ["channels.list", "playlistItems.list"]
        assert all(v.channel_id == CH for v in res)

    def test_channel_metadata_mapping_and_cadence(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        c = run(adapter.get_channel(CH))
        assert c.subscriber_count == 3_800_000
        assert "rick astley" in c.keywords or "rick" in c.keywords
        assert c.topic_categories == ["Music"]

    def test_resolve_handle_uses_cheap_lookup(self):
        fake = fixture_backed_fake()
        adapter, q, _, _ = make_live(fake)
        assert run(adapter.resolve_channel("handle", "RickAstleyVEVO")) == CH
        assert fake.method_names() == ["channels.list"] and q.status()["search"]["used"] == 0

    def test_resolve_unknown_handle_is_not_found(self):
        adapter, _, _, _ = make_live(fixture_backed_fake())
        assert code_of(adapter.resolve_channel("handle", "nobody")) == ErrorCode.CHANNEL_NOT_FOUND

    def test_resolve_custom_falls_back_to_search(self):
        fake = fixture_backed_fake()
        adapter, q, _, _ = make_live(fake)
        assert run(adapter.resolve_channel("custom", "rick astley")) == CH
        assert "search.list" in fake.method_names()

    def test_resolve_user_and_unknown_kind(self):
        adapter, _, _, _ = make_live(fixture_backed_fake())
        assert code_of(adapter.resolve_channel("user", "x")) == ErrorCode.CHANNEL_NOT_FOUND
        assert code_of(adapter.resolve_channel("weird", "x")) == ErrorCode.CHANNEL_NOT_FOUND

    def test_liked_capped_at_api_limit(self):
        from cerebra.domain.entities import VideoMetadata

        liked = [
            VideoMetadata(f"lik{i:08d}", "t", "", CH, "c", 60, "27", "en") for i in range(1200)
        ]
        fake = FakeYouTube(liked, [], liked=liked)
        adapter, _, _, _ = make_live(fake)
        res = run(adapter.get_liked_videos(max_results=5000))
        assert len(res) == 1000

    def test_playlist_items_use_add_date(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        pid = run(adapter.create_playlist("t", "d", "private"))
        run(adapter.add_playlist_item(pid, RICK, 0))
        items = run(adapter.get_playlist_items(pid))
        assert [v.video_id for v in items] == [RICK]
        assert items[0].published_at == "2026-11-21T00:00:00Z"

    def test_account_id_cached_and_no_channel_error(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        assert run(adapter.get_account_id()) == "UCaccount0000000000000000"
        run(adapter.get_account_id())
        assert fake.method_names().count("channels.list") == 1
        fake2 = fixture_backed_fake()
        fake2.account_channel_id = None
        adapter2, _, _, _ = make_live(fake2)
        assert code_of(adapter2.get_account_id()) == ErrorCode.NO_YOUTUBE_CHANNEL


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5, FR-1.3
class TestPureMappers:
    def test_video_mapping_edge_cases(self):
        v = la.video_from_resource(
            {
                "id": "abcdefghijk",
                "snippet": {"title": "t", "defaultLanguage": "en-GB"},
                "contentDetails": {"duration": "P0D"},
            }
        )
        assert v.duration_seconds == 0 and v.language == "en" and v.tags_missing
        bad = la.video_from_resource({"id": "x", "contentDetails": {"duration": "garbage"}})
        assert bad.duration_seconds == 0

    def test_hidden_subscriber_count_is_none(self):
        c = la.channel_from_resource(
            {
                "id": "UC1",
                "snippet": {},
                "statistics": {"hiddenSubscriberCount": True, "subscriberCount": "5"},
            }
        )
        assert c.subscriber_count is None

    def test_keyword_parsing_quoted_phrases(self):
        assert la._parse_keywords('python "machine learning" ai') == [
            "python",
            "machine learning",
            "ai",
        ]

    def test_cadence(self):
        assert la._cadence_per_month([]) == 0.0
        stamps = ["2026-01-01T00:00:00Z", "2026-01-31T00:00:00Z", "2026-03-02T00:00:00Z"]
        assert 1.0 < la._cadence_per_month(stamps) < 1.1
        assert la._cadence_per_month(["bad", "worse"]) == 0.0


# mark: CON-1, FR-5.8, NFR-SA-2, CON-5
class TestOfficialApiOnly:
    """CON-1 / NFR-SA-2 / CON-5."""

    def test_only_allowed_methods_ever_called(self):
        fake = fixture_backed_fake()
        adapter, _, _, _ = make_live(fake)
        run(adapter.search_videos("rick"))
        run(adapter.get_channel(CH))
        run(adapter.get_liked_videos())
        run(adapter.get_subscriptions())
        pid = run(adapter.create_playlist("t", "d", "private"))
        run(adapter.add_playlist_item(pid, RICK, 0))
        run(adapter.get_playlists())
        assert set(fake.method_names()) <= la.ALLOWED_METHODS

    def test_source_has_no_edit_delete_or_scraping(self):
        src = Path(la.__file__).read_text()
        tree = ast.parse(src)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not attrs & {"update", "delete", "rate", "setThumbnail", "markAsSpam"}
        imported = {
            a.name.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.Import)
            for a in n.names
        }
        imported |= {
            n.module.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module
        }
        assert not imported & {"requests", "urllib", "httpx", "bs4", "selenium", "pytube", "yt_dlp"}
        assert "videos.insert" not in src  # quota modelling only, never invoked

    def test_write_methods_are_exactly_two(self):
        assert {"playlists.insert", "playlistItems.insert"} == la.WRITE_METHODS

    def test_real_client_targets_official_googleapis_host(self):
        svc = la.default_service_factory(None, "dummy-key")
        req = svc.videos().list(part="snippet", id="abcdefghijk")
        assert req.uri.startswith("https://youtube.googleapis.com/youtube/v3/videos")
        assert "dummy-key" in req.uri  # key passed as query param, never logged here

    def test_liked_cap_documented(self):
        assert la.LIKED_VIDEO_API_CAP == 1000

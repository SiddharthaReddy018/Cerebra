"""Performance targets of SRS 5.1 that can be checked without the network.

NFR-PF-3 / NFR-PF-4 are dominated by Google latency and are verified by the live smoke run.
Thresholds are the SRS figures; they are generous for a laptop, so these are not flaky.
"""

# mark: NFR-PF-1, NFR-PF-2
from __future__ import annotations

import time

from cerebra.domain.scoring import rank_videos
from tests.conftest import make_video
from tests.support.helpers import run
from tests.support.live_env import make_env


# mark: NFR-PF-1
class TestLocalProcessing:
    def test_ranking_200_candidates_within_2_seconds(self):
        seed = make_video(video_id="seedvid0001", title="python machine learning tutorial")
        candidates = [
            make_video(
                video_id=f"cand{i:07d}",
                title=f"python tutorial part {i} {'ml' if i % 3 else 'web'}",
                description="learn programming with python " * 5,
                tags=["python", f"topic{i % 7}"],
            )
            for i in range(200)
        ]
        start = time.perf_counter()
        ranked = rank_videos(seed, candidates)
        elapsed = time.perf_counter() - start
        assert len(ranked) == 200
        assert elapsed < 2.0, f"ranking took {elapsed:.2f}s (NFR-PF-1 limit 2 s)"


# mark: NFR-PF-2
class TestCachedRequest:
    def test_cached_find_similar_within_1_second(self, tmp_path):
        env = make_env(tmp_path)
        find = env.container.find_similar
        run(find.find_similar_videos("pyvid000000", limit=10))  # warm the cache
        calls_before = len(env.fake.calls)
        start = time.perf_counter()
        run(find.find_similar_videos("pyvid000000", limit=10))
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0
        assert len(env.fake.calls) == calls_before  # served from cache, no API call (FR-5.9)

"""Golden / snapshot test: deterministic ranking on a fixed corpus (FR-1.9, L9)."""

from __future__ import annotations

from cerebra.domain.scoring import rank_videos
from tests.conftest import make_video

# Fixed corpus — never changes between runs (deterministic test)
_SEED = make_video(
    video_id="seedGOLDEN1",
    title="Introduction to machine learning with Python",
    description="Comprehensive guide to ML algorithms using scikit-learn",
    tags=["machine learning", "python", "scikit-learn", "tutorial"],
    category_id="27",
    language="en",
    duration_seconds=1800,
    channel_id="UCgolden000001234567890",
)

_CANDIDATES = [
    make_video(
        video_id="goldA000001",
        title="Machine learning fundamentals with scikit-learn",
        description="Learn ML basics with hands-on Python examples",
        tags=["machine learning", "scikit-learn", "python"],
        category_id="27",
        language="en",
        duration_seconds=1500,
        channel_id="UCgolden000001234567890",  # same channel
    ),
    make_video(
        video_id="goldB000002",
        title="Deep learning neural networks tutorial",
        description="Neural networks from scratch in Python",
        tags=["deep learning", "neural networks", "python"],
        category_id="27",
        language="en",
        duration_seconds=2700,
        channel_id="UCother000001234567890",
    ),
    make_video(
        video_id="goldC000003",
        title="Python for data analysis pandas numpy",
        description="Data analysis with pandas and numpy",
        tags=["python", "pandas", "data analysis"],
        category_id="27",
        language="en",
        duration_seconds=1200,
        channel_id="UCother000001234567890",
    ),
    make_video(
        video_id="goldD000004",
        title="Cooking Italian pasta recipes",
        description="Authentic Italian cuisine",
        tags=["cooking", "pasta", "italian"],
        category_id="22",
        language="en",
        duration_seconds=600,
        channel_id="UCcooking00001234567890",
    ),
]

# Expected ranking snapshot — computed once and locked in
_EXPECTED_ORDER = ["goldA000001", "goldB000002", "goldC000003", "goldD000004"]


# mark: FR-1.5, FR-1.9
class TestGoldenRanking:
    def test_ranking_matches_golden_snapshot(self):
        """FR-1.9: identical input → identical output on every run."""
        results = rank_videos(_SEED, _CANDIDATES)
        actual_order = [r.video_id for r in results]
        assert actual_order == _EXPECTED_ORDER, (
            f"Golden ranking changed! Expected {_EXPECTED_ORDER}, got {actual_order}. "
            "If the algorithm changed intentionally, update this snapshot."
        )

    def test_ranking_is_deterministic_across_calls(self):
        """FR-1.9: calling rank_videos twice gives identical result."""
        r1 = rank_videos(_SEED, _CANDIDATES)
        r2 = rank_videos(_SEED, _CANDIDATES)
        assert [r.video_id for r in r1] == [r.video_id for r in r2]
        for a, b in zip(r1, r2):
            import math

            assert math.isclose(a.score, b.score, abs_tol=1e-12)

    def test_seed_excluded(self):
        """FR-1.6: seed is never in results."""
        results = rank_videos(_SEED, [_SEED, *_CANDIDATES])
        ids = [r.video_id for r in results]
        assert _SEED.video_id not in ids

# Testing Lab Report — Part A: Property-Based Testing & Coverage

**Student Name:** Sai Harsha  
**Roll Number:** BT2024148  
**Course:** Software Engineering Lab (Autumn 2026)  
**Project:** Cerebra – YouTube Video Intelligence MCP Server  
**Date:** 2026-11-20  

---

## 1. Executive Summary

This report documents the property-based testing suite implemented using **Hypothesis** for the Cerebra MCP server, per the Software Engineering Lab requirements (Part A). A total of **8 property-based tests** (exceeding the required 5) were constructed, covering critical invariant properties across duration parsing, deterministic scoring, bounded weighted scores, knapsack playlist constraint solving, quota conservation, state machine transitions, and reference normalization.

Additionally, a comprehensive automated test suite spanning unit, property, contract, integration, and security layers achieved **81.82% overall line coverage** across `src/cerebra`, satisfying the ≥80% quality gate (NFR-TE-2).

---

## 2. Property Tests Specification & Results

All property tests are implemented in `tests/property/test_properties.py`.

### Test 1: Round-Trip Invariant for Duration Parsing (FR-1.3)
- **Property Pattern:** Round-Trip / Invertibility (`f⁻¹(f(x)) == x`)
- **Description:** For any non-negative integer second count generated across short, medium, and long durations (up to 30 days), converting to ISO 8601 (`seconds_to_iso8601`) and parsing back (`parse_duration`) returns the exact identical second value.
- **Hypothesis Strategy:** `st.integers(min_value=0, max_value=86400 * 30)`
- **Result:** **PASSED** (100 examples)

### Test 2: Invariance of Video Ranking to Input Candidate Ordering (FR-1.9)
- **Property Pattern:** Invariance / Permutation Invariance
- **Description:** Given a seed video and a list of candidate videos, permuting the candidate list does not alter the final ranked ordering or assigned scores. Determinism is enforced with lexicographical tie-breaking on `video_id`.
- **Hypothesis Strategy:** Composite strategy generating a fixed seed and random unique candidates, then shuffling.
- **Result:** **PASSED** (100 examples)

### Test 3: Cosine Similarity and Weight Structure (FR-1.5)
- **Property Pattern:** Structural / Boundary Invariant
- **Description:** For any text input with valid token representations, self-similarity `cos(a, a) ≈ 1.0` within `1e-6`, and pairwise similarity `cos(a, b) ∈ [0.0, 1.0]`. In addition, domain scoring weights (`w1=0.60, w2=0.15, w3=0.10, w4=0.10, w5=0.05`) strictly sum to 1.0.
- **Edge Case Discovered & Fixed:** Single-character strings (e.g. `'0'`) initially failed because scikit-learn's default token pattern `(?u)\b\w\w+\b` omitted single-character words, leading to empty vocabulary. Fixed by configuring `token_pattern=r"(?u)\b\w+\b"`.
- **Result:** **PASSED** (300 examples)

### Test 4: Weighted Score Boundedness (FR-1.5)
- **Property Pattern:** Boundedness / Range Invariant
- **Description:** `compute_weighted_score` strictly yields a score in `[0.0, 1.0]` for all possible combinations of cosine similarity, category match, channel match, language match, and duration band proximity.
- **Hypothesis Strategy:** Combinations of floats and booleans.
- **Result:** **PASSED** (200 examples)

### Test 5: Playlist Constraint Solver Invariants (FR-4.1 – FR-4.3)
- **Property Pattern:** Constraint Satisfaction / Boundedness
- **Description:** For any generated pool of candidate videos and target total duration, the greedy solver produces a playlist whose cumulative duration never exceeds the target total duration, where every video duration respects `max_video_duration_seconds`, and no video is duplicated.
- **Result:** **PASSED** (100 examples)

### Test 6: Quota Ledger Conservation & Idempotence (FR-5.8, NFR-QU-1)
- **Property Pattern:** Conservation / Reversibility
- **Description:** Reserving `(g, s)` quota and subsequently releasing `(g, s)` leaves the remaining general and search quotas strictly identical to their initial values.
- **Result:** **PASSED** (100 examples)

### Test 7: Proposal State Machine Invariants (SRS Appendix B.3)
- **Property Pattern:** State Transition Validity
- **Description:** Only valid transitions defined in the state graph are permitted. Transitions from terminal states (`CREATED`, `EXPIRED`, `CANCELLED`, `FAILED`) consistently raise `InvalidStateTransitionError`.
- **Result:** **PASSED** (100 examples)

### Test 8: Normalization Idempotence (FR-1.1)
- **Property Pattern:** Idempotence (`f(f(x)) == f(x)`)
- **Description:** Normalizing an already normalized 11-character video ID is strictly idempotent.
- **Result:** **PASSED** (100 examples)

---

## 3. Test Coverage Summary

Full test suite execution:
- **Total Tests:** 184 passing tests across unit, property, integration, contract, mcp_e2e, and security suites.
- **Overall Line Coverage:** **81.82%** (exceeds the 80% threshold required by NFR-TE-2).

### Module Coverage Breakdown

| Module | Statements | Miss | Branch | BrPart | Coverage |
|--------|-----------:|-----:|-------:|-------:|---------:|
| `cerebra.domain.normalise` | 46 | 0 | 22 | 0 | **100%** |
| `cerebra.domain.playlist_solver` | 36 | 0 | 16 | 0 | **100%** |
| `cerebra.domain.state_machine` | 11 | 0 | 4 | 0 | **100%** |
| `cerebra.infrastructure.composition` | 41 | 0 | 0 | 0 | **100%** |
| `cerebra.config` | 35 | 2 | 0 | 0 | **94%** |
| `cerebra.domain.duration` | 26 | 1 | 12 | 1 | **95%** |
| `cerebra.domain.quota` | 78 | 4 | 10 | 1 | **94%** |
| `cerebra.domain.scoring` | 89 | 7 | 14 | 1 | **92%** |
| `cerebra.domain.errors` | 65 | 5 | 0 | 0 | **92%** |
| `cerebra.ports.interfaces` | 44 | 4 | 0 | 0 | **91%** |
| `cerebra.infrastructure.persistence.sqlite_stores` | 120 | 15 | 16 | 1 | **87%** |
| `cerebra.domain.entities` | 151 | 21 | 10 | 0 | **83%** |
| `cerebra.mcp_tools.validation` | 89 | 13 | 54 | 6 | **83%** |
| `cerebra.infrastructure.clock` | 19 | 4 | 0 | 0 | **79%** |
| `cerebra.application.analyse` | 96 | 19 | 34 | 7 | **74%** |
| `cerebra.adapters.fixtures.fixture_adapter` | 140 | 34 | 40 | 7 | **71%** |
| `cerebra.application.recommend` | 70 | 16 | 22 | 6 | **70%** |
| `cerebra.application.playlist` | 113 | 35 | 26 | 4 | **69%** |
| `cerebra.application.find_similar` | 87 | 31 | 24 | 3 | **60%** |
| **TOTAL** | **1357** | **211** | **304** | **37** | **81.82%** |

---

## 4. What Coverage Shows and What It Does Not Show

### What Coverage Demonstrates:
1. **Critical Path Execution:** Core algorithms (ISO duration parser, TF-IDF cosine ranking, greedy knapsack solver, state machine transitions) are thoroughly exercised across both typical and boundary inputs.
2. **Defensive Validation:** Input sanitization (UUID validation, SHA-256 digest checking, path traversal blocking, parameter bounds) is tested and active.
3. **No Dead Code in Domain Logic:** Zero unreached lines in `normalise`, `playlist_solver`, and `state_machine`.

### What Coverage Does NOT Demonstrate:
1. **Live YouTube API Responses:** Under NFR-TE-1 and TR-5, automated tests run strictly against fixture adapters. Unannounced breaking changes in YouTube's live JSON responses can only be caught by contract tests in scheduled/manual workflows.
2. **True Concurrency Stresses:** While `QuotaLedger` and SQLite stores use thread locks, extreme high-concurrency race conditions (e.g. hundreds of simultaneous write requests) require distributed stress testing beyond in-process test fixtures.
3. **Live Network Timeouts & Partitions:** Simulated clock advances test logical timeout transitions, but transient TCP reset or SSL renegotiation failures require network fault injection.

---

## Addendum (2026-11-25): current figures

The figures above are the 2026-11-20 snapshot and are kept as written. State of `main` on 2026-11-25:

- Tests: 424 passed, 8 deselected (7 live_readonly + 1 live_write, excluded by default so CI never calls the live API).
- Coverage: 90.68% (2565 statements, 185 missed, 644 branches, 80 partial); gate is 80% (NFR-TE-2).
- Property tests: 8 Hypothesis tests in tests/property/test_properties.py.
- Traceability: 136 requirements, 380 rows (scripts/traceability.py).
- Lowest-covered module: application/find_similar.py at 60%.
- Live runs: read-only suite 7 passed, write test passed, FR-1 uncached 1.82 s and FR-4 proposal 1.2 s (see docs/testing/performance.md).


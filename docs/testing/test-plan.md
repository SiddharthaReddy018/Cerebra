# Cerebra Test Plan

**Project:** Cerebra – YouTube Video Intelligence MCP Server  
**Version:** 1.0  
**Date:** 2026-11-09  
**Team:** SegmentationFault  
**SRS refs:** NFR-TE-1 and NFR-TE-2, TR-1 through TR-9

---

## 1. Objectives

| Goal | Target | SRS ref |
|------|--------|---------|
| Unit test coverage ≥80% on core logic | ≥80% line coverage | NFR-TE-2 |
| All CI tests use fixture adapter only | Zero live API calls in CI | NFR-TE-1, TR-5 |
| Property tests (Lab Part A) | 8 Hypothesis tests (5 required) | Lab requirement |
| Deterministic ranking | Golden snapshot test passes | FR-1.9 |
| Security tests | Approval provenance, replay, expiry | ADR-A |

---

## 2. Test Layers

### Layer 1 — Unit Tests (`tests/unit/`)

**Purpose:** Test individual functions and classes in isolation. No I/O, no database, no network.

**Tools:** `pytest`, `pytest-asyncio`  
**Run time target:** < 30 seconds

| File | Module tested | Key SRS refs |
|------|--------------|--------------|
| `test_duration.py` | `domain/duration.py` | FR-1.3 (ISO 8601 parsing) |
| `test_normalise.py` | `domain/normalise.py` | FR-1.1, FR-1.2 |
| `test_scoring.py` | `domain/scoring.py` | FR-1.5, FR-1.9, BR-4 |
| `test_golden.py` | `domain/scoring.py` | FR-1.9 (determinism snapshot) |
| `test_quota.py` | `domain/quota.py` | FR-5.8, NFR-QU-1 |
| `test_state_machine.py` | `domain/state_machine.py` | SRS Appendix B.3 |
| `test_playlist_solver.py` | `domain/playlist_solver.py` | FR-4.1–4.3 |
| `test_validation.py` | `mcp_tools/validation.py` | FR-5.14, NFR-SE-4 |

### Layer 2 — Property Tests (`tests/property/`)

**Purpose:** Discover edge cases via automated random input generation.

**Tools:** `hypothesis` (≥6.0)  
**Run time target:** < 60 seconds (100 examples per test by default)

| Test | Property verified | SRS ref |
|------|-----------------|---------|
| `test_round_trip_seconds_iso8601` | `seconds_to_iso8601(parse_duration(s)) == s` | FR-1.3 |
| `test_ranking_order_does_not_matter` | Ranking is independent of candidate input order | FR-1.9 |
| `test_cosine_structure_and_weights` | Cosine similarity and scoring weights keep their structural invariants | FR-1.5 |
| `test_weighted_score_in_range` | All weighted scores stay in [0.0, 1.0] | FR-1.5 |
| `test_playlist_solver_structure` | Solver output satisfies the duration constraints | FR-4.2, FR-4.3 |
| `test_quota_ledger_recurrence` | Reserve/release sequences keep the ledger consistent with its budget | FR-5.8 |
| `test_state_machine_recurrence` | Random event sequences follow only allowed transitions | SRS Appendix B.3 |
| `test_extra_normalise_idempotence` | Normalising an already-normalised ID changes nothing | FR-1.1 |

### Layer 3 — Integration Tests (`tests/integration/`)

**Purpose:** Test SQLite stores and use cases wired together with fixture adapter.

**Tools:** `pytest`, `pytest-asyncio`, `tmp_path`  
**Run time target:** < 30 seconds

Covers: `SQLiteProposalStore`, `SQLiteCacheStore`, `SQLiteAuditLog`, `QuotaLedger` persistence across restarts, `ProposePlaylistUseCase` end-to-end with fixture adapter.

### Layer 4 — Contract Tests (`tests/contract/`)

**Purpose:** Verify that fixture adapter satisfies the same port contracts as the live adapter would.

**Tools:** `pytest`, `pytest-asyncio`  
**Key guarantee:** LSP — fixture adapter is a drop-in for live adapter in tests (NFR-TE-1).

### Layer 5 — MCP E2E Tests (`tests/mcp_e2e/`)

**Purpose:** Exercise FR-1 through FR-5 through the use case layer (without MCP transport overhead).

**Tools:** `pytest`, `pytest-asyncio`

Covers all 5 use cases: `find_similar_videos`, `find_similar_channels`, `recommend_videos`, `analyse_engagement`, `propose_playlist`.

### Layer 6 — Security Tests (`tests/security/`)

**Purpose:** Test approval provenance, replay attacks, expiry, and injection resistance.

**Tools:** `pytest`, `pytest-asyncio`

| Test | Attack vector |
|------|--------------|
| `test_wrong_digest_raises_approval_rejected` | Tampered digest |
| `test_expired_proposal_rejected` | Clock-advanced past expiry |
| `test_reused_proposal_rejected` | Same proposal_id used twice |
| `test_injection_via_metadata` | `[DATA]` prefix check |

---

## 3. What Tests Do NOT Cover

| Gap | Reason | Mitigation |
|-----|--------|-----------|
| Live YouTube API calls | NFR-TE-1 forbids live API in CI | Scheduled live-api workflows (.github/) |
| OAuth token refresh | No tokens in test environment | Manual test, CON-3 documented |
| Concurrent write races | SQLite lock tested indirectly | Phase 5 load testing |
| Network partition during write | Ambiguous timeout simulation | Integration tests use FakeClock |

---

## 4. CI Gates

| Gate | Test suite | Required to pass |
|------|-----------|-----------------|
| Fast gate (every push) | `tests/unit` + `tests/property` | Yes |
| Full gate (every PR) | All suites except `live-api` | Yes |
| Coverage gate | `--cov=cerebra` on the full suite (`tests/`) | ≥80% line coverage |
| Security gate | `tests/security` | Yes |
| Live readonly (scheduled daily) | `tests/integration -m live_readonly` | No (advisory) |
| Live write (manual only) | `tests/integration -m live_write` | No (manual sign-off) |

---

## 5. Test Data

- Fixture JSON files in `tests/fixtures/videos/`, `tests/fixtures/channels/`, `tests/fixtures/playlists/`
- No real user data; no real YouTube credentials in fixtures
- `conftest.py` provides `make_video()` and `make_channel()` helpers

---

## 6. Performance Targets (NFR-PF-1)

| Operation | Target p95 | Measurement |
|-----------|-----------|-------------|
| `find_similar_videos` (fixture) | < 500ms | `pytest-benchmark` or `time.perf_counter` |
| `propose_playlist` (fixture) | < 1s | Manual timing in integration tests |
| SQLite proposal write | < 100ms | Integration test timing |
| TF-IDF scoring (100 candidates) | < 200ms | Unit test timing |

See `docs/testing/performance.md` for measured values.

---

## 7. TR Coverage Map

| TR | Evidence (see docs/traceability.csv) |
|----|--------------------------------------|
| TR-1 | tests/unit: test_duration, test_normalise, test_quota, test_scoring |
| TR-2 | tests/integration: test_integration (TestProposalStoreIntegration), test_live_adapter (TestErrorMapping, TestReadBehaviour) |
| TR-3 | tests/mcp_e2e/test_mcp_e2e.py (find_similar, recommend, analyse, propose, uniform errors) |
| TR-4 | test_live_adapter (TestErrorMapping, TestWritesNeverRetried), test_mcp_e2e (TestUniformErrors), tests/unit/test_auth.py (TestTestingModeExpiry), test_duration, test_normalise |
| TR-5 | tests/integration/test_srs_requirements.py (TestCiNeverCallsLiveApi); live tests are excluded by default through pytest addopts markers |
| TR-6 | test_live_adapter (TestQuotaBeforeEveryCall); test_live_readonly (TestLiveBudgetGuard, live, manual) |
| TR-7 | tests/security (test_injection, test_security), tests/unit/test_auth.py (TestScopes), tests/unit/test_validation.py (TestSecurityValidation) |
| TR-8 | Inspection: skills/local-check/SKILL.md, results recorded in docs/testing/ |
| TR-9 | Inspection: this plan; defects get regression tests (example: tests/unit/test_error_traceback.py) |

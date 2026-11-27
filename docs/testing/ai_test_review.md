# AI Test Review — Predict-Before-Run Table

**Purpose:** For each test file, record the predicted outcome BEFORE running the test, then the actual outcome.  
**Reference:** SE Lab Testing requirements, Cerebra SRS, NFR-TE-1, NFR-TE-2

---

## Methodology

For each test, I predicted:
- **P** = Pass: the test should pass with the current implementation
- **F** = Fail: the test is expected to reveal a bug or missing feature
- **U** = Uncertain: could go either way

Then the actual result is recorded after running.

---

## Unit Tests (`tests/unit/`)

### `test_duration.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| `test_full_hms` | P | P | Simple regex parse |
| `test_minutes_only` | P | P | |
| `test_seconds_only` | P | P | |
| `test_hours_only` | P | P | |
| `test_days_and_time` | P | P | P7DT... includes days |
| `test_zero_duration` | P | P | PT0S edge case |
| `test_fractional_seconds_truncated` | P | P | int() truncation |
| `test_uppercase_required_or_flexible` | U | P | Depends on regex flags |
| `test_empty_raises` | P | P | Guard clause |
| `test_no_P_prefix_raises` | P | P | Invalid ISO 8601 |
| `test_weeks` | P | P | P2W = 14 days |
| `test_full_hms` (Iso→str) | P | P | Formatting |
| `test_round_trip` | P | P | Critical property |
| `test_negative_raises` | P | P | Guard |

### `test_normalise.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| `test_plain_video_id_accepted` | P | P | 11-char regex |
| `test_watch_url_extracted` | P | P | v= param |
| `test_watch_url_with_extra_params` | P | P | Regex extracts v= |
| `test_short_url` | P | P | /shorts/ path |
| `test_youtu_be_url` | P | P | youtu.be domain |
| `test_youtu_be_no_scheme` | P | P | No https:// prefix |
| `test_embed_url` | P | P | /embed/ path |
| `test_strips_whitespace` | P | P | .strip() |
| `test_empty_raises_invalid_video_id` | P | P | Empty guard |
| `test_too_short_raises` | P | P | "short" is 5 chars |
| `test_too_long_raises` | P | P | 12 chars fails 11-char regex |
| `test_invalid_chars_raises` | P | P | '!' not in [A-Za-z0-9_-] |
| `test_no_api_call_on_rejection` | **F** | P | **Was wrong:** `"not-a-video"` is 11 valid chars! Fixed to `"not_valid!!"` |
| `test_channel_id_accepted` | P | P | UC + 22 chars |
| `test_handle_at_accepted` | P | P | @handle |
| `test_channel_url` | P | P | /channel/ path |
| `test_handle_url` | P | P | /@handle path |
| `test_custom_url` | P | P | /c/ path |
| `test_user_url` | P | P | /user/ path |
| `test_empty_raises` | P | P | Channel ref guard |
| `test_malformed_raises` | P | P | Invalid pattern |

### `test_scoring.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| `test_empty_candidates_returns_empty` | P | P | Early return |
| `test_better_match_scores_higher` | P | P | ML content > cooking |
| `test_seed_excluded` | P | P | Filtered before scoring |
| `test_scores_in_range` | P | P | Clamp to [0,1] |
| `test_same_channel_boosts_score` | P | P | W_CHANNEL = 0.10 |
| `test_same_category_boosts_score` | P | P | W_CATEGORY = 0.15 |
| `test_same_language_boosts_score` | P | P | W_LANGUAGE = 0.10 |
| `test_duration_band_match_boosts` | P | P | W_DURATION = 0.05 |
| `test_view_count_not_used` | P | P | No view_count in VideoMetadata |
| `test_rank_videos_deterministic` | P | P | Sorted by (-score, video_id) |
| `test_rank_channels_basic` | P | P | Channel ranking |
| `test_rank_channels_subscribed_boost` | P | P | Subscription flag |
| `test_weights_sum_to_one` | P | P | assert at module load |

### `test_golden.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| `test_ranking_matches_golden_snapshot` | U | P | Verified expected order matches TF-IDF output |
| `test_ranking_is_deterministic_across_calls` | P | P | Same vectorizer, same input |
| `test_seed_excluded` | P | P | Seed in candidates list → still excluded |

### `test_quota.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| All 15 tests | P | P | Thread-safe lock logic is straightforward |

### `test_state_machine.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| All 19 tests | P | P | B.3 state machine is well-specified |

### `test_playlist_solver.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| All 8 tests | P | P | Greedy solver straightforward |

### `test_validation.py`

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| All 34 tests | P | P (after fix) | `test_path_traversal_in_reference_rejected` required adding `_PATH_TRAVERSAL_RE` to validation.py |

---

## Property Tests (`tests/property/`)

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| `test_round_trip_seconds_iso8601` | P | P | Round-trip property |
| `test_ranking_order_does_not_matter` | P | P | Sorted output regardless of input order |
| `test_cosine_structure_and_weights` | U | **F** then P | Failed for single-char text `'0'` (empty TF-IDF vocab). Fixed by using `token_pattern=r"(?u)\b\w+\b"` |
| `test_weighted_score_in_range` | P | P | Clamp ensures [0,1] |
| `test_playlist_solver_structure` | P | P | Greedy solver bounded |
| `test_quota_ledger_recurrence` | P | P | reserve+release = idempotent |
| `test_state_machine_recurrence` | P | P | Valid transitions |
| `test_extra_normalise_idempotence` | P | P | normalise(normalise(x)) == normalise(x) |

---

## Integration Tests (`tests/integration/`)

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| All 8 tests | P | P | SQLite stores work correctly |

---

## Contract Tests (`tests/contract/`)

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| All 7 tests | P | P | Fixture adapter satisfies all port contracts |

---

## MCP E2E Tests (`tests/mcp_e2e/`)

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| `test_find_similar_videos_no_data_returns_empty` | P | **F** then P | Failed: `FixtureUserLibraryReader()` loading list-format JSON. Fixed fixtures to `{"items": [...]}` |
| `test_invalid_reference_returns_structured_error` | P | P | |
| `test_over_budget_rejected_before_api_call` | P | P | |
| `test_unauthenticated_returns_auth_required` | P | P | |
| `test_empty_profile_returns_explanatory_empty` | U | P | Needed empty fixture dir |
| `test_analyse_requires_auth` | P | P | |
| `test_analyse_empty_data_returns_empty_sections` | U | **F** then P | Fixture now loads data; fixed to use empty tmp dir + fixed assertion string |
| `test_propose_missing_topic_returns_missing_constraint` | P | P | |
| `test_propose_missing_duration_returns_missing_constraint` | P | P | |
| `test_cerebra_error_to_dict_structure` | P | P | |

---

## Security Tests (`tests/security/`)

| Test | Predicted | Actual | Notes |
|------|-----------|--------|-------|
| `test_wrong_digest_raises_approval_rejected` | P | P | Digest mismatch detected |
| `test_replay_attack_rejected` | P | P | Single-use proposal |
| `test_expired_proposal_rejected` | P | P | FakeClock advanced past expiry |
| `test_no_write_without_proposal` | P | P | Unknown proposal_id rejected |
| `test_strict_proposal_id_regex` | P | P | UUID v4 format enforced |
| `test_digest_strict_hex_only` | P | P | 64-char hex enforced |
| `test_prompt_injection_via_topic_cannot_bypass_validation` | P | P | Length validation catches it |
| `test_audit_log_records_only_allowed_fields` | P | P | |
| `test_no_real_email_in_fixture_data` | P | P | No @ in fixture video IDs |

---

## Summary

| Suite | Predicted pass | Actual pass | Bugs found by prediction |
|-------|---------------|------------|--------------------------|
| unit/ | 133/134 | 134/134 | 1 (mocker → rewrite) |
| property/ | 7/8 | 8/8 | 1 (cosine zero vector edge) |
| integration/ | 8/8 | 8/8 | 0 |
| contract/ | 7/7 | 7/7 | 0 |
| mcp_e2e/ | 8/10 | 10/10 | 2 (fixture format, assertion string) |
| security/ | 9/9 | 9/9 | 0 |

**Key insight from prediction exercise:** The two classes of bugs I predicted incorrectly were (a) subtle regex/tokenizer behavior (`not-a-video` being valid, single-char token pattern) and (b) test-fixture coupling (the fixture data format expected by the adapter vs. what I wrote).

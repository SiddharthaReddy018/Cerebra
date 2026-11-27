# Cerebra Developer API

## 1. Layering

```
mcp_server.py ─► mcp_tools/validation ─► application/* ─► ports/interfaces ◄─ adapters/{fixtures,youtube}
                                              │                                   ▲
                                              └────────► domain/*                 │
infrastructure/composition.py  (the ONLY place concrete classes are built) ───────┘
```

* `domain/` – pure logic (scoring, solver, quota ledger, state machine, errors).
* `ports/interfaces.py` – the only dependency boundary (ISP protocols).
* `adapters/fixtures/` – deterministic, offline (CI). `adapters/youtube/live_adapter.py` – the
  **only** module that performs YouTube HTTP calls (CON-1, CON-6).
* `infrastructure/` – SQLite stores, clock, OAuth, token storage, quota persistence, composition.
* No global singletons: everything is constructed in `build_container()` and injected.

## 2. Ports

| Port | Methods |
|------|---------|
| `PublicVideoReader` | `get_video`, `get_videos`, `search_videos`, `get_channel_uploads` |
| `PublicChannelReader` | `get_channel`, `resolve_channel`, `search_channels` |
| `UserLibraryReader` | `get_liked_videos`, `get_subscriptions`, `get_playlists`, `get_playlist_items` |
| `PlaylistWriter` | `ensure_write_access`, `create_playlist`, `add_playlist_item` |
| `ApprovalStore` | `record`, `get`, `consume` (written only by `cerebra-cli approve`) |
| `TokenStore`, `ProposalStore`, `CacheStore`, `QuotaStore`, `AuditLog`, `Clock` | infrastructure |

`LiveYouTubeAdapter` implements all four YouTube ports; `FixtureVideoReader` & co. implement the
same protocols (LSP). `tests/contract/` runs the same suites against both.

### Composition

```python
from cerebra.infrastructure.composition import build_container

container = build_container(cfg, use_fixtures=True)  # offline, no OAuth (CI)
container = build_container(cfg, use_fixtures=False)  # live adapter + OAuth + CLI approval
```

Live mode always sets `require_approval=True` and binds proposals to the account.
`oauth=`, `service_factory=` and `clock=` are test injection points.

## 3. Live adapter rules

* Requests are built with `googleapiclient.discovery.build("youtube","v3", static_discovery=True)`.
* Allowed methods (`ALLOWED_METHODS`): `videos.list`, `channels.list`, `search.list`,
  `playlistItems.list`, `playlists.list`, `subscriptions.list` and the two writes
  `playlists.insert`, `playlistItems.insert`. Anything else raises before a request is built.
  `videos.insert` exists only in quota modelling. Tests also AST-scan the module for
  `update`/`delete`/`rate` and HTTP/scraping libraries.
* **Quota:** every call does `quota.reserve(cost)` → request → `quota.consume(method)`
  (`release` if it failed before reaching YouTube). Failed/ambiguous calls are assumed billed
  (ADR-B §5). Over budget → `QUOTA_BUDGET_EXCEEDED` with **zero** API calls.
* **Retries:** reads retry up to 3× on 5xx/429/network errors with exponential backoff. Writes
  are **never** retried (ADR-C §4).
* **Scopes:** reads `youtube.readonly`; writes `youtube`, obtained via
  `ensure_write_access()` → `OAuthManager.ensure_scope()` (incremental authorisation).
* Cost-aware choices: `forHandle` before `search.list` for `/c/` names; `search.list` capped at
  2 pages; channel uploads via the uploads playlist; liked videos capped at 1,000.

### Quota accounting in live mode

Use cases make the FR-5.8 up-front decision with `can_afford`; the adapter does the real
accounting. To avoid double counting, composition passes use cases an
`AdapterMeteredQuota` (check-only `reserve`, no-op `consume`/`release`). In fixture mode the
use cases use the real `QuotaLedger` with real holds, exactly as in Phase 2. Live mode uses
`PersistentQuotaLedger` (survives restarts, resets on the Pacific day change).

## 4. Write protocol (ADR-A, ADR-C)

```
PROPOSED ─(digest ok + CLI approval + quota preflight + write scope)─► CONFIRMED ─► PARTIAL
PARTIAL: item_states{video_id: PENDING|INSERTED|FAILED}, playlist_id, ambiguous persisted
         before every write ─► CREATED when all items INSERTED
playlists.insert definitive error ─► FAILED;  ambiguous (5xx/timeout) ─► stays PARTIAL
```

Order inside `CreatePlaylistUseCase.create` (a failure in steps 1-6 leaves the proposal
`PROPOSED` and the approval unused, so the human can fix the problem and retry):

1. load, expiry, single-use state
2. `hmac.compare_digest` on the digest
3. account binding (proposal and approval belong to the logged-in account)
4. CLI approval record matches digest and account
5. quota preflight for `50 + 50n` units
6. `writer.ensure_write_access()` (incremental scope upgrade)
7. consume the approval (single use)
8. persist CONFIRMED, then PARTIAL with all items PENDING
9. `playlists.insert`, persist `playlist_id`
10. per item: `playlistItems.insert` at position = items inserted so far, persist the result

`RecoverPlaylistUseCase` (`cerebra-cli recover`) reads remote state first: finds the playlist by
the proposal id embedded in its description, lists its items, inserts only what is missing.
Startup only *reports* pending recoveries on stderr; it performs no writes.

## 5. Errors

All failures are `CerebraError(code, message, retryable, suggested_action)`; MCP returns
`error.to_dict()`. Codes: `INVALID_VIDEO_ID`, `INVALID_CHANNEL_REF`, `VIDEO_NOT_FOUND_OR_PRIVATE`,
`CHANNEL_NOT_FOUND`, `AUTH_REQUIRED`, `AUTH_EXPIRED`, `INSUFFICIENT_SCOPE`,
`QUOTA_BUDGET_EXCEEDED`, `QUOTA_EXHAUSTED`, `PROPOSAL_NOT_FOUND`, `PROPOSAL_EXPIRED`,
`PROPOSAL_ALREADY_USED`, `PROPOSAL_INVALID_STATE`, `MISSING_CONSTRAINT`, `PLAYLIST_INFEASIBLE`,
`NO_YOUTUBE_CHANNEL`, `PARTIAL_CREATION`, `INVALID_ARGUMENT`, `TRANSIENT_ERROR`,
`YOUTUBE_API_ERROR`, `APPROVAL_REJECTED`.
HTTP mapping: 401→`AUTH_EXPIRED`; 403 `quotaExceeded|dailyLimitExceeded`→`QUOTA_EXHAUSTED`;
403 `insufficientPermissions`→`INSUFFICIENT_SCOPE`; `youtubeSignupRequired`→`NO_YOUTUBE_CHANNEL`;
429/5xx/network→`TRANSIENT_ERROR`; other 4xx→`YOUTUBE_API_ERROR`.

## 6. Configuration (environment only; no secrets in code)

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | – | OAuth desktop client |
| `CEREBRA_YOUTUBE_API_KEY` | – | optional key for public reads without login |
| `CEREBRA_USE_FIXTURES` | `0` | `1` → fixture adapter (CI) |
| `CEREBRA_USE_KEYRING` | `1` | `0` → file token store only |
| `CEREBRA_TOKEN_FILE` | `~/.config/cerebra/token.json` | fallback token file (0600) |
| `CEREBRA_REFRESH_TOKEN` | - | headless runs (scheduled live CI): refresh token used in memory only, never stored or logged |
| `CEREBRA_REFRESH_TOKEN_SCOPES` | `youtube.readonly` | scopes that token carries (space/comma separated); declare `youtube` for the write test |
| `CEREBRA_INTERACTIVE_AUTH` | `1` | allow browser consent from `create_playlist`; `0` → `INSUFFICIENT_SCOPE` |
| `CEREBRA_DATA_DIR` | `~/.cerebra` | SQLite database |
| `CEREBRA_QUOTA_BUDGET` / `CEREBRA_SEARCH_BUDGET` | `10000` / `100` | pools |
| `CEREBRA_PROPOSAL_EXPIRY` | `900` | seconds |
| `CEREBRA_CACHE_TTL`, `CEREBRA_CACHE_MAX`, `CEREBRA_MAX_CANDIDATES` | see `config.py` | cache / candidates |

## 7. Testing

```bash
PYTHONPATH=src pytest tests/ --cov=cerebra --cov-report=term-missing
```

No test touches the network. `tests/support/fake_youtube.py` mimics the googleapiclient
resource objects (and records every call); `tests/support/live_env.py` builds a fully wired
live container on top of it. A new adapter must pass `tests/contract/`.

### Live API suites (manual / scheduled only)

`tests/integration/test_live_readonly.py` (marker `live_readonly`) and
`tests/integration/test_live_write.py` (marker `live_write`) call the REAL YouTube API. They are
deselected by default (`addopts` in `pyproject.toml`) and skip themselves without credentials, so
PR CI never reaches them (NFR-TE-1, TR-5). They run only from `.github/workflows/live-api-readonly.yml`
(weekly, strict budget) and `live-api-write.yml` (manual; human types the confirmation phrase).

```bash
# local manual run, throwaway account only
export GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... CEREBRA_REFRESH_TOKEN=...
export CEREBRA_QUOTA_BUDGET=500 CEREBRA_SEARCH_BUDGET=5
PYTHONPATH=src pytest tests/integration -m live_readonly -v
```

The write test creates ONE private playlist named `CEREBRA-CI-TEST-<run id>` through the real
approval path and never edits or deletes anything (NFR-SA-2); the account owner removes the
playlist by hand. `tests/integration/test_live_write_dryrun.py` runs the same flow against the
in-memory double on every PR. Results are passed through `scripts/redact_results.py` before upload.

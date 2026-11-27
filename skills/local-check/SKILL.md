---
name: local-check
description: >
  Build the project, run the automated test suite, start the local server,
  invoke a read-only fetch, and report pass/fail for each step.
  Uses fixtures only — cannot modify YouTube (SK-2.2).
  Cites brain note: [[cerebra-dev-workflow]].
---

# local-check

Verify the local development environment end-to-end without touching the live API.

## Steps

### Step 1 — Build / install
```bash
cd /path/to/SE
pip install -e ".[dev]"
```
Report: PASS / FAIL + error output.

### Step 2 — Run automated test suite (SK-2.1)
```bash
PYTHONPATH=src pytest tests/unit tests/property tests/integration tests/contract tests/mcp_e2e tests/security -v --tb=short
```
Report: PASS (all green) / FAIL (list failing tests).
**No live API calls permitted** (NFR-TE-1).

### Step 3 — Start the local server (fixture mode)
```bash
PYTHONPATH=src python -m cerebra.mcp_server &
```
Wait up to 5 seconds for startup. Report: PASS / FAIL.

### Step 4 — Read-only fetch (SK-2.2)
Invoke `find_similar_videos` with a known fixture video ID (e.g. `dQw4w9WgXcQ`).
Verify the response is a valid list (empty is OK).
**Must use fixture adapter — cannot call the live YouTube API.**
Report: PASS / FAIL.

### Step 5 — Report
Print a summary table:

| Step | Status | Notes |
|------|--------|-------|
| Build | ✅/❌ | |
| Tests | ✅/❌ | N passed, M failed |
| Server start | ✅/❌ | |
| Read-only fetch | ✅/❌ | |

Overall: PASS only if all 4 steps pass.

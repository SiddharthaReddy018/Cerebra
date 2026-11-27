# Live API smoke runbook (Gap 3; TR-5, NFR-PF-3, NFR-PF-4, NFR-SE-2)

Purpose: prove the live adapter against the REAL YouTube API once, by hand. CI never does this
(NFR-TE-1); scheduled/manual workflows exist for later runs.

## 0. Rules
- Use a THROWAWAY Google account that has a YouTube channel. Never your main account (NFR-SA-1).
- OAuth client type: Desktop app. Consent screen in Testing; add the throwaway account as a test user.
- Refresh tokens expire after 7 days in Testing (CON-3). Re-run section 2 when tests skip or fail auth.
- Nothing from this run is pasted anywhere unredacted (NFR-SE-2, NFR-PV-1). See section 6.

## 1. Environment (new terminal, repo root)
    cd ~/Documents/SE_Complete && source .venv/bin/activate
    export GOOGLE_CLIENT_ID="..."
    export GOOGLE_CLIENT_SECRET="..."
    export CEREBRA_QUOTA_BUDGET=500
    export CEREBRA_SEARCH_BUDGET=5

Alternative: put the same values in a git-ignored `.env` (copy `.env.example`, `chmod 600`) and
load it into this shell with `set -a; source .env; set +a`. pytest does not read `.env` itself.

## 2. Get a refresh token without touching the keychain
    export CEREBRA_USE_KEYRING=0
    export CEREBRA_TOKEN_FILE=/tmp/cerebra-live-token.json
    cerebra-cli login            # browser opens; sign in with the THROWAWAY account
    cerebra-cli status           # expect authenticated: true
    export CEREBRA_REFRESH_TOKEN="$(python -c "import json;print(json.load(open('/tmp/cerebra-live-token.json'))['credentials']['refresh_token'])")"
    rm -f /tmp/cerebra-live-token.json
    unset CEREBRA_USE_KEYRING CEREBRA_TOKEN_FILE

## 3. Read-only live tests (cost: a few hundred units at most)
    python -m pytest tests/integration -m live_readonly -k "not write" -v --tb=short --junitxml=live-results.xml

Expected: TestLiveAuth, TestLiveVideoReads, TestLiveSimilarity, TestLiveUserLibrary,
TestLiveBudgetGuard pass; none skipped. A skip names the missing env var.

## 4. Write test (OPTIONAL, creates ONE private playlist)
Needs a token that was granted the write scope: repeat section 2 with `cerebra-cli login --write`, then:

    export CEREBRA_REFRESH_TOKEN_SCOPES="https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube"
    export CEREBRA_LIVE_WRITE_CONFIRM="I-CONFIRM-WRITE-TO-TEST-ACCOUNT"
    python -m pytest tests/integration -m live_write -k test_create_private_test_playlist -v --tb=short --junitxml=write-results.xml

Result: one PRIVATE playlist named CEREBRA-CI-TEST-<id> on the throwaway account. Delete it by hand
in YouTube (the code never deletes, NFR-SA-2).

## 5. Latency (closes NFR-PF-3, NFR-PF-4; record in the table below)
Run from repo root with the section 1 + 2 env set (each costs real quota; budget is capped at 500):

    python -c "import asyncio,tempfile,time;from pathlib import Path;from tests.support.live_real import build_live_container,STABLE_PUBLIC_VIDEO as V;c=build_live_container(Path(tempfile.mkdtemp()),budget=500);t=time.perf_counter();asyncio.run(c.find_similar.find_similar_videos(V,limit=10));print('FR-1 uncached s:',round(time.perf_counter()-t,2),'quota:',c.quota.status())"

    python -c "import asyncio,tempfile,time;from pathlib import Path;from tests.support.live_real import build_live_container;c=build_live_container(Path(tempfile.mkdtemp()),budget=500);t=time.perf_counter();asyncio.run(c.propose.propose(topic='linear algebra',total_duration_seconds=3600,max_video_duration_seconds=1200,language='en'));print('FR-4 proposal s:',round(time.perf_counter()-t,2),'quota:',c.quota.status())"

Targets: FR-1 uncached < 10 s (NFR-PF-3); FR-4 proposal < 20 s (NFR-PF-4). Neither writes to YouTube.
If a call errors (auth, budget), the message names the cause; fix env and rerun.

| Date | Check | Measured | Quota used | Result |
|------|-------|----------|------------|--------|
| 2026-11-22 | read-only suite | 4.25 s total | ~105 general | 7 passed |
| 2026-11-22 | FR-1 uncached | 1.82 s | 105 general, 1 search | PASS (NFR-PF-3 < 10 s) |
| 2026-11-22 | FR-4 proposal (account_id stubbed) | 1.25 s | 101 general, 1 search | PARTIAL PASS (NFR-PF-4 < 20 s): 8 items; get_account_id/channels.list skipped via injected stub, no channel on throwaway account |
| 2026-11-25 | FR-4 proposal (full, unstubbed) | 1.2 s | 102 general, 1 search | PASS (NFR-PF-4 < 20 s): end-to-end incl. get_account_id/channels.list; throwaway account now has a channel |

## 6. Redact before sharing anything
    python scripts/redact_results.py live-results.xml write-results.xml
    grep -E "ya29\.|1//|AIza|GOCSPX|@" live-results.xml write-results.xml || echo clean
Share only the redacted files. Then clean up:

    rm -f live-results.xml write-results.xml
    unset CEREBRA_REFRESH_TOKEN GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET CEREBRA_REFRESH_TOKEN_SCOPES CEREBRA_LIVE_WRITE_CONFIRM

## 7. CI workflows (optional follow-up)
- live-api-readonly (weekly + manual) needs repo secrets GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
  CEREBRA_REFRESH_TOKEN_READONLY.
- live-api-write (manual, typed confirmation) needs GOOGLE_CLIENT_ID_TEST, GOOGLE_CLIENT_SECRET_TEST,
  CEREBRA_REFRESH_TOKEN_WRITE_TEST.
- Tokens in secrets expire after 7 days in Testing, so scheduled runs will fail until refreshed.

# Cerebra User Guide

Cerebra is a local MCP server that lets an AI assistant (e.g. Claude Desktop) find similar
YouTube videos and channels, recommend videos, analyse your engagement and, **only after you
approve it yourself**, create a playlist in your account.

> **Results are based on liked videos, subscriptions and playlists — NOT on watch history.**
> The YouTube Data API does not provide watch history, so Cerebra never uses or claims it.
> Liked videos are limited by the API to roughly your 1,000 most recent likes.

## 1. Install

```bash
pip install -e .          # Python 3.11+
```

This installs two commands: `cerebra` (the MCP server) and `cerebra-cli` (you, in a terminal).

## 2. Connect your Google account

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project, enable
   **YouTube Data API v3**, and create an OAuth client of type **Desktop app**.
2. Export the credentials in the shell that launches the server (never commit them):

   ```bash
   export GOOGLE_CLIENT_ID="…"
   export GOOGLE_CLIENT_SECRET="…"
   ```

   Or keep them in an untracked env file instead: copy `.env.example` to
   `~/.config/cerebra/.env` (used by the MCP server and `cerebra-cli`) or to `./.env` in the
   repo for development, fill in the values and run `chmod 600` on it. Real environment
   variables win over the file. `.env` is git-ignored; never commit real values.
3. Authorise read-only access (a browser window opens):

   ```bash
   cerebra-cli login
   ```

Cerebra starts with the **read-only** scope (`youtube.readonly`). Write access (`youtube`) is
requested later, only when you create your first playlist (see §5).

Where the token is kept: your operating system's keyring when available, otherwise
`~/.config/cerebra/token.json` (owner-only, mode `0600`). It is never written to logs, the
database or the repository.

> **Testing-mode apps:** if your Google Cloud app is in "Testing" status, Google expires the
> refresh token after **7 days**. `cerebra-cli status` warns you in the last 24 hours; run
> `cerebra-cli login` again. Pending proposals are not lost.

Public-only lookups (similar videos/channels) can alternatively use an API key:
`export CEREBRA_YOUTUBE_API_KEY="…"`. Library-based tools always need `login`.

## 3. Add Cerebra to Claude Desktop

```json
{
  "mcpServers": {
    "cerebra": {
      "command": "cerebra",
      "env": {
        "GOOGLE_CLIENT_ID": "…",
        "GOOGLE_CLIENT_SECRET": "…"
      }
    }
  }
}
```

## 4. What you can ask for

| Tool | What it does | Needs login |
|------|--------------|-------------|
| `find_similar_videos` | Videos similar to a URL or 11-character ID | no (API key or login) |
| `find_similar_channels` | Channels similar to a URL, `@handle` or ID | no (API key or login) |
| `recommend_videos` | Suggestions from your likes, subscriptions, playlists | yes |
| `analyse_engagement` | Top channels, categories, duration habits, trends | yes |
| `propose_playlist` | Builds a playlist **proposal** — nothing is created | yes |
| `create_playlist` | Creates the playlist, **after your terminal approval** | yes + write |

Video titles and descriptions are treated as untrusted data, never as instructions.

## 5. Creating a playlist (you stay in control)

1. Ask your assistant to propose a playlist (topic and total length are required).
2. In **your own terminal** review and approve it:

   ```bash
   cerebra-cli approve <proposal_id>
   ```

   You see the topic, every video and the privacy setting, then must type the first 8
   characters of the digest. This command refuses to run without an interactive terminal, so
   neither the assistant nor a script can approve for you.
3. Tell your assistant to call `create_playlist` with the `proposal_id` and
   `confirmation_digest` from the proposal.
4. The first time, a browser window asks you to grant write access (`youtube` scope). If you
   prefer to do it up-front: `cerebra-cli login --write`.

Rules: a proposal expires after **15 minutes**, an approval works **once**, playlists are
**private** unless you ask otherwise, and Cerebra only ever **adds a new playlist and its
videos** — it never edits or deletes anything that already exists.

### If something is interrupted

A network error can leave a playlist half-filled. Cerebra records progress before each step and
never blindly retries. To finish safely:

```bash
cerebra-cli pending               # proposals needing attention
cerebra-cli recover <proposal_id> # reads YouTube first, then adds only what is missing
```

Recovery adopts an existing playlist instead of creating a duplicate, and never re-adds videos
that are already there.

## 6. Quota

YouTube gives each project 10,000 units/day (resets at midnight Pacific). `search.list` costs
100 units and has its own 100-call/day pool; creating a playlist costs 50 units plus 50 per
video. Cerebra estimates a workflow **before** calling YouTube and refuses if it will not fit,
and remembers usage across restarts. `cerebra-cli status` shows the numbers; tune with
`CEREBRA_QUOTA_BUDGET` / `CEREBRA_SEARCH_BUDGET`.

## 7. Command reference

| Command | Purpose |
|---------|---------|
| `cerebra-cli login [--write]` | Authorise (read-only, or also write) |
| `cerebra-cli logout` | Revoke at Google and delete the local token |
| `cerebra-cli approve <id>` | Human approval of a playlist proposal |
| `cerebra-cli recover <id>` / `pending` | Finish / list interrupted writes |
| `cerebra-cli status` | Auth, token-expiry hint and quota |

## 8. Troubleshooting

| Error code | Meaning / fix |
|------------|---------------|
| `AUTH_REQUIRED` | Run `cerebra-cli login`. |
| `AUTH_EXPIRED` | Token revoked or the 7-day Testing limit passed: `cerebra-cli login`. |
| `INSUFFICIENT_SCOPE` | Write access missing: `cerebra-cli login --write`. |
| `APPROVAL_REJECTED` | No terminal approval yet (or digest/account mismatch): `cerebra-cli approve <id>`. |
| `PROPOSAL_EXPIRED` | Over 15 minutes: propose again. |
| `PROPOSAL_ALREADY_USED` | That proposal was already used: propose again. |
| `QUOTA_BUDGET_EXCEEDED` | Workflow would not fit today's budget; nothing was called. |
| `QUOTA_EXHAUSTED` | YouTube says the daily quota is spent; wait for midnight Pacific. |
| `NO_YOUTUBE_CHANNEL` | Create a YouTube channel for the account first. |
| `PARTIAL_CREATION` | Unclear outcome of a write: `cerebra-cli recover <id>`. |

Environment variables: see `docs/developer-api.md` §6.

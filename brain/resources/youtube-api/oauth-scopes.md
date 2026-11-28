---
title: YouTube Data API v3 OAuth scopes
type: research-note
status: reviewed
source: https://developers.google.com/youtube/v3/guides/auth/installed-apps
accessed: 2026-11-22
author: SegmentationFault
requirements: [FR-5.1, NFR-SE-1, CON-3]
---

# YouTube Data API v3 OAuth Scopes Reference

**Last verified:** 2026-11-09  
**Source:** https://developers.google.com/youtube/v3/guides/auth/installed-apps

---

## Scopes Used by Cerebra

| Scope | Usage | Phase |
|-------|-------|-------|
| `https://www.googleapis.com/auth/youtube.readonly` | Read liked videos, subscriptions, channel/video metadata | Phases 2–3 |
| `https://www.googleapis.com/auth/youtube` | Create playlists + add items | Phase 4+ |

---

## Full Scope Reference (YouTube Data API v3)

| Scope | Permits |
|-------|---------|
| `youtube.readonly` | Read all YouTube resources for authenticated user |
| `youtube` | Full manage account (read + write) |
| `youtube.force-ssl` | Equivalent to `youtube` but requires HTTPS (redundant) |
| `youtube.upload` | Upload videos only |
| `youtubepartner` | Partner-only operations |
| `youtube.channel-memberships.creator` | Manage channel memberships |

---

## Decision Notes (ADR-D)

- **No narrower write scope exists** for `playlists.insert` + `playlistItems.insert` only
- Using `youtube` (full manage) is unavoidable; code-level restriction enforces the port interface
- `videos.insert` is **never called** even though the `youtube` scope permits it (NFR-SA-2)
- Incremental authorization: start with `youtube.readonly`, upgrade to `youtube` only when `create_playlist` is first invoked

---

## 7-Day Refresh Expiry (CON-3)

In **Testing mode** (unverified OAuth app), refresh tokens expire after 7 days.  
In **Production mode** (verified app), refresh tokens do not expire.

Mitigation: `oauth_manager.ensure_valid_token()` detects expiry and prompts re-auth. Pending proposals are preserved in SQLite across re-auth.

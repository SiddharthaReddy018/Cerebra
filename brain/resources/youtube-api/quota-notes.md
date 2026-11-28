---
title: YouTube API v3 quota notes
type: research-note
status: reviewed
source: https://developers.google.com/youtube/v3/getting-started#quota
accessed: 2026-11-22
author: SegmentationFault
requirements: [FR-5.6, FR-5.7, FR-5.8, NFR-QU-1]
---

# YouTube API v3 Quota Notes

**Last verified:** 2026-11-09  
**Source:** https://developers.google.com/youtube/v3/getting-started#quota

---

## Daily Limits

| Pool | Limit | Reset |
|------|-------|-------|
| General units | 10,000 units/day | Midnight Pacific Time |
| `search.list` calls | 100 calls/day | Midnight Pacific Time |
| `videos.insert` calls | 100 calls/day | Midnight Pacific Time |

> [!WARNING]
> `search.list` has a **separate call quota** of 100/day, independent of the 10,000 general unit pool. Even if you have general units available, you cannot make more than 100 search calls per day. This makes search a scarce resource for high-volume applications.

---

## Per-Operation Costs

| Operation | Cost |
|-----------|-----|
| `videos.list` | 1 unit |
| `channels.list` | 1 unit |
| `playlistItems.list` | 1 unit |
| `search.list` | 100 units (also counts against 100 calls/day limit) |
| `playlists.insert` | 50 units |
| `playlistItems.insert` | 50 units per call |
| `playlists.list` | 1 unit |
| `subscriptions.list` | 1 unit |
| Invalid requests | ≥ 1 unit (may be charged even on error) |
| Additional pages | Same cost as first page (each `pageToken` = new call) |

---

## Cerebra Budget Model (ADR-B)

### FR-1 (find_similar_videos) Without Search
- 1 `videos.list` for seed = 1 unit
- Up to 50 `videos.list` for candidates = 50 units
- **Total: ~51 general units per call**

### FR-4 (propose + create playlist, 10 videos)
- Proposal: ~50 general units (candidate fetch)
- Creation: 50 (playlists.insert) + 10×50 (playlistItems.insert) = 550 units
- **Total: ~600 general units per playlist**

### SRS NFR-QU-1 Conflict (C-1)
The SRS assumed ~250 units/FR-1 based on `search.list`. With the 100 calls/day search limit, that approach would be exhausted after 100 FR-1 calls. **Resolution (ADR-B):** prefer `videos.list` chains; treat `search.list` as a last-resort scarce resource with explicit budget tracking.

---

## Re-Verification Reminder (TBD-4)

> [!IMPORTANT]
> These figures should be re-verified against the official quota page at implementation time, specifically for Phase 3 (live adapter). Quota costs can change with API updates. Cite the official page URL and verification date in the commit.

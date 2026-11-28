---
title: ISO 8601 durations from the YouTube API
type: research-note
status: reviewed
source: https://developers.google.com/youtube/v3/docs/videos (contentDetails.duration); ISO 8601
accessed: 2026-11-22
author: SegmentationFault
drafted-with: ai, see [[ai-session-2026-11-22-brain-drafts]]
requirements: [FR-4.2, FR-4.3, FR-3.5, NFR-US-2, TR-1]
---

# ISO 8601 durations

## Format
Durations look like PT4M13S, PT1H, PT45S, or P1DT2H for very long items: P starts the period, an
optional day part follows, T separates the time part, then hours (H), minutes (M), seconds (S).
Any component can be missing.

## Rules for Cerebra
- Convert to integer seconds once, at the adapter boundary, and use seconds everywhere else.
- Live or upcoming items can report a zero length; treat them as not usable for playlists.
- Format back to a human form such as 14:32 only at the presentation edge (NFR-US-2).
- The playlist solver (FR-4.3) and duration bands (FR-3.5) both depend on this parser being right,
  so it has unit tests and a property test that round-trips seconds through the ISO form (TR-1).

Related: [[videos-list-metadata]], [[playlist-writes]].

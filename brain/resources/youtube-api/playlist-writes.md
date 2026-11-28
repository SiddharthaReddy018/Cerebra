---
title: Writing playlists: playlists.insert and playlistItems.insert
type: research-note
status: reviewed
source: https://developers.google.com/youtube/v3/docs/playlists/insert; https://developers.google.com/youtube/v3/docs/playlistItems/insert
accessed: 2026-11-22
author: SegmentationFault
drafted-with: ai, see [[ai-session-2026-11-22-brain-drafts]]
requirements: [FR-4.5, FR-4.9, FR-4.12, FR-4.13, NFR-SA-1, NFR-SA-2]
---

# Playlist writes

## The two write calls
- playlists.insert creates an empty playlist: title, optional description, privacy status. Cerebra
  defaults to private (FR-4.9).
- playlistItems.insert adds one video per call: it names the playlist and the video resource, with
  an optional position.
- Each call costs 50 units (see [[quota-notes]]), so a 10-video playlist is about 550 units.

## Consequences
- Creation is not atomic. The playlist can exist while some items failed, hence the PARTIAL state and
  the retry of missing items only (FR-4.13; ADR-C and ADR-F in docs/adr).
- Writes need the broader scope, requested only when first needed ([[oauth-scopes]]).
- An account without a YouTube channel gets youtubeSignupRequired; Cerebra returns
  NO_YOUTUBE_CHANNEL and creates nothing (FR-4.12). See [[api-errors-and-retry]].
- Cerebra implements only these two write methods; it never edits or deletes content (NFR-SA-2).

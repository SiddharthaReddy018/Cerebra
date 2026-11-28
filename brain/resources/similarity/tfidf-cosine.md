---
title: TF-IDF and cosine similarity for Cerebra
type: research-note
status: reviewed
source: https://scikit-learn.org/stable/modules/feature_extraction.html#tfidf-term-weighting
accessed: 2026-11-22
author: SegmentationFault
drafted-with: ai, see [[ai-session-2026-11-22-brain-drafts]]
requirements: [FR-1.5, FR-1.8, FR-1.9, FR-2.3, CON-5, BR-4]
---

# TF-IDF and cosine similarity

## Idea
A term gets a high weight in a document when it appears often there (term frequency) and rarely
across the rest of the collection (inverse document frequency). Each document becomes a sparse
vector of such weights. Cosine similarity is the dot product of two vectors divided by the product
of their lengths: 1 means the same direction, 0 means no shared weighted terms.

## Why Cerebra needs it
The API has no related-videos or related-channels endpoint (CON-5), so similarity is computed by us
from text fields: title, description and tags for videos (FR-1.5). See [[videos-list-metadata]] for
where those fields come from.

## Implementation notes
- scikit-learn offers a TF-IDF vectoriser and a cosine function; fitting on the seed plus its
  candidates means the idf reflects the candidate set, not the whole web.
- The text score is only one term of the weighted total: w1 for cosine, then category, same channel,
  language and duration band (weights recorded in ADR-A and [[overview]]).
- Output must be deterministic (FR-1.9): same input and provider data give the same ranking, ties
  broken by ID.
- Missing tags are normal (AS-3): score from the remaining fields and set tags_missing (FR-1.8).

## Limits
Bag of words has no notion of synonyms or meaning, and a tiny corpus makes idf noisy. Popularity
signals such as view counts are deliberately excluded from ranking (BR-4).

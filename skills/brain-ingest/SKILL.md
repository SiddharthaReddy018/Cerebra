---
name: brain-ingest
description: >
  Add new material (article, document, meeting, AI session) to brain/inbox/
  as a distilled note with frontmatter and [[wikilinks]], then log the run
  in brain/log.md. Cites brain note: [[cerebra-knowledge-system]].
---

# brain-ingest

Ingest new material into the second brain PARA vault.

## Steps

### Step 1 — Identify the source
Determine: title, source URL or reference, type (article/meeting/AI session/doc), date.

### Step 2 — Distil (SK-3.2)
Summarise the content **in the team's own words**. Do NOT paste copyrighted text.
Capture:
- Core insight (2–4 sentences)
- Key concepts (as [[wikilinks]] where possible)
- Connections to open requirements, ADRs, or brain notes

### Step 3 — Write the note
Create `brain/inbox/YYYY-MM-DD-<slug>.md` with this frontmatter:

```yaml
---
title: "<title>"
source: "<URL or reference>"
type: article | meeting | ai-session | doc
date: YYYY-MM-DD
tags: [cerebra, <topic>]
status: inbox
---
```

Body: distilled summary + [[wikilinks]].

### Step 4 — Log the run (SK-3.1)
Append to `brain/log.md`:
```
## YYYY-MM-DD brain-ingest
- Ingested: <title>
- Note: brain/inbox/YYYY-MM-DD-<slug>.md
```

### Rules
- One note per source.
- Link to requirement IDs, ADR IDs and issue numbers where relevant.
- Never paste sensitive data, tokens, or personal info.

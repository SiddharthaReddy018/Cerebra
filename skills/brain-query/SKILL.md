---
name: brain-query
description: >
  Answer questions from the second brain, citing notes used. Draft documents
  such as ADRs citing brain notes. Only logs runs — no note modifications.
  Cites brain note: [[cerebra-knowledge-system]].
---

# brain-query

Query the second brain to answer questions or draft documents.

## Steps

### Step 1 — Understand the question
Identify what is being asked. Is it a factual lookup, a draft request, or a decision query?

### Step 2 — Search the brain
Read relevant notes from `brain/projects/`, `brain/areas/`, `brain/resources/`.
List the notes consulted.

### Step 3 — Compose the answer or draft
Cite the notes used as `[[note-name]]`.
For ADR drafts, follow the template: Context / Options / Decision / Rationale / Consequences.

### Step 4 — Log the run only (SK-5.1, SK-5.2)
Append to `brain/log.md`:
```
## YYYY-MM-DD brain-query
- Question: <summary>
- Notes consulted: [[note-1]], [[note-2]]
- Output: answer/draft
```
**Do not modify any note except log.md** (SK-5.2).
Any resulting change (e.g. a new ADR) goes through a pull request.

---
name: brain-refine
description: >
  Move notes from inbox into PARA folders, merge duplicates, fix broken
  [[wikilinks]], and update brain/index.md.
  Cites brain note: [[cerebra-knowledge-system]].
---

# brain-refine

Organise and refine the second brain vault.

## Steps

### Step 1 — Review inbox
List all files in `brain/inbox/`. For each note determine its PARA destination:
- **Projects**: active work tied to a deadline (e.g. Stage 2 deliverables)
- **Areas**: ongoing responsibilities (e.g. engineering practices, testing)
- **Resources**: reference material (e.g. YouTube API docs, MCP spec)
- **Archive**: completed or obsolete items

### Step 2 — Move notes (SK-4.1)
Move each file to the correct PARA folder. Update all [[wikilinks]] that referenced the old path.

### Step 3 — Merge duplicates
Identify notes covering the same topic. Merge content, keeping the richer version. Delete the duplicate.

### Step 4 — Fix broken links
Scan all `.md` files for `[[links]]` that do not resolve to an existing file. Fix or note as `[[MISSING: ...]]`.

### Step 5 — Update brain/index.md (SK-4.1)
Update the index to reflect the current structure. Add entries for new notes. Remove entries for deleted notes.

### Step 6 — Log the run
Append to `brain/log.md`:
```
## YYYY-MM-DD brain-refine
- Moved: N notes
- Merged: M duplicates
- Fixed: K broken links
- Index updated: yes
```

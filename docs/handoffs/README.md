# Cross-stream handoffs

When a change in one stream means another stream has to **do** or **know** something, the author
leaves a note here. Each stream's coding agent reads its folder before it starts work.

```
docs/handoffs/
  stream-a/   notes FOR Stream A (web & phone)
  stream-b/   notes FOR Stream B (agent, realtime & objects)
  stream-c/   notes FOR Stream C (twin, data & simulation)
```

## Reading (do this at the start of every work session)

Notes may sit on a branch that isn't merged yet, so look across all remote branches. Replace
`stream-a` with your stream:

```bash
git fetch --all --prune
for b in $(git branch -r --format='%(refname:short)' | grep -v HEAD); do
  git grep -l '^status: open' "$b" -- docs/handoffs/stream-a/ 2>/dev/null
done
# read one:  git show origin/stream-b/agent-v0:docs/handoffs/stream-a/<file>.md
```

For each open note: do the items under "What you need to do", or tell the author why not.

## Writing

Add a note in the **recipient's** folder, in the same commit as the change that causes it (or
right after). One file per topic: `YYYY-MM-DD-from-<a|b|c>-<topic>.md`.

```markdown
---
from: B
to: A
date: 2026-11-14
status: open            # open | done | declined
source: stream-b/agent-v0 @ 850c835
---
# Short title

**Why:** one or two sentences on what changed.

## What you need to do
- [ ] Concrete action, with file paths or contract names.

## Details
Shapes, examples, what is not built yet.
```

Rules:
- Write for the other stream's agent: it has not seen your code. Name files, fields and events.
- Only things the recipient must act on or rely on. Internal details stay in commit messages.
- A note never changes a contract by itself. Schema changes still follow I-G3 and I-G6 in
  WORKSTREAMS.md; the note points to them.

## Closing

The recipient brings the file into their branch
(`git checkout origin/<author-branch> -- docs/handoffs/<their-folder>/<file>.md`), ticks the boxes,
sets `status: done` (or `declined` with a reason) and adds a line `resolved: <branch> @ <commit>`.

`docs/stream-a-handoff.md` (C → A, the twin and interior GLB) was written before this folder
existed and stays where it is.

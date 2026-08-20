---
name: knowledge-base
description: Use when the user asks to save, write down, keep, or update a document, note, or list — anything titled they expect back verbatim later ("save this as…", "add X to my reading list", "what's in my notes about…").
---

# Knowledge base

Mai keeps the user's documents as markdown files in a `Mai` folder in **their own
Google Drive**. Use the `docs` tools (`list_documents`, `read_document`,
`save_document`, `delete_document`) — never CodeAct/Drive API calls for this.

## Documents vs memory
- **Document**: titled content the user expects back verbatim — notes, lists,
  briefs, drafts, plans they'll keep adding to.
- **Memory**: small standalone facts ("takes coffee black"). Keep using memory
  for those; don't create a document for a one-line fact.

## Editing safely
`save_document` **overwrites**. To change an existing document:
1. `read_document(title)` to get the full current text.
2. Apply the user's change to that text (append, rewrite a section, …).
3. `save_document(title, <the complete updated text>)`.
Never save from what you remember of a document — always re-read first.

## Conventions
- Titles: short and human, e.g. `reading list`, `apartment hunt` — no `.md`, the
  tools handle extensions. Reuse an existing title when the user means the same
  document (check `list_documents` when unsure).
- Content is plain markdown; keep the user's own wording.
- Deleting is destructive — confirm with the user first.

## Not connected yet?
If a docs tool replies that Drive isn't connected, it includes a connect link —
send that link to the user as-is and tell them Mai only gets a single folder it
creates ("Mai"), nothing else in their Drive. After they confirm they've
approved, retry the original action.

---
name: word-document-safe-editing
description: Safely inspect, edit, rewrite, and verify Microsoft Word DOCX documents while preserving formatting and protected structure. MUST be invoked for every Word document editing request, whether or not EndNote fields are known to be present, so the document is preflighted and the safest supported route is used instead of destructive paragraph-level replacement or format conversion.
---

# Word document safe editing

Invoke this Skill for every Word document edit. Use the installed
`endnote-safe-word` CLI to preflight every DOCX and, when active EndNote
fields are present, before and after each edit. The Skill and CLI are
installed together from this repository per `INSTALL.md`; detailed schema and
fallback rules remain in the linked references.

## Non-negotiable rules

1. Never overwrite the source DOCX.
2. Never use `paragraph.text`, `cell.text`, Markdown/HTML/RTF conversion, Pandoc
   round trips, or full-document regeneration.
3. Treat every complex field from `w:fldChar begin` through its matching
   `w:fldChar end` as one immutable object.
4. Never directly alter citation display text, `w:instrText`, embedded EndNote data,
   or the bibliography field.
5. Use `patch` only for one safe ordinary `w:t` node. Use the rewrite-view commands
   for supported substantial edits and citation movement.
6. Edit only `paragraphs[].text` in a rewrite-view JSON. Keep every `CIT` and `FMT`
   token exactly once and never change frozen metadata.
7. Do not bypass a refusal with another DOCX library or conversion route. Report
   unsupported locations for manual editing.
8. A structural PASS is necessary but not sufficient. Open every final candidate in
   Microsoft Word with the original EndNote library and run **Update Citations and
   Bibliography**.
9. Never invoke `osascript`, directly or indirectly.

## Choose the route

- Use `patch` for a small, exact replacement contained wholly in one ordinary text
  node without moving a citation.
- Use `rewrite-export` and `rewrite-apply` for a substantial rewrite of one
  contiguous supported direct-body paragraph range, including moving complete
  citation fields with their claims.
- Use `move-citations` only as a low-level prepared-placeholder operation when no
  prose rewrite is needed.

## Preflight

```bash
endnote-safe-word scan manuscript.docx --json manuscript.preflight.json
```

Stop on XML errors, unbalanced fields, or an unexplained field/record count.

## Local exact patch

Prepare the schema described in `references/patch-schema.md`, then run:

```bash
endnote-safe-word patch manuscript.docx \
  --patches patches.json \
  --output manuscript_patched.docx \
  --report manuscript.patch-report.json
```

Do not broaden a refused patch to paragraph-level replacement.

## Native substantial rewrite

Export the selected direct-body paragraphs:

```bash
endnote-safe-word rewrite-export manuscript.docx \
  --paragraphs 1,2,3,4 \
  --json manuscript.rewrite-view.json
```

Rewrite only the string value of each `paragraphs[].text`. Treat `[[CIT:Cn]]` as a
complete EndNote citation field and `[[FMT:Fn]]` as a complete directly formatted
run. Use citation metadata and source context to place each citation after the claim
it supports. Keep every token exactly once; do not invent citations or claims.

Map the edited view back to native OOXML:

```bash
endnote-safe-word rewrite-apply manuscript.docx \
  --view manuscript.rewrite-view.json \
  --output manuscript_rewritten.docx \
  --report manuscript.rewrite-report.json
```

The apply step pins the source hash and metadata, reinserts deep copies of the
original OOXML atoms, reparses prose and atom positions, and enforces field,
formatting, record-identity, and package-surface invariants.

## Transaction verification

`rewrite-apply` runs the complete automated verification inside the transaction
(field and formatting invariants, package change surface, and reparsed semantic
paragraph views) and reports it in the `--report` JSON. Use that report as the
automated gate and reject the candidate unless `status` is `pass` and
`failures` is empty.

Run standalone `verify` or `check-surface` only for independent audit or
troubleshooting:

```bash
endnote-safe-word verify manuscript.docx manuscript_rewritten.docx \
  --json manuscript.verify.json

endnote-safe-word check-surface manuscript.docx manuscript_rewritten.docx \
  --editable-paragraphs 1,2,3,4 \
  --json manuscript.surface.json
```

A rewrite that moved complete citation fields changes citation order, and a
plain `verify` then fails by design. Pass `--allow-field-reordering` with the
`--expected-citation-order` list from the apply report to verify the declared
order instead.

Never treat an automated pass as a substitute for the Word and EndNote gate
below.

## Word and EndNote gate

Open the output in desktop Microsoft Word, open the original EndNote library, run
**Update Citations and Bibliography**, inspect citation placement, bibliography,
direct formatting, superscripts/subscripts, footnotes, equations, and tracked
changes, then save and reopen another new copy.

## Refusal conditions

Return an explicit manual-edit list when the requested range is noncontiguous or
the document/range contains unsupported nested body content, simple fields,
reference-list fields, tracked/protected wrappers, hidden prose, malformed tokens,
unbalanced fields, or citation boundaries the selected transaction cannot model.

Do not claim success when field signatures, record identities, token accounting,
declared citation order, protected nodes, direct formatting, or package scope differ.

## Status wording

Describe a passing candidate as **validated by automated structural guardrails**.
After the native gate passes, record it separately as a user- or operator-observed
Microsoft Word and EndNote PASS. Do not generalize one fixture's result to every
manuscript, Word version, EndNote version, or output style.

For external-editor fallback boundaries, read
`references/word-ai-integration.md`.

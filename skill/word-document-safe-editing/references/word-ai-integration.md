# Integration with a general Word editing tool

Prefer EndNote Safe Word's native rewrite-view route whenever the selected
paragraphs are supported. It gives the language model ordinary prose plus opaque
citation/format identifiers while deterministic code alone mutates OOXML.

```text
scan -> rewrite-export -> prose/token edit -> rewrite-apply -> verify/check-surface -> Word/EndNote
```

Use `patch` instead for a small exact edit in one ordinary `w:t` node.

## External editor fallback

Use a general DOCX editor only when both native routes refuse and the user accepts
the broader risk:

1. Run `word-document-safe-editing scan` on the untouched source and retain the JSON report.
2. Give the editor a copy and never overwrite the source.
3. Prohibit paragraph/cell `.text` replacement, document-format conversion, field
   edits, and changes to revisions or content controls.
4. Require the editor to produce a new DOCX.
5. Run `verify` and `check-surface` immediately and reject any failure.
6. Complete the Microsoft Word and EndNote gate with the original library.

An external editor's own “valid DOCX” result is not acceptance evidence. Its output
is an untrusted candidate until the independent checks and native application gate
pass.

## What the guardrails detect

- EndNote field count, type, semantic hash, atomic order, and citation order;
- embedded record identity and repeated-record multiplicity changes;
- field begin/separate/end marker changes and unbalanced fields;
- direct formatting and superscript/subscript count changes;
- unexpected DOCX parts, relationships, content types, protected nodes, paragraph
  properties, and run-property additions;
- XML parse failures.

## What automated checks cannot establish

- whether Microsoft Word renders and saves the candidate correctly;
- whether EndNote refreshes all citations and the traveling library without warning;
- whether style-inherited formatting changed visually;
- whether a source citation or bibliography entry was already semantically wrong;
- whether behavior generalizes across Word, EndNote, output-style, or producer
  versions.

The 2026-07-18 native rewrite fixture passed a user-observed Word/EndNote check. That
supports the preferred route but does not remove the per-output native gate.

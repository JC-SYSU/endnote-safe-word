# Patch JSON schema (alpha)

Top-level form:

```json
{"patches": [PATCH, PATCH]}
```

Each `PATCH` supports:

- `find` — required non-empty exact string.
- `replace` — required replacement string.
- `part` — optional; defaults to `word/document.xml`.
- `expected_count` — optional; defaults to `1`. Use `null` to skip count enforcement.
- `occurrence` — optional positive 1-based occurrence number.
- `context_contains` — optional text that must occur in the same paragraph.

The alpha only modifies matches contained wholly in a single safe `w:t` node. It refuses matches in fields, tracked revisions, tracked moves, or content controls.

Use `rewrite-export` and `rewrite-apply`, not a broader patch, when the requested edit
must restructure multiple runs or move complete citations within a supported
contiguous paragraph range.

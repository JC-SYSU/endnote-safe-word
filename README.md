# EndNote Safe Word — validated alpha

A conservative CLI, optional MCP server, and Codex Skill for editing `.docx`
manuscripts that contain active EndNote Cite While You Write fields.

The tool never treats arbitrary AI edits as safe. It inventories and fingerprints
EndNote fields, exposes supported citations and direct-format runs as opaque atoms,
refuses protected structures, writes a new DOCX, and verifies structural invariants.
The exact capability, evidence boundary, and next work are maintained in
[`docs/STATUS.md`](docs/STATUS.md).

## Install a release

Download and extract a versioned `endnote-safe-word-<version>.tar.gz` release bundle,
then run its installer:

```bash
tar -xzf endnote-safe-word-0.1.0a4.tar.gz
cd endnote-safe-word-0.1.0a4
./install.sh
```

The installer verifies the bundle checksums and installs:

- a versioned runtime under `~/.local/share/endnote-safe-word/`;
- stable CLI and MCP links under `~/.local/bin/`;
- the `word-document-safe-editing` Skill under
  `~/.codex/skills/word-document-safe-editing/`;
- the `word-document-safe-editing` MCP server entry point.

Third-party dependencies may be downloaded by pip. Add `~/.local/bin` to `PATH` if
needed. The installer prints, but does not execute, the stable `codex mcp add`
command. It never needs or modifies a development checkout.

The Skill is intentionally broad: agents must invoke it for every Word-document
editing request so they preflight the DOCX and choose a format-preserving route.
The current native mutation engine remains conservative and may refuse structures
outside its documented support boundary.

Install locations can be overridden with `ENDNOTE_SAFE_WORD_DATA_HOME`,
`ENDNOTE_SAFE_WORD_BIN_HOME`, `CODEX_HOME`, or `CODEX_SKILLS_HOME`.

Developers should use [`CONTRIBUTING.md`](CONTRIBUTING.md); release maintainers should
use [`docs/RELEASING.md`](docs/RELEASING.md). Editable installation is intentionally
not part of the user workflow.

## Safety boundary

- Never overwrite the source manuscript.
- Never use paragraph-level `.text` replacement or Markdown/HTML conversion.
- Complete EndNote fields are immutable atoms.
- Edits inside fields, tracked changes, tracked moves, content controls, hidden
  prose, and unsupported nested structures are refused.
- A passing verifier is a structural guardrail, not proof that Word and EndNote will
  refresh successfully. Every candidate still needs the native application gate.

## Choose a command

| Need | Command | Status |
|---|---|---|
| Inventory a manuscript | `scan` | Supported alpha |
| Exact edit in one safe `w:t` node | `patch` | Supported alpha |
| Substantial contiguous body rewrite | `rewrite-export`, then `rewrite-apply` | Preferred validated-alpha route |
| Prepared citation relocation | `move-citations` | Low-level alpha |
| Compare source and candidate | `verify`, `check-surface` | Required guardrails |

Exit code `0` means pass. Exit code `2` means refusal, validation failure, or input
error.

## Scan

```bash
endnote-safe-word scan manuscript.docx --json manuscript.preflight.json
```

Stop on XML errors, unbalanced fields, or unexplained field/record counts.

## Exact local patch

```bash
endnote-safe-word patch manuscript.docx \
  --patches patches.json \
  --output manuscript_patched.docx \
  --report manuscript.patch-report.json
```

Patch files contain exact `find`/`replace` strings and normally require one match.
The patcher refuses replacements spanning Word runs or entering protected content.
Use the rewrite route rather than broadening a refused patch.

## Native substantial rewrite

Export one contiguous range of direct body paragraphs:

```bash
endnote-safe-word rewrite-export manuscript.docx \
  --paragraphs 1,2,3,4 \
  --json manuscript.rewrite-view.json
```

Edit only each `paragraphs[].text` value. Keep every `[[CIT:Cn]]` citation token and
`[[FMT:Fn]]` directly formatted run token exactly once. Then apply the view to a new
DOCX:

```bash
endnote-safe-word rewrite-apply manuscript.docx \
  --view manuscript.rewrite-view.json \
  --output manuscript_rewritten.docx \
  --report manuscript.rewrite-report.json
```

The apply command pins the source hash and frozen metadata, reinserts original OOXML
subtrees, and enforces field, record, formatting, token, and package-surface
invariants. It refuses unsupported fields, reference-list selections, nested body
paragraphs, protected wrappers, malformed tokens, and stale or tampered views.

## Verification

`rewrite-apply` runs the complete automated verification inside the transaction
and reports it in the `--report` JSON: EndNote and formatting invariants
(`verification`), the DOCX package change surface (`change_surface`), and
reparsed semantic paragraph views (`checks`). Use that report as the automated
gate: reject the candidate unless `status` is `pass` and `failures` is empty.

The standalone `verify` and `check-surface` commands remain available for
independent audit or troubleshooting, for example to compare two documents
outside a rewrite transaction:

```bash
endnote-safe-word verify manuscript.docx manuscript_rewritten.docx \
  --json manuscript.verify.json

endnote-safe-word check-surface manuscript.docx manuscript_rewritten.docx \
  --editable-paragraphs 1,2,3,4 \
  --json manuscript.surface.json
```

When a rewrite moved complete citation fields, citation order changed and a
plain `verify` fails by design. Pass `--allow-field-reordering` together with
the `--expected-citation-order` list from the apply report to verify the
declared order instead.

The Word and EndNote gate below remains mandatory for every candidate
regardless of automated results.

## Word and EndNote gate

Open the output in desktop Microsoft Word with the original EndNote library, run
**Update Citations and Bibliography**, inspect citations, bibliography, direct
formatting, superscripts/subscripts, footnotes, equations, and tracked changes, then
save and reopen another new copy. Keep the untouched master.

## Important limitations

- Alpha quality; one real fixture passed a user-observed Word/EndNote check, which
  does not establish universal compatibility.
- Native rewriting currently supports one contiguous range of direct body
  paragraphs and refuses documents containing nested body paragraphs such as tables
  or text boxes.
- Bibliography fields are not edited or reordered.
- Semantic OOXML fingerprints are preserved; byte-identical serialization is not
  promised.
- Direct superscript/subscript counts do not include formatting inherited solely
  from styles.
- Unusual fields, text boxes, damaged OOXML, and different Word/EndNote versions
  need additional validation.

## Project records

- Current facts and evidence: [`docs/STATUS.md`](docs/STATUS.md)
- Documentation map: [`docs/README.md`](docs/README.md)

Historical experiment protocols and raw validation records are retained in the
private development repository and are not part of this public release.

MIT. No warranty. Do not use the only copy of a manuscript.

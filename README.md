# EndNote Safe Word — validated alpha

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

A conservative CLI, optional MCP server, and Codex Skill for editing `.docx`
manuscripts that contain active EndNote Cite While You Write fields.

The tool never treats arbitrary AI edits as safe. It inventories and fingerprints
EndNote fields, exposes supported citations and direct-format runs as opaque atoms,
refuses protected structures, writes a new DOCX, and verifies structural invariants.
The exact capability, evidence boundary, and next work are maintained in
[`docs/STATUS.md`](docs/STATUS.md).

## Install

**Let your agent do it.** Copy the block below and paste it into your agent
(Codex, Claude Code, or any coding agent). The agent reads INSTALL.md and
installs the three components (CLI, MCP server, Skill):

```text
You are installing EndNote Safe Word. The source repository is
<this repository>. Do the following:

1. Clone (or cd into) the repository at $REPO.
2. Read INSTALL.md in full and execute it step by step: probe the
   machine and pick the install profile (section 0), install the CLI
   (section 1), verify the CLI (section 2), register the MCP server
   (section 3), and install the Skill (section 4).
3. Before any step that writes outside the repository (~/.codex,
   ~/.claude, shell rc files), show me the exact file and content and
   wait for my confirmation.
4. When everything is done, run the acceptance checklist (section 5)
   and report each item's result to me. Anything that fails: fix it
   per the troubleshooting table (section 6) before reporting back.
```

If you prefer to install by hand, follow [`INSTALL.md`](INSTALL.md) directly.
The three components: an `endnote-safe-word` CLI, the
`word-document-safe-editing` MCP server (you register with your agent client),
and the `word-document-safe-editing` Skill (the agent behavior contract, kept
in the client's skills directory).

The Skill is intentionally broad: agents must invoke it for every Word-document
editing request so they preflight the DOCX and choose a format-preserving route.
The current native mutation engine remains conservative and may refuse structures
outside its documented support boundary.

Release maintainers should use [`docs/RELEASING.md`](docs/RELEASING.md). Editable
installation is intentionally not part of the user workflow.

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

# Project status

Updated: 2026-08-12

This is the single current project source for capability, evidence, refusal
boundaries, and next work. Detailed historical protocols and full validation
records are retained in the private development repository and are not part of
this public release.

## Current designation

EndNote Safe Word is a **validated alpha** for conservative, field-aware editing of
DOCX manuscripts. Its preferred substantial-edit route separates language-model
rewriting from DOCX mutation:

1. native OOXML is parsed without document-format conversion;
2. complete EndNote fields and nonbaseline direct-format runs become opaque tokens;
3. the model edits only ordinary prose and token positions;
4. deterministic code reinserts the original OOXML subtrees;
5. structural, package-surface, semantic-position, and prose checks precede Word/
   EndNote review.

Development source, generated release artifacts, and user installations are
separate. Users install a checksummed versioned bundle into user-level directories;
the repository's editable environment and generated `release/` output are
maintenance concerns and are not part of normal use. This packaging change does not
broaden document-editing behavior or evidence.

The installed agent integration is named `word-document-safe-editing` for both the
Skill and MCP server. The Skill is a mandatory preflight for every Word-document
editing request, including documents not known to contain EndNote fields, so agents
do not begin with destructive paragraph-level replacement or format conversion.
This invocation policy does not broaden the native mutation engine: unsupported
structures still refuse under the boundaries below.

## Supported routes

| Need | Preferred route | Status |
|---|---|---|
| Inventory a manuscript | `scan` | Supported alpha |
| Small exact edit in one safe `w:t` node | `patch` | Supported alpha |
| Substantial rewrite of a contiguous direct-body range | `rewrite-export` then `rewrite-apply` | Validated alpha; preferred |
| Same-paragraph citation reorder with prepared placeholders | `move-citations` | Supported low-level alpha |
| Compare a source and candidate | `verify` and `check-surface` | Required guardrails |

## Evidence summary

The 2026-07-18 real-fixture experiment rewrote four introduction paragraphs from
358 to 325 words while preserving 10 EndNote-related fields, 6 citation anchors,
9 record identities, the bibliography field, and measured direct-format spans. The
complete `[6]` citation moved between paragraphs with its field hash and record
identity unchanged.

- 49 automated tests passed; the focused ruff checks passed.
- Independent strict verification, package-surface validation, atom/prose
  comparison, and ZIP integrity checks passed.
- The user reported a successful Microsoft Word and EndNote check for this fixture;
  this is user-attested evidence, not automated Word object-model evidence.
- Fixture SHA-256:
  `0cb1b72c0cf6f08f2d4b92cd7cb0b279a780f0c61e25e462847a78241ab296ba`.

This evidence validates one fixture and does not establish universal compatibility.
The full historical report and raw experiment records are retained in the private
development repository.

## Current refusal boundary

The native rewrite route refuses noncontiguous ranges, documents with nested body
paragraphs such as tables or text boxes, selected reference-list fields, simple or
unsupported Word fields, tracked/protected wrappers, hidden prose, malformed or
duplicated tokens, and stale or tampered views. It does not edit the bibliography
field or synthesize EndNote fields.

## Next work

- Test copied 10–20 page manuscripts with approximately 40–60 references and more
  citation occurrences while keeping each transaction bounded.
- Add fixtures for combined/repeated/adjacent/boundary fields, footnotes, equations,
  representative EndNote styles and versions, and Word for macOS/Windows behavior.
- Harden failed-output handling and resumable reports before exposing more
  interfaces.
- Keep field-signature, record-identity, package-surface, tracked-change, and
  superscript/subscript tests mandatory for every broadened behavior.

## Promotion beyond alpha

Multiple independent real manuscripts must pass field/record conservation, exact
token accounting, protected-structure checks, Word open/save/reopen, EndNote
refresh, direct-format preservation, and documented refusal behavior.

## Provenance

The integrated native-rewrite decision record, detailed historical validation
report, and raw experiment protocols are retained in the private development
repository and are not part of this public release.

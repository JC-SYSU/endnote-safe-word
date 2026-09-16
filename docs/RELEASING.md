# Releasing

word-document-safe-editing is published as a GitHub repository. Each version is a git
tag; the tag triggers CI, which runs the test suite and creates a GitHub
Release with generated notes. There is no self-extracting bundle — the
deliverable is the repository itself, installed per `INSTALL.md`.

## Version rules

- `pyproject.toml` `[project] version` and `word_document_safe_editing.__version__`
  must name the same version; `tests/test_version_consistency.py` enforces it.
- GitHub tag names must be `v<version>`, for example `v0.1.0a4`. The release
  workflow refuses a tag that does not match the project version.
- Bump the two version files together, in the same commit, as the last commit
  before the tag.

## Standard release

```bash
git commit -m "chore(release): bump version to 0.1.0a5"   # both version files
git tag v0.1.0a5
git push origin main
git push origin v0.1.0a5
```

`.github/workflows/release.yml` then:

1. validates that the tag matches the project version;
2. creates a GitHub Release for the tag with automatically generated notes.
   There are no artifact attachments; users deploy from the repository per
   `INSTALL.md`.

`.github/workflows/ci.yml` runs pytest (Python 3.10-3.12) and ruff on every
push and pull request.

## Manual fallback release

If CI is unavailable, create the Release locally with `gh`:

```bash
gh release create v0.1.0a5 --verify-tag --generate-notes
```

## Acceptance

1. The GitHub Release page shows the `v<version>` tag and generated notes.
2. Installing per `INSTALL.md` from the tagged commit works: acceptance
   checklist items 5.1-5.4 pass.

Microsoft Word and EndNote validation remains mandatory for document
candidates; creating a release adds no new product capability or evidence.
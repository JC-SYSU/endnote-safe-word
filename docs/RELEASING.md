# Releasing

EndNote Safe Word is published to GitHub with the standard tag-driven flow:
a version tag triggers CI that builds the release bundle and creates a GitHub
Release with the bundle as an attachment.

## Version rules

- `pyproject.toml` `[project] version` and `endnote_safe_word.__version__` must
  name the same version; `packaging/build_release.py` refuses to build
  otherwise.
- GitHub tag names must be `v<version>`, for example `v0.1.0a4`. The release
  workflow refuses a tag that does not match the project version.
- Bump the two files together, in the same commit, as the last commit before
  the tag.

## Standard release (CI)

```bash
git commit -m "chore(release): bump version to 0.1.0a5"   # both version files
git tag v0.1.0a5
git push origin main
git push origin v0.1.0a5
```

The `.github/workflows/release.yml` workflow then:

1. validates that the tag matches the project version;
2. builds the release bundle (`packaging/build_release.py`);
3. creates a GitHub Release for the tag, attaching
   `endnote-safe-word-<version>.tar.gz` and its `SHA256SUMS`, with
   automatically generated notes.

The `.github/workflows/ci.yml` workflow runs pytest (Python 3.10-3.12) and
ruff on every push and pull request.

## Local preflight (optional)

Before tagging, verify the bundle locally:

```bash
python packaging/build_release.py
```

The command refuses to overwrite an existing release of the same version.
The output is:

```text
release/
├── endnote-safe-word-<version>/
│   ├── install.sh
│   ├── LICENSE
│   ├── README.md
│   ├── SHA256SUMS
│   ├── VERSION
│   ├── skill/word-document-safe-editing/
│   └── wheels/endnote_safe_word-<version>-py3-none-any.whl
└── endnote-safe-word-<version>.tar.gz
```

Preconditions: Python 3.10 or newer with `build` installed, and a Git worktree
(the builder uses the current Git commit timestamp as `SOURCE_DATE_EPOCH`).
The builder validates that the wheel contains every package module and policy
JSON, and rejects test, cache, documentation, and development-build files in
the wheel. Release artifacts are never committed to the repository
(`release/` is git-ignored).

## Manual fallback release

If CI is unavailable, create the Release locally with `gh`:

```bash
python packaging/build_release.py
gh release create v0.1.0a5 \
  "release/endnote-safe-word-0.1.0a5.tar.gz" \
  "release/endnote-safe-word-0.1.0a5/SHA256SUMS" \
  --verify-tag --generate-notes
```

## Acceptance

1. The GitHub Release page shows the `v<version>` tag, the generated notes,
   and both attachments.
2. Download the archive from the Release page, verify `SHA256SUMS` inside the
   extracted bundle against the page values.
3. Install from outside the source checkout with temporary user-level
   destination overrides.
4. Confirm `endnote-safe-word --version`, the MCP executable, the installed
   Skill, and the stable `current` links.
5. Confirm command collisions are refused and an existing changed Skill is
   backed up rather than deleted.

The bundle includes this project's wheel but not third-party dependency
wheels, so pip may use the network during installation. Microsoft Word and
EndNote validation remains mandatory for document candidates; building a
release adds no new product capability or evidence.
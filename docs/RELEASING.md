# Building a release bundle

Release artifacts are generated locally from the development repository. They are
never committed and do not require a configured remote or CI system.

## Preconditions

- Use Python 3.10 or newer in a development environment installed with `.[dev,mcp]`.
- Ensure `pyproject.toml` and `endnote_safe_word.__version__` name the same version.
- Run the complete automated test suite.
- Review tracked changes before building; unrelated untracked files are not copied
  into the wheel or bundle.

## Build

```bash
python packaging/build_release.py
```

The command refuses to overwrite an existing release of the same version. Remove a
known generated `release/endnote-safe-word-<version>/` directory and matching
archive explicitly before rebuilding.

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

The builder uses the current Git commit timestamp as `SOURCE_DATE_EPOCH`, validates
that the wheel contains every package module and policy JSON, and rejects test,
cache, documentation, and development-build files in the wheel.

## Acceptance

1. Verify `SHA256SUMS` inside the extracted bundle.
2. Install from outside the source checkout with temporary user-level destination
   overrides.
3. Confirm `endnote-safe-word --version`, the MCP executable, the installed Skill,
   and the stable `current` links.
4. Confirm command collisions are refused and an existing changed Skill is backed
   up rather than deleted.
5. Confirm the Git worktree contains no generated wheel or archive.

The bundle includes this project's wheel but not third-party dependency wheels, so
pip may use the network during installation. Microsoft Word and EndNote validation
remains mandatory for document candidates; building a release adds no new product
capability or evidence.

# Development and Maintenance

This repository is the development workspace. Users install versioned release
bundles and should not run from this checkout.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e '.[dev,mcp]'
pytest
```

Editable installation is for development only. Keep generated wheels and release
archives out of Git.

## Required workflow

1. Read `AGENTS.md` and inspect `git status --short --branch` before editing.
2. Preserve unrelated user changes and stage only files for the current change.
3. Add focused tests for behavior changes. Document-structure changes must cover
   fields, tracked changes, and superscript/subscript preservation.
4. Run relevant checks and record commands and results.
5. Commit every maintenance change before considering the task complete.
6. Confirm the worktree is clean apart from explicitly documented user changes.

## Documentation maintenance

`README.md` is the user entrypoint. `docs/STATUS.md` is the single current project
fact, evidence, and next-work source. Keep both and
`skill/word-document-safe-editing/SKILL.md` aligned with behavior. Keep development guidance
here and release operations in `docs/RELEASING.md`.

Completed protocols and raw reports belong under dated `docs/archive/` paths. Label
manual Microsoft Word/EndNote evidence as `user-observed` or `operator-observed`,
not automated object-model evidence.

## Commit messages

Use a concise `type(scope): summary` subject and a body with:

```text
What changed:
Why:
Verification:
Risk or follow-up:
```

Do not combine unrelated refactors, formatting churn, generated artifacts, or user
files with a maintenance commit.

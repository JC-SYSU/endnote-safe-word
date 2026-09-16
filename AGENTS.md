# Repository instructions

- This project is intentionally conservative. Do not broaden patch behavior without adding refusal tests.
- Never introduce paragraph-level `.text` replacement or DOCX conversion through Markdown/HTML.
- EndNote field signatures must remain unchanged after a successful patch.
- New features require tests for fields, tracked changes, and superscript preservation.
- A passing verifier is a structural guardrail, not a replacement for validation in Microsoft Word and EndNote.
- Keep `README.md` as the user entrypoint, `docs/STATUS.md` as the single current
  project fact/evidence/next-work source, and the project Skill aligned when
  behavior changes.
- Archive completed experiment protocols under a dated `docs/archive/` directory;
  current workflow documents must link to the integrated milestone rather than
  treating archived plans as active work.

## Automation safety

- Never invoke `osascript` in this project. This prohibition includes direct shell
  calls and indirect use through helper scripts, wrappers, or test automation.
- If a Microsoft Word or EndNote validation step would require `osascript`, use a
  non-`osascript` interface instead or report that the native step is unavailable;
  do not bypass this rule.

## Version control and maintenance commits

- This project must remain a Git repository. Do not perform maintenance work while leaving the worktree without a commit.
- Every maintenance change must be committed before the task is considered complete. This includes source code, tests, documentation, the Codex Skill, packaging, configuration, and generated project metadata that is intentionally kept in the repository.
- Before editing, inspect `git status --short --branch`; preserve unrelated user changes and do not mix them into the maintenance commit.
- Keep each commit single-purpose and stage only files belonging to that change.
- Every commit message must explain the change carefully. Use a concise subject and a body covering `What changed`, `Why`, `Verification`, and any `Risk or follow-up`.
- A maintenance task is not complete until the relevant checks have been run, their results are recorded in the commit message or task report, and `git status --short --branch` is clean except for explicitly documented user changes.

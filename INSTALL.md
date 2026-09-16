# Installation guide (for the agent performing the install)

This document is an operating spec: run the steps in order. Every step lists an
**expected output** and **failure handling**. When everything passes, run the
"Acceptance checklist" at the end and report each item's result to the user.
Do not skip steps: the three components (CLI, MCP server, Skill) are
independent, and a missing one shows up as "installed but silently not
working" (for example the CLI installed but the MCP stanza missing: the agent
never preflights).

Let `$REPO` be the repository root. All commands run from `$REPO`. Platforms:
macOS / Linux / Windows; on Windows replace every `python3` below with
`python` (or `py -3`). The Codex config directory is `~/.codex` on all three;
Claude Code uses `~/.claude`. Everything in sections 3-4 writes outside this
repository — show the user the exact file and content before writing.

## 0. Probe the machine first — pick the install profile

```bash
python3 --version        # must be 3.11 or newer
command -v codex         # Codex CLI on PATH?
command -v claude        # Claude Code CLI on PATH?
```

| Profile | Detected | Install what |
| ---- | ---- | ---- |
| **A** | Codex present | CLI + Codex MCP registration + Codex skills directory |
| **B** | Claude Code present | CLI + Claude Code MCP registration + Claude skills directory |
| **C** | both present | CLI + both MCP registrations + both skills directories |
| not installable | neither client | the tool is driven by agents; install at least one client first |

| # | Check | Command | Expected | Failure handling |
| ---- | ---- | ---- | ---- | ---- |
| 0.1 | Python version | `python3 --version` | 3.11.x or newer | install Python 3.11+, then restart the install |
| 0.2 | Git | `command -v git` | path | install git |
| 0.3 | Codex dir (profile A/C) | `ls ~/.codex/` | config.toml or skills/ may not exist yet | installer steps create them |

## 1. Install the CLI package

Install the package plus the optional MCP dependency. Prefer a virtual
environment at `$REPO/.venv`; keep using the same interpreter below.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[mcp]'
```

Expected: `Successfully installed endnote-safe-word-0.1.0a4` (or the current
version).
Failure: missing network or pip index — check the pip source; `lxml` must
come from PyPI wheels.

## 2. Verify the CLI

```bash
.venv/bin/endnote-safe-word --version
.venv/bin/endnote-safe-word --help
```

Expected: version prints `0.1.0a4` (matching pyproject.toml), help lists the
eight subcommands (scan, patch, move-citations, rewrite-export, rewrite-apply,
verify, check-surface, intro-text).
Failure: import error — re-run section 1; version mismatch — stop and report,
do not continue.

If the user wants the command on `PATH`, add `$REPO/.venv/bin` to the shell
rc **with the user's confirmation** (section 3-4 write rule applies).

## 3. Register the MCP server

The MCP server exposes `scan`, `verify`, and `patch` to the agent. The server
entry point is the console script `word-document-safe-editing-mcp` from the
installed package.

**Profile A or C — Codex.** Append this stanza to `~/.codex/config.toml`
(section `[mcp_servers]` likely does not exist yet; create it). Confirm the
file change with the user before writing:

```toml
[mcp_servers.word-document-safe-editing]
command = "/ABSOLUTE/PATH/TO/$REPO/.venv/bin/word-document-safe-editing-mcp"
```

**Profile B or C — Claude Code.** Either use the CLI (writes
`~/.claude.json`; worth showing the user what it adds) or append the same
stanza-shaped entry manually. With the CLI:

```bash
claude mcp add word-document-safe-editing -- \
  /ABSOLUTE/PATH/TO/$REPO/.venv/bin/word-document-safe-editing-mcp
```

Expected: `codex mcp list` / `claude mcp list` shows
`word-document-safe-editing` connected.
Failure: parse error after edit — restore the previous file content and
report; command not found — the package install from section 1 failed or the
venv path is wrong.

## 4. Install the Skill

The Skill is the agent behavior contract (preflight, opaque atoms, refusal
rules) and, for agents, the way the workflow becomes discoverable. Copy the
whole directory:

```bash
cp -R "$REPO/skill/word-document-safe-editing" ~/.codex/skills/
# Profile B/C additionally:
cp -R "$REPO/skill/word-document-safe-editing" ~/.claude/skills/
```

Create `~/.codex/skills/` and `~/.claude/skills/` if they do not exist. This
writes outside the repository — confirm before copying. Do not copy a stray
`.DS_Store` or cache files; the source tree in `$REPO/skill/` contains only
`SKILL.md`, `references/`, and `scripts/`.

Expected: `ls ~/.codex/skills/word-document-safe-editing/SKILL.md` exists.
Failure: source path missing — the clone is incomplete; re-clone.

Do not install a copy under both names or keep an old
`endnote-safe-word`-named copy alongside: the skill must appear under the
single name `word-document-safe-editing`.

## 5. Acceptance checklist

Run each item and report the outcome to the user:

| # | Check | Command | Expected |
| ---- | ---- | ---- | ---- |
| 5.1 | CLI version | `.venv/bin/endnote-safe-word --version` | prints the repository version |
| 5.2 | MCP registered | `codex mcp list` (or `claude mcp list`) | `word-document-safe-editing` appears |
| 5.3 | Skill installed | `ls ~/.codex/skills/word-document-safe-editing/` (and `~/.claude/skills/` for B/C) | SKILL.md and references/ present |
| 5.4 | End-to-end preflight | `.venv/bin/endnote-safe-word scan <some .docx>` | exit 0, JSON report with `error_count: 0` for a healthy document |

Any failed item: fix per the table below before reporting back.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
| ---- | ---- | ---- |
| `endnote-safe-word: command not found` | venv bin not used | use `.venv/bin/endnote-safe-word`, or add `$REPO/.venv/bin` to PATH with user confirmation |
| `mcp list` shows the server but calls fail | wrong absolute path in the stanza | re-check the path with `.venv/bin/which word-document-safe-editing-mcp` |
| Skill not loaded by the agent | wrong directory or name | remove stray copies; keep exactly `word-document-safe-editing` |
| `ImportError: No module named 'mcp'` | installed without `.[mcp]` | rerun section 1 with the `mcp` extra |
| version mismatch after update | `pip install -e .` stale | rerun section 1 after `git pull` |

## 7. Uninstall

```bash
.venv/bin/python -m pip uninstall endnote-safe-word
rm -rf "$REPO/.venv"                       # the venv created by section 1
rm -rf ~/.codex/skills/word-document-safe-editing   # and ~/.claude/skills/...
```

Remove the `[mcp_servers.word-document-safe-editing]` stanza from
`~/.codex/config.toml` (or run `claude mcp remove word-document-safe-editing`
for Claude Code). These removals also write outside the repository — confirm
with the user before deleting.
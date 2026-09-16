# EndNote Safe Word @VERSION@

This release bundle installs the CLI, optional MCP runtime, and Codex Skill without
requiring a source checkout.

## Install

```bash
./install.sh
```

The installer verifies `SHA256SUMS`, creates a versioned virtual environment under
`~/.local/share/endnote-safe-word`, links both commands into `~/.local/bin`, and
installs the `word-document-safe-editing` Skill under
`~/.codex/skills/word-document-safe-editing`. Third-party Python
dependencies may be downloaded by pip.

If `~/.local/bin` is not on `PATH`, add it before invoking the commands. The
installer prints the exact MCP registration command but does not modify Codex
configuration.

Agents must invoke the Skill for every Word-document editing request so the DOCX is
preflighted before a format-preserving edit route is selected.

## Verify

```bash
endnote-safe-word --version
endnote-safe-word scan manuscript.docx --json manuscript.preflight.json
```

Never overwrite the only copy of a manuscript. Automated structural verification
must still be followed by a Microsoft Word and EndNote refresh check.

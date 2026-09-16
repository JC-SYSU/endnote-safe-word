#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DATA_HOME="${ENDNOTE_SAFE_WORD_DATA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/endnote-safe-word}"
BIN_HOME="${ENDNOTE_SAFE_WORD_BIN_HOME:-$HOME/.local/bin}"
SKILL_HOME="${CODEX_SKILLS_HOME:-${CODEX_HOME:-$HOME/.codex}/skills}"
PYTHON="${PYTHON:-python3}"
VERSION_DIR="$DATA_HOME/versions/$VERSION"
CURRENT="$DATA_HOME/current"
WHEEL="$(find "$ROOT/wheels" -maxdepth 1 -type f -name 'endnote_safe_word-*.whl' -print)"

fail() {
  echo "Installation refused: $*" >&2
  exit 2
}

[[ -n "$VERSION" ]] || fail "VERSION is empty"
[[ -f "$WHEEL" ]] || fail "expected exactly one project wheel"

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$ROOT" && sha256sum --check SHA256SUMS)
elif command -v shasum >/dev/null 2>&1; then
  (cd "$ROOT" && shasum -a 256 --check SHA256SUMS)
else
  fail "sha256sum or shasum is required"
fi

for command_name in endnote-safe-word word-document-safe-editing-mcp; do
  link="$BIN_HOME/$command_name"
  expected="$CURRENT/.venv/bin/$command_name"
  if [[ -e "$link" || -L "$link" ]]; then
    [[ -L "$link" ]] || fail "$link exists and is not a managed symlink"
    [[ "$(readlink "$link")" == "$expected" ]] || \
      fail "$link points outside this installation"
  fi
done
legacy_mcp_link="$BIN_HOME/endnote-safe-word-mcp"
if [[ -e "$legacy_mcp_link" || -L "$legacy_mcp_link" ]]; then
  [[ -L "$legacy_mcp_link" ]] || fail "$legacy_mcp_link exists and is not a managed symlink"
  [[ "$(readlink "$legacy_mcp_link")" == "$CURRENT/.venv/bin/endnote-safe-word-mcp" ]] || \
    fail "$legacy_mcp_link points outside this installation"
fi
if [[ -e "$CURRENT" || -L "$CURRENT" ]]; then
  [[ -L "$CURRENT" ]] || fail "$CURRENT exists and is not a managed symlink"
  [[ "$(readlink "$CURRENT")" == versions/* ]] || \
    fail "$CURRENT points outside this installation"
fi

installing_version=""
skill_tmp=""
trap '[[ -z "${installing_version:-}" ]] || rm -rf "$installing_version"; rm -rf "${skill_tmp:-}"' EXIT
mkdir -p "$DATA_HOME/versions" "$BIN_HOME" "$SKILL_HOME"
if [[ -d "$VERSION_DIR" ]]; then
  [[ "$(cat "$VERSION_DIR/.endnote-safe-word-version" 2>/dev/null || true)" == "$VERSION" ]] || \
    fail "$VERSION_DIR exists but is not a completed managed installation"
else
  mkdir "$VERSION_DIR"
  installing_version="$VERSION_DIR"
  "$PYTHON" -m venv "$VERSION_DIR/.venv"
  "$VERSION_DIR/.venv/bin/python" -m pip install "${WHEEL}[mcp]"
  printf '%s\n' "$VERSION" > "$VERSION_DIR/.endnote-safe-word-version"
  installing_version=""
fi

skill_tmp="$(mktemp -d "$SKILL_HOME/.word-document-safe-editing.XXXXXX")"
cp -R "$ROOT/skill/word-document-safe-editing/." "$skill_tmp/"
skill_target="$SKILL_HOME/word-document-safe-editing"
legacy_skill_target="$SKILL_HOME/endnote-safe-word"
if [[ -e "$legacy_skill_target" ]]; then
  backup_root="$SKILL_HOME/.backups"
  mkdir -p "$backup_root"
  legacy_backup="$backup_root/endnote-safe-word-$(date +%Y%m%d%H%M%S)-$$"
  mv "$legacy_skill_target" "$legacy_backup"
  echo "Previous legacy Skill backed up to: $legacy_backup"
fi
if [[ -e "$skill_target" ]]; then
  if diff -qr "$skill_target" "$skill_tmp" >/dev/null; then
    rm -rf "$skill_tmp"
    skill_tmp=""
  else
    backup_root="$SKILL_HOME/.backups"
    mkdir -p "$backup_root"
    backup="$backup_root/word-document-safe-editing-$(date +%Y%m%d%H%M%S)-$$"
    mv "$skill_target" "$backup"
    if ! mv "$skill_tmp" "$skill_target"; then
      mv "$backup" "$skill_target"
      fail "could not install Skill; restored $skill_target"
    fi
    skill_tmp=""
    echo "Previous Skill backed up to: $backup"
  fi
else
  mv "$skill_tmp" "$skill_target"
  skill_tmp=""
fi

ln -sfn "versions/$VERSION" "$CURRENT"
ln -sfn "$CURRENT/.venv/bin/endnote-safe-word" "$BIN_HOME/endnote-safe-word"
ln -sfn "$CURRENT/.venv/bin/word-document-safe-editing-mcp" "$BIN_HOME/word-document-safe-editing-mcp"
if [[ -L "$legacy_mcp_link" ]]; then
  unlink "$legacy_mcp_link"
fi

echo "Installed EndNote Safe Word $VERSION"
echo "CLI: $BIN_HOME/endnote-safe-word"
echo "MCP server: $BIN_HOME/word-document-safe-editing-mcp"
echo "Codex Skill: $skill_target"
echo
echo "Ensure $BIN_HOME is on PATH."
echo "Register the stable MCP command with Codex (not executed automatically):"
echo "  codex mcp add word-document-safe-editing -- '$CURRENT/.venv/bin/word-document-safe-editing-mcp'"
echo "Use the same stable MCP executable for Claude Code and OpenCode."

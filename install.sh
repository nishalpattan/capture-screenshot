#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/nishalpattan/capture-screenshot"
SKILL_NAME="capture-screenshot"
DETECTED=()   # agents whose skills directory exists
INSTALLED=()  # agents newly cloned this run

clone_if_missing() {
  local dest="$1"
  local label="$2"
  DETECTED+=("$label")
  if [ -d "$dest" ]; then
    echo "  already installed at $dest — skipping"
    return
  fi
  if [ -e "$dest" ]; then
    echo "  warning: $dest exists but is not a directory — skipping $label"
    return
  fi
  git clone --quiet "$REPO" "$dest"
  echo "  installed at $dest"
  INSTALLED+=("$label")
}

echo "capture-screenshot installer"
echo "============================"

# Claude Code
if [ -d "$HOME/.claude/skills" ]; then
  echo "Claude Code detected:"
  clone_if_missing "$HOME/.claude/skills/$SKILL_NAME" "Claude Code"
fi

# OpenAI Codex
if [ -d "$HOME/.codex/skills" ]; then
  echo "OpenAI Codex detected:"
  clone_if_missing "$HOME/.codex/skills/$SKILL_NAME" "OpenAI Codex"
fi

# OpenCode
if [ -d "$HOME/.config/opencode/skills" ]; then
  echo "OpenCode detected:"
  clone_if_missing "$HOME/.config/opencode/skills/$SKILL_NAME" "OpenCode"
fi

echo ""

if [ ${#DETECTED[@]} -eq 0 ]; then
  echo "No agent skills directories found. Install manually:"
  echo ""
  echo "  Claude Code:    git clone $REPO ~/.claude/skills/$SKILL_NAME"
  echo "  OpenAI Codex:   git clone $REPO ~/.codex/skills/$SKILL_NAME"
  echo "  OpenCode:       git clone $REPO ~/.config/opencode/skills/$SKILL_NAME"
elif [ ${#INSTALLED[@]} -gt 0 ]; then
  echo "Done. Installed for: ${INSTALLED[*]}"
  echo "Restart your agent and ask it to take a screenshot to verify."
else
  echo "Already installed for: ${DETECTED[*]} — nothing to do."
fi

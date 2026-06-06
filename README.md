# Capture Screenshot

A cross-platform screenshot skill for AI coding agents. Captures the current screen or named windows using native OS tools with explicit user consent and a privacy-first design.

Works with **Claude Code**, **OpenAI Codex**, and **OpenCode**.

---

## Privacy

Screenshots are saved locally to `~/Desktop/screenshots/` or the system clipboard. The skill never sends images to an external service, never stores data outside your machine, and uses owner-only file permissions (700/600). See [Privacy, Consent, and Intended Use](SKILL.md) in `SKILL.md` for the full policy.

---

## Requirements

| OS | Required |
|----|----------|
| macOS | Built-in `screencapture`; `clang` (Command Line Tools) for named/active-window capture |
| Linux | One of: `gnome-screenshot`, `grim`+`wl-copy`, `spectacle`, `scrot`, or ImageMagick `import` |
| Windows | Python 3 + PowerShell (built-in on Windows 11+) |

See [`references/dependencies.md`](references/dependencies.md) for per-distro package names and macOS permission routing.

---

## Installation

The skill can be installed **globally** (available in all projects) or **per-project** (committed to a single repo).

### Claude Code

**Global** — available in every project:
```bash
git clone https://github.com/nishalpattan/capture-screenshot \
  ~/.claude/skills/capture-screenshot
```

**Per-project** — commit alongside your code:
```bash
git clone https://github.com/nishalpattan/capture-screenshot \
  .claude/skills/capture-screenshot
```

Invoke explicitly: `/capture-screenshot`
Or let Claude activate it automatically when you ask for a screenshot.

---

### OpenAI Codex

**Global:**
```bash
git clone https://github.com/nishalpattan/capture-screenshot \
  ~/.codex/skills/capture-screenshot
```

**Per-project:**
```bash
git clone https://github.com/nishalpattan/capture-screenshot \
  .codex/skills/capture-screenshot
```

Invoke explicitly: `$capture-screenshot`
Or Codex picks it up automatically when the task matches.

---

### OpenCode

**Global:**
```bash
git clone https://github.com/nishalpattan/capture-screenshot \
  ~/.config/opencode/skills/capture-screenshot
```

**Per-project:**
```bash
git clone https://github.com/nishalpattan/capture-screenshot \
  .opencode/skills/capture-screenshot
```

OpenCode also searches `.claude/skills/` and `.agents/skills/`, so an existing Claude Code installation is automatically recognised.

---

## Usage

After installing, prompt your agent naturally — the skill activates automatically when the task matches screenshot capture. Alternatively use the platform's explicit invocation command above.

All captures require you to confirm the scope and destination before anything is written.

---

### Full-screen capture — save to Desktop

```
Capture a screenshot of my current screen.
```

Saves to `~/Desktop/screenshots/MM_DD_YYYY_HH_MM_SS/screen.png`.

---

### Full-screen capture — copy to clipboard

```
Take a screenshot and copy it to my clipboard.
```

The agent will warn that clipboard images may be read or retained by other apps and recommend the Desktop destination for later inspection.

---

### Active window

```
Screenshot the active window.
```

Captures whichever window is currently focused without prompting for a name.

---

### Named window

```
Take a screenshot of the Terminal window.
```

Matches any visible window whose title or process name contains "Terminal". If multiple windows match, the skill refuses and asks for a more specific query rather than guessing.

---

### Multiple named windows in one request

```
Screenshot both my Terminal and VS Code windows.
```

Runs one capture per query and saves separate files: `terminal.png`, `visual-studio-code.png`, etc. Each is written atomically with owner-only permissions.

---

### All windows matching a name

```
Capture every Chrome window.
```

The agent passes `--allow-multiple-matches` for you, saving `chrome.png`, `chrome-001.png`, and so on.

---

### Named window to clipboard

```
Screenshot the Figma window and copy it to my clipboard.
```

Captures the Figma window and places the image on the clipboard.

---

### Dry run — preview output paths without capturing

```
Show me where the screenshot would be saved, but don't actually capture anything.
```

Prints the resolved output path(s) and exits without invoking any screenshot tool or creating any file.

---

## Output

- **Desktop:** `~/Desktop/screenshots/MM_DD_YYYY_HH_MM_SS/<label>.png`
  - Folder permissions: `700` (owner-only)
  - File permissions: `600` (owner-only)
  - Written via a private temp path and atomically renamed — no partial files
- **Clipboard:** Writes the image directly to the system clipboard and prints `clipboard`
- Duplicate labels within the same request get numeric suffixes automatically: `terminal.png` → `terminal-001.png`
- File names use the sanitized app/query label only — window titles are never included

---

## Safety guarantees

- Never falls back from a narrow target (`window`, `active`) to a broader one (`fullscreen`) without a separate explicit approval
- Refuses to write into symlinked folders or paths outside your home directory
- Multiple window matches for a query cause the skill to ask for clarification rather than capturing all
- macOS Screen Recording permission must be granted to the host app (Codex, Claude, your terminal). See [`references/dependencies.md`](references/dependencies.md) for details

---

## Running the tests

```bash
python3 -m unittest discover -s tests
```

All tests run without capturing the screen. A dry-run check:

```bash
python3 scripts/capture_screenshot.py --help
```

# Capture Screenshot

> Give your AI agent eyes. One command, three platforms.

A privacy-first screenshot skill for AI coding agents. Works with **Claude Code**, **OpenAI Codex**, and **OpenCode** on macOS, Linux, and Windows — no external services, no data leaves your machine.

<!-- replace the line below with: ![Demo](demo.gif) once recorded -->
> **Demo GIF coming soon** — record with QuickTime (macOS) or [asciinema](https://asciinema.org) and drop the file here as `demo.gif`.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/nishalpattan/capture-screenshot/main/install.sh | bash
```

The script detects which agents are installed and places the skill in the right directory automatically. Restart your agent and ask it to take a screenshot.

<details>
<summary>Manual install (per platform)</summary>

**Claude Code**
```bash
# Global (all projects)
git clone https://github.com/nishalpattan/capture-screenshot ~/.claude/skills/capture-screenshot

# Per-project
git clone https://github.com/nishalpattan/capture-screenshot .claude/skills/capture-screenshot
```
Invoke: `/capture-screenshot`

**OpenAI Codex**
```bash
git clone https://github.com/nishalpattan/capture-screenshot ~/.codex/skills/capture-screenshot
```
Invoke: `$capture-screenshot`

**OpenCode**
```bash
git clone https://github.com/nishalpattan/capture-screenshot ~/.config/opencode/skills/capture-screenshot
```
OpenCode also recognises `.claude/skills/` and `.agents/skills/` automatically.

</details>

---

## What it does

- **Captures the screen or any named window** using native OS tools — no third-party dependencies on macOS or Windows
- **Grabs background & occluded windows by name** — even when they're behind other windows — without raising them or stealing focus. Minimized windows can't be captured by any OS, so the skill says so clearly and asks you to restore them rather than capturing the wrong thing
- **Saves locally with owner-only permissions** (`~/Desktop/screenshots/MM_DD_YYYY_HH_MM_SS/<label>.png`) or copies to the clipboard
- **Requires explicit user approval** before every capture — never runs silently or falls back to a wider capture scope

---

## Platform requirements

| OS | Required tools |
|----|----------------|
| macOS | Built-in `screencapture`; `clang` (Command Line Tools) for named/active-window capture |
| Linux | One of: `gnome-screenshot`, `grim`+`wl-copy`, `spectacle`, `scrot`, or ImageMagick `import`. Named-window capture also uses `xdotool`; `xprop`/`xwininfo` are optional, for minimized-window detection |
| Windows | Python 3 + PowerShell (built-in on Windows 11+) |

See [`references/dependencies.md`](references/dependencies.md) for per-distro package names and macOS permission routing.

---

## Usage

After installing, prompt your agent naturally — the skill activates automatically when the task matches, or invoke it explicitly with the platform command above. Every capture asks you to confirm scope and destination first.

### Full-screen → Desktop

```
Capture a screenshot of my current screen.
```
Saves to `~/Desktop/screenshots/MM_DD_YYYY_HH_MM_SS/screen.png`.

### Full-screen → clipboard

```
Take a screenshot and copy it to my clipboard.
```

### Active window

```
Screenshot the active window.
```

### Named window

```
Take a screenshot of the Terminal window.
```
Works even if the window is in the background or covered by other windows — it captures the window's own content without raising it or stealing focus. Fails safely if multiple windows match — asks for a more specific name rather than guessing.

### Minimized window

```
Screenshot the Slack window.   # …while Slack is minimized
```
No OS can capture a minimized window (it has no live image). Instead of guessing, the skill reports it clearly — *"'Slack' is minimized — restore it and retry."* — and exits without capturing. Restore the window and ask again.

### Multiple named windows

```
Screenshot both my Terminal and VS Code windows.
```
Saves `terminal.png` and `visual-studio-code.png` in the same timestamped folder.

### All windows matching a name

```
Capture every Chrome window.
```
Saves `chrome.png`, `chrome-001.png`, etc.

### Named window → clipboard

```
Screenshot the Figma window and copy it to my clipboard.
```

### Dry run — preview paths without capturing

```
Show me where the screenshot would be saved, but don't actually capture anything.
```

---

## Output

- **Desktop:** `~/Desktop/screenshots/MM_DD_YYYY_HH_MM_SS/<label>.png`
  - Folder and file permissions: owner-only (`700` / `600`)
  - Written via a private temp path and atomically renamed
- **Clipboard:** image copied to the system clipboard; prints `clipboard`
- Duplicate labels get numeric suffixes: `terminal.png` → `terminal-001.png`
- Window titles are never used in file names — only the sanitized app/query label

---

## Safety guarantees

- Never falls back from `window` or `active` to `fullscreen` without separate approval
- Captures occluded/background windows without raising them or stealing focus; never auto-restores, raises, or minimizes a window
- Minimized windows are reported, not captured — and never silently swapped for the wrong window or the whole screen
- Refuses to write into symlinked folders or paths outside your home directory
- Multiple window matches cause the skill to ask for clarification, not capture all
- Warns you before any step that can trigger an OS permission prompt (macOS Screen Recording); the skill never uses Automation/app-control to capture

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) to add support for new display servers, window backends, or platforms.

---

## Running the tests

```bash
python3 -m unittest discover -s tests
```

All tests run without capturing the screen.

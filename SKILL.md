---
name: capture-screenshot
description: Use when the user asks to capture a screenshot, screen grab, full-screen image, current visible screen, active/specific app or window screenshot, multiple window screenshots, clipboard screenshot, or Desktop screenshots folder output on macOS, Windows, or Linux.
---

# Capture Screenshot

Capture the current screen or explicitly named windows using the host OS. Treat every screenshot as sensitive local data.

## Privacy, Consent, and Intended Use

This skill is for user-directed educational, debugging, documentation, and accessibility-support workflows.

Do not use it for covert capture, monitoring, surveillance, to bypass OS permissions, or to capture content without appropriate authorization.

This skill does not send screenshots to an external service, does not store data in an external database, and only writes locally to the user-selected destination: clipboard or `~/Desktop/screenshots/...`.

Screenshots may contain sensitive data and remain under the user's local control until deleted. This notice is not legal advice; users are responsible for following applicable laws, policies, and consent requirements.

## Required Workflow

1. Determine the OS before choosing a command: macOS is `Darwin`, Windows is PowerShell/.NET, Linux is `Linux`.
2. Before any capture, ask the user to approve the exact scope and choose `desktop` or `clipboard`. If a step may trigger an OS permission prompt, tell the user it is coming before you run it (see macOS Permissions and Prompts).
3. Warn that clipboard images may be read or retained by other apps. Recommend `desktop` for multiple windows or later inspection.
4. Use the bundled helper with `--consent-confirmed` only after approval. Do not bypass the helper for normal captures.
5. Desktop output goes under `~/Desktop/screenshots/mm_dd_yyyy_hh_mm_ss/` with private folder/file permissions where supported.
6. File names use sanitized app/query labels only, never full window titles. The helper adds numeric suffixes for duplicates.
7. Report only the saved path(s) or `clipboard`. Do not render, open, upload, inspect, summarize, or OCR the image unless the user separately consents.

## macOS Permissions and Prompts

macOS may show a one-time permission prompt the first time a capture runs from a given host app. Always tell the user *before* you run the command that a prompt may appear, so it is expected rather than surprising, and name which permission it is. If a permission was granted, tell the user afterward how to turn it off.

### Screen Recording — always required to capture

This is the only permission a normal capture needs. The capture itself never controls other apps.

- Triggered the first time `screencapture` runs from a host app. Until granted, captures fail or produce black/empty images.
- Granted to the launching app, not this skill folder. Shown as `Screen & System Audio Recording` in `System Settings -> Privacy & Security`.
  - Codex Desktop runs need `Codex` enabled.
  - CLI runs need the launching terminal enabled, such as `Terminal`, `iTerm`, `Ghostty`, or `VS Code`.
  - If both Desktop and CLI are used, both host apps may need permission.
- After changing the toggle, macOS may require quitting and reopening the host app.
- To turn it off: `System Settings -> Privacy & Security -> Screen & System Audio Recording`, then toggle the host app off.
- Do not instruct users to enable unrelated entries unless that app is actually launching the capture command.

### Automation ("control of <App>") — not used by this skill

The bundled helper never sends Apple Events, so a normal capture never triggers an "allow control of <App>" prompt. Only use app-control commands (for example AppleScript / `osascript` to raise, restore, or minimize a window) if the user explicitly asks for it. When you do:

- Tell the user first that macOS will show a one-time `"<host app> wants to control <App>"` Automation prompt and that they must click Allow.
- Afterward, tell them it can be revoked at `System Settings -> Privacy & Security -> Automation` by toggling the controlled app off under the host app.
- Never auto-restore, raise, or minimize a window to work around `window_not_capturable`; report it and let the user restore it.

## Commands

macOS and Linux:

```bash
python3 "$SKILL_DIR/scripts/capture_screenshot.py" \
  --consent-confirmed \
  --destination desktop \
  --target fullscreen
```

Named or multiple windows:

```bash
python3 "$SKILL_DIR/scripts/capture_screenshot.py" \
  --consent-confirmed \
  --destination desktop \
  --target window \
  --query "Terminal" \
  --query "ChatGPT"
```

Windows 11+:

```powershell
python "$env:SKILL_DIR\scripts\capture_screenshot.py" `
  --consent-confirmed `
  --destination desktop `
  --target fullscreen
```

The Python script detects Windows and delegates to the bundled PowerShell script automatically. As a fallback when Python is unavailable, invoke the PowerShell script directly:

```powershell
powershell.exe -NoProfile -Sta -File "$env:SKILL_DIR\scripts\capture_screenshot.ps1" `
  -ConsentConfirmed `
  -Destination desktop `
  -Target fullscreen
```

## Safety Rules

- Never fall back from `window` or `active` to `fullscreen` without separate approval.
- If a named-window query has multiple matches, ask for a more specific target. Do not print private titles unless the user explicitly approves a sensitive listing flow.
- Background/occluded windows capture their own content without being raised; the skill does not steal focus. Minimized windows have no drawable contents — the helper reports them as `window_not_capturable` (exit 75). Ask the user to restore the window; never auto-restore, raise, or fall back to a wider scope.
- If screen capture is blocked, explain the needed host-app OS permission; do not retry with broader scope.
- Warn the user before any step that can trigger an OS permission prompt, and name the permission. The capture needs only Screen Recording; never run app-control/automation commands as part of a capture. If the user explicitly asks for one, warn about the Automation prompt first and explain how to revoke it afterward (see macOS Permissions and Prompts).
- Do not auto-install dependencies. See `references/dependencies.md` when an OS tool is missing.

## Verification

For dry-run checks that do not capture the screen:

```bash
python3 -m unittest discover -s "$SKILL_DIR/tests"
python3 "$SKILL_DIR/scripts/capture_screenshot.py" --help
```

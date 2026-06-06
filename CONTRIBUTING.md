# Contributing

Contributions are welcome — especially new platform support, additional Linux display-server backends, and improved error messages. This document explains where to make changes and how to test them without triggering a real screen capture.

---

## Architecture

```
SKILL.md                         ← agent entry point (read by Claude Code / Codex / OpenCode)
scripts/
  capture_screenshot.py          ← main logic: macOS + Linux (delegates to .ps1 on Windows)
  capture_screenshot.ps1         ← Windows capture via .NET / Win32
  find_macos_window_id.m         ← Objective-C helper compiled at runtime for macOS window lookup
references/
  dependencies.md                ← per-OS tool requirements
tests/
  test_capture_screenshot.py     ← headless unit + integration tests
```

The Python script is the single entry point on all platforms. On Windows it detects `platform.system() == "Windows"` in `main()` and delegates immediately to the PowerShell script via `subprocess`.

---

## Adding a new Linux display server

The relevant function is `plan_capture()` in `scripts/capture_screenshot.py` (lines 160–258). It branches on `session_type` (the value of `$XDG_SESSION_TYPE`).

1. **Detect your tool** — add the tool name to the `detect_tools(...)` call in `main()` (around line 491).

2. **Add a branch inside `plan_capture()`** — follow the existing pattern:

```python
if platform_name == "Linux":
    session = (session_type or "").lower()

    if target == "fullscreen":
        # existing branches …

        if your_tool:                          # add here
            return CapturePlan(True, "ok", "ok", ((your_tool, "{output}"),))
```

   Each `CapturePlan` carries a tuple of command tuples. Use `"{output}"` as a placeholder for the output path and `"{temp-output}"` when the tool writes to stdout and you need a temp file.

3. **Write a test** — add a case to `CaptureScreenshotUnitTests` in `tests/test_capture_screenshot.py`. Pass `tools={"your_tool": "/usr/bin/your_tool"}` and `session_type="your_session"` to `plan_capture()` and assert `plan.ok is True` and `plan.commands` contains the expected command tuple.

---

## Adding a new window-targeting backend (Linux)

The function `resolve_linux_named_window()` (around line 317) uses `xdotool` to enumerate window IDs by name. To add an alternative:

1. Add a fallback `elif` after the `xdotool` block — detect your tool with `shutil.which`.
2. Parse its output into a tuple of string window IDs and return a `ResolutionResult`.
3. Add a test that mocks the subprocess call or uses `CAPTURE_SCREENSHOT_TEST_WINDOWS` (see Testing below).

---

## Adding macOS capture tool support

`resolve_macos_with_helper()` (around line 281) compiles `find_macos_window_id.m` with `clang` at runtime. If you want to add a fallback for machines without `clang`:

- Add a branch before the `clang` check that tries an alternative tool.
- Return a `ResolutionResult` with the found window IDs as strings.
- The IDs are passed to `plan_capture()` → `screencapture -l <id>`.

---

## Testing without a display

Two environment variables let you test platform-specific paths on any machine:

| Variable | Purpose |
|----------|---------|
| `CAPTURE_SCREENSHOT_TEST_PLATFORM` | Override `platform.system()` (e.g. `"Darwin"`, `"Linux"`, `"Windows"`) |
| `CAPTURE_SCREENSHOT_TEST_WINDOWS` | JSON list of `{"id": int, "owner": str, "title": str}` objects injected as the macOS window list |

Example — test a macOS named-window dry run on Linux:

```bash
CAPTURE_SCREENSHOT_TEST_PLATFORM=Darwin \
CAPTURE_SCREENSHOT_TEST_WINDOWS='[{"id": 42, "owner": "Terminal", "title": "bash"}]' \
python3 scripts/capture_screenshot.py \
  --consent-confirmed --destination desktop --target window --query Terminal --dry-run
```

Run the full suite:

```bash
python3 -m unittest discover -s tests -v
```

---

## Good first issues

Not sure where to start? These are well-scoped, self-contained tasks:

| # | Title | Skills needed |
|---|-------|---------------|
| 1 | Add Wayland multi-monitor capture via `wlr-randr` | Python, Wayland basics |
| 2 | Add KDE Plasma `spectacle` active-window support | Python, Linux desktop |
| 3 | Add `--timeout` flag for slow window rendering | Python |
| 4 | Improve macOS Screen Recording permission error messages | Python, macOS |
| 5 | Detect WSL and return a clear unsupported-platform error | Python, Windows/Linux |

Open an issue describing your approach before starting a large change — helps avoid duplicated work.

---

## Pull request checklist

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] New behaviour is covered by at least one test
- [ ] `bash -n install.sh` passes (if `install.sh` was modified)
- [ ] No new third-party dependencies introduced

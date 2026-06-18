# Capture-Screenshot — Daily Review Log

This file is append-only. Each entry is headed `## YYYY-MM-DD` (UTC) and groups
findings under Security / Bugs & regressions / Data leaks / UX.

Severity levels: **critical** / **high** / **medium** / **low** / **info**

---

## 2026-06-11

### Security

**[low] `find_macos_window_id.m` stores `CGWindowID` in a signed `int`, not `uint32_t`**
`scripts/find_macos_window_id.m:33,97`
`CGWindowID` is a `uint32_t`. The `window_number` helper casts it into an `int *` and
calls `CFNumberGetValue(..., kCFNumberIntType, number)`, which uses a 32-bit signed
read. If a window number exceeds INT_MAX (~2.1 billion), the stored value is negative.
`printf("%d\n", number)` then prints a negative decimal string, which `_validate_integer_ids`
rejects via `.isdigit()` (leading `-` fails the test), causing an `EXIT_USAGE` error
instead of returning the window ID. In practice macOS assigns sequential IDs that rarely
approach INT_MAX in normal use, making this theoretical, but the mismatch between
`CGWindowID` (unsigned) and `int` (signed) is a latent correctness defect.
_Suggested fix:_ Declare `number` and `first_capturable` as `unsigned int`, change the
`CFNumberGetValue` call to `kCFNumberSInt32Type` (or use `kCGWindowNumber` directly
with `CGWindowID`), and print with `"%u\n"`.

---

### Bugs & regressions

**[medium] `run_command(check=True)` propagates `CalledProcessError` as an unhandled traceback on any tool failure**
`capture_screenshot.py:465` and callers in `execute_plan`
All screenshot-tool invocations inside `execute_plan` call `run_command` which ends
with `subprocess.run(args, check=True)`. If a tool exits non-zero — e.g., `screencapture`
fails because Screen Recording permission was revoked mid-session, `gnome-screenshot`
returns an error, or `grim` cannot connect to the Wayland compositor — Python raises
`subprocess.CalledProcessError`. This exception is not caught anywhere in `run_command`,
`execute_plan`, or `main`, so the process exits with a raw Python traceback and an
implicit exit code of 1 rather than a structured `die()` message and a documented exit
code. The 2026-06-09 entry noted this specifically for the `clang` compile step; the
same gap applies to every screenshot tool call at runtime.
_Suggested fix:_ Catch `subprocess.CalledProcessError` in `run_command` (or in
`execute_plan` around the `run_command` call) and call
`die(f"capture tool failed (exit {e.returncode}): {e.cmd[0]}", EXIT_UNAVAILABLE)`,
preserving the structured error-message and exit-code contract for all tool failures.

**[medium] macOS `--allow-multiple-matches` + `--destination clipboard` silently discards all captures except the last**
`capture_screenshot.py:225–231` and `execute_plan:499–502`
When multiple window IDs match a query on macOS with `--allow-multiple-matches
--destination clipboard`, `plan_capture` builds one `screencapture -x -l <id> -c`
command per window. In `execute_plan`, each command is executed in order; each
successive `screencapture -c` overwrites the clipboard. Only the last-matched window's
image survives. The function prints a single `"clipboard"` regardless of how many
windows were captured, giving no indication which window is on the clipboard or that
earlier captures were silently discarded.
_Suggested fix:_ Either (a) return `CapturePlan(False, "clipboard_allows_one",
"clipboard destination supports only one window at a time; use desktop for multiple
captures")` when `destination == "clipboard"` and `len(window_ids) > 1`, or (b)
document the last-wins behavior and print a warning to stderr listing how many
captures were requested vs. written to the clipboard.

**[medium] Linux fullscreen + Wayland + clipboard falls through to `import -window root` which fails at runtime on pure Wayland**
`capture_screenshot.py:258–260`
When `--target fullscreen --destination clipboard` is requested on a Wayland session
and neither `gnome-screenshot` nor `grim` is installed, but ImageMagick `import` and
`xclip`/`xsel` are present, `plan_capture` returns a plan with
`(import_cmd, "-window", "root", "{temp-output}")`. At runtime, `import -window root`
connects to the X11 DISPLAY. On a pure Wayland system with no XWayland active, this
call fails with a non-zero exit status, which (see finding above) surfaces as an
unhandled `CalledProcessError` traceback rather than a clean unsupported message. The
session-type guard for Wayland only blocks the `window` target, not the `fullscreen`
clipboard path.
_Suggested fix:_ When `session == "wayland"`, skip the `import`+xclip/xsel branch for
fullscreen clipboard (since `import` is inherently X11). Return
`CapturePlan(False, "missing_dependency_fullscreen", …)` without the `import` option,
matching the Wayland-aware behaviour of the `window` target.

**[low] Windows `Find-WindowHandles` does not guard against zero-size visible windows**
`capture_screenshot.ps1:181–219` (`Find-WindowHandles`), `Get-WindowBounds:222–234`
`Find-WindowHandles` retains any handle that passes `IsWindowVisible` and matches the
query by title or process name. Windows that are visible but have zero or negative
dimensions (e.g., certain shell-notification or hidden-tray windows) pass this filter.
When `Get-WindowBounds` is later called for such a handle it throws "window has no
drawable bounds", which exits with code 1 via `throw` rather than a structured error
code, and blocks any remaining handles from being processed in the loop.
_Suggested fix:_ In `Get-WindowBounds` or at the call site in the `foreach` loop,
catch the zero-bounds case and either skip the handle with a stderr warning or return
a `CapturePlan`-equivalent error with exit 74 (unavailable).

---

### Data leaks

No new findings. The CalledProcessError tracebacks discussed above include only the
tool path and exit code in the `CalledProcessError` message; command arguments contain
temp-file paths and integer window IDs but no window titles. The multi-clipboard
overwrite bug involves only captured pixel data, not metadata from window titles.

---

### UX

**[low] No timeout on screenshot-tool subprocess calls; a hung tool blocks indefinitely**
`capture_screenshot.py:465` (`subprocess.run(args, check=True)`)
`run_command` and the `clang` compile step in `resolve_macos_with_helper` use
`subprocess.run` without a `timeout` parameter. A tool that hangs — e.g.,
`gnome-screenshot` waiting on a D-Bus response, `screencapture` blocked by a macOS
permission dialog, or `clang` hitting a system resource limit — will block the Python
process indefinitely. In agent integrations this freezes the calling agent with no
feedback or timeout signal.
_Suggested fix:_ Pass a reasonable `timeout` (e.g., 30 s for screenshot tools, 60 s
for the clang compile) to each `subprocess.run` call, catching `subprocess.TimeoutExpired`
and calling `die("capture timed out — tool did not complete in time", EXIT_UNAVAILABLE)`.

**[info] CapturePlan clipboard commands include dead arguments beyond index [0]**
`capture_screenshot.py:256,258–260`
The `CapturePlan` commands for the two-step clipboard paths include trailing arguments
on the second command (e.g., `(wl_copy, "--type", "image/png")` and `(clip,)`). In
`execute_plan`, only `plan.commands[1][0]` is used (the tool path); the remaining
elements are silently discarded and the correct arguments are re-applied inside
`copy_file_to_clipboard`. A reader of `plan_capture` may incorrectly believe these
arguments are passed to the clipboard tool by the general `run_command` path.
_Suggested fix:_ Normalise the second command to just `(wl_copy,)` and `(clip,)`,
matching what `execute_plan` actually consumes; or add a comment explaining that args
beyond `[0]` are intentionally unused and the tool logic lives in
`copy_file_to_clipboard`.

---

## 2026-06-08

### Security

**[medium] `execute_plan` clipboard temp file lands in world-traversable `/tmp`**
`capture_screenshot.py:492–496`
When destination is `clipboard` and the plan uses `{temp-output}` (grim/wl-copy,
import/xclip), `tempfile.NamedTemporaryFile` places the PNG in the system temp
directory, which is world-traversable (mode 0o1777 on Linux). The file is created
with 0o600, so content is protected, but an unprivileged attacker sharing the machine
can observe the file's existence and metadata (filename contains a predictable PID).
By contrast, the desktop path's `private_temp_png()` creates the temp file inside the
already-secured 0o700 request directory, which also hides metadata.
_Suggested fix:_ Create the clipboard temp file inside the same 0o700 request
directory used for desktop captures, or in a fresh `tempfile.mkdtemp(mode=0o700)`,
and `secure_file()` it explicitly before writing.

**[medium] TOCTOU race in `ensure_private_directory` between symlink check and `mkdir`**
`capture_screenshot.py:103–114`
The symlink check (`path.is_symlink()`) and the subsequent `path.mkdir()` are not
atomic. A local attacker with write access to the parent directory could replace the
target with a symlink between these two calls. `path.mkdir(exist_ok=True)` follows
symlinks (it succeeds if the symlink target is an existing directory), so the
subsequent `path.chmod(0o700)` would then chmod the symlink's target rather than a
new directory under the user's control. On Linux, `mkdir(2)` itself is not O_NOFOLLOW;
there is no POSIX-portable way to create a directory without following a symlink.
_Suggested fix:_ On Linux/macOS, open the parent directory with O_DIRECTORY and use
`os.mkdir` relative to that fd (via `os.open` + `os.mkdir` at the fd level), or add a
post-creation re-check that the path is still not a symlink after `mkdir`. Document the
residual race for shared-machine deployments.

**[medium] PowerShell script accepts arbitrary `OutputRoot` without home-containment check**
`capture_screenshot.ps1:8,131–135`
The Python orchestrator validates that `output_root` is within the user's home
directory (`_validate_output_root`), then passes it to the PowerShell script. However,
the PS script itself applies no equivalent check. If a user (or another process) invokes
`capture_screenshot.ps1` directly, they can pass any filesystem path as `-OutputRoot`
and the script will happily create/populate it, potentially writing screenshots to
arbitrary locations.
_Suggested fix:_ Add a home-containment guard at the top of the PS script analogous to
`_validate_output_root`, comparing `$OutputRoot` resolved path against
`[Environment]::GetFolderPath('UserProfile')`.

**[low] `_escape_ere` does not escape the `-` character**
`capture_screenshot.py:77–79`
The regex character class `[][\\.*+?{}()|^$]` escapes common ERE metacharacters but
omits `-`. While `-` is only special inside bracket expressions in ERE (not outside
them), a user query that itself contains a bracket expression like `[a-z]` passed to
xdotool would have the brackets escaped but the inner `-` left unescaped, potentially
producing unintended matches. The practical impact is limited because window names
rarely contain lone bracket expressions.
_Suggested fix:_ Add `\-` to the escaped set, or switch to `re.escape()` then
manually un-escape characters that the ERE engine must see as literal.

**[info] `kCGWindowListOptionAll` loads all window titles into helper process memory**
`scripts/find_macos_window_id.m:68`
`CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)` fetches metadata
(including titles) for every window on the system. Titles are used only for substring
matching and are never printed to stdout/stderr, but they transiently reside in the
helper's address space. On macOS 10.15+ this call requires the Screen Recording
permission, providing OS-level consent enforcement. Already well-handled; noted for
completeness.

**[info] `install.sh` relies solely on HTTPS transport for repository integrity**
`install.sh:4,27`
`git clone --quiet "$REPO" "$dest"` validates integrity only via TLS certificate
verification and Git's SHA-1 object model. There is no signature verification
(e.g., `git verify-commit`) or pinned commit hash. This is standard practice for
public Git repositories and not a significant risk given the HTTPS URL.

---

### Bugs & regressions

**[medium] Linux active-window capture ignores KDE Spectacle and scrot**
`capture_screenshot.py:277–285`
`plan_capture` for `target == "active"` on Linux only succeeds if `gnome-screenshot`
is available. Both `spectacle` and `scrot` support active-window capture
(`spectacle -b -n -a -o <file>` and `scrot -u <file>`) but are not tried as fallbacks.
On KDE or minimal GNOME-free desktops, active-window capture always fails with
`missing_dependency_active_window` even when appropriate tools are present.
_Suggested fix:_ Add `spectacle` (`-b -n -a -o {output}`) and `scrot` (`-u {output}`)
as fallbacks in the `active` branch of `plan_capture`, mirroring the fallback chain
used for fullscreen.

**[low] `plan_capture` accepts a `label` parameter that it never uses**
`capture_screenshot.py:200,205`
The `label` parameter is part of the function's public signature but is never
referenced inside `plan_capture`. The label is consumed by `prepare_output_paths`
instead. This creates a misleading API and a dead parameter.
_Suggested fix:_ Remove `label` from `plan_capture`'s signature, or document that it
is reserved for a future structured-metadata pass-through.

**[low] `_linux_window_is_viewable` xprop parse checks entire stdout for "iconic"**
`capture_screenshot.py:375–378`
```python
return "iconic" not in proc.stdout.lower()
```
The check scans the entire xprop output for the substring `"iconic"` rather than
extracting the specific state token. Although xprop `-id <id> WM_STATE` only outputs
the WM_STATE property (so rogue "iconic" substrings in other properties are not
present), the approach is fragile. If xprop output format varies across versions, a
property value or comment containing "iconic" could produce a false negative.
_Suggested fix:_ Parse the specific state token with a narrower regex, e.g.,
`re.search(r'window state:\s*(\w+)', output, re.I)` and compare the captured group.

**[low] `find_macos_window_id.m` uses last non-flag argument as query; multiple bare args silently drop all but last**
`scripts/find_macos_window_id.m:41–48`
The C helper assigns `query_arg = argv[i]` for every non-flag argument, so if a caller
passes two bare arguments (e.g., shell word-splitting a query that contains spaces),
only the last word is used as the query. The Python caller always passes the full query
as a single list element (no shell involved), so this is harmless in practice, but
direct invocation of the binary is silently wrong.
_Suggested fix:_ Detect more than one non-flag argument and exit with code 64 (usage
error) or concatenate them with a space.

**[info] `secure_file(output)` after `os.replace` is redundant**
`capture_screenshot.py:511,518`
`private_temp_png` creates the temp file with mode 0o600. `secure_file(temp_output)`
is called before rename, so the renamed file at `output` inherits 0o600. The
subsequent `secure_file(output)` re-applies 0o600 unnecessarily. Harmless correctness
belt-and-suspenders; no fix required.

---

### Data leaks

No new findings. The privacy-preserving invariants are well-enforced:
- All error and status messages echo only the user's query, never the real window title
  (verified across macOS resolution, Linux resolution, PS script, and not_capturable_message).
- `sanitize_label` strips URLs and non-alphanumeric characters before embedding any
  label in filesystem paths.
- The dry-run output path test (`test_dry_run_output_has_no_window_title_metadata`)
  confirms no title leakage through dry-run paths.
- `CAPTURE_SCREENSHOT_TEST_WINDOWS` env var carries window titles in test mode only
  and is validated before use.

---

### UX

**[medium] Linux active-window capture silently unavailable on non-GNOME desktops**
`capture_screenshot.py:277–285`
(Same root cause as the bug above.) On KDE Plasma, Sway, or bare X11 environments,
`--target active` always fails with a missing-dependency error even when Spectacle or
scrot are installed. Users on those desktops have no active-window path.
_Suggested fix:_ Same as the bug entry above.

**[low] `request_folder_name` uses local clock, not UTC**
`capture_screenshot.py:66`
Folder names like `06_08_2026_14_30_00` are ambiguous across timezones and will
change unexpectedly when the system clock crosses DST boundaries.
_Suggested fix:_ Use `dt.datetime.utcnow()` (or `dt.datetime.now(dt.timezone.utc)`)
and document the convention. Coordinate this with the PowerShell equivalent
(`Get-Date` in `New-RequestFolder`).

**[low] Test suite has no end-to-end coverage for Linux clipboard paths (grim/wl-copy, import/xclip)**
`tests/test_capture_screenshot.py`
The Linux clipboard plan branch that uses `{temp-output}` (lines 255–259 of
`capture_screenshot.py`) is exercised by `plan_capture` unit tests but not by an
integration test that runs a fake grim/wl-copy toolchain. A regression in
`execute_plan`'s clipboard-with-temp-output branch would not be caught by the current
test suite.
_Suggested fix:_ Add an integration test using fake shell scripts (following the
pattern of `test_windows_delegates_to_powershell` and `_write_fake_tool`) that
exercises the full grim→wl-copy clipboard flow end-to-end.

**[info] `execute_plan` uses `EXIT_USAGE` (64) for an internal invariant error**
`capture_screenshot.py:506`
`die("internal error: command/output mismatch", EXIT_USAGE)` uses the "usage error"
exit code for a condition that is actually a programming error (mismatched lists from
`plan_capture` and `prepare_output_paths`). A caller checking exit codes could
misinterpret this as a user-provided argument problem.
_Suggested fix:_ Define a dedicated `EXIT_INTERNAL = 70` (sysexits.h EX_SOFTWARE)
and use it for internal assertions.

---

## 2026-06-09

### Security

**[low] Windows `EnumWindows`/`GetWindowText` require no OS-level permission gate**
`capture_screenshot.ps1:177–193` (`Find-WindowHandles`)
On Windows, `EnumWindows` + `GetWindowText` enumerate all visible window titles
without any OS consent prompt, special privilege, or permission toggle. This is
distinct from the macOS model (noted as info on 2026-06-08), where
`CGWindowListCopyWindowInfo` requires the Screen Recording permission. On Windows a
shared-machine co-tenant could in principle observe that the skill is running a window
title scan (e.g., via process handle or ETW), and the absence of an OS-level gate
means there is no user-facing notice prior to the enumeration. The titles are never
printed and are used only for query matching, so there is no direct data leak; the
concern is the lack of an equivalent OS-enforced consent step.
_Suggested fix:_ No code change is possible at the application layer (EnumWindows
requires no privilege). Document in SKILL.md that Windows window title enumeration has
no OS gate, so the skill's own consent check (`-ConsentConfirmed`) is the only guard
on Windows.

---

### Bugs & regressions

**[medium] Uncaught `CalledProcessError` if `clang` compilation of the macOS helper fails**
`capture_screenshot.py:resolve_macos_with_helper` (~line 335)
```python
subprocess.run([clang, "-framework", "ApplicationServices", str(helper_source), "-o", str(helper)], check=True)
```
If `clang` is available on `$PATH` (so the `shutil.which` check passes) but compilation
fails — for example because Xcode Command Line Tools are installed but the
ApplicationServices framework header is missing, or because the SDK path is wrong —
`subprocess.run(..., check=True)` raises `subprocess.CalledProcessError`. This
exception is not caught anywhere in `resolve_macos_with_helper` or `main()`, so the
process exits with an unhandled traceback rather than a structured
`ResolutionResult(False, …)` and a clean error message.
_Suggested fix:_ Wrap the clang invocation in a `try/except subprocess.CalledProcessError`
and return `ResolutionResult(False, "helper_compile_failed", "Could not compile macOS
window helper — check that Xcode Command Line Tools are fully installed.")`.

**[low] PS dry-run mode can return duplicate paths when multiple windows share a label**
`capture_screenshot.ps1:104–119` (`New-CapturePath`)
`New-CapturePath` determines uniqueness by checking `Test-Path` on the filesystem.
In `--dry-run --allow-multiple-matches` mode, no files are written to disk, so every
call for the same label returns the same candidate path (e.g., `chrome.png`). If two
windows match the same query, `Capture-ToDestination` prints the same path twice. The
Python version avoids this with an in-memory `reserved` set passed between calls.
_Suggested fix:_ Introduce a script-level `$script:ReservedPaths` hash set (e.g.,
`[System.Collections.Generic.HashSet[string]]::new()`) and consult it in
`New-CapturePath` alongside `Test-Path`, mirroring `unique_capture_path`'s `reserved`
parameter.

**[low] PS `throw` statements exit with code 1 rather than structured exit codes**
`capture_screenshot.ps1` (multiple `throw` sites)
Several error conditions — missing query for window target, no matching window found,
multiple matching windows, and PowerShell internal errors — are raised with `throw`,
which causes the script to exit with code 1. Only the two `exit 75`
(`window_not_capturable`) and `exit 0` paths use structured codes. The Python
orchestrator's callers may check the exit code for routing (e.g., distinguishing
`EXIT_USAGE=64` from `EXIT_UNAVAILABLE=74`); any error that falls through `throw`
returns 1 instead, inconsistent with the documented code table.
_Suggested fix:_ Replace `throw` with `[Console]::Error.WriteLine(…); exit <code>`
for each structured error case, matching the exit codes defined in the Python script
(`EXIT_USAGE=64`, `EXIT_UNAVAILABLE=74`).

---

### Data leaks

No new findings. Window titles continue to be confined to in-process memory on all
platforms. The PS `Test-BitmapAllBlack` warning message includes `$Label` (the
user-supplied query text, not a window title), which is acceptable. The `throw`
messages include the user's query text (e.g., the needle in `Find-WindowHandles`) but
never window titles retrieved via `GetWindowText`.

---

### UX

**[low] macOS helper binary is recompiled with `clang` on every named-window request**
`capture_screenshot.py:resolve_macos_with_helper` (~line 325–342)
`find_macos_window_id` is compiled from source into a fresh `TemporaryDirectory` on
each invocation of `--target window` or `--target active` on macOS. `clang`
compilation adds roughly 0.5–1 s of latency to every such request. The compiled binary
is discarded when the context manager exits and rebuilt the next time.
_Suggested fix:_ Cache the compiled binary alongside the source (e.g., in
`skill_dir/scripts/.cache/find_macos_window_id`) keyed on the source's `mtime` or
hash, and only recompile when the source changes. Fall back to recompile if the cache
is stale or missing.

**[info] `Test-BitmapAllBlack` sparse-grid sampling may miss narrow non-black content**
`capture_screenshot.ps1:Test-BitmapAllBlack` (~line 220–237)
The GPU/Electron black-capture warning samples one pixel every `width/16` columns and
`height/16` rows. On a 1920×1080 window, columns are sampled every 120 pixels, meaning
a 119-pixel-wide stripe of non-black content between two sample columns is invisible to
the check. The warning is advisory-only and does not block the save, so this is
cosmetic; the user sees no warning but still receives the (mostly-black) PNG. No fix
is required, but a note in code comments that the check is a coarse heuristic would
avoid misreading the function as exhaustive.

---

## 2026-06-10

### Security

**[low] `_test_windows()` does not catch `json.JSONDecodeError` on malformed input**
`capture_screenshot.py:308`
```python
parsed = json.loads(raw)
```
If `CAPTURE_SCREENSHOT_TEST_WINDOWS` contains malformed JSON, `json.loads` raises
`json.JSONDecodeError`, which propagates as an unhandled exception with a raw Python
traceback rather than a clean `die()` message. This variable is only active in test/debug
scenarios, so production risk is minimal, but the failure mode is inconsistent with
every other validation path in the module.
_Suggested fix:_ Wrap in `try/except json.JSONDecodeError` and call
`die("CAPTURE_SCREENSHOT_TEST_WINDOWS is not valid JSON: ...", EXIT_USAGE)`.

---

### Bugs & regressions

**[high] Linux X11 named-window clipboard capture always crashes with "internal error: missing output path"**
`capture_screenshot.py:288–296` (`plan_capture`) and `capture_screenshot.py:499–502` (`execute_plan`)
When `--target window --destination clipboard` is used on Linux X11 with `xdotool` and
`import` available, `plan_capture` returns commands containing `"{output}"` placeholders
for every window ID:
```python
commands = tuple((import_cmd, "-window", str(window_id), "{output}") for window_id in window_ids)
```
The `"{output}"` placeholder signals a desktop-bound path. In `execute_plan`, the
clipboard branch at line 499 calls `run_command(command)` without an `output` argument.
`run_command` immediately dies with "internal error: missing output path" (exit 64) when
it encounters `"{output}"` in the command with `output=None`. The Wayland path is
correctly rejected earlier (`unsupported_wayland_window_capture`), but the X11 path is
not guarded. The user receives an opaque internal error rather than a working capture or
a clean "not supported" message.
_Suggested fix:_ Either (a) return a `CapturePlan(False, "unsupported_linux_x11_window_clipboard", …)`
for this combination explicitly in `plan_capture`, or (b) use `"{temp-output}"` and pipe
to `xclip`/`xsel` by adding those to the plan (mirroring the grim+wl-copy path), plus a
`copy_file_to_clipboard` call in `execute_plan`.

**[medium] `xsel` clipboard backend sets no MIME type on clipboard content**
`capture_screenshot.py:475–476`
```python
elif name == "xsel":
    subprocess.run([tool, "--clipboard", "--input"], input=data, check=True)
```
When `xsel` is the clipboard tool (the fallback when `xclip` is absent),
`copy_file_to_clipboard` writes raw PNG bytes to the clipboard without specifying a MIME
type. `xclip` uses `-t image/png` and `wl-copy` uses `--type image/png`; `xsel` has no
equivalent flag. Most graphical applications (browsers, office suites, image editors)
look for a typed `image/png` selection target and will fail to paste or will paste as
raw binary. The capture appears to succeed (exit 0, "clipboard" printed) but the result
is not usable.
_Suggested fix:_ Prefer `xclip` over `xsel` in `plan_capture` (already done:
`clip = xclip or xsel`), and add a warning when `xsel` is selected that paste
compatibility may be limited. Long-term, replace `xsel` in the clipboard path with a
`xclip`-only requirement or with `wl-copy` on Wayland.

**[low] `install.sh` does not guard against an unset or empty `$HOME`**
`install.sh:36,41,47`
All three agent skill paths are constructed as `"$HOME/.claude/skills"`, `"$HOME/.codex/skills"`,
and `"$HOME/.config/opencode/skills"`. If `$HOME` is unset (unusual but possible in
restricted or CI environments), these expand to `"/.claude/skills"`, `"/.codex/skills"`,
and `"/.config/opencode/skills"`. A stray `[ -d "/.claude/skills" ]` that returns
true (e.g., on a container image that pre-populates that path) would cause
`clone_if_missing` to attempt `git clone "$REPO" "/.claude/skills/capture-screenshot"`,
writing into a system-owned directory and likely failing with a permission error or,
worse, succeeding if run as root.
_Suggested fix:_ Add `[ -z "$HOME" ] && { echo "error: \$HOME is not set"; exit 1; }` near
the top of the script, before the first path check.

---

### Data leaks

No new findings. Window title isolation continues to hold across all platforms:
- Linux X11 crash path (above) emits only the static string "internal error: missing
  output path" — no window title is exposed in the error.
- `xsel` clipboard bug writes raw PNG bytes, not metadata derived from window titles.
- All error messages in `find_macos_window_id.m` continue to emit only static strings or
  the `unknown`/`minimized` reason token with no title content.

---

### UX

**[medium] Linux X11 named-window clipboard capture surfaces an opaque internal error**
`capture_screenshot.py:499–502`
(Same root cause as the high-severity bug above.) A user running
`capture_screenshot.py --target window --destination clipboard --query Firefox` on Linux
X11 receives exit code 64 and the message "internal error: missing output path" — which
gives no hint that clipboard capture of named windows is unsupported on this platform.
The macOS and Wayland paths return structured, actionable codes; X11 clipboard/window
should do the same.
_Suggested fix:_ Same as the bug entry above — return a structured `CapturePlan(False, …)`
rather than letting the internal placeholder mismatch surface to the user.

---

## 2026-06-13

### Security

**[medium] Empty `--query ""` matches every visible window on all three platforms**
`capture_screenshot.py:580–594`, `capture_screenshot.ps1:384–401`, `find_macos_window_id.m:107`
`parse_args` and the per-platform window resolution functions accept an empty string as a
valid query value. The `--target window` guard at line 580 only checks `if not args.query`
(list is non-empty); it does not reject elements that are empty strings. On Linux, xdotool
`search --name ""` matches all windows with a non-empty title. On macOS, `CFStringFind`
with an empty-string needle always returns a match (`range.location != kCFNotFound`), so
the C helper classifies every normal-layer window as a hit. On Windows,
`String.IndexOf("", OrdinalIgnoreCase)` returns 0 (≥ 0 = match), causing
`Find-WindowHandles` to collect every visible window. With `--allow-multiple-matches`, all
visible windows are captured, silently breaking the privacy guarantee that the capture scope
is never wider than the user's named target. Without that flag the result is either a
"multiple matches" error (harmless) or, on a single-window desktop, capture of the one
remaining window (not the intended target).
_Suggested fix:_ Add a validation step — in `parse_args` or at the start of the window
resolution functions — that rejects any empty-string query element with `die("--query must
not be empty", EXIT_USAGE)`. Add a corresponding test.

**[medium] `agents/openai.yaml` sets `allow_implicit_invocation: true`**
`agents/openai.yaml:7`
The OpenAI agent YAML policy enables implicit invocation, meaning the capture-screenshot
skill can be selected by the model without an explicit user request. In an agentic pipeline
where the model independently decides to capture a screenshot, the consent gate provided by
`--consent-confirmed` / `-ConsentConfirmed` could be satisfied programmatically without a
visible user approval step. This partially undermines the consent enforcement described in
SKILL.md ("Before any capture, ask the user to approve the exact scope"). The SKILL.md
instructions apply to a human-in-the-loop workflow; `allow_implicit_invocation` relaxes
that assumption.
_Suggested fix:_ Either set `allow_implicit_invocation: false` to require explicit user
invocation, or document in SKILL.md and the YAML file why implicit invocation is safe (e.g.,
if the model is still required to prompt for `--consent-confirmed` before executing the
command).

**[low] TOCTOU between `output.exists()` check and `os.replace()` in `execute_plan`**
`capture_screenshot.py:512–514`
```python
if output.exists() or output.is_symlink():
    die("refusing to overwrite an existing screenshot path", EXIT_PRIVACY)
os.replace(temp_output, output)
```
Between the existence check and the `os.replace` call, a local attacker or concurrent
process could create a symbolic link at `output`. `os.replace()` on Linux atomically
replaces the target of a symlink (i.e., it follows the link and overwrites the pointed-to
file) rather than replacing the symlink itself. This could cause the screenshot to be
written to an attacker-controlled path. Note: this is distinct from the 2026-06-08 finding,
which covers the TOCTOU in `ensure_private_directory` between `is_symlink()` and `mkdir`.
_Suggested fix:_ On Linux, use `os.open` with `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW`
to create the final file atomically without following symlinks, then write temp content
into it; or verify post-replace that `output` is not a symlink.

---

### Bugs & regressions

**[medium] Windows DPI scaling causes incorrect capture bounds for active and named-window targets**
`capture_screenshot.ps1:226–234` (`Get-WindowBounds`), `capture_screenshot.ps1:239–246` (`Copy-Rectangle`)
`GetWindowRect` returns window coordinates in logical (DPI-unscaled) pixels. In .NET's
GDI+ layer, `Graphics.CopyFromScreen` operates in device (physical) pixels as reported by
the DC. On displays with display scaling (e.g., 150% or 200% DPI), logical and physical
coordinate spaces diverge: a window whose logical rect is (0, 0, 960, 540) occupies
(0, 0, 1440, 810) in physical pixels. `Copy-Rectangle` constructs a `Drawing.Bitmap` with
the logical dimensions and blits the physical-pixel region, resulting in a capture that is
undersized (missing the right/bottom portion of the window) or misaligned. `PrintWindow`
(used in `Copy-Window`) is unaffected because it renders into the DC at the window's own
resolution. The issue affects `--target active` and `--target fullscreen` on scaled
displays.
_Suggested fix:_ Retrieve the DPI scale factor (via `Graphics.DpiX / 96.0`) and multiply
the logical rect dimensions before allocating the bitmap and calling `CopyFromScreen`, or
set the PowerShell process to be Per-Monitor DPI aware via a manifest / `SetProcessDpiAwareness`.

**[low] `Copy-Rectangle` fails or produces a wrapped capture when a window has negative screen coordinates**
`capture_screenshot.ps1:239–246` (`Copy-Rectangle`)
On multi-monitor systems where the primary monitor is not the leftmost display, windows
positioned on monitors to the left of the primary have negative `.Left` or `.Top`
coordinates in `GetWindowRect`. `Graphics.CopyFromScreen` with negative source coordinates
is undefined in some .NET implementations and may throw, silently wrap the coordinates to
zero, or produce an incorrectly offset capture. This affects `--target active` and named
`--target window` captures on such configurations.
_Suggested fix:_ Guard against negative bounds by clamping to the virtual screen rectangle
(`[Windows.Forms.SystemInformation]::VirtualScreen`) or by catching exceptions from
`CopyFromScreen` and reporting `window_not_capturable` with a descriptive message about
the off-screen position.

**[low] PowerShell temp file uses a dot-prefix (hidden attribute), inconsistent with Python's documented avoidance**
`capture_screenshot.ps1:163` vs `capture_screenshot.py:437–439`
`New-TemporaryCapturePath` names the temp file `.{stem}.{PID}.{index}.tmp.png`
(dot-prefix). The Python path explicitly avoids dot-prefixes because macOS `screencapture`
refuses to write to hidden files, and includes a comment explaining this. While Windows has
no such restriction, some endpoint-security and backup agents skip hidden files (files with
the dot-prefix or the Hidden attribute). A screenshot capture that fails between writing the
temp file and the `Move-Item` would leave a hidden residual file not visible in Explorer.
The `finally` cleanup block does handle this case, so data exposure risk is low, but the
inconsistency between platforms is a latent maintenance hazard.
_Suggested fix:_ Name the temp file without a leading dot, e.g.,
`'{0}.{1}.{2:D3}.tmp.png' -f $stem, $PID, $i`, consistent with the Python version.

---

### Data leaks

No new findings. The empty-query bug (Security above) would result in captures of
unintended windows, but the output path and filenames are still derived from the
sanitized query (empty → "capture") rather than actual window titles. The DPI and
negative-coordinate issues involve pixel data, not metadata. The `allow_implicit_invocation`
concern is about consent process, not title leakage. All previously documented title-privacy
invariants continue to hold in the reviewed code.

---

### UX

**[low] No test coverage for empty `--query ""` validation**
`tests/test_capture_screenshot.py`
The test suite has no test that passes `--query ""` (or `--query` with an empty string)
and asserts an early exit with `EXIT_USAGE`. Given that the empty-query issue silently
expands capture scope (see Security above), a targeted regression test is warranted.
_Suggested fix:_ Add a test that calls `parse_args` or runs the script subprocess with
`--target window --query ""` and asserts `EXIT_USAGE` (exit code 64) and a message
containing "must not be empty".

**[info] `allow_implicit_invocation: true` in `agents/openai.yaml` is not mentioned in SKILL.md**
`agents/openai.yaml:7`, `SKILL.md`
SKILL.md's "Required Workflow" section instructs the agent to ask the user for approval
before each capture. The `allow_implicit_invocation: true` policy in the OpenAI YAML
could allow the skill to be selected without the user explicitly typing a capture request,
which is not discussed in SKILL.md. A user unfamiliar with this YAML knob might assume
explicit invocation is always required.
_Suggested fix:_ Add a note to SKILL.md (or to the YAML file itself) explaining the
implicit-invocation policy and confirming that the consent guard still applies even when
the skill is invoked implicitly.

---

## 2026-06-12

### Security

**[low] PowerShell `$matches` variable name collides with the automatic regex variable**
`capture_screenshot.ps1:204`
`Find-WindowHandles` assigns `$matches = [System.Collections.Generic.List[IntPtr]]::new()`,
shadowing PowerShell's built-in automatic variable `$Matches` (populated after `-match` and
`Select-String` operations). No regex operations currently occur in this function, so there
is no runtime bug, but PSScriptAnalyzer raises `PSAvoidAssignmentToAutomaticVariable` for this
assignment. A future maintainer who adds a `-match` expression inside `Find-WindowHandles`
would find `$matches` already holding the `List[IntPtr]` instead of the regex capture groups,
producing a hard-to-diagnose failure.
_Suggested fix:_ Rename `$matches` to `$matchedHandles` (or similar) throughout
`Find-WindowHandles`.

**[low] `install.sh` does not verify `git` is available before invoking `git clone`**
`install.sh:27`
`clone_if_missing` calls `git clone` without first checking that `git` exists in `PATH`. On a
system where git is absent, execution fails with `git: command not found` after the installer
has already printed the banner and detected agent directories, producing a confusing mid-run
failure with no clear remediation message. In a container image where `/.claude/skills` happens
to exist (e.g., a pre-built image) and the process runs as root, a missing git binary that is
later installed by a setup hook could introduce a window where the check passes but git is
absent.
_Suggested fix:_ Add `command -v git >/dev/null 2>&1 || { echo "error: git is required but not
found in PATH"; exit 1; }` near the top of the script, before the first agent-detection block.

---

### Bugs & regressions

**[low] `execute_plan` clipboard `{temp-output}` dispatch is silently broken for any plan that uses `{temp-output}` in a non-two-command sequence**
`capture_screenshot.py:491`
The branch that routes clipboard captures through a temp file checks:
```python
if destination == "clipboard" and len(plan.commands) == 2 and "{temp-output}" in plan.commands[0]:
```
The `len(plan.commands) == 2` guard is an undocumented implicit contract between `plan_capture`
and `execute_plan`. If a future `plan_capture` path adds a single-command or three-command plan
containing `{temp-output}`, the condition is False and execution falls through to the standard
clipboard branch (line 499), which calls `run_command(command)` without a `temp_output` argument.
`run_command` then immediately dies with "internal error: missing temporary output path" (exit 64).
The failure is silent at plan construction time and only surfaces at runtime. This is structurally
related to the 2026-06-11 finding about dead args beyond `commands[1][0]` in clipboard plans; both
stem from the implicit two-command contract.
_Suggested fix:_ Replace the `len == 2` guard with `any("{temp-output}" in cmd for cmd in
plan.commands)` so the dispatch is robust to command count. Add a comment documenting the
two-step `capture → copy-to-clipboard` structure and why `commands[1][0]` is the only element
consumed from the second command.

**[info] `plan_capture` re-reads `XDG_SESSION_TYPE` from the environment when `session_type` is the empty string**
`capture_screenshot.py:234`
```python
session = (session_type or os.environ.get("XDG_SESSION_TYPE") or "").lower()
```
`main()` passes `os.environ.get("XDG_SESSION_TYPE")` (line 631), which returns `None` when the
variable is absent (not `""`), so the double-read is harmless in the common case. However, if
`XDG_SESSION_TYPE` is exported as an empty string in the environment, `main()` passes `""` to
`plan_capture`, which evaluates as falsy and falls through to `os.environ.get` again — reading
the same empty string. The API creates a subtle ambiguity: callers cannot explicitly pass "no
session type override" because `""` is indistinguishable from `None` as a signal to fall back to
the environment.
_Suggested fix:_ Use `session_type if session_type is not None else os.environ.get("XDG_SESSION_TYPE", "")`
in `plan_capture`, treating `None` as "read from environment" and `""` as an explicit "unset" override.

---

### Data leaks

No new findings. Window title isolation continues to hold across all reviewed code paths. The
`$matches` naming issue involves window handle integers (IntPtr), not titles. The `install.sh`
git-availability failure exposes no user data. All error messages in all three platform paths
continue to echo only the user-supplied query text, never real window titles retrieved from the OS.

---

### UX

**[low] Windows: `PrintWindow` black-image detection warns but does not fall back to `CopyFromScreen`**
`capture_screenshot.ps1:306–314`
When `Test-BitmapAllBlack` detects that `PrintWindow` returned an all-black bitmap (the known
failure mode for GPU/DirectX/Electron windows such as Chrome), `Capture-ToDestination` writes the
warning to stderr but still saves and returns the black PNG. Since the user explicitly named (or
brought forward) the target window, it is typically unoccluded and suitable for a screen-buffer
blit via `CopyFromScreen`. An automatic silent fallback to `Copy-Rectangle` would deliver a
usable screenshot instead of a guaranteed-useless black image. The current behaviour forces the
user to bring the window forward, try again, and is not documented in the error message.
_Suggested fix:_ After detecting an all-black `PrintWindow` result, retry via `Copy-Rectangle`
and use that bitmap instead. Log a single debug-level warning (e.g., to stderr if `-Verbose` is
active) that a CopyFromScreen fallback was used.

**[low] `install.sh` provides no early-exit message when `git` is unavailable**
`install.sh:27`
(Same root cause as the Security finding above.) On a git-free system the user sees the installer
banner, agent detection output, and then an OS error for each `clone_if_missing` invocation,
rather than a single actionable "git is required" message before any output.
_Suggested fix:_ Same as the Security entry above.

---

## 2026-06-14

### Security

**[medium] README documents `curl | bash` as the primary install method, creating a supply-chain trust gap**
`README.md:15`
```bash
curl -fsSL https://raw.githubusercontent.com/nishalpattan/capture-screenshot/main/install.sh | bash
```
This pattern downloads and immediately executes the script without giving the user any opportunity to review it. If the GitHub repository or CDN is compromised (account takeover, malicious PR merged, CDN cache poisoning), or if the network path is under active attack (HTTPS mitigates most scenarios but not a compromised CA), arbitrary code runs on the user's machine. Unlike the 2026-06-08 "HTTPS transport for repository integrity" info finding (which covers cloned history), this finding is about the single-command install UX — users typically run it without auditing the script first. The script itself is short and auditable (63 lines, only `git clone`), which limits actual blast radius, but the pattern itself is the concern.
_Suggested fix:_ Add a two-step form to the README: `curl -fsSL ... > install.sh && cat install.sh` (review) then `bash install.sh` (run). Alternatively, publish a SHA-256 checksum alongside each release and document a `shasum -c` verification step.

**[low] macOS helper binary compiled without explicit hardening flags**
`capture_screenshot.py:339`
```python
subprocess.run([clang, "-framework", "ApplicationServices", str(helper_source), "-o", str(helper)], check=True)
```
The clang invocation does not pass `-fstack-protector-strong`, `-D_FORTIFY_SOURCE=2`, or an explicit `-Wl,-pie`. On current macOS, clang enables PIE by default for executables and applies reasonable stack protection, so this is not an active vulnerability. However, making the flags explicit ensures the binary is hardened portably across SDK upgrades, alternative toolchains (e.g., `clang` from Homebrew vs. Xcode), and future macOS versions where defaults might change.
_Suggested fix:_ Extend the compile command to `[clang, "-framework", "ApplicationServices", "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", str(helper_source), "-o", str(helper)]`.

---

### Bugs & regressions

**[medium] Windows `--allow-multiple-matches --destination clipboard` silently discards all captures except the last**
`capture_screenshot.ps1:317–319, 396–401`
The 2026-06-11 entry documented this behavior for the macOS/Python path; the Windows PowerShell script has the same issue independently. When `$AllowMultipleMatches` is set and `$Destination` is `clipboard`, `Capture-ToDestination` is called for each matched window handle in sequence. Each call executes `[Windows.Forms.Clipboard]::SetImage($bitmap)`, which atomically replaces the clipboard contents. For N matched windows, N `"clipboard"` lines are written to stdout, but only the last window's image remains. Users have no indication that earlier captures were discarded.
_Suggested fix:_ Mirror the macOS suggestion from 2026-06-11: either (a) detect `$AllowMultipleMatches -and $Destination -eq 'clipboard'` and exit early with a structured error — `[Console]::Error.WriteLine("clipboard_allows_one: clipboard destination supports only one window at a time; use desktop for multiple captures"); exit 74` — or (b) emit a stderr warning listing how many captures were requested versus how many survived on the clipboard.

**[low] macOS `--target active` cannot be exercised under the `CAPTURE_SCREENSHOT_TEST_PLATFORM=Darwin` test stub**
`capture_screenshot.py:325–328`
`resolve_macos_with_helper` short-circuits to the in-process `resolve_macos_window_ids` function only when `test_windows is not None and not active`. When `--target active` is used, `active=True`, so the condition is `False` and the function falls through to real clang compilation and a real `CGWindowList` query, regardless of `CAPTURE_SCREENSHOT_TEST_WINDOWS` being set. In a Linux CI environment (where clang is absent), this produces `ResolutionResult(False, "missing_dependency_clang", ...)` rather than a controlled test outcome. On macOS CI, it attempts a real window-system query. As a result, the test suite has no coverage for macOS active-window capture and cannot test that code path without a live macOS display session.
_Suggested fix:_ Extend the test-stub branch: change the condition to `if test_windows is not None:` and, for the `active=True` case, return the first capturable entry from `test_windows` (e.g., the entry with the lowest index that has `capturable` not False). Add a corresponding test `test_macos_active_window_returns_first_capturable` that sets both env variables and asserts a successful resolution.

---

### Data leaks

No new findings. The Windows clipboard last-wins bug (above) involves pixel data only; no window title metadata is written to the clipboard or to any output message. The `"clipboard"` string printed per capture does not encode title information. All previously documented title-privacy invariants continue to hold across all three platform paths.

---

### UX

**[low] No test case exercises the macOS `--target active` end-to-end flow**
`tests/test_capture_screenshot.py`
Consequent on the bug entry above: the test suite covers macOS window-by-name resolution, minimized-window detection, multiple-match handling, and dry-run path output, but has no test for the active-window path (`--target active --destination desktop` or clipboard on Darwin). A regression in `resolve_macos_with_helper` when `active=True` — for example, a change to the `--frontmost` flag handling, the proc.returncode dispatch, or the `_validate_integer_ids` call — would not be caught.
_Suggested fix:_ Once the test stub is extended (see Bugs & regressions above), add integration tests:
1. `CAPTURE_SCREENSHOT_TEST_WINDOWS=[{"id":5,"owner":"Terminal","title":"x"}]` + `--target active --dry-run` → asserts `proc.returncode == 0` and printed path contains `active-window`.
2. `CAPTURE_SCREENSHOT_TEST_WINDOWS=[{"id":5,"owner":"Terminal","title":"x","capturable":false}]` + `--target active` → asserts `proc.returncode == EXIT_NOT_CAPTURABLE`.

**[info] `unique_capture_path` uses `EXIT_PRIVACY` for a resource-exhaustion condition**
`capture_screenshot.py:99`
```python
die("could not allocate a unique screenshot filename", EXIT_PRIVACY)
```
`EXIT_PRIVACY = 73` is the documented exit code for privacy-enforcement failures (consent not given, symlink detected, permission lock-down failed). Running out of the 1000-candidate filename namespace is a resource/state problem, not a privacy violation. A caller inspecting exit codes would misclassify this as a privacy refusal. In practice, generating 1000 same-label screenshots in one session is essentially impossible, so this is cosmetic.
_Suggested fix:_ Use `EXIT_UNAVAILABLE = 74` (or define `EXIT_INTERNAL = 70` as suggested in the 2026-06-08 entry) for this failure path.

---

## 2026-06-15

### Security

**[low] `capture_screenshot.ps1:93` — `New-Item` uses `-Path` instead of `-LiteralPath` for directory creation**
`capture_screenshot.ps1:93`
Every other path operation in the script uses `-LiteralPath` (fourteen call-sites: `Get-Item`, `Test-Path`, `Get-Acl`, `Set-Acl`, `Move-Item`, `Remove-Item`, `Get-Item` for reparse-point check). The sole exception is the directory creation call:
```powershell
New-Item -ItemType Directory -Path $Path -Force | Out-Null
```
PowerShell's `-Path` parameter interprets wildcard metacharacters (`[`, `]`, `*`, `?`). If `$OutputRoot` contains literal brackets — for example, a user's desktop folder named `[screenshots]` — `New-Item -Path` may expand the pattern to zero or multiple matching paths and fail with a non-obvious error, or (in edge cases) silently create a directory at an unintended location. Critically, the ACL operations immediately after use `-LiteralPath $Path`, so the ACE is applied to the literal string while the directory may have been created via an expanded path — a mismatch.
Python's `_validate_output_root` checks home-containment but does not strip wildcard characters from `$OutputRoot`, and when the PS script is invoked directly (without the Python orchestrator) there is no home-containment check at all (noted in 2026-06-08), leaving `$Path` fully user-controlled.
_Suggested fix:_ Replace `New-Item -ItemType Directory -Path $Path -Force` with `New-Item -ItemType Directory -LiteralPath $Path -Force`, consistent with every other path operation in the script.

---

### Bugs & regressions

**[low] `capture_screenshot.ps1:22–62` — `Add-Type` inline C# recompiles on every fresh PowerShell process**
`capture_screenshot.ps1:22`
The 95-line inline C# block (Win32 P/Invoke declarations for `EnumWindows`, `GetWindowText`, `GetWindowRect`, `PrintWindow`, etc.) is compiled by `Add-Type` into a dynamic in-memory assembly at the start of every fresh PowerShell process. While PowerShell caches `Add-Type` results within a single runspace, each new `pwsh -File` invocation starts a fresh process with no cache. The compilation adds roughly 300–800 ms of fixed overhead to every capture request. The analogous macOS concern (clang recompiling `find_macos_window_id.m` on each call) was documented in the 2026-06-09 entry; the Windows path has the same class of latency issue.
_Suggested fix:_ Pre-compile the Win32 declarations to a `.dll` at install time (`Add-Type -TypeDefinition ... -OutputAssembly scripts/Win32Capture.dll -OutputType Library`) and load it with `[System.Reflection.Assembly]::LoadFrom(...)` at runtime, recompiling only when the assembly is absent or outdated. This reduces per-invocation overhead to a single `Assembly.LoadFrom` call.

**[info] `capture_screenshot.py:491` — `{temp-output}` dispatch assumes `plan.commands[0]` is a tuple, but `in` operator tests element membership, not substring**
`capture_screenshot.py:491`
```python
if destination == "clipboard" and len(plan.commands) == 2 and "{temp-output}" in plan.commands[0]:
```
`plan.commands[0]` is a tuple of strings (e.g., `(grim, "{temp-output}")`). The `in` operator tests for exact element membership, not for a substring. This is correct for the current command structures, but the expression reads ambiguously to a maintainer who might think `in` is testing for a substring of a string. The 2026-06-12 entry documented the fragile `len == 2` guard; this note adds that the `in` check is also non-obvious in isolation.
_Suggested fix:_ Add a comment: `# checks whether "{temp-output}" is one of the argument strings in the first command`, or rewrite as `any(part == "{temp-output}" for part in plan.commands[0])` to make the intent unambiguous.

---

### Data leaks

No new findings. All previously documented title-privacy invariants continue to hold in the reviewed code. The `New-Item -Path` issue could cause incorrect directory creation but would not expose window title metadata. The `Add-Type` compilation path involves no user data. Error messages for the newly analysed paths echo only static strings or the user-supplied query, never real window titles.

---

### UX

**[low] `capture_screenshot.ps1` — clipboard destination may throw with MTA threading error when script is invoked directly without `-Sta`**
`capture_screenshot.ps1:317–319`
`[Windows.Forms.Clipboard]::SetImage($bitmap)` requires the calling thread to be in Single-Threaded Apartment (STA) mode. When the Python orchestrator invokes the PS script it explicitly passes `-Sta` (line 537 of `capture_screenshot.py`), ensuring the correct apartment state. However, if the script is invoked directly — e.g., `pwsh -File capture_screenshot.ps1 -ConsentConfirmed -Destination clipboard ...` — PowerShell 7+ (`pwsh`) defaults to MTA threading. `SetImage` then throws:
```
Current thread must be set to single thread apartment (STA) mode before OLE calls can be made.
```
This manifests as an unhandled terminating error (exit 1) with a .NET stack trace rather than a structured exit code. PowerShell 5.1 (`powershell.exe`) already defaults to STA, so only `pwsh` direct invocations are affected.
_Suggested fix:_ Add a threading-model check near the top of the script and emit a clear error: `if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne [System.Threading.ApartmentState]::STA -and $Destination -eq 'clipboard') { [Console]::Error.WriteLine("clipboard capture requires STA threading — invoke with: pwsh -Sta -File capture_screenshot.ps1 ..."); exit 64 }`.

**[info] No test exercises the `--allow-multiple-matches` + desktop path for a multi-window query returning more than one ID**
`tests/test_capture_screenshot.py`
`test_macos_allow_multiple_returns_all_capturable` (line 231) verifies that `resolve_macos_window_ids` returns multiple IDs, and `test_prepare_output_paths_suffixes_duplicate_labels_in_one_request` (line 53) verifies filename deduplication. However, there is no end-to-end integration test that runs the full `main()` with `--allow-multiple-matches`, a multi-window stub, and `--destination desktop`, verifying that (a) two separate `.png` paths are printed, (b) each path is unique, and (c) the `reserved`-set deduplication in `prepare_output_paths` is exercised in the subprocess path. A regression in the `labels.extend(...)` / `prepare_output_paths` interaction would be silent.
_Suggested fix:_ Add an integration test using `CAPTURE_SCREENSHOT_TEST_WINDOWS` with two capturable windows and `--allow-multiple-matches --destination desktop --dry-run`, asserting two distinct output paths are printed on separate lines.

---

## 2026-06-16

### Security

**[medium] macOS Screen Recording permission denied on 10.15+ silently yields "no matching window" rather than a permission diagnostic**
`scripts/find_macos_window_id.m:67`
On macOS Catalina (10.15) and later, `CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)` requires the Screen Recording permission. When that permission has been denied (or revoked), the API does NOT return `NULL` — it returns a non-NULL `CFArrayRef` containing only windows belonging to the calling process itself (the transient helper binary has no windows), filtered of title strings. The existing NULL guard at line 70 (`if (!windows) { return 1; }`) therefore never fires. The loop iterates zero or near-zero entries, so `capturable_count == 0` and `present_count == 0`; the helper exits with code 2. Python maps code 2 to `ResolutionResult(False, "no_matching_window", "No matching on-screen window found.")`. The user receives a misleading usage-style error with no indication that the Screen Recording permission must be granted in System Preferences → Privacy & Security. This undermines the consent-enforcement story: macOS's OS-level gate is the primary safeguard for the window-title enumeration, so its silent failure mode is security-relevant, not just cosmetic.
_Suggested fix:_ After `CGWindowListCopyWindowInfo` returns a non-NULL but zero-count array, emit a specific token on stderr (e.g., `screen_recording_denied`) and return exit code 5. In Python's `resolve_macos_with_helper`, map exit code 5 to a new `ResolutionResult(False, "screen_recording_permission_denied", "Screen Recording permission is required — grant it in System Preferences → Privacy & Security → Screen Recording, then retry.")`.

**[low] `ensure_private_directory` with `parents=True` secures only the leaf directory; intermediate parents created by Python's `mkdir` use default permissions**
`capture_screenshot.py:108`
`path.mkdir(mode=0o700, parents=True, exist_ok=True)` follows Python's documented `parents=True` semantics: only the leaf directory receives the supplied `mode`; missing intermediate ancestors are created with the default mode (typically `0o755`, further modified by umask). If a user supplies a deep `--output-root` such as `~/new_project/captures/screenshots` where `new_project/captures` does not yet exist, those ancestors are created world-traversable. Other users on a shared machine can therefore observe the existence of the directory hierarchy (but not its contents). The subsequent `path.chmod(0o700)` call only tightens the leaf. In the default case (`~/Desktop/screenshots`), `~/Desktop` already exists, so no new intermediary is created and this is benign; the risk appears only when a non-standard `--output-root` with non-existent parents is used.
_Suggested fix:_ Walk the ancestors from the deepest existing one down and `chmod(0o700)` each newly created directory, or use a manual `os.makedirs`-equivalent that passes the mode to each created level. Alternatively, add documentation that `--output-root` parents must already exist.

---

### Bugs & regressions

**[medium] `--query` values are silently discarded when `--target` is `fullscreen` or `active`; no warning is emitted**
`capture_screenshot.py` (`main()`, fullscreen/active branches ~line 591–596)
`parse_args` defines `--query` as an optional `append` argument with no constraint on which `--target` values it may accompany. In `main()`, `args.query` is consumed only inside the `if args.target == "window":` branch. When `--target fullscreen` or `--target active` is used with one or more `--query` values — e.g., `--target fullscreen --query Safari` — those queries are silently ignored and a full-screen or active-window capture proceeds. The user may have intended to narrow the scope (e.g., believing `--query` filters a multi-monitor fullscreen to one display), receiving instead a much wider capture than requested. This contradicts the privacy-first principle of never broadening scope silently.
_Suggested fix:_ After the target branch selection, add a guard:
```python
if args.query and args.target != "window":
    die(f"--query is only valid with --target window (got --target {args.target})", EXIT_USAGE)
```
Add a corresponding test asserting `EXIT_USAGE` when `--query` is supplied with a non-window target.

**[low] PowerShell default `$OutputRoot` resolves to a relative path on Windows Server Core where `GetFolderPath('Desktop')` returns an empty string**
`capture_screenshot.ps1:8`
The default parameter value is:
```powershell
[string]$OutputRoot = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'screenshots')
```
On Windows Server Core, Nano Server, and container images without a desktop shell, `[Environment]::GetFolderPath('Desktop')` returns an empty string `""`. `Join-Path "" 'screenshots'` evaluates to `screenshots` (a bare relative path). When the script is invoked directly without the Python orchestrator (which always passes `-OutputRoot` explicitly), `Protect-Directory -Path 'screenshots'` creates a directory named `screenshots` in whatever the current working directory happens to be — potentially outside the user's home. Combined with the previously documented absence of home-containment enforcement in the PS script (2026-06-08), this creates a path where screenshots land in an unintended, unprotected location.
_Suggested fix:_ Change the default to `(Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Desktop\screenshots')` or validate at the top of the script that `$OutputRoot` is non-empty and rooted, failing with a structured message if not.

---

### Data leaks

No new findings. The `--query` silent-discard bug (above) routes the discarded query values to oblivion rather than to any output or log, so no title or query text is exposed. The Screen Recording permission failure path emits only the static exit code 2 and a static string; no window title information reaches the helper's output. All previously documented title-privacy invariants continue to hold in the reviewed code.

---

### UX

**[medium] No diagnostic path exists when macOS Screen Recording permission is missing or denied**
`scripts/find_macos_window_id.m:67`, `capture_screenshot.py:resolve_macos_with_helper`
(Same root cause as the security finding above.) When Screen Recording permission is absent, every named-window and active-window request on macOS fails with the generic "no_matching_window" / "No matching on-screen window found" message. The user has no indication that the problem is a system permission rather than a typo in the app name. The error message for `no_matching_window` suggests checking the window name, sending the user on a fruitless debugging path. A first-time installer is especially likely to hit this: the skill's `install.sh` grants no permission automatically, and the OS's permission prompt may have been dismissed or may not appear until the Screen Recording permission is triggered — which it currently isn't because the failed API call returns a partial result rather than failing visibly.
_Suggested fix:_ Same as the security finding: add an exit code 5 from the helper and map it to a human-readable permission guidance message in Python.

**[low] `test_skill_notice_documents_privacy_consent_and_intended_use` raises `FileNotFoundError` rather than a descriptive assertion failure if `SKILL.md` is absent or renamed**
`tests/test_capture_screenshot.py:SKILL_MD` (module level, line ~13)
`SKILL_MD = ROOT / "SKILL.md"` is defined at module level and used inside the test as `SKILL_MD.read_text(encoding="utf-8")`. If the file does not exist (e.g., renamed to `skill.md` on a case-sensitive filesystem, or deleted), the test fails with an unhandled `FileNotFoundError` rather than an assertion failure, which obscures the root cause when running the full test suite.
_Suggested fix:_ Wrap the `read_text` call in a `try/except FileNotFoundError` or add `self.assertTrue(SKILL_MD.exists(), "SKILL.md not found — is the file path correct?")` as the first assertion in the test.

---

## 2026-06-17

### Security

**[low] Helper compilation temp directory is visible in world-traversable `/tmp`, leaking capture timing metadata to co-tenants**
`capture_screenshot.py:337` (`resolve_macos_with_helper`)
`tempfile.TemporaryDirectory(prefix="screenshot-window.")` creates a directory in the system temp directory (typically `/tmp` on Linux/macOS). The temp directory itself is created with mode 0o700 (contents are protected), but its existence in the world-traversable `/tmp` (mode 0o1777) is visible to any user who can run `ls /tmp`. A co-tenant can therefore observe that a `screenshot-window.XXXXXX` directory exists, deduce that a macOS named-window or active-window capture is in progress, and correlate its creation timestamp to infer capture timing. The directory content (compiled helper binary, window IDs emitted at runtime) remains protected. This is distinct from the 2026-06-08 finding (clipboard temp PNG in `/tmp`): the desktop-path temp file is created inside a 0o700 request directory so metadata is also hidden, but the compilation directory does not receive the same treatment.
_Suggested fix:_ Create the compilation temp directory inside a pre-existing private directory (e.g., under the same `ensure_private_directory`-created output root, or a `tempfile.mkdtemp()` inside the user's home), ensuring the directory name is not visible in world-traversable space. If a home-rooted location is impractical for the compile step, at minimum note in privacy documentation that capture attempts create a visible directory entry in `/tmp`.

**[low] `sanitize_label` strips only `http://` and `https://` schemes; other URL-like schemes (`ftp://`, `file://`, `mailto:`) are not removed**
`capture_screenshot.py:71`, `capture_screenshot.ps1:69`
```python
label = re.sub(r"https?://", "", label)
```
and
```powershell
$label = $Value.ToLowerInvariant() -replace 'https?://', ''
```
A window title containing `ftp://my.server/private-path` or `file:///etc/internal-notes` produces a sanitized label such as `ftp-my-server-private-path` or `file-etc-internal-notes`. No path traversal is possible (all non-alphanumeric characters subsequently become `-`), and the scheme component leaks no more information than the rest of the title would. However, the scheme prefix (`ftp-`, `file-`) remains in the filename, partly defeating the purpose of URL-stripping (which presumably targets privacy — keeping server names out of filenames when a browser tab title contains a URL). Both the Python and PowerShell implementations mirror this narrow pattern; it appears intentional for HTTP/HTTPS only, but is undocumented.
_Suggested fix:_ Broaden the pattern to strip any URL scheme: `re.sub(r"[a-z][a-z0-9+\-.]*://", "", label, flags=re.I)` (RFC 3986 scheme grammar) and apply the same change to the PowerShell equivalent. Or add a comment explaining why only HTTP/HTTPS schemes are intentionally removed.

---

### Bugs & regressions

**[medium] Windows `GetWindowRect` returns DWM extended-frame bounds including invisible drop-shadow margin, causing stray pixels in captured images**
`capture_screenshot.ps1:225–246` (`Get-WindowBounds`, `Copy-Rectangle`, `Copy-Window`)
On Windows Vista and later with Desktop Window Manager (DWM) enabled, `GetWindowRect` returns the "extended frame" bounds for DWM-composited windows, which include an invisible drop-shadow region — typically 7–9 logical pixels on each side. When `Copy-Rectangle` is used (for fullscreen and as the screen-blit path in `Copy-Window` fallback), the bitmap dimensions are based on these extended bounds and `CopyFromScreen` captures the corresponding screen region: the shadow margin pixels contain whatever is rendered behind the window at those positions (desktop or neighboring window content). For the `Copy-Window` path (`PrintWindow` + GDI+), the DC is also sized to the extended bounds; pixels in the shadow margin are not written by `PrintWindow` and remain as the zero-initialized GDI+ color (black), producing a narrow black border around the actual window content in the saved PNG. The Windows API `DwmGetWindowAttribute(hWnd, DWMWA_EXTENDED_FRAME_BOUNDS, &rect, sizeof(rect))` returns the visible client-frame bounds excluding the shadow, matching what the user sees on screen.
_Suggested fix:_ Add a `DwmGetWindowAttribute` P/Invoke signature to the inline C# block in the PowerShell script and update `Get-WindowBounds` to prefer `DWMWA_EXTENDED_FRAME_BOUNDS` over `GetWindowRect` for any window handle that is not zero (i.e., for named and active captures). Fall back to `GetWindowRect` when the DWM call fails (e.g., non-DWM window or Windows Server Core). Fullscreen capture already uses `SystemInformation.VirtualScreen` and is unaffected.

**[low] `test_windows_delegates_to_powershell` embeds temp file path in shell script without quoting, fragile on paths with spaces**
`tests/test_capture_screenshot.py:309`
```python
fake_ps.write_text(
    f'#!/bin/sh\nprintf "%s\\n" "$@" > {args_file}\necho "fake/path.png"\n'
)
```
`args_file` is a `Path` object whose string representation is interpolated directly into the shell script without quoting. On macOS, `tempfile.TemporaryDirectory()` creates directories under `/private/var/folders/…` (which currently contains no spaces), and on Linux under `/tmp/tmpXXXXXX`. If a CI runner configures `TMPDIR` to a path with spaces (not uncommon on macOS GitHub Actions), the shell redirect `> /path with spaces/file.txt` would be parsed incorrectly, causing the fake `powershell.exe` to fail with a shell error rather than writing the expected args file. The test would then report a false failure in `captured = args_file.read_text()` (FileNotFoundError) rather than in the code under test, obscuring the root cause.
_Suggested fix:_ Quote the path in the shell script: `f'#!/bin/sh\nprintf "%s\\n" "$@" > "{args_file}"\necho "fake/path.png"\n'`, or use `shlex.quote(str(args_file))` to handle any metacharacters robustly.

---

### Data leaks

No new findings. The DWM shadow-margin pixels captured by `Copy-Rectangle` are rendered screen content of neighboring windows or the desktop (pixel data only, not window title metadata). The helper compilation temp directory in `/tmp` leaks existence and timing metadata, as documented above under Security, but not window IDs or image content. The incomplete URL scheme stripping in `sanitize_label` could leave `ftp-` or `file-` prefixes in filenames, but not server names or path segments beyond what the full label sanitization already permits. All previously documented title-privacy invariants continue to hold across all three platform paths.

---

### UX

**[low] WSL (Windows Subsystem for Linux) is not detected; capture attempts silently fall through to tool-not-found errors with no platform guidance**
`capture_screenshot.py:570` (`main()`, platform detection)
On WSL, `platform.system()` returns `"Linux"`, so the code enters the Linux path. WSL environments typically have no X display server, Wayland compositor, or GNOME session running. All tool detections via `detect_tools()` return empty results, and `plan_capture` exits with `missing_dependency_fullscreen` or `missing_dependency_named_window`. The error message gives no indication that the running environment is WSL or that the Windows native capture path (invoking the script from PowerShell directly) should be used instead. CONTRIBUTING.md acknowledges this gap under "Good first issues" item 5.
_Suggested fix:_ Detect WSL by reading `/proc/version` for the substring `microsoft` or `WSL` (case-insensitive) before the Linux tool-detection block, and `die("WSL is not a supported capture environment — run the script from a native Windows PowerShell session to use the Windows capture path", EXIT_UNAVAILABLE)`.

**[info] CONTRIBUTING.md code snippet for `plan_capture()` shows `(session_type or "").lower()` but the real implementation also falls back to `os.environ.get("XDG_SESSION_TYPE")`**
`CONTRIBUTING.md:35–44`, `capture_screenshot.py:234`
The CONTRIBUTING.md example shows:
```python
session = (session_type or "").lower()
```
The actual code is:
```python
session = (session_type or os.environ.get("XDG_SESSION_TYPE") or "").lower()
```
A contributor following the docs snippet would omit the environment variable fallback and potentially produce a plan that ignores `XDG_SESSION_TYPE` when `session_type` is an empty string, introducing a silent regression. The discrepancy is cosmetic (a simplified example), but could mislead a contributor adding a new session type branch.
_Suggested fix:_ Update the CONTRIBUTING.md snippet to match the actual code or add a comment noting that the snippet is simplified and contributors should read the actual function signature.

---

## 2026-06-18

### Security

**[low] `secure_file()` catches `PermissionError` only, not the full `OSError` hierarchy**
`capture_screenshot.py:117–121`
```python
def secure_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except PermissionError:
        die("could not secure screenshot file permissions", EXIT_PRIVACY)
```
`path.chmod()` may raise other `OSError` subclasses: `FileNotFoundError` (ENOENT, if the file
was deleted between creation and the chmod call), `OSError` with `EROFS` (read-only filesystem),
or `NotADirectoryError`. These propagate as unhandled Python exceptions — a raw traceback with
exit code 1 — rather than a structured `die()` message. In practice the file is always freshly
created by `private_temp_png` or `os.replace`, making ENOENT very unlikely, but the narrow
`except PermissionError` leaves other error modes unhandled in a privacy-critical path.
_Suggested fix:_ Broaden the catch to `except OSError as e:` and use
`die(f"could not secure screenshot file permissions: {e.strerror}", EXIT_PRIVACY)`, consistent
with the intent of the surrounding code.

**[low] `Copy-Window` leaks GDI `$bitmap` if `FromImage` or `GetHdc` raises before the outer `finally`**
`capture_screenshot.ps1:255–271`
```powershell
$bitmap = [Drawing.Bitmap]::new($Bounds.Width, $Bounds.Height)
$graphics = [Drawing.Graphics]::FromImage($bitmap)   # could throw
try {
    $hdc = $graphics.GetHdc()                         # could throw
    ...
} finally {
    $graphics.Dispose()
}
```
If `FromImage` throws (e.g., out-of-GDI-handle condition), `$graphics` is never assigned and
`$bitmap` is never disposed, because the `try/finally` is never entered. Likewise if `GetHdc`
throws, the outer `finally` disposes `$graphics` but `$bitmap` is not cleaned up (the `if -not
$ok` path that calls `$bitmap.Dispose()` is never reached). The bitmap allocated on line 255 is
then leaked until the process exits. `Capture-ToDestination`'s own `try/finally` does not cover
this allocation because `Copy-Window` throws rather than returning `$bitmap`. In practice,
`FromImage` and `GetHdc` rarely fail on a freshly allocated bitmap, but GDI exhaustion on
resource-constrained systems can trigger this.
_Suggested fix:_ Restructure `Copy-Window` with a trap around the full allocation block, or move
the `$bitmap` disposal into the same `finally` as `$graphics`: at function exit, if `$bitmap`
is not being returned (i.e., an exception is in flight), call `$bitmap.Dispose()`.

---

### Bugs & regressions

**[low] `clang` compilation has no `capture_output=True`; compiler diagnostics emit on the user's terminal**
`capture_screenshot.py:339`
```python
subprocess.run([clang, "-framework", "ApplicationServices", str(helper_source), "-o", str(helper)], check=True)
```
The helper binary's runtime output is captured (`capture_output=True` on line 347), but the
`clang` compilation step is not. Any warnings clang emits (e.g., implicit-function-declaration
notes, SDK deprecation notices) go directly to the calling process's stderr, intermixed with the
script's own output. A user running a normal `--target window` request would see unexpected clang
diagnostic lines that give no actionable guidance. When compilation fails (already noted as the
2026-06-09 bug), the error is already visible on terminal before the unhandled `CalledProcessError`
propagates; this finding is about the success path also leaking diagnostics.
_Suggested fix:_ Add `stderr=subprocess.PIPE` (or `capture_output=True`) to the clang invocation
and include `e.stderr` in the structured `ResolutionResult` message if compilation fails (the fix
proposed in the 2026-06-09 entry would naturally capture stderr at that point).

**[low] `resolve_linux_named_window` calls `_linux_window_is_viewable` for every matched ID before the `allow_multiple` count check**
`capture_screenshot.py:405–416`
```python
classified = [(wid, _linux_window_is_viewable(wid, tools)) for wid in ids]
if any(state is None for _, state in classified):
    capturable = ids
else:
    capturable = tuple(wid for wid, state in classified if state)
    ...
if len(capturable) > 1 and not allow_multiple:
    return ResolutionResult(False, "multiple_matches", ...)
```
`_linux_window_is_viewable` calls `subprocess.run([xprop, ...])` or `subprocess.run([xwininfo, ...])`
for each window ID. When a query matches N windows (e.g., a common app name) and
`allow_multiple=False`, the function performs N subprocess round-trips before concluding
"multiple matches" and returning an error. Even when the second ID makes the multiple-match
outcome certain, all remaining IDs are still classified. On a machine where xprop is slow or the
X server is under load, this adds noticeable latency proportional to N. Combined with the
no-timeout concern (2026-06-11 entry), a single hung `xprop` call blocks all subsequent
classifications.
_Suggested fix:_ In the `not allow_multiple` path, break out of the classification loop as soon
as two capturable IDs have been found — a short-circuit that avoids all remaining subprocess
calls. The `allow_multiple` path must still classify all IDs.

---

### Data leaks

No new findings. All previously documented title-privacy invariants continue to hold across all
three platform paths. The `clang` diagnostic output (bugs above) includes only source-file paths
and compiler codes, not window titles or user data. The GDI bitmap leak involves only pixel data
in kernel-managed memory, inaccessible to other processes. Error messages on all three platforms
continue to echo only the user-supplied query text, never real window titles retrieved from the OS.

---

### UX

**[info] `clang` compile warnings appear on user terminal during normal operation**
`capture_screenshot.py:339`
(Same root cause as the bugs finding above.) From a user-experience perspective, a normal
`--target window` capture might print clang warnings such as deprecation notices or
implicit-conversion notes before the screenshot path is printed. These lines have no meaning
to an end user and provide no remediation guidance. On macOS systems where the Xcode Command
Line Tools version diverges from the SDK, deprecation warnings can appear even for clean source.
_Suggested fix:_ Same as the bugs entry above — redirect the compile stderr to `subprocess.PIPE`
and surface it only in structured error messages on failure.

**[low] `resolve_linux_named_window` performs unnecessary subprocess round-trips on common-name queries**
`capture_screenshot.py:405–416`
(Same root cause as the bugs finding above, UX angle.) A user querying a common process name
(e.g., `--query "a"`, `--query "Code"`) on a desktop with many open windows experiences latency
proportional to the match count before receiving the "multiple matches" error, with no progress
indication. The delay is invisible because the script produces no interim output.
_Suggested fix:_ Same as the bugs entry — short-circuit classification after two capturable
matches are found when `allow_multiple=False`.

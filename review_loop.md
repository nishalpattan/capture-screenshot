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

---

## 2026-06-19

### Security

**[low] `ensure_private_directory` has the same narrow error-catch pattern as the 2026-06-18 `secure_file` finding**
`capture_screenshot.py:107,112`
`path.mkdir(mode=0o700, parents=True, exist_ok=True)` (line 107) and `path.stat()` (line 112) are
outside the `try/except PermissionError` block that guards only `path.chmod()`. If `mkdir` fails
with `OSError(ENOSPC)` (disk full), `OSError(EROFS)` (read-only filesystem), or
`NotADirectoryError` (a parent path component is a regular file), the exception propagates as an
unhandled Python traceback with exit code 1. In a privacy-critical path where the output directory
is being secured before any screenshot data is written, these failure modes should surface as
structured `die()` messages. The 2026-06-18 entry documented the identical pattern in `secure_file`;
this finding extends it to the sibling function `ensure_private_directory`.
_Suggested fix:_ Wrap both `path.mkdir()` and `path.stat()` in a `try/except OSError as e:` block
and call `die(f"could not create private screenshots directory: {e.strerror}", EXIT_PRIVACY)`,
consistent with the approach proposed for `secure_file`.

**[low] `_validate_output_root` validates the resolved path but passes the original (potentially relative) string to PowerShell**
`capture_screenshot.py:522–527, 541`
`_validate_output_root(path)` calls `path.resolve()` (line 525) to obtain the canonical absolute
path for the home-containment check. However, `_run_powershell_script` passes `str(args.output_root)`
(line 541) — the original, unresolved value — to PowerShell via `-OutputRoot`. If the user supplies
a relative `--output-root` such as `--output-root screenshots`, Python resolves `screenshots` to an
absolute path (e.g., `/home/user/projects/screenshots`) and validates that result. PowerShell
receives the bare string `"screenshots"` and uses `Protect-Directory -Path 'screenshots'`, creating
the directory relative to PowerShell's inherited cwd. Because Python and the PowerShell subprocess
share the same working directory (subprocess inherits the parent's cwd), the effective absolute
path is identical in practice, so this is not an active vulnerability. It is, however, a latent
maintenance hazard: any future change that sets a different `cwd=` in the `subprocess.run` call
inside `_run_powershell_script` would silently break the invariant that the validated path and the
used path are the same.
_Suggested fix:_ Pass `str(args.output_root.resolve())` to PowerShell to make the absolute-path
guarantee explicit and robust to future refactoring.

---

### Bugs & regressions

**[low] `run_command` does not redirect screenshot-tool stdout/stderr, risking structured-output pollution**
`capture_screenshot.py:465`
`subprocess.run(args, check=True)` inherits the calling process's stdout and stderr file descriptors.
Screenshot tools such as ImageMagick `import`, `spectacle`, and `gnome-screenshot` may emit
diagnostic warnings or informational lines. Because `run_command` provides no redirection, these
lines are written directly to the Python script's stdout — interleaved with the structured output
(file paths or `"clipboard"`) that callers (AI agents, shell scripts) parse. For example,
`import -window root output.png` may emit X11 connection warnings to stderr; some builds of
`spectacle` write a status line to stdout before writing the file. An agent parsing the script's
stdout for the saved path could be confused by extra, unexpected lines. The 2026-06-18 entry
documented the same issue for the `clang` compilation step specifically; this finding extends it to
the runtime screenshot-tool invocations.
_Suggested fix:_ In `run_command`, pass `stderr=subprocess.PIPE` to capture tool stderr and include
it in any `CalledProcessError` message (which also addresses the 2026-06-11 `CalledProcessError`
finding). For stdout, pass `stdout=subprocess.DEVNULL` unless the tool is known to produce output
needed by the caller — none of the currently used screenshot tools write meaningful data to stdout
(they write to the output file path instead). This change fully isolates tool diagnostic output from
the Python script's structured result lines.

**[info] `_test_windows()` validates `"id"`, `"capturable"`, and `"state"` field types but not `"owner"` or `"title"`**
`capture_screenshot.py:310–322`
`_test_windows()` checks that `"id"` is an `int`, `"capturable"` is a `bool`, and `"state"` is a
`str`, but applies no type validation to `"owner"` or `"title"`. Both fields are consumed in
`resolve_macos_window_ids` via `str(window.get("owner", ""))` and `str(window.get("title", ""))`,
so a non-string value (e.g., `{"owner": 42}`) silently coerces to `"42"` rather than triggering a
validation error. This is inconsistent with the explicit checks on the other three fields and could
mask a malformed test fixture where an integer was accidentally used where a string was intended.
The risk is test-only; there is no production impact.
_Suggested fix:_ Add `if "owner" in entry and not isinstance(entry["owner"], str): die(...)` and the
equivalent for `"title"`, consistent with the existing validation pattern for the other fields.

---

### Data leaks

No new findings. All previously documented title-privacy invariants continue to hold in the reviewed
code. The `ensure_private_directory` error-handling gap involves OS-level error strings (`ENOSPC`,
`EROFS`) — not window titles. The `run_command` stdout/stderr concern involves tool diagnostics
(X11 display strings, rendering status lines), not window title metadata retrieved from the OS.
The relative-path PowerShell issue involves only the output directory path. No new code paths that
could expose window titles were identified.

---

### UX

**[low] README "Named window → clipboard" example works on macOS but crashes with an opaque error on Linux X11**
`README.md:122–126`, `capture_screenshot.py:288–296, 499–502`
The README presents "Screenshot the Figma window and copy it to my clipboard" as a working usage
example. On macOS this works correctly. On Linux X11 with `xdotool` + ImageMagick `import` (the
only currently supported named-window capture backend on X11), the 2026-06-10 high-severity bug
applies: `plan_capture` returns commands containing `"{output}"` placeholders, but `execute_plan`'s
clipboard branch (line 499) calls `run_command(command)` without the `output` argument, causing
an immediate exit with "internal error: missing output path" (exit 64). The feature as documented
is not functional on that platform. A user on a minimal, GNOME-free X11 desktop who follows this
example will receive the opaque internal error with no hint that named-window clipboard capture is
unsupported on their setup.
_Suggested fix:_ Add a platform qualification to the README example noting that named-window
clipboard capture is supported on macOS and Windows but not on Linux X11 at present. Alternatively,
fix the underlying 2026-06-10 bug (return `CapturePlan(False, "unsupported_linux_x11_window_clipboard", …)`)
and update the README once the fix is in place.

**[info] `CONTRIBUTING.md` test-data schema omits the `capturable` and `state` fields**
`CONTRIBUTING.md:77`, `capture_screenshot.py:_test_windows(), resolve_macos_window_ids()`
The CONTRIBUTING.md table documents `CAPTURE_SCREENSHOT_TEST_WINDOWS` entries as having fields
`{"id": int, "owner": str, "title": str}`. The actual implementation also handles `"capturable":
bool` (defaults to `true`; set `false` to simulate a minimized or off-Space window) and `"state":
str` (e.g., `"minimized"`, `"offscreen"`, `"unknown"`) used in `not_capturable_message`. Tests in
`test_capture_screenshot.py` depend on both fields (lines 99, 126, 149, 164, 236). A contributor
writing a new test for minimized-window behaviour using only CONTRIBUTING.md as a reference would
not discover these fields. The 2026-06-17 info entry noted a different CONTRIBUTING.md inaccuracy
(the `plan_capture` code snippet omitting the `XDG_SESSION_TYPE` fallback); this finding is a
distinct, additional omission in the test-data documentation.
_Suggested fix:_ Extend the CONTRIBUTING.md table entry to document the full schema:
`{"id": int, "owner": str, "title": str, "capturable": bool (default true), "state": str (optional, e.g. "minimized" / "offscreen" / "unknown")}`.

---

## 2026-06-20

### Security

**[low] `Copy-Rectangle` leaks GDI `$bitmap` if `Graphics::FromImage()` raises before the `try` block is entered**
`capture_screenshot.ps1:238-246`
The pattern is identical to the `Copy-Window` finding from 2026-06-18 but at a different function.
`$bitmap` is allocated unconditionally on line 238, then `[Drawing.Graphics]::FromImage($bitmap)` is
called. If `FromImage` raises (e.g., GDI handle exhaustion on a resource-constrained host), the `try`
block is never entered, so neither `$graphics.Dispose()` (in the inner `finally`) nor any cleanup for
`$bitmap` runs. The caller (`Capture-ToDestination`) has a `try/finally { $bitmap.Dispose() }` guard,
but because `Copy-Rectangle` throws rather than returning, the caller's `$bitmap` variable is never
assigned, leaving the allocated `Bitmap` object unreachable for the duration of the process.
_Suggested fix:_ Wrap the `$bitmap` allocation and `FromImage` call in a `try` block with a `catch`
that disposes `$bitmap` and re-throws, or restructure so `$bitmap` is disposed inside the same
`finally` as `$graphics`:
```powershell
$bitmap = [Drawing.Bitmap]::new($Bounds.Width, $Bounds.Height)
try {
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try { $graphics.CopyFromScreen(...); return $bitmap }
    finally { $graphics.Dispose() }
} catch { $bitmap.Dispose(); throw }
```

**[low] `CopyFromScreen` in `Copy-Rectangle` can throw `Win32Exception` on display-unavailable sessions with no structured handling**
`capture_screenshot.ps1:241`
`$graphics.CopyFromScreen(...)` calls the GDI `BitBlt` API internally. In environments where no
physical or virtual display frame-buffer is accessible — Remote Desktop sessions with GPU
acceleration disabled, Citrix ICA sessions, Windows Server Core without a display driver, or
headless CI runners — `CopyFromScreen` raises `System.ComponentModel.Win32Exception`. Because
`$ErrorActionPreference = 'Stop'` is set globally, this becomes a terminating error and the script
exits with code 1 and a raw .NET exception trace. There is no equivalent to the `Test-BitmapAllBlack`
advisory path that exists for `PrintWindow`. The `--target fullscreen` and `--target active` code
paths both route through `Copy-Rectangle` (the former always, the latter as the `Copy-Window` screen
fallback), so both are affected.
_Suggested fix:_ Wrap the `CopyFromScreen` call in `try/catch [System.ComponentModel.Win32Exception]`
and emit `[Console]::Error.WriteLine("window_not_capturable: screen buffer is unavailable in this
session — try running in a session with an active display"); exit 75` to give a structured, actionable
error code matching the documented exit table.

**[info] `_validate_integer_ids` uses `str.isdigit()` rather than `str.isdecimal()`, accepting non-decimal Unicode digit characters**
`capture_screenshot.py:83`
```python
if not id_str.isdigit():
```
Python's `str.isdigit()` returns `True` for superscript and subscript digits (`²`, `³`, `⁴` …),
Roman numeral digits, and other Unicode characters classified as "digit" but not "decimal" (e.g.,
`"²".isdigit()` is `True`, `"²".isdecimal()` is `False`). In practice the macOS
`CGWindowListCopyWindowInfo` helper and xdotool exclusively emit ASCII decimal strings, so this
cannot be exploited; but the validation is technically wider than intended and would silently accept
a non-decimal digit string that would then fail at the OS API call site (e.g., `screencapture -l
²`). Using `str.isdecimal()` or `re.fullmatch(r'[0-9]+', id_str)` would express the intended
constraint precisely.
_Suggested fix:_ Replace `id_str.isdigit()` with `id_str.isdecimal()` (or an explicit ASCII-digit
regex) so the guard matches exactly the set of strings that are valid decimal window IDs.

**[info] Test-override environment variables have no production-mode guard**
`capture_screenshot.py:302-322` (`_test_platform`, `_test_windows`)
`CAPTURE_SCREENSHOT_TEST_PLATFORM` and `CAPTURE_SCREENSHOT_TEST_WINDOWS` are read
unconditionally from the environment with no check that the process is running in a test context.
If either variable is inadvertently set in a production or agent-pipeline environment — for example,
leaked from a CI step that did not clean up its exported variables, or set by a co-process sharing
the same environment — the script silently redirects platform detection or window resolution to
mock values without any warning. A consumer (human or agent) receives a plausible success but the
capture was performed against synthetic data. There is no `--no-test-overrides` flag or similar
explicit opt-in to test mode.
_Suggested fix:_ Add a note to the module docstring and to SKILL.md warning that these variables
must never be set in production. Alternatively, gate their use on an explicit `--test-mode` flag
or on the presence of both variables together, so a single stray variable cannot silently alter
behaviour.

---

### Bugs & regressions

**[low] Linux `--target window --allow-multiple-matches` in dry-run does not exercise the xdotool + xprop label-deduplication chain in any integration test**
`tests/test_capture_screenshot.py`
`test_prepare_output_paths_suffixes_duplicate_labels_in_one_request` (line 53) verifies filename
deduplication in isolation, and `test_linux_viewable_window_passes_through` (line 198) exercises
`resolve_linux_named_window` with a fake xdotool. However, no integration test wires these together:
no test runs the full script subprocess with `CAPTURE_SCREENSHOT_TEST_PLATFORM=Linux`, fake xdotool
returning two IDs, fake xprop marking both viewable, `--allow-multiple-matches`, and
`--destination desktop --dry-run`, then asserts that two distinct output paths are printed on
separate lines. A regression in the `labels.extend([sanitize_label(query)] * len(resolution.ids))`
→ `prepare_output_paths(labels)` → `reserved` set deduplication chain would not be caught. The
analogous macOS gap was documented in 2026-06-15 (info); this finding is the distinct Linux path.
_Suggested fix:_ Add an integration test using `_write_fake_tool` (following the pattern of
`test_linux_viewable_window_passes_through`) for both xdotool (printing two IDs) and xprop
(printing `window state: Normal` for each), then run the script subprocess with
`--target window --allow-multiple-matches --destination desktop --dry-run --query Calculator`
and assert two distinct `.png` paths are printed.

---

### Data leaks

No new findings. The `CopyFromScreen` exception path (above) surfaces only a .NET Win32Exception
message containing an OS error code and a static description — no window title metadata. The
`Copy-Rectangle` GDI leak involves only pixel data in kernel-managed GDI memory, inaccessible to
other processes. The `.isdigit()` widening and env-var guard gap involve no user data exposure.
All previously documented title-privacy invariants continue to hold across all three platform paths.

---

### UX

**[low] `CopyFromScreen` display-unavailable failure gives no actionable guidance (UX dimension of the Security finding above)**
`capture_screenshot.ps1:241`
A user running `--target fullscreen` or `--target active` in a Remote Desktop, Citrix, or headless
Windows session receives exit code 1 and a multi-line .NET exception stack trace. The trace
(`System.ComponentModel.Win32Exception: The handle is invalid`) gives no hint that the fix is to
use a locally attached console session or to bring the session to the foreground. The `PrintWindow`
path at least has `Test-BitmapAllBlack` that surfaces a user-readable warning; `CopyFromScreen` has
no equivalent.
_Suggested fix:_ Same as the Security entry above — catch `Win32Exception` around `CopyFromScreen`
and emit a structured `window_not_capturable` message with display-session guidance before exiting
with code 75.

---

## 2026-06-21

### Security

**[low] `install.sh` TOCTOU between existence checks and `git clone` in `clone_if_missing`**
`install.sh:15–29`
The three sequential existence guards (`[ -L "$dest" ]`, `[ -d "$dest" ]`, `[ -e "$dest" ]`) and
the subsequent `git clone "$REPO" "$dest"` are not atomic. On a shared machine, a local attacker
with write access to the parent skills directory (`$HOME/.claude/skills/`, etc.) could place a
symlink at `$dest` in the window between `[ -e "$dest" ]` returning false and `git clone`
executing. `git clone` follows the symlink and writes repository files into the symlink target
(an attacker-chosen directory) rather than the intended skills location. Exploitation requires
precise timing but no elevated privileges. This is the install-time analogue of the 2026-06-08
`ensure_private_directory` TOCTOU and the 2026-06-13 `os.replace` symlink findings.
_Suggested fix:_ After a successful `git clone`, add a post-clone symlink guard:
`[ -L "$dest" ] && { echo "error: $dest is a symlink after clone — aborting"; exit 1; }`.
Alternatively, note the residual race in a comment for shared-machine deployments.

---

### Bugs & regressions

**[medium] `_linux_window_is_viewable` xprop path misclassifies windows on other virtual desktops as capturable**
`capture_screenshot.py:374–383`
`xprop -id N WM_STATE` reports `Window state: Normal` for windows that reside on other EWMH
virtual desktops in common window managers (Openbox, XFWM, Mutter, i3). Such windows are not
minimized (ICCCM Iconic state), so the check `"iconic" not in proc.stdout.lower()` returns
`True` (capturable). Yet these windows are unmapped from the current display; ImageMagick
`import -window <id>` called on them typically returns a black or stale cached image. The
`xwininfo` fallback (lines 380–383) correctly uses `Map State: IsViewable`, which is `False`
for unmapped off-desktop windows. However, because `xprop` is checked first (line 374:
`if xprop:`) and its non-None result is returned immediately without falling through to
`xwininfo`, systems where xprop is available silently receive an incorrect capture instead of
a `window_not_capturable` error — even when xwininfo is also installed and would have
identified the window as non-viewable.
_Suggested fix:_ After the xprop WM_STATE check returns a "Normal" (non-Iconic) result, also
query `_NET_WM_STATE` via `xprop -id N _NET_WM_STATE` and return `False` if `_NET_WM_STATE_HIDDEN`
is present. Alternatively, always fall through to xwininfo as a cross-check whenever xprop
returns "Normal", rather than short-circuiting. Add a test with a fake xprop emitting
`window state: Normal` and a fake xwininfo emitting `Map State: IsUnMapped` to confirm the
combined path returns `False`.

**[low] `prepare_output_paths` creates the timestamped request directory before `plan_capture` runs, leaving empty directories on failed fullscreen and active-window captures**
`capture_screenshot.py:623–633` (`main()`), `capture_screenshot.py:420–434` (`prepare_output_paths`)
In `main()`, `prepare_output_paths(..., create=not args.dry_run)` is called before
`plan_capture()`. For `--target fullscreen` (label `["screen"]` set at line 604) and for
`--target active` on Linux where resolution succeeds but the tool check in `plan_capture`
subsequently fails (e.g., no gnome-screenshot on a headless session), the request directory
(`~/Desktop/screenshots/MM_DD_YYYY_HH_MM_SS/`) is created and secured on disk before the
missing-tool error is returned. `execute_plan` then calls `die()`, leaving behind an empty
timestamped directory. On systems where screenshot tools are absent or transiently unavailable,
repeated failed attempts accumulate empty directories with no indication that cleanup is needed.
For `--target window` the window resolution runs first and exits early on failure, so that
path is less exposed; the gap mainly affects fullscreen captures on tool-absent systems.
_Suggested fix:_ Move the `prepare_output_paths` call to after `plan_capture` returns a
successful plan (i.e., `plan.ok` is True), so directories are only created when a capture is
certain to proceed. Alternatively, clean up the request directory in `execute_plan`'s error
path: if `not plan.ok` and the request dir was just created and is empty, remove it before
calling `die()`.

**[low] `Capture-ToDestination` `finally { $bitmap.Dispose() }` references an unset `$bitmap` when `Copy-Window` or `Copy-Rectangle` throws before returning**
`capture_screenshot.ps1:295–349`
`$bitmap` is assigned by calling `Copy-Window` or `Copy-Rectangle` (lines 309 and 314). If
either function throws before returning — for example, from `[Drawing.Graphics]::FromImage` or
`[Drawing.Bitmap]::new` as documented in the 2026-06-18 (`Copy-Window`) and 2026-06-20
(`Copy-Rectangle`) GDI-leak findings — `$bitmap` is never assigned in `Capture-ToDestination`'s
scope. The outer `try { ... } finally { $bitmap.Dispose() }` block then executes its `finally`
clause with `$bitmap` unset. Under `Set-StrictMode -Version Latest`, referencing an unset
variable throws "Variable is not set", which becomes a second terminating error under
`$ErrorActionPreference = 'Stop'`. Depending on PowerShell version, this secondary error can
mask the original GDI exception in the error record, making the root cause harder to diagnose
in practice. The 2026-06-18 and 2026-06-20 entries documented GDI leaks inside the helper
functions themselves; this finding is the companion issue at the caller.
_Suggested fix:_ Initialize `$bitmap = $null` before the `Copy-Window`/`Copy-Rectangle`
branch and guard the `finally` disposal: `if ($null -ne $bitmap) { $bitmap.Dispose() }`.
This prevents the secondary unset-variable error and makes the cleanup logic explicit regardless
of which allocation path was taken.

---

### Data leaks

No new findings. The `_linux_window_is_viewable` misclassification (above) can cause a silent
capture of an off-desktop window's pixel data, but the script output and filename derive only
from the user's sanitized query — no actual window title is exposed through any output path.
The install.sh TOCTOU involves file system layout only; no screenshot content or window title
metadata is at risk. The `$bitmap` unset-variable issue involves only GDI pixel memory
(inaccessible to other processes). All previously documented title-privacy invariants continue
to hold across all three platform paths: error messages echo only user-supplied query text,
`sanitize_label` strips URLs and non-alphanumeric content before embedding labels in paths,
and the macOS helper never prints window titles to stdout or stderr.

---

### UX

**[low] Empty timestamped directories accumulate silently on failed fullscreen captures**
`capture_screenshot.py:623–633`, `capture_screenshot.py:420–434`
(UX dimension of the Bugs entry above.) A user on a system without a supported screenshot tool
who repeatedly attempts `--target fullscreen --destination desktop` receives a
`missing_dependency_fullscreen` error each time, but also silently accumulates a new empty
`~/Desktop/screenshots/MM_DD_YYYY_HH_MM_SS/` directory for every attempt. Nothing in the
error output indicates these stale directories were created or that they need to be cleaned up.
Over many retries (e.g., while installing the missing tool), the screenshots folder fills with
empty timestamped directories.
_Suggested fix:_ Same as the Bugs entry — defer directory creation to after plan validation, or
remove empty request directories in the error exit path.

**[info] `_linux_window_is_viewable` misclassification gives no warning; user receives a black or stale PNG with exit 0**
`capture_screenshot.py:374–383`
(UX dimension of the medium Bug entry above.) When a window on another virtual desktop is
misclassified as capturable and `import -window <id>` is used, the tool exits 0 and writes a
black or stale-content PNG to the output path. The Python script reports success (prints the
path and exits 0). The user has no indication that the captured window was not on the current
desktop and that the image content may be incorrect.
_Suggested fix:_ Same as the Bug entry — cross-check with `xwininfo` Map State or
`_NET_WM_STATE_HIDDEN` before classifying a window as capturable, so a
`window_not_capturable` error is returned rather than a silent incorrect capture.

---

## 2026-06-22

### Security

**[low] macOS fullscreen `screencapture` omits `-x`, playing an audible shutter sound; inconsistent with silent named-window captures**
`capture_screenshot.py:220`
The fullscreen macOS plan is `(screencapture, "{output}")` — no `-x` flag. The named-window
plan (`plan_capture` lines 228–230) and the active-window variant both use
`(screencapture, "-x", "-l", str(window_id), "{output}")`, explicitly suppressing the shutter
sound. On a macOS system where the screenshot sound is enabled (the default), every fullscreen
capture produces an audible click, while named-window and active-window captures are silent. In
an agent-automated pipeline this is unexpected and potentially disruptive. It also reveals the
capture mode to a nearby observer via audio: the presence or absence of the click discloses
whether a fullscreen or a targeted capture was taken — a minor but non-obvious information leak
about capture intent.
_Suggested fix:_ Add `-x` to the fullscreen command: `(screencapture, "-x", "{output}")` and,
for the clipboard variant, `(screencapture, "-x", "-c")`. If retaining the sound for
transparency is a deliberate design choice, apply it consistently to all capture modes and
document the rationale; in its current asymmetric form it creates divergent UX with no clear
intent.

**[low] `Protect-Directory` ACL failure on an externally-owned `$OutputRoot` produces an unstructured terminating error**
`capture_screenshot.ps1:97–112`
`Get-Acl` / `Set-Acl` on a directory owned by a different Windows account raises
`System.UnauthorizedAccessException`. Under `$ErrorActionPreference = 'Stop'` this terminates
the script with a raw .NET exception trace and exit code 1 instead of a structured
`[Console]::Error.WriteLine` / `exit 74`. The scenario is reachable whenever
`capture_screenshot.ps1` is invoked directly (without the Python orchestrator) and
`$OutputRoot` points to a path the caller does not own — more plausible given that the PS
script applies no home-containment check of its own (noted in the 2026-06-08 entry). Combined
with the 2026-06-16 finding that `New-Item` uses `-Path` rather than `-LiteralPath`, a
wildcard-containing `$OutputRoot` could silently create a directory at an unintended location
where the ACL operation then fails.
_Suggested fix:_ Wrap the `Get-Acl` / `Set-Acl` pair in
`try/catch [System.UnauthorizedAccessException]` and emit
`[Console]::Error.WriteLine("screenshots_folder_error: cannot secure permissions on $Path — use a path you own exclusively"); exit 74`.

**[info] `Sanitize-Label` in PowerShell lacks the `or 'capture'` fallback in the truncation branch**
`capture_screenshot.ps1:76–79`
Python's `sanitize_label` (line 74) uses `label[:80].strip("-") or "capture"` — the `or "capture"`
ensures a non-empty return even if all 80 characters are hyphens. PowerShell's `Sanitize-Label`
guards the pre-truncation empty case with `IsNullOrWhiteSpace` and returns `'capture'`, but the
`>80` branch (`$label.Substring(0, 80).Trim('-')`) has no subsequent empty-guard. Under current
sanitization rules — non-alphanumeric characters collapse to a single hyphen, so the label must
contain alphanumeric content to reach 80 characters — an empty result after truncation is
unreachable. The asymmetry is a latent inconsistency: if the regex rules change (for example, to
strip more characters), the PowerShell truncation branch could silently return an empty string
where Python would return `'capture'`.
_Suggested fix:_ `$t = $label.Substring(0, 80).Trim('-'); if ([string]::IsNullOrWhiteSpace($t)) { return 'capture' }; return $t`, matching the Python semantics exactly.

---

### Bugs & regressions

**[medium] `execute_plan` does not validate that the screenshot tool wrote non-empty data before reporting success**
`capture_screenshot.py:507–519`
After `run_command(command, output=temp_output)` returns without raising (exit 0), the code
renames the temp PNG to the final output path and prints the path to stdout — signalling success.
No check is made that the tool actually wrote any bytes. Known cases where a screenshot tool
exits 0 but writes an empty or degenerate file include:
- `screencapture` on certain macOS configurations exits 0 and writes 0 bytes when Screen
  Recording permission is denied. The 2026-06-16 entry documented the `window-helper`
  path returning "no_matching_window" instead of a permission diagnostic; the present finding is
  the downstream capture step, which receives a valid-looking command but cannot obtain pixel data.
- `grim` is documented to exit 0 with a zero-byte file when the Wayland compositor's frame
  callback times out silently.
- `import -window root` exits 0 with a 1×1 white PNG on some headless X11 display configurations.
In all three cases the caller — agent or user script — receives a file path on stdout and exit 0,
but the saved PNG is unusable with no error or warning.
_Suggested fix:_ Immediately after `run_command` returns, assert
`temp_output.stat().st_size > 0`; if the file is empty, call
`die("capture tool wrote no data — check screen recording permissions and display availability", EXIT_UNAVAILABLE)`.
Optionally verify the first 4 bytes match the PNG magic number (`b'\x89PNG'`) to catch
non-empty but corrupt output.

**[low] `install.sh` appends label to `DETECTED` before the skip-guards run, producing a misleading "Already installed" summary when the install was skipped**
`install.sh:11, 14–29`
`clone_if_missing` adds `$label` to `DETECTED` on line 11 — before the symlink guard (line 14),
the existing-directory guard (line 19), and the non-directory guard (line 23). If any guard
triggers a skip-and-return, the label remains in `DETECTED` with nothing in `INSTALLED`. At the
end of the script (lines 55–66), the condition `${#INSTALLED[@]} -gt 0` is false and
`${#DETECTED[@]} -gt 0` is true, so the summary prints
`"Already installed for: Claude Code — nothing to do."` — a false-success message that
directly contradicts any skip-warning the user saw moments earlier. The most impactful case is
the symlink skip: the user sees `"warning: $dest is a symlink — skipping Claude Code"` and then
`"Already installed for: Claude Code — nothing to do."`, which suggests the skill is functional
when in fact it was not installed.
_Suggested fix:_ Move `DETECTED+=("$label")` to after the skip guards — only add the label when
the destination is a valid real directory (either pre-existing or newly cloned). Introduce a
`SKIPPED` array for symlink/non-dir cases and include it in the final summary so the outcome is
unambiguous.

**[low] 2026-06-13 medium finding — `--query` silently discarded with non-window targets — has not been fixed or regression-tested**
`capture_screenshot.py:main()` (~lines 591–596), `tests/test_capture_screenshot.py`
The 2026-06-13 entry identified that `--query` values are silently ignored when
`--target fullscreen` or `--target active` is used. The suggested fix was to add an early guard:
```python
if args.query and args.target != "window":
    die(f"--query is only valid with --target window (got --target {args.target})", EXIT_USAGE)
```
and a corresponding regression test. As of today neither the guard nor the test has been added.
The silent-discard behaviour — which can cause the user to believe their query was respected
while a broader fullscreen capture proceeded — remains present in `main()`. Given the
privacy-first design goal ("never fall back from `window` or `active` to `fullscreen` without
separate approval"), a user who mistakenly passes `--query Safari --target fullscreen` receives
no warning that their query was ignored.
_Suggested fix:_ Implement the guard and test as described in the 2026-06-13 entry. This is a
carry-over tracking item.

---

### Data leaks

No new findings. The empty-file finding above concerns pixel data absence rather than metadata
leakage — an empty PNG contains no window title or content to expose. The `Protect-Directory`
ACL exception message includes only the directory path (user-supplied `$OutputRoot`), not any
window title. The `install.sh` DETECTED-label issue involves agent product names only. All
previously documented title-privacy invariants continue to hold across all three platform paths:
error messages echo only user-supplied query text (never real window titles from the OS),
`sanitize_label` strips URLs and non-alphanumeric content before embedding labels in paths, and
the macOS C helper never prints window title strings to stdout or stderr.

---

### UX

**[low] `install.sh` symlink-skip warning is immediately contradicted by the final "Already installed" summary**
`install.sh:14–17, 55–66`
(UX dimension of the Bugs entry above.) A user with `~/.claude/skills/capture-screenshot`
symlinked sees:
```
warning: /home/user/.claude/skills/capture-screenshot is a symlink — skipping Claude Code
...
Already installed for: Claude Code — nothing to do.
```
The final line directly contradicts the warning. A user glancing at the summary line would
dismiss the symlink concern and assume everything is working, when in fact the skill may be
pointing at a stale or missing target and failing silently at runtime.
_Suggested fix:_ Same as the Bugs entry — track skipped entries in a `SKIPPED` array, show them
separately in the summary (e.g., `"Skipped (symlink): Claude Code"`), and omit skipped labels
from the "Already installed" or "Done" messages.

**[info] `CONTRIBUTING.md` stale line-number reference for the `detect_tools` call**
`CONTRIBUTING.md:29`
The contributing guide says: "add the tool name to the `detect_tools(...)` call in `main()` (around
line 491)". In the current source the `detect_tools(...)` call sits around line 606. The referenced
line 491 now falls in the middle of `plan_capture`, a different function entirely. A new
contributor following this reference will be inspecting the wrong code section.
_Suggested fix:_ Replace the specific line number with a context description: "find the
`detect_tools(...)` call near the bottom of `main()`, just before the `plan_capture(...)` call."

**[info] Command/output path count mismatch is detected at execution time, not at plan-construction time**
`capture_screenshot.py:505–507`
The guard `if len(plan.commands) != len(output_paths): die("internal error: command/output mismatch", EXIT_USAGE)` runs inside `execute_plan`, after directories have been created and output paths allocated by `prepare_output_paths`. A mismatch — which would be a programming error in `plan_capture` — is therefore only discovered at the moment of execution, not when the plan is validated. The 2026-06-12 entry flagged the fragile `len==2` dispatch heuristic; this note extends it to the general observation that no structural validation of the plan is performed between `plan_capture` returning and `execute_plan` running. On a failed plan (`plan.ok == False`) this is immaterial since `execute_plan` dies immediately; the concern is valid plans where the command and output counts are coherent but diverge after a future refactor.
_Suggested fix:_ Assert `len(plan.commands) == len(output_paths)` immediately after `prepare_output_paths` returns (in `main()`), before calling `execute_plan`. This surfaces the invariant at the right abstraction level and keeps `execute_plan`'s guard as a belt-and-suspenders runtime check rather than the sole detection point.

---

## 2026-06-25

### Security

**[medium] PowerShell parameter injection via `--query` values beginning with `-`**
`capture_screenshot.py:539–540` (`_run_powershell_script`)
`_run_powershell_script` constructs the PowerShell invocation by appending pairs of
`"-Query", q` for each user-supplied query:
```python
for q in args.query:
    cmd += ["-Query", q]
```
When a query value begins with `-` followed by a name that matches a declared `param()` entry in
`capture_screenshot.ps1` — such as `"-DryRun"`, `"-AllowMultipleMatches"`, or `"-OutputRoot"` —
PowerShell's `pwsh -File` parameter binder treats the token as a named switch rather than a value
for `$Query`. Concretely:
- `--query "-DryRun"` → PowerShell sees `-Query` with no value (PowerShell stops before `-DryRun`
  since it recognises it as a switch) and then sets `$DryRun = $true`. The script prints
  destination paths without capturing any pixels, exits 0, and Python's `main()` propagates the
  zero exit code — the caller (agent or shell script) receives a success signal with no screenshot
  having been taken.
- `--query "-AllowMultipleMatches"` → `$Query = @()` and `$AllowMultipleMatches = $true`. Because
  `$Query` is now empty, the `if ($Query.Count -eq 0)` guard fires and the script throws "window
  target requires at least one query" — exit 1. No capture occurs, but the flag is activated
  without the user opting in via Python's `--allow-multiple-matches`.
- `--query "-OutputRoot"` → `$Query = @()` and the subsequent token (`str(args.output_root)`)
  that Python placed after the loop as the real `-OutputRoot` value is consumed by PowerShell as the
  value for this injected `-OutputRoot`, potentially binding the output root twice to the same
  value — harmless but surprising. With additional careful crafting, a caller could cause the real
  `-OutputRoot` argument to be parsed as a positional (and lost).
An AI agent that passes a user-supplied window query verbatim to `--query` could be manipulated:
a user who names their window (or fabricates a query like) `"-DryRun"` would cause a silent
no-capture with exit 0 while the agent believes the screenshot succeeded. Python's consent and
output-root validation run before `_run_powershell_script`, so those guards are unaffected, but the
per-capture behaviour of the PowerShell script is subvertable.
_Suggested fix:_ In `parse_args` or early in `main()`, reject any `--query` value that begins with
`-`:
```python
for q in args.query:
    if q.startswith('-'):
        die(f"--query value must not begin with '-': {q!r}", EXIT_USAGE)
```
Add a corresponding test asserting `EXIT_USAGE` when `--query "-DryRun"` is passed. Alternatively,
pass queries to PowerShell using a positional-array workaround that avoids `-Query` named binding,
though this requires restructuring the PowerShell `param()` block.

---

### Bugs & regressions

**[low] `resolve_macos_with_helper` exit-code-4 stderr token extraction relies on an undocumented implicit "last-line" convention**
`capture_screenshot.py:359–364`
When the macOS helper exits with code 4 (window present but not capturable), Python reads the
reason token from the helper's stderr:
```python
if proc.returncode == 4:
    token = ""
    if proc.stderr and proc.stderr.strip():
        token = proc.stderr.strip().splitlines()[-1].strip()
    return ResolutionResult(False, "window_not_capturable", not_capturable_message(query, token))
```
The "last line" heuristic is an undocumented implicit contract between `find_macos_window_id.m`
(which currently emits exactly one line: `"unknown\n"` for exit code 4) and the Python caller.
If a future change to the C helper adds a diagnostic line before or after the reason token —
for example, for new error subtypes or debug output — `splitlines()[-1]` would silently consume
the wrong line. The result would be `not_capturable_message` receiving a garbled or empty `state`
token, falling through to the generic "exists but cannot be captured" message rather than the
more specific minimized/offscreen variant. No data is leaked (the token never appears in output),
but the user-facing diagnostic message silently degrades.
_Suggested fix:_ Document the one-line-on-stderr protocol explicitly in both the C source (a
comment at the `fprintf(stderr, "unknown\n")` call site) and the Python function (a code comment
at the `splitlines()[-1]` extraction). Alternatively, emit the reason token on a dedicated prefix
(e.g., `"reason: unknown\n"`) and parse it with a regex in Python, making the contract explicit
and immune to additional log lines from either side.

**[high, carry-over] Linux X11 named-window clipboard capture crashes with "internal error: missing output path"**
`capture_screenshot.py:288–296, 499–502`
First reported 2026-06-10. `plan_capture` emits `{output}` placeholders for the X11 named-window
clipboard path; `execute_plan`'s clipboard branch calls `run_command(command)` without `output=`,
triggering the immediate `die()`. Unresolved as of main branch at this review.

**[medium, carry-over] `--query` silently discarded when `--target` is `fullscreen` or `active`**
`capture_screenshot.py:main()` (~lines 596–605)
First reported 2026-06-13. No guard has been added; the silent-discard behaviour that contradicts
the privacy-first design goal remains present. Unresolved as of main branch at this review.

---

### Data leaks

No new findings. Window title isolation continues to hold on all three platforms. The PowerShell
parameter-injection finding above (Security) can suppress a capture or activate flags, but the
activation of `$DryRun` or `$AllowMultipleMatches` does not expose any window title metadata —
the script produces no output or only synthesised path strings derived from the sanitized query.
All previously documented title-privacy invariants continue to hold in the reviewed code.

---

### UX

**[info] macOS exit-code-4 reason-token fragility degrades user-facing diagnostic messages silently**
`capture_screenshot.py:359–364`
(UX dimension of the Bugs finding above.) If the "last-line" implicit protocol between
`find_macos_window_id.m` and `resolve_macos_with_helper` breaks due to an added diagnostic line
in the helper, the user receives the generic `"'<query>' exists but cannot be captured (minimized
or off-screen) — restore it and retry."` message instead of the more actionable `"'<query>' is
minimized — restore it and retry."`. The degradation is silent: the script still exits with
`EXIT_NOT_CAPTURABLE (75)`, so callers see the correct code, but the human-readable hint loses
its specificity. The helper currently produces only the one-line output mandated by the protocol,
so this is a latent rather than active UX regression.
_Suggested fix:_ Same as the Bugs entry — formalise the protocol with a structured prefix or an
explicit comment, so future contributors know the last-line constraint and preserve it.

**[info] Carry-over: `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review. No new technical information.

**[info] Carry-over: PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review. No new technical information.
## 2026-06-23

### Security

**[medium] `capture_screenshot.ps1` has no home-directory containment check for `-OutputRoot`**
`scripts/capture_screenshot.ps1:6` (the `-OutputRoot` parameter)
The Python orchestrator enforces `_validate_output_root(args.output_root)` at
`capture_screenshot.py:549` before delegating to PowerShell, so the guard runs when
`capture_screenshot.py` is the entry point. However, nothing prevents a caller from
invoking `capture_screenshot.ps1` directly (e.g., in a script or CI pipeline) with an
arbitrary `-OutputRoot` value such as `C:\Windows\System32\screenshots`,
`\\\\server\\share\\exfil`, or any path outside the user profile. The PS1 script will
create and ACL-protect whatever path is supplied — writing screenshots to unintended
locations including network shares or system directories without raising an error.
_Suggested fix:_ After the consent gate in the PS1 script, add a guard that resolves
`$OutputRoot` and asserts it starts with
`[Environment]::GetFolderPath('UserProfile')`, mirroring Python's `_validate_output_root`.
Emit a structured error message and `exit 64` on violation.

**[low] Windows `Protect-Directory` leaves a new directory world-accessible between `New-Item` and `Set-Acl`**
`scripts/capture_screenshot.ps1:58–81` (`Protect-Directory`)
When the destination directory does not yet exist, `New-Item -ItemType Directory`
creates it with ACLs inherited from the parent folder — typically allowing SYSTEM,
Administrators, and the current user. `Get-Acl` and `Set-Acl` are called immediately
after, but there is a brief TOCTOU window between directory creation and ACL hardening
where another local user or process could write files into the new directory. On a
shared workstation this is exploitable in principle: a racing process could plant a
file inside the screenshots folder before the owner-only ACL is applied.
_Suggested fix:_ Pass a pre-built `DirectorySecurity` object to
`[System.IO.Directory]::CreateDirectory(path, directorySecurity)` instead of calling
`New-Item` followed by a separate `Set-Acl`, setting the owner-only ACL atomically at
creation time.

**[low] Clipboard pipeline temp file is created in world-searchable `/tmp` rather than a secured private directory**
`scripts/capture_screenshot.py` (`execute_plan`, ~line 484–490)
When `destination == "clipboard"` and a two-command pipeline is selected (e.g.,
`grim` + `wl-copy` on Wayland, or `import` + `xclip`/`xsel` on X11),
`execute_plan` creates `tempfile.NamedTemporaryFile(prefix="capture-screenshot.", suffix=".png")`
in the system temp directory (`/tmp` or `$TMPDIR`). Python creates this file with
mode `0o600`, so its contents are protected, but the filename — including the
`capture-screenshot.` prefix — is visible to any local user via `ls /tmp` or
`inotify` watchers for the brief window (typically < 1 s) while the screenshot is
written and read. This allows other users on a shared machine to detect that a
screenshot capture occurred and approximately when. All other temp files use
`private_temp_png()`, which places them inside the `0o700`-protected per-request
output directory where even the filename is not visible to other users. The
clipboard-pipeline branch is the only path that deviates from this pattern.
_Suggested fix:_ Create the temp file inside a private directory under
`~/.cache/capture-screenshot/` (Linux) or `~/Library/Caches/capture-screenshot/`
(macOS) with mode `0o700`, rather than in `/tmp`.

---

### Bugs & regressions

**[medium] Windows dry-run with `--allow-multiple-matches` reports duplicate output paths when the same label matches multiple windows**
`scripts/capture_screenshot.ps1` (`New-CapturePath`, `Capture-ToDestination`)
`New-CapturePath` prevents filename collisions by checking `Test-Path -LiteralPath $candidate`
on the filesystem. In dry-run mode, `$script:RequestFolder` is set to
`Get-RequestFolderPath` (which never creates the folder), so `Test-Path` always
returns `$false` for every candidate. Multiple calls to `Capture-ToDestination` with
the same `$Label` (e.g., three Chrome windows matched by `--allow-multiple-matches`)
each return `chrome.png`, `chrome.png`, `chrome.png` — identical paths — because the
filesystem has nothing to reserve against. The Python side avoids this by passing a
`reserved` set through `unique_capture_path` / `prepare_output_paths`, which performs
in-memory reservation regardless of filesystem state.
_Suggested fix:_ Maintain a `$script:dryRunReserved` `HashSet[string]` initialized
before the per-query loop. Extend `New-CapturePath` with an optional `-Reserved`
parameter (or check the script-scoped set directly) to skip candidates already
reserved, matching Python's `reserved` set semantics.

**[low] `find_macos_window_id.m` silently accepts multiple positional arguments, using only the last**
`scripts/find_macos_window_id.m:42–52` (argument-parsing loop)
The `else` branch of the argument loop unconditionally assigns `argv[i]` to
`query_arg`, overwriting any previously stored value. If two positional arguments are
passed (e.g., due to a future Python refactor that mistakenly passes an extra token),
the first is silently discarded and the helper runs against the second without any
error or warning. Since the helper is called from Python with controlled arguments
this is not currently exploitable, but the silent-overwrite behaviour makes the helper
fragile to caller-side changes.
_Suggested fix:_ Track positional argument count. After the loop, if
`!frontmost && positional_count > 1`, print a usage error to `stderr` and `return 64`.

---

### Data leaks

No new findings. Window titles continue to be fully protected across all platform
paths. Error and warning messages echo only user-supplied query text, never
OS-reported titles. `sanitize_label` strips URLs and non-alphanumeric content before
embedding labels in paths. The macOS C helper never writes window title strings to
stdout or stderr. The clipboard pipeline temp-file finding above (Security) concerns
metadata visibility (filename only), not screenshot pixel data or window title content.

---

### UX

**[info] `Test-BitmapAllBlack` uses `GetPixel()` per sample — measurably slow on high-DPI displays**
`scripts/capture_screenshot.ps1` (`Test-BitmapAllBlack`)
`$Bitmap.GetPixel($x, $y)` acquires and releases the bitmap's internal lock on every
call. With a 16-division grid the function makes up to ~256 `GetPixel` calls. On a
4K (3840×2160) display each call traverses GDI+'s managed/native boundary; benchmarks
show 50–200 ms of overhead on lower-end hardware, delaying the printed confirmation
or clipboard write.
_Suggested fix:_ Replace the per-pixel loop with a single `LockBits` +
`Marshal.Copy` call to snapshot the pixel array into a `byte[]`, then index the flat
array. This reduces lock overhead from O(samples) to O(1).

**[info] Carry-over: `--query` silently discarded with non-window targets remains unresolved (first reported 2026-06-13)**
`scripts/capture_screenshot.py` (`main()`, fullscreen/active branches)
The guard proposed in the 2026-06-13 entry — reject `--query` when `--target` is
`fullscreen` or `active` — has not been implemented as of this review. The
silent-discard behaviour remains. No new technical information; carried forward as a
tracking note.

## 2026-06-24

### Security

**[low] `resolve_macos_with_helper` does not catch `subprocess.CalledProcessError` from clang compilation**
`scripts/capture_screenshot.py` (`resolve_macos_with_helper`, the `subprocess.run([clang, ...], check=True)` call)
If `clang` exits non-zero (e.g., missing Xcode Command Line Tools, source file edited
with a syntax error since install), an unhandled `subprocess.CalledProcessError`
propagates to the top level. Python prints a full stack trace to stderr that reveals:
the absolute path to the skill's installation directory (embedded in the
`str(helper_source)` argument) and the full compile command. This is inconsistent with
the codebase's `die()` error-handling pattern and produces an opaque, unhelpful error
message for end users rather than an actionable one. First noted in 2026-06-09/21;
the fix has not been applied as of the current `main` branch.
_Suggested fix:_ Wrap the `subprocess.run([clang, ...], check=True)` call in
`try/except subprocess.CalledProcessError` and call
`die("macOS window helper failed to compile; ensure Xcode Command Line Tools are installed", EXIT_UNAVAILABLE)`.

**[low] `window_number()` uses signed `int` / `kCFNumberIntType` for a `uint32_t` CGWindowID**
`scripts/find_macos_window_id.m:30-33` (`window_number` function)
`CGWindowID` is a `uint32_t`, but `CFNumberGetValue` is called with `kCFNumberIntType`
(C `int`, signed 32-bit on all Apple platforms) and the result stored in `int number`.
A window ID larger than `INT_MAX` (2,147,483,647) would silently produce a negative
value. The negative integer is then printed via `printf("%d\n", number)`;
`_validate_integer_ids` in Python would reject it (the minus sign makes `.isdigit()`
return False) and `die` with `EXIT_USAGE` — a confusing failure unrelated to what the
user asked for. In practice, sequentially assigned CGWindowIDs do not reach this range
under normal use, but the type mismatch is structurally wrong and noted as early as
2026-06-11 without being resolved.
_Suggested fix:_ Declare `number` as `uint32_t`, use `kCFNumberSInt32Type`, and emit
`printf("%u\n", number)`.

**[info] `Get-WindowTitle` uses a fixed 1024-character `StringBuilder` buffer**
`scripts/capture_screenshot.ps1:127-135` (`Get-WindowTitle` function)
`GetWindowText` is called with a `StringBuilder` capacity of 1024. The Windows API
supports window titles up to 32767 characters; titles longer than 1023 characters are
silently truncated. A user query that matches only in the truncated portion of a long
title would produce a false negative (window not found). No realistic window title
approaches this length today, but the truncation is silent and the Windows
documentation makes no guarantee about third-party application title lengths.
_Suggested fix:_ Call `GetWindowText($Handle, $null, 0)` first to query the required
character count, then allocate `[Text.StringBuilder]::new($requiredLength + 1)` before
the real call.

---

### Bugs & regressions

**[info] `grim`+`wl-copy` clipboard plan has dead args in `commands[1]`; inconsistent with `import`+clipboard convention**
`scripts/capture_screenshot.py:plan_capture` (Linux Wayland fullscreen clipboard branch)
For the `grim`+`wl-copy` pipeline, `plan_capture` returns
`commands[1] = (wl_copy, "--type", "image/png")`. In `execute_plan`, only
`plan.commands[1][0]` (the binary path) is consumed — the trailing `"--type"` and
`"image/png"` args are never forwarded to any subprocess. `copy_file_to_clipboard`
constructs its own `["wl-copy", "--type", "image/png"]` invocation independently.
By contrast, the `import`+`xclip`/`xsel` pipeline correctly sets `commands[1]` to
just `(clip,)`. The trailing args in the `grim`+`wl-copy` plan are dead code; a
future maintainer extending `execute_plan` to pass `commands[1]` directly to
`subprocess.run` would unintentionally duplicate the `--type image/png` flags.
_Suggested fix:_ Trim the `grim`+`wl-copy` plan entry to `(wl_copy,)` to match the
`import`+clipboard convention, or add a comment to `execute_plan` documenting that
`commands[1]` is a tool-path sentinel rather than a full subprocess argv.

**[low] `Find-WindowHandles` calls `Get-ProcessNameForWindow` unconditionally for every visible window**
`scripts/capture_screenshot.ps1` (`Find-WindowHandles`, the `Get-ProcessNameForWindow`
call inside `EnumWindowsProc`)
`Get-ProcessNameForWindow` invokes `Get-Process -Id $pid` for every visible top-level
window during enumeration, including transient system UI elements (tooltips, IME
candidates, `SysShadow` windows) that will never match the user's query. On systems
with process-creation auditing enabled (Windows Security Event Log, Sysmon), each
`Get-Process` call may generate audit noise. On a busy desktop the enumeration
performs O(N) process-name lookups per screenshot invocation. The process-name lookup
is a secondary match signal; calling it unconditionally wastes resources on
non-matching windows.
_Suggested fix:_ Short-circuit: only invoke `Get-ProcessNameForWindow` when the title
check (`$title.IndexOf($Needle, ...)`) returns -1. A title match is sufficient to add
the handle; avoiding the process lookup for title-matching windows reduces both
latency and potential audit-log noise.

---

### Data leaks

No new findings. Window titles continue to be excluded from all stdout/stderr output
on all platforms. The macOS C helper writes only integer window IDs to stdout, never
title strings. PS1 error and warning messages use the caller-supplied `$queryText`
(the user's search string), not the OS-reported window title. `sanitize_label` strips
URLs and non-alphanumeric content before embedding the query in output file paths.

---

### UX

**[info] macOS helper binary is recompiled from source on every capture invocation**
`scripts/capture_screenshot.py` (`resolve_macos_with_helper`)
`resolve_macos_with_helper` compiles `find_macos_window_id.m` with `clang` inside a
fresh `TemporaryDirectory` on every named-window or active-window capture on macOS.
Clang typically takes 0.3–1 s for this translation unit on a modern Mac. The compiled
binary is discarded immediately after use. This adds a perceptible, unnecessary delay
to every macOS window capture that has no equivalent on the Linux path (which calls
pre-installed system tools directly).
_Suggested fix:_ Cache the compiled binary at, e.g.,
`~/.cache/capture-screenshot/find_macos_window_id`. On each call, compare the source
file's mtime (or a SHA-256 hash) against a stored value; only recompile when the
source has changed. Fall back to recompilation if the cache directory cannot be
created or the cached binary is not executable.

**[info] Carry-over: `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review. No new technical information.

**[info] Carry-over: PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review. No new technical information.

---

## 2026-06-26

### Security

**[medium, carry-over] PowerShell parameter injection via `--query` values beginning with `-`**
`capture_screenshot.py:539–540` (`_run_powershell_script`)
First reported 2026-06-25. No fix has been applied: `parse_args` still imposes no constraint on query values that begin with `-`, and no regression test has been added. A query value such as `"-DryRun"` causes PowerShell's `pwsh -File` parameter binder to activate `$DryRun` silently, causing the script to print destination paths and exit 0 without capturing any pixels. The calling agent or shell script receives a success signal with no screenshot taken.
_Suggested fix:_ Add a validation loop in `main()` (or `parse_args`) before `_run_powershell_script` is called:
```python
for q in args.query:
    if q.startswith('-'):
        die(f"--query value must not begin with '-': {q!r}", EXIT_USAGE)
```
Add a corresponding integration test asserting `EXIT_USAGE` when `--query "-DryRun"` is passed with `CAPTURE_SCREENSHOT_TEST_PLATFORM=Windows`.

**[low] Compiled macOS helper source is not integrity-checked before execution**
`capture_screenshot.py:325–365` (`resolve_macos_with_helper`), `scripts/find_macos_window_id.m`
`resolve_macos_with_helper` compiles `find_macos_window_id.m` from the skill installation directory (`skill_dir / "scripts" / "find_macos_window_id.m"`) on every named-window or active-window capture. No checksum or signature of the source file is verified before compilation. If an attacker has write access to `~/.claude/skills/capture-screenshot/scripts/` — for example, via a misconfigured group-write bit on the skills directory, or via another process running as the same user — they can modify `find_macos_window_id.m`. The next capture invocation compiles and executes the modified source under the calling user's account. This requires same-user or root access and has no privilege-escalation potential on a single-user system, but is worth documenting as a trust-boundary concern. The 2026-06-24 entry noted the unhandled `CalledProcessError` from a failed compile; this finding is the pre-compilation attack surface.
_Suggested fix:_ Add `chmod go-w scripts/find_macos_window_id.m` (and the scripts directory) to `install.sh` after `git clone`, ensuring only the owner can modify the source. Document the trust boundary in SKILL.md. Alternatively, compute a SHA-256 hash of the source at install time and verify it in `resolve_macos_with_helper` before invoking `clang`.

---

### Bugs & regressions

**[high, carry-over] Linux X11 named-window clipboard crashes with "internal error: missing output path"**
`capture_screenshot.py:288–296, 499–502`
First reported 2026-06-10. `plan_capture` for the X11 named-window clipboard path still emits `{output}` placeholders; `execute_plan`'s clipboard branch calls `run_command(command)` without `output=`, triggering `die()` with exit 64. Unresolved as of main branch at this review.

**[medium, carry-over] `--query` silently discarded when `--target` is `fullscreen` or `active`**
`capture_screenshot.py:main()` (~lines 591–596)
First reported 2026-06-13. No guard has been added; the silent-discard behaviour that contradicts the privacy-first design goal remains present. Unresolved as of main branch at this review.

**[low] `test_windows_delegates_to_powershell` does not assert `-OutputRoot` forwarding**
`tests/test_capture_screenshot.py:303–326`
The test verifies that `-ConsentConfirmed`, `-Destination`, `-Target`, and `-DryRun` are correctly forwarded to the PowerShell stub, but `-OutputRoot` is absent from the checked list. A regression in `_run_powershell_script` that drops or renames the `-OutputRoot` argument would be silent: PowerShell would fall back to its built-in default path (see the 2026-06-16 finding: on Server Core, `GetFolderPath('Desktop')` returns `""` and the effective root becomes the bare relative string `"screenshots"`), potentially writing screenshots outside the Python-validated home-contained root. The silence is especially concerning because `_validate_output_root` runs on the Python side against `args.output_root` before delegation, so any mismatch between the validated path and the path actually received by PowerShell would go undetected.
_Suggested fix:_ Pass an explicit `--output-root` value in the test subprocess invocation and add `"-OutputRoot"` (plus the value) to the `for expected in ...` assertion loop.

---

### Data leaks

No new findings. All window-title privacy invariants continue to hold across all three platform paths. The macOS helper source-modification attack vector described above (Security) would allow an attacker to run arbitrary code, but the currently shipped `find_macos_window_id.m` never writes window title strings to stdout or stderr — it emits only integer window IDs (stdout) and static reason tokens (`unknown`, `minimized`, `offscreen`) on stderr. Error messages in `capture_screenshot.py` and `capture_screenshot.ps1` continue to echo only user-supplied query text, never OS-reported window titles. `sanitize_label` strips URLs and non-alphanumeric content before embedding any query in output file paths.

---

### UX

**[low] No integration test validates `-OutputRoot` forwarding through the full Windows delegation path**
`tests/test_capture_screenshot.py:303–326`
(Same root cause as the Bugs finding above.) The fake `powershell.exe` in `test_windows_delegates_to_powershell` captures and records all arguments it receives, but the test never asserts on `-OutputRoot`. There is no test that passes a custom `--output-root`, verifies the forwarded `-OutputRoot` value, and confirms that the PowerShell script would use it as the output directory root. A complete end-to-end check is blocked by the need for a real Windows PS environment, but the missing assertion is fixable in the existing fake-shell framework on Linux/macOS CI.
_Suggested fix:_ Same as the Bugs entry — pass `--output-root` explicitly and add it to the checked flags list.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review. No new technical information.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review. No new technical information.

---

## 2026-06-27

### Security

**[low] `resolve_macos_with_helper` does not handle exit code 64 from the macOS helper binary**
`capture_screenshot.py:354–365` (`resolve_macos_with_helper` exit-code dispatch)
`find_macos_window_id.m` exits with code 64 (EX_USAGE) for two cases: (a) called without
`--frontmost` and without a positional query (line 52), and (b) `CFStringCreateWithCString`
returns NULL for a non-UTF-8 query string (line 60). Python's dispatch block only handles
return codes 0, 2, 3, and 4; any other code — including 64 — falls through to
`ResolutionResult(False, "window_query_failed", "Could not query the window list.")`. In
practice code 64 is unreachable from normal Python invocation (Python always passes either
`--frontmost` or the query as a well-formed UTF-8 list element), but a future refactor that
incorrectly assembles `command` would surface an opaque "Could not query the window list"
message instead of an actionable usage-error diagnostic. The incomplete dispatch also makes it
harder to audit the full exit-code contract between the C helper and its Python caller.
_Suggested fix:_ Add `if proc.returncode == 64: return ResolutionResult(False, "window_helper_usage_error", "macOS window helper reported a usage error — check query arguments.")` before the final catch-all return, and document the full exit-code table in a comment at the top of the dispatch block.

---

### Bugs & regressions

**[low] PowerShell dry-run mode evaluates `Get-WindowBounds` before the dry-run guard runs, making `--dry-run` fragile**
`capture_screenshot.ps1:375–376` (active-window path) and `capture_screenshot.ps1:399–401` (named-window loop)
Both capture paths call `Capture-ToDestination` with `Get-WindowBounds -Handle $handle` as a
positional argument. PowerShell evaluates all arguments before entering the function body, so
`GetWindowRect` is called unconditionally — even in dry-run mode. The dry-run early-return
check (`if ($DryRun) { ... return }`) at the top of `Capture-ToDestination` comes too late to
prevent this real Win32 API call. If a matched window is destroyed or hidden between handle
discovery (`Find-WindowHandles` / `GetForegroundWindow`) and the subsequent `Get-WindowBounds`
call — a plausible race condition during rapid window switching or automated testing — `GetWindowRect`
returns false and `Get-WindowBounds` throws `"could not read window bounds"`. This terminates the
script with exit 1 under `$ErrorActionPreference = 'Stop'` even though `--dry-run` is supposed
to be a safe planning-only pass that produces no side effects and requires no real-time window
geometry. On a stable desktop this race is rare; under CI automation or window-management tests it
is more likely.
_Suggested fix:_ Change each call site from
`Capture-ToDestination -Bounds (Get-WindowBounds -Handle $handle) -Label $label -Handle $handle`
to passing `$handle` only, and move `Get-WindowBounds` inside `Capture-ToDestination` after the
dry-run guard. In dry-run the bounds are unused, so computing them is waste. Alternatively, add a
`$DryRun` pre-check before each `Get-WindowBounds` call in the outer scope.

**[info] Multi-window desktop capture leaves already-committed PNGs when a later capture fails mid-loop**
`capture_screenshot.py:507–519` (`execute_plan`, `for command, output in zip(...)` loop)
When `--allow-multiple-matches` resolves N windows, `execute_plan` iterates `(command, output)`
pairs sequentially. For each successful iteration the temp file is atomically renamed to the final
output path and its path is printed to stdout. If the K-th capture fails — via `CalledProcessError`
(per the 2026-06-11 finding) or `die()` from the overwrite guard — the `finally` block removes
that iteration's temp file, but the already-committed PNGs from iterations 1 through K−1 remain
on disk. A caller (agent or shell script) parsing stdout for the set of saved paths receives an
incomplete listing with exit code 1; there is no explicit indication of which captures succeeded
and no automatic cleanup of the partial set. The caller must correlate the printed paths against
the error to determine which files are usable, and must manually delete any unwanted partial
captures.
_Suggested fix:_ Collect successfully committed paths in a list inside `execute_plan`. Wrap the
iteration in a try/except that, on any error, deletes all paths already in the list before
re-raising (or calling `die()`). This gives callers clean all-or-nothing semantics: either all N
paths are present and exit is 0, or none are present and exit is non-zero.

---

### Data leaks

No new findings. Window-title privacy invariants continue to hold across all three platform paths.
The `Get-WindowBounds` dry-run race (above) involves only window geometry (integers from
`RECT.Left/Top/Right/Bottom`) — no title metadata. The partial-capture orphan issue involves
pixel-data files under the 0o700-secured output directory; no window title is embedded in file
paths (labels come from `sanitize_label(query)`, not from OS-reported titles). Error messages on
all three platforms continue to echo only the user-supplied query, never real window titles.

---

### UX

**[low] README "background/occluded capture" claim is not qualified for Linux X11**
`README.md` (the "What it does" and named-window example sections, added in commit b6aac65)
The updated README states: "Grabs background & occluded windows by name — even when they're
behind other windows — without raising them or stealing focus" and "Works even if the window is
in the background or covered by other windows — it captures the window's own content without
raising it or stealing focus." These claims are accurate for macOS (`screencapture -l` renders
the specific window layer via CoreGraphics) and Windows (the `PrintWindow(PW_RENDERFULLCONTENT)`
API renders the window's own content regardless of occlusion). On **Linux X11**, however, the
capture tool is ImageMagick `import -window <id>`, which uses `XGetImage` against the X11 server.
Modern composited Linux applications (GPU-accelerated GTK4/Qt6, Electron, browsers using
WebGL/hardware decoding) do not maintain a persistent X11 backing store; the X server only holds
the on-screen pixels for the window's visible region, not the window's own render buffer. For a
window that is fully occluded by another window, `import -window` captures whatever the X server
has in the backing store — typically stale content or a blank/black region — not the window's
actual current content. The README's unconditional claim may lead Linux X11 users to expect
behaviour that only holds on macOS and Windows, resulting in silent black or stale screenshots
that are indistinguishable from a successful capture.
_Suggested fix:_ Add a qualification to the README background-capture bullet:
"macOS and Windows capture the window's own rendering even when occluded; on Linux X11, `import`
captures the backing store, which may be blank or stale for GPU-composited applications." Or add
a note to `references/dependencies.md` listing the Linux limitation.

**[info, carry-over] Linux X11 named-window clipboard crashes with an opaque internal error (first reported 2026-06-10)**
Still unresolved. No new technical information.

**[info, carry-over] PowerShell parameter injection via `--query` values beginning with `-` (first reported 2026-06-25)**
Still unresolved. No new technical information.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved. No new technical information.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved. No new technical information.


---

## 2026-06-28

### Security

**[medium, carry-over] PowerShell parameter injection via `--query` values beginning with `-`**
`capture_screenshot.py:539–540` (`_run_powershell_script`)
First reported 2026-06-25. No fix has been applied: `parse_args` still imposes no constraint on query values that begin with `-`, and no regression test exists. A query value such as `"-DryRun"` causes PowerShell's `pwsh -File` parameter binder to activate `$DryRun` silently, producing a success exit code with no screenshot taken.
_Suggested fix:_ Add a validation loop in `main()` before `_run_powershell_script` is called rejecting any `--query` value that starts with `-`, and add an integration test asserting `EXIT_USAGE` for that input with `CAPTURE_SCREENSHOT_TEST_PLATFORM=Windows`.

**[low, carry-over] Compiled macOS helper source is not integrity-checked before execution**
`capture_screenshot.py:325–365` (`resolve_macos_with_helper`), `scripts/find_macos_window_id.m`
First reported 2026-06-26. No fix has been applied. An attacker with write access to the skill's `scripts/` directory could replace `find_macos_window_id.m`; the next capture invocation compiles and executes the modified source under the calling user's account.
_Suggested fix:_ Tighten write permissions on the scripts directory during install (`chmod go-w`) or verify a stored SHA-256 of the source before invoking `clang`.

**[low, carry-over] `resolve_macos_with_helper` does not handle exit code 64 from the macOS helper binary**
`capture_screenshot.py:354–365` (exit-code dispatch)
First reported 2026-06-27. No fix has been applied. Return code 64 (EX_USAGE) from `find_macos_window_id` falls through to the opaque `window_query_failed` catch-all rather than a specific diagnostic.
_Suggested fix:_ Add a `proc.returncode == 64` branch returning a `window_helper_usage_error` result and document the full exit-code table in a comment.

---

### Bugs & regressions

**[high, carry-over] Linux X11 named-window clipboard crashes with "internal error: missing output path"**
`capture_screenshot.py:288–296, 499–502`
First reported 2026-06-10. `plan_capture` for the X11 named-window clipboard path emits `{output}` placeholders; the clipboard branch of `execute_plan` calls `run_command(command)` without `output=`, triggering `die()` with exit 64. Unresolved as of this review.

**[medium, carry-over] `--query` silently discarded when `--target` is `fullscreen` or `active`**
`capture_screenshot.py:main()` (~lines 591–596)
First reported 2026-06-13. No guard has been added; the silent-discard behaviour remains. Unresolved as of this review.

**[low] `plan_capture` accepts a `label` parameter that is never used in its body**
`capture_screenshot.py:200–299` (`plan_capture` function signature and its caller at line 629)
`plan_capture` declares `label: str` but does not reference `label` anywhere in its body. The caller in `main()` evaluates `label=labels[0] if labels else "capture"` and passes the result, but the value is silently discarded by the function. `CapturePlan` objects contain only command tuples; label assignment remains entirely in `main()`'s `labels` list and `output_paths`. The dead parameter could mislead a future contributor into expecting the label to influence plan construction (e.g., embedding the label in output filenames within the plan) when it has no effect.
_Suggested fix:_ Remove the `label` parameter from `plan_capture` and its call site. If a label-aware plan is needed in future, add it back with a documented role.

**[low, carry-over] `test_windows_delegates_to_powershell` does not assert `-OutputRoot` forwarding**
`tests/test_capture_screenshot.py:303–326`
First reported 2026-06-26. `-OutputRoot` is still absent from the checked flag list. A regression that drops this argument would go undetected. Unresolved as of this review.

**[low, carry-over] PowerShell dry-run evaluates `Get-WindowBounds` before the dry-run guard**
`capture_screenshot.ps1:375–376` (active path) and `:399–401` (named-window loop)
First reported 2026-06-27. `Get-WindowBounds` (and the real `GetWindowRect` Win32 call) is evaluated as a positional argument before `Capture-ToDestination` enters its `if ($DryRun)` early-return guard. A window destroyed between handle discovery and the bound check causes a fatal exception even under `--dry-run`. Unresolved as of this review.

**[info, carry-over] Multi-window desktop capture leaves already-committed PNGs when a later capture fails mid-loop**
`capture_screenshot.py:507–519` (`execute_plan`)
First reported 2026-06-27. No atomic all-or-nothing rollback exists. Unresolved as of this review.

---

### Data leaks

No new findings. Window-title privacy invariants continue to hold across all three platform paths. The dead `label` parameter in `plan_capture` involves only the user's query string (already sanitized by `sanitize_label` before being placed in file paths); no OS-reported window title is embedded. The macOS C helper continues to emit only integer window IDs on stdout and static reason tokens (`unknown`) on stderr. Error messages in `capture_screenshot.py` and `capture_screenshot.ps1` echo only the caller-supplied query text, never OS-reported titles.

---

### UX

**[info] Empty request directories are created before capture planning and left behind on plan failure**
`capture_screenshot.py:623` (`prepare_output_paths`) vs. `capture_screenshot.py:624–632` (`plan_capture` / `execute_plan`)
`prepare_output_paths` is called (with `create=True`) before `plan_capture` runs. It creates the 0o700-secured `output_root` and a timestamped `request_dir` subdirectory (e.g., `~/Desktop/screenshots/06_28_2026_12_34_56/`). If `plan_capture` then returns `ok=False` (e.g., no screenshot tool found on the platform), `execute_plan` immediately calls `die()`, leaving the empty, timestamp-named subdirectory on disk. The directory is access-restricted and contains no data, so there is no privacy risk; however, the empty folder may confuse users who inspect the output location and see a timestamped directory with no screenshots in it.
_Suggested fix:_ Move `prepare_output_paths` after `plan_capture` and the tool-detection logic, creating the directory only once the plan is confirmed as viable. Alternatively, delete the empty request dir in the failure path of `execute_plan` (only when `len(output_paths) > 0` and no file has been committed yet).

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11**
`README.md`
First reported 2026-06-27. The Linux X11 `import -window` backing-store limitation is still undocumented in the README. Unresolved as of this review.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation**
First reported 2026-06-25. Still unresolved; clang compilation adds 0.3–1 s latency to every macOS named-window or active-window capture.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review. No new technical information.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review. No new technical information.

---

## 2026-06-29

### Security

**[low] `Path.home()` in `parse_args` default and `_validate_output_root` raises unhandled `RuntimeError` when `$HOME` is unset**
`capture_screenshot.py:523` (`_validate_output_root`) and `capture_screenshot.py:555` (`parse_args`, `--output-root` default)
`Path.home()` raises `RuntimeError: 'HOME' environment variable not set.` on systems where neither
`$HOME` is set nor the user has a parseable passwd entry — for example, minimal containers, CI
runners, or environments where the calling process cleared the environment. In `parse_args`, the
`--output-root` default is computed as `Path.home() / "Desktop" / "screenshots"`, evaluated on
every call to `parse_args`. In `_validate_output_root`, `Path.home().resolve()` is called
unconditionally. Either call propagates an unhandled `RuntimeError` traceback with exit code 1,
bypassing all `die()` error handling and all structured exit codes. The `install.sh` HOME guard
(2026-06-10 finding) protects the bash installer but the Python script itself has no equivalent
guard. This is distinct from that finding — the Python module is a separate entry point.
_Suggested fix:_ Wrap `Path.home()` calls in a helper that catches `RuntimeError` and converts it
to `die("could not determine user home directory — ensure $HOME is set", EXIT_USAGE)`. For the
`parse_args` default, use a sentinel of `None` and resolve the home path in `main()` after the
guard check, rather than evaluating `Path.home()` at parse time.

**[info] `find_macos_window_id.m` silently ignores a positional query when `--frontmost` is also supplied**
`scripts/find_macos_window_id.m:51–54, 91–103`
The usage guard at line 51 is `!frontmost && !query_arg`: it fires only when neither is present.
When both `--frontmost` and a positional query are supplied, the guard passes, `query` is allocated
via `CFStringCreateWithCString`, but inside the main loop the `if (frontmost)` branch is entered
unconditionally, returning the first on-screen normal-layer window without consulting `query`. The
positional argument is silently discarded. Python always passes one or the other — never both —
so this is unreachable from the Python caller in normal operation. A direct invocation of the
compiled binary from a shell script or alternative orchestrator, however, receives a valid exit-0
result that ignores the search term with no warning. The 2026-06-23 finding documented silent-
overwrite of multiple positional arguments; this is a distinct silent-ignore case specific to the
`--frontmost`+positional combination.
_Suggested fix:_ After the argument-parsing loop, add:
`if (frontmost && query_arg) { fprintf(stderr, "error: --frontmost and a query are mutually exclusive\n"); return 64; }`
This makes the interface unambiguous and prevents accidental misuse by direct callers.

---

### Bugs & regressions

**[low] `copy_file_to_clipboard` performs no emptiness check on the temp PNG before piping to the clipboard tool**
`capture_screenshot.py:468–478` (`copy_file_to_clipboard`) and `capture_screenshot.py:491–496` (`execute_plan`, `{temp-output}` clipboard branch)
For the two-command clipboard path (`grim`+`wl-copy` on Wayland, `import`+`xclip`/`xsel` on X11),
`execute_plan` calls `run_command(plan.commands[0], temp_output=temp_path)` to write the screenshot,
then passes `temp_path` to `copy_file_to_clipboard`. That function reads all bytes with
`data = path.read_bytes()` and pipes them to the clipboard tool with no check that `data` is
non-empty or begins with PNG magic bytes `\x89PNG`. If the screenshot tool exits 0 but writes zero
bytes — documented for `grim` on Wayland compositor frame-callback timeout, and for
`import -window root` on headless X11 — `data = b""` is piped to `wl-copy --type image/png` or
`xclip -t image/png`. Both tools accept empty stdin without error, setting an empty clipboard item.
The script then prints `"clipboard"` and exits 0: a false-success signal. The 2026-06-22 finding
covers the same empty-file failure for the desktop output path; this is the distinct clipboard-path
variant (`execute_plan:491-496`) not addressed by that entry.
_Suggested fix:_ After `run_command(plan.commands[0], temp_output=temp_path)` returns, check
`temp_path.stat().st_size > 0`; if the file is empty, call
`die("capture tool wrote no data — check display availability and screen recording permissions",
EXIT_UNAVAILABLE)`. Alternatively, perform the check inside `copy_file_to_clipboard` before
calling `read_bytes()`.

**[low] `os.replace(temp_output, output)` in `execute_plan` propagates unhandled `OSError`**
`capture_screenshot.py:514` (`execute_plan`, desktop output loop)
`run_command` failures propagating as unhandled `CalledProcessError` are a known gap (first noted
2026-06-11). The immediately following `os.replace()` on line 514 can also raise `OSError` — for
example `ENOSPC` (disk full between temp-write and rename) or `EPERM` (filesystem remounted
read-only). Such an exception propagates through `execute_plan` and `main()` as an unhandled
Python traceback with exit code 1. The `finally` block correctly cleans up the temp file (which
still exists if `os.replace` failed), but the error itself carries no structured exit code and no
actionable message. On `--allow-multiple-matches` captures, paths from earlier iterations are
already committed and printed to stdout (the 2026-06-27 partial-capture info finding), worsening
the inconsistency.
_Suggested fix:_ Wrap `os.replace(temp_output, output)` in `try/except OSError as e:` and call
`die(f"could not rename screenshot to final path: {e.strerror}", EXIT_UNAVAILABLE)`, consistent
with the structured-error pattern used for other I/O failures in the same function.

**[high, carry-over] Linux X11 named-window clipboard crashes with "internal error: missing output path"**
`capture_screenshot.py:288–296, 499–502`
First reported 2026-06-10. Still unresolved as of this review.

**[medium, carry-over] PowerShell parameter injection via `--query` values beginning with `-`**
`capture_screenshot.py:539–540`
First reported 2026-06-25. Still unresolved as of this review.

**[medium, carry-over] `--query` silently discarded when `--target` is `fullscreen` or `active`**
`capture_screenshot.py:main()` (~lines 596–605)
First reported 2026-06-13. Still unresolved as of this review.

---

### Data leaks

No new findings. All window-title privacy invariants continue to hold across all three platform
paths. The `Path.home()` `RuntimeError` (above) reveals only an OS error message with no window
title or screenshot content. The `find_macos_window_id.m` silent-ignore concerns only the caller-
supplied query string — no OS-reported title is exposed. The `copy_file_to_clipboard` empty-file
finding involves the absence of pixel data rather than its exposure. The `os.replace` exception
path includes only OS error strings and the output file path (derived from `sanitize_label(query)`,
not from an OS-reported window title). Error messages in `capture_screenshot.py` and
`capture_screenshot.ps1` continue to echo only user-supplied query text, never real window titles.

---

### UX

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11**
`README.md`
First reported 2026-06-27. The Linux X11 `import -window` backing-store limitation remains
undocumented in the README. Still unresolved as of this review.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation**
First reported 2026-06-24. Still unresolved; clang compilation adds 0.3–1 s latency per macOS
named-window or active-window capture request.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review. No new technical information.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review. No new technical information.

---

## 2026-06-30

### Security

**[info] Correction to 2026-06-13 TOCTOU finding: `os.replace`/`rename(2)` on POSIX replaces the symlink entry itself, not the symlink target**
`capture_screenshot.py:514`
The 2026-06-13 entry stated: "`os.replace()` on Linux atomically replaces the target of a symlink (i.e., it follows the link and overwrites the pointed-to file) rather than replacing the symlink itself." This is factually incorrect. POSIX `rename(2)` — which CPython's `os.replace` calls on Linux and macOS — replaces the directory entry at the destination path atomically. If the destination is a symlink, the symlink file itself is overwritten and removed; the symlink's target file is not written to. Consequently, the specific threat scenario described ("a screenshot written to an attacker-controlled path via a symlink placed at `output`") does not materialise: a symlink placed at `output` between the `exists()` check and `os.replace()` would be replaced by the screenshot file, not followed. The underlying TOCTOU window is real (a regular file created at `output` between the two calls would be overwritten without the overwrite guard firing), but the scenario is less dangerous than the 2026-06-13 description implied. The practical impact of the remaining race is further constrained by the 0o700 per-request directory, which prevents other users from creating files at `output` in the first place.
_Suggested fix:_ Update the 2026-06-13 finding's description to reflect the correct `rename(2)` semantics and note that a hardlink planted at `output` by the same-user (or root) could still trigger a silent overwrite.

**[low] PowerShell `Move-Item` TOCTOU: file created at `$path` between `Get-Item` check and `Move-Item` produces unstructured exit 1**
`capture_screenshot.ps1:330–337`
In `Capture-ToDestination`, the script guards against overwriting an existing destination with `Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue` (line 330) and throws a structured error if something is found. The actual rename on line 337 is `Move-Item -LiteralPath $tempPath -Destination $path -ErrorAction Stop`. Between these two lines, a race exists: if another process creates a file at `$path`, `Move-Item` without `-Force` fails with `System.IO.IOException: Cannot create a file when that file already exists.` Under `$ErrorActionPreference = 'Stop'` this is a terminating error that exits with code 1 — unstructured and indistinguishable from other runtime failures. Unlike Python's `os.replace` (which on POSIX replaces the destination atomically and raises only on cross-device moves), Windows `MoveFileEx` without `MOVEFILE_REPLACE_EXISTING` refuses to overwrite, so the race is a denial-of-service rather than an overwrite. The `finally` block removes `$tempPath` correctly. The practical risk on a single-user desktop is low; on shared machines with write access to the output directory it is a plausible denial-of-service for targeted capture requests.
_Suggested fix:_ Wrap the `Move-Item` call in `try/catch [System.IO.IOException]` and emit `[Console]::Error.WriteLine("refusing to overwrite an existing screenshot path"); exit 73` to produce a structured, auditable exit code consistent with the intent of the preceding guard.

**[low, carry-over] PowerShell parameter injection via `--query` values beginning with `-`**
`capture_screenshot.py:539–540`
First reported 2026-06-25. No fix applied. A query value such as `"-DryRun"` causes `pwsh -File` to activate `$DryRun` silently, producing exit 0 with no screenshot taken. Still unresolved as of main branch at this review.

**[low, carry-over] Compiled macOS helper source is not integrity-checked before execution**
`capture_screenshot.py:325–365`
First reported 2026-06-26. No fix applied. Still unresolved as of this review.

**[low, carry-over] `resolve_macos_with_helper` does not handle exit code 64 from the macOS helper binary**
`capture_screenshot.py:354–365`
First reported 2026-06-27. No fix applied. Still unresolved as of this review.

**[low, carry-over] `Path.home()` raises unhandled `RuntimeError` when `$HOME` is unset**
`capture_screenshot.py:523, 555`
First reported 2026-06-29. No fix applied. Still unresolved as of this review.

---

### Bugs & regressions

**[high, carry-over] Linux X11 named-window clipboard crashes with "internal error: missing output path"**
`capture_screenshot.py:288–296, 499–502`
First reported 2026-06-10. `plan_capture` emits `{output}` placeholders for the X11 named-window clipboard path; `execute_plan`'s clipboard branch calls `run_command(command)` without `output=`, triggering `die()` with exit 64. Still unresolved as of main branch at this review.

**[medium, carry-over] `--query` silently discarded when `--target` is `fullscreen` or `active`**
`capture_screenshot.py:main()` (~lines 596–605)
First reported 2026-06-13. No guard added; the silent-discard behaviour that contradicts the privacy-first design goal remains present. Still unresolved as of this review.

**[medium, carry-over] Windows dry-run with `--allow-multiple-matches` reports duplicate output paths for same-label windows**
`capture_screenshot.ps1` (`New-CapturePath`, `Capture-ToDestination`)
First reported 2026-06-23. `New-CapturePath` checks filesystem existence; in dry-run no files are written so the collision guard never fires and duplicate paths are returned. Still unresolved as of this review.

**[low] No regression test for `_validate_output_root` rejecting a path outside the user home directory**
`tests/test_capture_screenshot.py`, `capture_screenshot.py:522–527`
`_validate_output_root` is the sole Python-side guard ensuring screenshots are never written outside the user's home directory. Its failure path — `die("output-root must be within the user home directory", EXIT_USAGE)` — is exercised by no test in the current suite. If a future refactor weakens or removes this check, no existing test would catch the regression. Given that the Python home-containment guard is the primary protection for the non-Windows path (the PS1 script has no equivalent check when invoked directly, noted in the 2026-06-08 and 2026-06-23 entries), a silently broken guard would allow writes to arbitrary locations such as `/tmp/screenshots` or `/etc/screenshots` without any other safety net.
_Suggested fix:_ Add an integration test that invokes the script subprocess with `--output-root /tmp/screenshots` and asserts `proc.returncode == EXIT_USAGE` and a message containing "home directory". Add a companion positive test that a path within `$HOME` passes validation without error.

**[low, carry-over] `test_windows_delegates_to_powershell` does not assert `-OutputRoot` forwarding**
`tests/test_capture_screenshot.py:303–326`
First reported 2026-06-26. Still unresolved as of this review.

**[low, carry-over] PowerShell dry-run evaluates `Get-WindowBounds` before the dry-run guard**
`capture_screenshot.ps1:375–376, 399–401`
First reported 2026-06-27. Still unresolved as of this review.

**[low, carry-over] `os.replace()` in `execute_plan` propagates unhandled `OSError`**
`capture_screenshot.py:514`
First reported 2026-06-29. Still unresolved as of this review.

---

### Data leaks

No new findings. Window-title privacy invariants continue to hold across all three platform paths. The `Move-Item` TOCTOU produces an error rather than a data leak — the screenshot bytes never reach an attacker-visible location. The `_validate_output_root` test gap involves a missing regression test, not an active data-exposure path (the guard itself is correctly implemented in the current code). The correction to the 2026-06-13 `os.replace` finding actually reduces the stated risk: an attacker cannot redirect the screenshot to an arbitrary path via a symlink planted at `output` because `rename(2)` replaces the symlink itself rather than following it. All error messages continue to echo only the user-supplied query text, never OS-reported window titles. `sanitize_label` strips URLs and non-alphanumeric characters before embedding labels in filesystem paths.

---

### UX

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11**
`README.md`
First reported 2026-06-27. The Linux X11 `import -window` backing-store limitation remains undocumented in the README. Still unresolved as of this review.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation**
First reported 2026-06-24. Still unresolved; clang compilation adds 0.3–1 s latency per macOS named-window or active-window capture request.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review. No new technical information.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review. No new technical information.

---

## 2026-07-01

### Security

**[medium] Whitespace-only `--query` bypasses the empty-string rejection proposed on 2026-06-13**
`capture_screenshot.py:133` (`_matches`), `capture_screenshot.ps1:212–213` (`Find-WindowHandles`), `find_macos_window_id.m:107` (`contains`)

`_matches(" ", title)` in Python evaluates `bool(" ")` as `True` and `" " in title.casefold()` as `True` for virtually every window title that contains a space — which is nearly all of them.  On Windows, `title.IndexOf(" ", StringComparison.OrdinalIgnoreCase)` returns ≥ 0 for any space-containing title, and similarly `processName.IndexOf(" ", …)` does the same.  On macOS the C helper calls `CFStringFind(title, CFSTR(" "), kCFCompareCaseInsensitive)` with identical over-broad results.  On Linux, `xdotool search --name " "` matches every window whose name contains a space.

The 2026-06-13 proposed fix checks `if not q` (empty string only); it does not reject `" "`.  The filename fallback (`sanitize_label(" ")` → `""` → `"capture"`) hides any indication of over-broad scope from the output path — the caller receives files named `capture.png`, `capture-001.png`, etc., with no indication that dozens of windows matched.

`allow_multiple=False` path: if exactly one matching window happens to be on-screen the capture proceeds silently on what may be an unintended window.  `allow_multiple=True` path: all on-screen windows with a space in their title are captured.

**Fix:** in `capture_screenshot.py`, add `if not q.strip(): sys.exit(EXIT_USAGE)` in argument validation before any platform dispatch.  In `capture_screenshot.ps1`, add a guard `if ([string]::IsNullOrWhiteSpace($queryText)) { throw 'query must not be blank or whitespace-only' }`.  The C helper requires no change because the Python/PowerShell callers gate the value before invoking it.

**[low, carry-over] PowerShell `--query` parameter injection (first reported 2026-06-25)**
Still unresolved as of this review. `_run_powershell_script` passes each query value as a bare `-Query q` element; a value like `; Start-Process calc` could inject a new statement.

**[low] `Protect-Directory` creates intermediate parent directories with world-accessible ACLs**
`capture_screenshot.ps1:93` (`Protect-Directory`)

`New-Item -ItemType Directory -Path $Path -Force` creates all missing intermediate parent directories.  Only the leaf directory (`$Path` itself) then receives `Set-Acl` with the owner-only `FileSystemAccessRule`.  Any intermediate directories that did not previously exist are created with Windows inherited (default) ACLs — typically world-traversable — and are never explicitly locked down.

This is the PowerShell analogue of the Python `parents=True` finding documented on 2026-06-16 but has not been explicitly raised for the PS code path.  In practice the `OutputRoot` is typically `Desktop\screenshots`, so the only intermediate directory created is `screenshots` itself, which then receives the owner-only ACL in the `New-RequestFolder`-level call.  The risk materialises when `--output-root` points to a deep path whose parents do not yet exist.

**Fix:** walk `$Path`'s ancestors from root to leaf and call `Set-Acl` on each intermediate directory that was just created by `New-Item -Force`, or create them one level at a time with explicit ACLs.  Alternatively restrict `OutputRoot` depth so intermediate parents cannot be newly created.

**[low, carry-over] Compiled macOS C helper source not integrity-checked at runtime (first reported 2026-06-26)**
Still unresolved as of this review.

**[low, carry-over] `Path.home()` can raise `RuntimeError` on misconfigured systems (first reported 2026-06-29)**
Still unresolved as of this review.

**[low, carry-over] `Move-Item` TOCTOU window between `New-CapturePath` and the actual rename (first reported 2026-06-30)**
Still unresolved as of this review.

---

### Bugs & regressions

**[low] Partial `git clone` leaves a stale directory; next `install.sh` run silently reports "already installed"**
`install.sh:19–21, 27`

`set -euo pipefail` (line 2) causes the script to exit immediately if `git clone` returns non-zero (network interruption, disk-full mid-clone, authentication failure).  `git clone` may have already created the destination directory and begun writing objects into it before failing, leaving a partial directory tree on disk.

On the next `install.sh` invocation, `[ -d "$dest" ]` at line 19 is `true`, so the function prints `"$label already installed at $dest — skipping"` and returns without retrying the clone.  The user is given no indication that the prior install was incomplete; the partial directory is treated as a successful installation.

**Fix:** after `git clone` fails (or before the early-return guard), verify that the destination directory contains a valid Git repository (e.g., `git -C "$dest" rev-parse HEAD >/dev/null 2>&1`), and retry or report an error if it does not.  Alternatively, on clone failure clean up the partial directory with `rm -rf "$dest"` so the early-return guard does not trigger on the next run.

**[high, carry-over] Linux X11 named-window clipboard path crashes (first reported 2026-06-10)**
Still unresolved as of this review.

**[medium, carry-over] `--query` silently discarded with non-window `--target` values (first reported 2026-06-13)**
Still unresolved as of this review.

**[medium, carry-over] Windows dry-run `allow_multiple` produces duplicate output paths (first reported 2026-06-23)**
Still unresolved as of this review.

**[medium, carry-over] PowerShell `--query` parameter injection (first reported 2026-06-25)**
Still unresolved as of this review.

**[low, carry-over] No regression test for `_validate_output_root` (first reported 2026-06-30)**
Still unresolved as of this review.

**[low, carry-over] `test_windows_delegates_to_powershell` does not assert `-OutputRoot` forwarding (first reported 2026-06-26)**
Still unresolved as of this review.

**[low, carry-over] PowerShell dry-run evaluates `Get-WindowBounds` before the `$DryRun` guard (first reported 2026-06-27)**
Still unresolved as of this review.

**[low, carry-over] `os.replace()` in `execute_plan` propagates unhandled `OSError` (first reported 2026-06-29)**
Still unresolved as of this review.

---

### Data leaks

No new findings. The whitespace-only `--query` security finding (above) involves scope over-extension — more windows captured than intended — but `sanitize_label` converts the blank/whitespace value to `"capture"` before embedding it in any filesystem path or output message, so window-title content is not exposed.  All window-title privacy invariants continue to hold across all three platform paths.  Error messages and dry-run output continue to echo only the user-supplied query string, never OS-reported window titles.

---

### UX

**[low] No test asserts that a whitespace-only `--query` triggers `EXIT_USAGE`**
`tests/test_capture_screenshot.py`

The existing test suite checks `test_requires_explicit_consent` for `EXIT_PRIVACY` and `test_macos_named_window_plan_does_not_fallback_to_fullscreen` for the no-window-found path, but there is no test for `--query " "` (whitespace-only query).  This is a direct companion gap to the security finding above: without a test, a future whitespace-guard fix can be removed or bypassed without a CI signal.

**Suggested test:** a subprocess test that runs with `--query " "` and asserts `returncode == EXIT_USAGE` and that the combined stderr/stdout does not contain any window-title text.

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11**
First reported 2026-06-27. Still unresolved as of this review.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation**
First reported 2026-06-24. Still unresolved; clang compilation adds 0.3–1 s latency per macOS named-window or active-window capture request.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review.

---

## 2026-07-02

### Security

**[low, NEW] `ensure_private_directory` TOCTOU between `is_symlink()` check and `mkdir()` call**
`capture_screenshot.py:92–103` (`ensure_private_directory`)

`is_symlink()` is called and, if it returns `False`, `path.mkdir(mode=0o700, parents=True, exist_ok=True)` is invoked. Between these two calls an attacker who controls the parent directory could create a symlink at `path`. With `exist_ok=True`, `mkdir()` would follow the symlink, silently succeed if the symlink target is an existing directory, and all subsequent `chmod(0o700)` and file writes would operate on the symlink target rather than the intended path. On a shared system this could be used to redirect screenshot output or to apply restrictive permissions to an attacker-chosen directory. Exploitability is low on a single-user machine where only the owner controls `$HOME`.

**Suggested fix:** After `mkdir()`, re-check `path.is_symlink()` (or use `os.lstat()` and verify the path is a real directory), and die if the check now returns True. Alternatively use `os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)` after creation to get a symlink-safe fd and verify it.

**[medium, carry-over] Whitespace-only `--query` bypasses the empty-string rejection (first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] PowerShell `--query` parameter injection (first reported 2026-06-25)**
Still unresolved as of this review.

**[low, carry-over] `Protect-Directory` creates intermediate parent directories with world-accessible ACLs (first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] Compiled macOS C helper source not integrity-checked at runtime (first reported 2026-06-26)**
Still unresolved as of this review.

**[low, carry-over] `Path.home()` can raise `RuntimeError` on misconfigured systems (first reported 2026-06-29)**
Still unresolved as of this review.

**[low, carry-over] `Move-Item` TOCTOU window between `New-CapturePath` and the actual rename (first reported 2026-06-30)**
Still unresolved as of this review.

---

### Bugs & regressions

**[low, NEW] `unique_capture_path` exits with `EXIT_PRIVACY` (73) on filename-slot exhaustion**
`capture_screenshot.py:104` (`unique_capture_path`)

When all 1000 filename slots are occupied, `die("could not allocate a unique screenshot filename", EXIT_PRIVACY)` exits with code 73. Running out of filename slots is a resource-exhaustion condition, not a privacy violation — exit code `EXIT_USAGE` (64) would better reflect the cause and avoid confusing agents or callers that inspect the exit code to distinguish privacy errors from other failures.

**Suggested fix:** Change the `die()` call in `unique_capture_path` to use `EXIT_USAGE` instead of `EXIT_PRIVACY`.

**[low, NEW] Unhandled `CalledProcessError` from `clang` compilation surfaces as Python traceback**
`capture_screenshot.py:~283` (`resolve_macos_with_helper`)

`subprocess.run([clang, "-framework", "ApplicationServices", str(helper_source), "-o", str(helper)], check=True)` raises `subprocess.CalledProcessError` on compilation failure (SDK header change, disk full, permission error). This propagates unhandled through `main()` and prints a raw Python traceback to stderr. Every other error path in the script uses `die()` for structured, agent-parseable error output. The inconsistency makes macOS helper compilation failures harder to diagnose from agent output.

**Suggested fix:** Wrap the clang `subprocess.run` in a `try/except subprocess.CalledProcessError` and call `die("macOS window helper compilation failed", EXIT_UNAVAILABLE)` with a brief description.

**[high, carry-over] Linux X11 named-window clipboard path crashes (first reported 2026-06-10)**
Still unresolved as of this review.

**[medium, carry-over] `--query` silently discarded with non-window `--target` values (first reported 2026-06-13)**
Still unresolved as of this review.

**[medium, carry-over] Windows dry-run `allow_multiple` produces duplicate output paths (first reported 2026-06-23)**
Still unresolved as of this review.

**[low, carry-over] Partial `git clone` leaves a stale directory; next `install.sh` run silently reports "already installed" (first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] No regression test for `_validate_output_root` (first reported 2026-06-30)**
Still unresolved as of this review.

**[low, carry-over] `test_windows_delegates_to_powershell` does not assert `-OutputRoot` forwarding (first reported 2026-06-26)**
Still unresolved as of this review.

**[low, carry-over] PowerShell dry-run evaluates `Get-WindowBounds` before the `$DryRun` guard (first reported 2026-06-27)**
Still unresolved as of this review.

**[low, carry-over] `os.replace()` in `execute_plan` propagates unhandled `OSError` (first reported 2026-06-29)**
Still unresolved as of this review.

---

### Data leaks

No new findings. All window-title privacy invariants continue to hold across macOS, Linux, and Windows paths. The whitespace-only `--query` finding (Security above) causes over-broad window matching but `sanitize_label` converts the blank/whitespace label to `"capture"` before writing any filesystem path, so no window-title content leaks into output filenames or messages. Error messages and dry-run output continue to echo only the user-supplied query string.

---

### UX

**[info, NEW] `first_capturable` variable name in `find_macos_window_id.m` is misleading**
`scripts/find_macos_window_id.m:~97–113`

In the single-match path (`!allow_multiple`), each iteration overwrites `first_capturable = number`, so after the loop the variable holds the *last* capturable window ID encountered, not the first. The behavior is correct only because when `capturable_count > 1` the code returns exit 3 (multiple matches) without reading `first_capturable`, and when `capturable_count == 1` there is exactly one assignment. The misleading name could confuse a future maintainer into thinking the front-to-back iteration order guarantees the topmost window is returned when allow_multiple is false.

**Suggested fix (naming only):** Rename `first_capturable` to `single_capturable` or `matched_capturable` to make the invariant explicit.

**[low, carry-over] No test asserts that whitespace-only `--query` triggers `EXIT_USAGE` (first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11 (first reported 2026-06-27)**
Still unresolved as of this review.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation (first reported 2026-06-24)**
Still unresolved as of this review. Clang compilation adds 0.3–1 s latency per macOS named-window or active-window capture request.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review.

## 2026-07-03

### Security

No new findings.

**[medium, carry-over] Whitespace-only `--query` bypasses empty-string rejection (`capture_screenshot.py`, first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] PowerShell `--query` value injection via `-`-prefixed strings (`capture_screenshot.py:_run_powershell_script`, first reported 2026-06-25)**
Still unresolved as of this review.

**[low, carry-over] TOCTOU between `is_symlink()` and `mkdir()` in `ensure_private_directory` (`capture_screenshot.py`, first reported 2026-07-02)**
Still unresolved as of this review.

**[low, carry-over] `Protect-Directory` creates intermediate parent directories with world-accessible ACLs (`capture_screenshot.ps1`, first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] Compiled macOS C helper source not integrity-checked at runtime (`capture_screenshot.py:resolve_macos_with_helper`, first reported 2026-06-26)**
Still unresolved as of this review.

**[low, carry-over] `Path.home()` raises `RuntimeError` on misconfigured systems (`capture_screenshot.py:_validate_output_root`, first reported 2026-06-29)**
Still unresolved as of this review.

**[low, carry-over] `Move-Item` TOCTOU between existence check and rename (`capture_screenshot.ps1`, first reported 2026-06-30)**
Still unresolved as of this review.

### Bugs & regressions

**[low, NEW] `private_temp_png` catches only `FileExistsError`, leaving other `OSError` subtypes as unhandled tracebacks (`capture_screenshot.py:444`)**

`os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)` is wrapped in a retry loop that catches only `FileExistsError`. Other `OSError` subclasses — `PermissionError` (EPERM/EACCES), disk-full (`ENOSPC`), read-only filesystem (`EROFS`) — propagate as raw Python tracebacks with exit code 1, bypassing `die()` and producing no structured error message or documented exit code. This is the same class of omission previously documented for `secure_file` (2026-06-18, catches only `PermissionError`) and `ensure_private_directory` (2026-06-19), but `private_temp_png` has not been called out before.

Suggested fix: change `except FileExistsError: continue` to:
```python
except OSError as e:
    if e.errno == errno.EEXIST:
        continue
    die(f"could not create private temporary screenshot file: {e.strerror}", EXIT_PRIVACY)
```
(and add `import errno` at the top of the file).

**[high, carry-over] X11 clipboard capture crashes with `subprocess.CalledProcessError` when no window matches xdotool search (`capture_screenshot.py:resolve_linux_named_window`, first reported 2026-06-30)**
Still unresolved as of this review.

**[medium, carry-over] Supplied `--query` is silently discarded when target is not `window` (`capture_screenshot.py:main`, first reported 2026-07-01)**
Still unresolved as of this review.

**[medium, carry-over] Windows dry-run emits duplicate output lines when multiple capture commands are planned (`capture_screenshot.ps1`, first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] `unique_capture_path` does not call `die()` on unexpected `OSError`; propagates raw traceback (`capture_screenshot.py`, first reported 2026-07-02)**
Still unresolved as of this review.

**[low, carry-over] `CalledProcessError` from clang compilation surfaces as unstructured traceback rather than a clean `die()` message (`capture_screenshot.py:resolve_macos_with_helper`, first reported 2026-06-28)**
Still unresolved as of this review.

**[low, carry-over] Partial git clone of repo into skills directory not cleaned up on interruption (`install.sh`, first reported 2026-06-26)**
Still unresolved as of this review.

**[low, carry-over] Test coverage gap: no test for `private_temp_png` failure modes other than name collision (`tests/test_capture_screenshot.py`, first reported 2026-06-18)**
Still unresolved as of this review.

### Data leaks

No new findings.

### UX

**[low, RE-FLAGGED from 2026-06-17] Unquoted temp-file path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`)**

The fake PowerShell stub script is written with an unquoted `args_file` path:
```python
fake_ps.write_text(
    f'#!/bin/sh\nprintf "%s\\n" "$@" > {args_file}\necho "fake/path.png"\n'
)
```
First documented 2026-06-17 but subsequently dropped from carry-over tracking without being fixed. On CI runners where `TMPDIR` contains spaces (e.g. `/home/runner/work/tmp dir`), the shell redirect is parsed incorrectly — the path is word-split, causing a false test failure that masks the real PowerShell delegation behavior under test.

Suggested fix: quote the embedded path as `> "{args_file}"`, or use `shlex.quote(str(args_file))` when building the shell string.

**[low, carry-over] No test asserts that whitespace-only `--query` triggers `EXIT_USAGE` (first reported 2026-07-01)**
Still unresolved as of this review.

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11 (first reported 2026-06-27)**
Still unresolved as of this review.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation (first reported 2026-06-24)**
Still unresolved as of this review. Clang compilation adds 0.3–1 s latency per macOS named-window or active-window capture request.

**[info, carry-over] `--query` silently discarded with non-window targets (first reported 2026-06-13)**
Still unresolved as of this review.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved as of this review.

---

## 2026-07-04

### Security

**[medium] Clipboard capture (grim / import path) stores screenshot PNG in system `/tmp` rather than the secured 0o700 request directory (`capture_screenshot.py:492`)**

When `--destination clipboard` is used on Linux with `grim` or `import`, `execute_plan` creates a temporary PNG via `tempfile.NamedTemporaryFile(prefix="capture-screenshot.", suffix=".png")`. This lands in the system temp directory (usually `/tmp`) — outside the 0o700 request directory that desktop captures use. Python's `NamedTemporaryFile` creates files at mode 0o600 by default (the mode is subject to umask; with a pathological umask of 0o177 the result can be 0o000, readable only by root). The contrast with the desktop code path (`private_temp_png` + `secure_file`, all inside the 0o700 request directory) means clipboard captures have a weaker privacy guarantee for the brief interval the PNG lives on disk. On a shared machine, a root process can inspect `/tmp` regardless of permissions.

Suggested fix: instead of `NamedTemporaryFile`, create the temp file inside `ensure_private_directory`-protected request directory using `private_temp_png`, then pipe the bytes to the clipboard tool and delete it — matching the security model of the desktop path.

**[low] `kCGWindowListOptionAll` in macOS helper enumerates windows from every user session on a shared macOS system (`find_macos_window_id.m:68`)**

`CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)` returns window metadata (owner names, window titles) for all GUI processes on the system, including those belonging to other logged-in users in fast-user-switching sessions. A query string that happens to partially match another user's application name could return that user's window information (owner/title metadata used for matching). The actual capture step (`screencapture -l <id>`) would likely be denied by macOS SIP/security, but the metadata is returned and the window ID printed to stdout. On single-user systems (the common case) this is a non-issue.

Suggested fix: document the limitation, or filter the window list with `kCGWindowListOptionOnScreenOnly` for shared system deployments (noting this would miss minimized/off-Space windows, requiring a separate all-windows pass for the "present but not capturable" case).

### Bugs & regressions

**[low] `$matches` in `Find-WindowHandles` shadows PowerShell's automatic `$Matches` variable under `Set-StrictMode -Version Latest` (`capture_screenshot.ps1:204`)**

`Find-WindowHandles` declares `$matches = [System.Collections.Generic.List[IntPtr]]::new()`. In PowerShell, `$Matches` (case-insensitive) is an automatic variable populated by the `-match` and `-replace` regex operators. Under `Set-StrictMode -Version Latest`, assigning to a name that aliases an automatic variable is permitted but causes subtle hazards: any `-match` expression evaluated later in the same scope would overwrite `$matches` with a hashtable, silently replacing the window-handle list and causing `return $matches` to return a hashtable instead of a `List[IntPtr]`. While no `-match` expression currently exists in `Find-WindowHandles`, the clash is a maintenance trap.

Suggested fix: rename the local collection to `$windowHandles` (or similar) throughout `Find-WindowHandles`.

**[low] `run_command` and the macOS helper compile step have no timeout; a hanging tool blocks the CLI indefinitely (`capture_screenshot.py:339`, `capture_screenshot.py:465`)**

Both `subprocess.run([clang, ...], check=True)` (compile) and `subprocess.run(args, check=True)` (screenshot tool) specify no `timeout=` argument. A tool that hangs (e.g., `screencapture` waiting for a permission dialog that never appears, `grim` blocked on a Wayland compositor event, or `clang` running on a degraded system) will freeze the calling agent indefinitely with no recovery path.

Suggested fix: add a reasonable `timeout` (e.g., 30 s for screencapture/grim, 60 s for clang) and catch `subprocess.TimeoutExpired` to call `die("capture tool timed out", EXIT_UNAVAILABLE)`.

### Carry-overs (unresolved from prior entries)

**[high, carry-over] `CalledProcessError` from `subprocess.run(check=True)` propagates as a raw Python traceback for any tool failure (`capture_screenshot.py:339,465`, first reported 2026-06-09)**
Still unresolved.

**[high, carry-over] X11 clipboard capture crashes with `subprocess.CalledProcessError` when xdotool search returns no matches (`capture_screenshot.py:resolve_linux_named_window`, first reported 2026-06-30)**
Still unresolved.

**[medium, carry-over] Supplied `--query` is silently discarded when target is not `window` (`capture_screenshot.py:main`, first reported 2026-07-01)**
Still unresolved.

**[medium, carry-over] Windows dry-run emits duplicate output lines when multiple capture commands are planned (`capture_screenshot.ps1`, first reported 2026-07-01)**
Still unresolved.

**[low, carry-over] `unique_capture_path` does not call `die()` on unexpected `OSError`; propagates raw traceback (`capture_screenshot.py`, first reported 2026-07-02)**
Still unresolved.

**[low, carry-over] `CalledProcessError` from clang compilation surfaces as unstructured traceback rather than a clean `die()` message (`capture_screenshot.py:resolve_macos_with_helper`, first reported 2026-06-28)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the retry loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] Partial git clone of repo into skills directory not cleaned up on interruption (`install.sh`, first reported 2026-06-26)**
Still unresolved.

**[low, carry-over] Test coverage gap: no test for `private_temp_png` failure modes other than name collision (`tests/test_capture_screenshot.py`, first reported 2026-06-18)**
Still unresolved.

### Data leaks

No new findings.

### UX

**[info] Active-window capture on non-GNOME Linux desktops always fails with `missing_dependency_active_window` even when `scrot`+`xdotool` could serve the request (`capture_screenshot.py:277–286`)**

`plan_capture` for `target == "active"` on Linux checks only for `gnome-screenshot`. If absent, it returns `missing_dependency_active_window` regardless of what other tools are present. On KDE, LXDE, or bare X11 desktops without GNOME, `scrot` combined with `xdotool getactivewindow` (or `xdotool getwindowfocus`) could capture the active window. The existing code silently ignores these tools for the `active` target.

Suggested fix: when `gnome-screenshot` is absent and `scrot`+`xdotool` are both present, build a plan: `(xdotool, "getactivewindow")` → feed the returned window ID to `(import, "-window", "<id>", "{output}")`, or simply `(scrot, "--focused", "{output}")` (scrot supports `--focused` natively).

**[low, carry-over] Unquoted `args_file` path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17, RE-FLAGGED)**
Still unresolved.

**[low, carry-over] No test asserts that whitespace-only `--query` triggers `EXIT_USAGE` (first reported 2026-07-01)**
Still unresolved.

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11 (first reported 2026-06-27)**
Still unresolved.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation (first reported 2026-06-24)**
Still unresolved.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved.

## 2026-07-05

### Security

**[medium, carry-over] PowerShell parameter injection via leading-dash `--query` values (`capture_screenshot.py`, `_run_powershell_script`, first reported 2026-06-25)**
Still unresolved. A `--query` value beginning with `-` is forwarded to PowerShell as a bare argument and may be misinterpreted as a flag. Suggested fix: prefix each query value with `--` or validate that no query begins with `-` before forwarding.

**[medium, carry-over] Whitespace-only `--query` value expands scope silently (`capture_screenshot.py`, first reported 2026-07-01)**
Still unresolved. A blank or whitespace-only query string matches every window on macOS and every xdotool result on Linux. Exit `EXIT_USAGE` before entering any resolution path.

**[medium, carry-over] Clipboard temp file written to world-accessible `/tmp` (`capture_screenshot.py:copy_file_to_clipboard`, first reported 2026-07-04)**
Still unresolved. The file is created with `tempfile.NamedTemporaryFile` in the system temp directory, which is globally readable before the clipboard tool consumes it. Suggested fix: use a private directory with an explicit `0o600` mode, matching the `private_temp_png` pattern.

**[low, carry-over] `ensure_private_directory` TOCTOU between symlink check and `mkdir` (`capture_screenshot.py:ensure_private_directory`, first reported 2026-07-02)**
Still unresolved.

**[low, carry-over] Compiled macOS helper binary is not integrity-checked between compilation and execution (`capture_screenshot.py`, first reported 2026-06-26)**
Still unresolved.

**[low, carry-over] `Path.home()` raises `RuntimeError` when `$HOME` is unset (`capture_screenshot.py:_validate_output_root`, first reported 2026-06-29)**
Still unresolved.

**[low, carry-over] `Move-Item` TOCTOU between `New-CapturePath` allocation and rename (`capture_screenshot.ps1`, first reported 2026-06-30)**
Still unresolved.

**[low, carry-over] `Protect-Directory` does not harden intermediate parent directories created by `New-Item -Force` (`capture_screenshot.ps1`, first reported 2026-07-01)**
Still unresolved.

### Bugs & regressions

**[low, NEW] `copy_file_to_clipboard` has three `subprocess.run` calls without `timeout=` (`capture_screenshot.py:468–478`)**

Each call to `wl-copy`, `xclip`, and `xsel` omits a `timeout=` argument. If a clipboard daemon hangs or blocks indefinitely on stdin, the CLI will never return. The 2026-07-04 entry covered missing timeouts on `run_command` and the clang compilation call, but this separate function was not addressed.

Suggested fix: add `timeout=30` to each `subprocess.run` call and catch `subprocess.TimeoutExpired`, forwarding to `die("clipboard tool timed out", EXIT_UNAVAILABLE)`.

**[high, carry-over] Linux X11 named-window clipboard path crashes with `KeyError: 'output'` (`capture_screenshot.py`, first reported 2026-06-10)**
Still unresolved. The `execute_plan` clipboard branch calls `run_command(command)` without an `output=` argument, so the `{output}` placeholder in the import command template is never substituted and raises `KeyError`.

**[high, carry-over] Unhandled `CalledProcessError` propagates as a raw Python traceback (`capture_screenshot.py:run_command`, first reported 2026-06-09)**
Still unresolved. When a subprocess exits non-zero, the raw exception and traceback reach the terminal instead of a structured error message.

**[medium, carry-over] `--query` flag value is silently discarded when `--target` is `fullscreen` or `active` (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved. No validation raises an error when `--query` is provided with a non-window target.

**[medium, carry-over] Windows dry-run path allocation may produce duplicate filenames across invocations (`capture_screenshot.ps1:Get-RequestFolderPath`, first reported 2026-06-23)**
Still unresolved. Second-level timestamp granularity means two calls within the same second return identical folder paths.

**[low, carry-over] No test covers `_validate_output_root` rejecting paths outside `$HOME` (`tests/test_capture_screenshot.py`, first reported 2026-06-30)**
Still unresolved.

**[low, carry-over] `test_windows_delegates_to_powershell` does not assert `-OutputRoot` forwarding (`tests/test_capture_screenshot.py`, first reported 2026-06-26)**
Still unresolved.

**[low, carry-over] PS1 dry-run evaluates `Get-WindowBounds` before dry-run guard fires (`capture_screenshot.ps1`, first reported 2026-06-27)**
Still unresolved.

**[low, carry-over] `os.replace()` after `shutil.copy2` can raise unhandled `OSError` on cross-device moves (`capture_screenshot.py`, first reported 2026-06-29)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-EEXIST `OSError` in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved. An I/O or permission error on the target directory causes the loop to silently continue rather than fail fast.

**[low, carry-over] Unquoted `args_file` path in generated shell script in `test_windows_delegates_to_powershell` (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved. A temp directory path containing spaces would break the `printf … > $args_file` line.

### Data leaks

No new findings. All window-title privacy invariants continue to hold across macOS, Linux, and Windows paths. The sanitize-label pipeline strips titles before output; only the app/owner name appears in filenames and console output.

### UX

**[low, carry-over] No test asserts that whitespace-only `--query` triggers `EXIT_USAGE` (first reported 2026-07-01)**
Still unresolved.

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11 (first reported 2026-06-27)**
Still unresolved.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation (first reported 2026-06-24)**
Still unresolved.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved.

---

## 2026-07-06

### Security

**[low] `Protect-Directory` applies the owner-only ACL after directory creation, leaving a brief window with inherited permissions (`capture_screenshot.ps1:82–112`)**
When `$Path` does not yet exist, `Protect-Directory` calls `New-Item -ItemType Directory … -Force`, which creates the directory with the parent's inherited ACL. The code then reads the ACL (`Get-Acl`), strips all existing entries, adds an owner-only `FullControl` rule, and writes it back (`Set-Acl`). During the interval between `New-Item` completing and `Set-Acl` completing — typically a few milliseconds but potentially longer under I/O pressure — any user or process with write permission on the parent directory can enumerate the newly-created folder, create files inside it, or read directory metadata. This applies to both the `$OutputRoot` directory and each per-request timestamp subdirectory. The analogous Python finding (TOCTOU between `is_symlink()` and `mkdir`, first reported 2026-06-08) applies to a different stage; this is specific to the ACL-application latency in the PowerShell path.
_Suggested fix:_ Create the directory with a restrictive security descriptor from the start (using `New-Object System.Security.AccessControl.DirectorySecurity`, adding the owner-only ACE, then passing it to `New-Item -ItemType Directory … -SecurityDescriptor`), avoiding the inherited-then-overwrite sequence entirely.

**[info] The 2026-06-13 `os.replace()` TOCTOU finding may be overstated for Linux (`capture_screenshot.py:512–514`)**
The 2026-06-13 entry states that `os.replace(temp_output, output)` "atomically replaces the target of a symlink". On Linux, `os.replace()` calls `rename(2)`, which atomically replaces the destination *directory entry* (the symlink itself), not the file the symlink points to. A race-created symlink at `output` would therefore be replaced by the screenshot file without following the link — the write lands at the correct path. macOS `rename(2)` has the same semantics. The conservative guard (`output.is_symlink()` check before `os.replace`) remains worthwhile and should be kept, but the attack scenario described in 2026-06-13 may not be achievable in practice on POSIX systems. The finding should be re-verified or re-categorised as a belt-and-suspenders check rather than a live TOCTOU.

### Bugs & regressions

**[low] Multiple `--query` values that resolve to the same underlying window ID produce duplicate captures with different filenames, silently (`capture_screenshot.py:585–595`)**
When `--allow-multiple-matches` is combined with multiple `--query` arguments (e.g., `--query Firefox --query Browser`), each query resolves its matching window IDs independently. If both queries return the same window ID — because one window's title or owner matches both terms — that ID appears in `window_ids` twice and `labels` contains both sanitized query strings. `plan_capture` builds one capture command per window ID (including duplicates), and `prepare_output_paths` produces two distinct filenames (e.g., `firefox.png` and `browser-001.png`). The same window is then captured twice, producing two files with identical pixel content but different names. No warning is emitted. This affects macOS and Linux; the Windows path independently enumerates per query but has the same duplication property.
_Suggested fix:_ After collecting all `window_ids` and `labels` from queries in `main()`, deduplicate on `window_ids` using a `seen_ids` set while building the lists, and emit a warning to stderr when IDs are dropped.

**[info] `Get-RequestFolderPath` (dry-run code path) does not call `Protect-Directory`, so reparse-point and non-directory checks on `$OutputRoot` are skipped in dry-run mode (`capture_screenshot.ps1:138–141, 351–354`)**
In production mode, `New-RequestFolder` calls `Protect-Directory $OutputRoot`, which checks for reparse points before building the request folder path. In dry-run mode, `Get-RequestFolderPath` simply constructs the path string without any validation. A dry-run invocation with a reparse-point `$OutputRoot` (which would be refused in a real capture) will print paths that silently reference a location that would be rejected if the user removed `--dry-run`, misleading the user about where files would actually land.
_Suggested fix:_ Add a lightweight reparse-point check in `Get-RequestFolderPath` (or at the dry-run call site), matching the guard already in `Protect-Directory`: check `(Get-Item -LiteralPath $OutputRoot -Force -ErrorAction SilentlyContinue).Attributes` for `ReparsePoint` and throw if found.

### Carry-overs (most critical unresolved, for visibility)

**[high, carry-over] Linux X11 named-window clipboard capture crashes with "internal error: missing output path" (`capture_screenshot.py`, first reported 2026-06-10)**
Still unresolved.

**[high, carry-over] Unhandled `CalledProcessError` from `subprocess.run(check=True)` propagates as raw Python traceback (`capture_screenshot.py:339,465`, first reported 2026-06-09)**
Still unresolved.

**[medium, carry-over] Clipboard temp file created in world-accessible `/tmp` rather than secured 0o700 request directory (`capture_screenshot.py:492`, first reported 2026-07-04)**
Still unresolved.

**[medium, carry-over] Whitespace-only or empty `--query` value silently expands capture scope to all visible windows (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[medium, carry-over] PowerShell parameter injection via leading-dash `--query` values (`capture_screenshot.py:_run_powershell_script`, first reported 2026-06-25)**
Still unresolved.

**[low, carry-over] `copy_file_to_clipboard` subprocess calls have no `timeout=` (`capture_screenshot.py:468–478`, first reported 2026-07-05)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] Unquoted `args_file` path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved.

### Data leaks

No new findings. All previously documented title-privacy invariants continue to hold across macOS, Linux, and Windows code paths. The duplicate-capture bug above produces duplicate pixel data under distinct filenames, but filenames continue to derive from the sanitized user query, never from window titles retrieved from the OS. The `Protect-Directory` ACL window exposes only directory existence and metadata to co-tenants, not screenshot content.

### UX

**[info] `detect_tools()` in `main()` scans the full cross-platform tool list regardless of current platform (`capture_screenshot.py:606–619`)**
`detect_tools` is called unconditionally with the union of macOS and Linux tools (`screencapture`, `gnome-screenshot`, `grim`, `wl-copy`, `spectacle`, `scrot`, `import`, `xdotool`, `xclip`, `xsel`). On macOS every Linux-specific entry returns `None`; on Linux `screencapture` is never found. Each `shutil.which()` call is fast, so overhead is negligible, but a platform-gated tool list would reduce noise when debugging tool detection and align detection with the actual dispatch in `plan_capture`.
_Suggested fix:_ Build the tool-name list conditionally on `platform_name` before calling `detect_tools`, passing only the tools relevant to the current platform.

**[info] No test covers the duplicate-capture scenario where multiple `--query` values resolve to the same window ID (`tests/test_capture_screenshot.py`)**
Consequent on the bug above: no test passes two query strings that produce an overlapping window ID and verifies — or flags as a defect — the resulting duplicate output paths. A regression in any deduplication fix would be silent.
_Suggested fix:_ Add a test using `CAPTURE_SCREENSHOT_TEST_WINDOWS` with a single window entry whose owner matches two different `--query` values, asserting that only one output path is printed rather than two identical captures.

**[low, carry-over] No test asserts that whitespace-only `--query` triggers `EXIT_USAGE` (first reported 2026-07-01)**
Still unresolved.

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11 (first reported 2026-06-27)**
Still unresolved.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation (first reported 2026-06-24)**
Still unresolved.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved.

## 2026-07-07

### Security

**[medium] `--query` value `--frontmost` is interpreted as a helper flag, silently capturing the frontmost window instead of searching for a match (`capture_screenshot.py:340–346`, `find_macos_window_id.m:41–48`)**
In `resolve_macos_with_helper`, the user-supplied `query` string is appended to the helper command as a bare positional argument (`command.append(query)`). The compiled C helper's argument parser treats every token beginning with `--` as a flag before falling through to `query_arg`. Passing `--query --frontmost` therefore sets `frontmost = 1` in the C code, enabling frontmost-window mode regardless of the intended search term; the helper returns whatever window is currently on top rather than searching for a match. No error is raised and no warning is emitted. A value of `--allow-multiple` instead sets `allow_multiple = 1` with `query_arg = NULL`, triggering the usage-error path (exit 64), which Python maps to `window_query_failed` — so only `--frontmost` produces a silent behavioral bypass rather than an error.
_Suggested fix:_ Separate flags from the query with a `--` sentinel in the helper invocation: build the command as `[str(helper)] + (["--allow-multiple"] if allow_multiple else []) + ["--", query]`, then update the C argument loop to stop flag processing at `"--"`. This is the standard POSIX convention for end-of-options.

### Bugs & regressions

**[medium] Window-name matching is case-insensitive on macOS but case-sensitive on Linux X11, causing identical queries to succeed on one platform and silently fail on the other (`find_macos_window_id.m:10`, `capture_screenshot.py:134`, `capture_screenshot.py:392`)**
On macOS, `CFStringFind` is called with `kCFCompareCaseInsensitive` in the helper, and `_matches()` uses `.casefold()` for the in-process mock path, making all macOS window matching case-insensitive. On Linux, `resolve_linux_named_window` passes `_escape_ere(query)` verbatim to `xdotool search --name`, which applies POSIX ERE with default case-sensitive matching. A query of `--query terminal` succeeds on macOS when the owner is "Terminal" but returns "no matching window" on Linux unless the case matches exactly. Cross-platform agent scripts relying on this skill will exhibit inconsistent behaviour depending on the host OS.
_Suggested fix:_ Wrap the escaped query in a case-insensitive ERE alternation before passing to xdotool, or prefix it with `(?i)` if the installed xdotool version supports Perl-compatible regex extensions. Alternatively, convert the query and each candidate name to lowercase in Python before passing the ERE, and document the case-normalisation.

**[low] macOS fullscreen `screencapture` omits the `-x` (silence) flag, playing an audible camera shutter on fullscreen captures while named-window captures are silent (`capture_screenshot.py:220`)**
The fullscreen branch at line 220 builds the command as `(screencapture, "{output}")` or `(screencapture, "-c")` with no `-x` flag. The named-window branches at lines 228 and 230 both include `-x`, suppressing sound. The result is that a fullscreen capture produces an audible shutter sound on macOS — potentially startling the user, breaking meeting recordings, or revealing that a capture occurred — while window captures are silent. There is no documented reason for this inconsistency.
_Suggested fix:_ Add `-x` to both fullscreen command variants: `(screencapture, "-x", "{output}")` and `(screencapture, "-x", "-c")`.

### Data leaks

No new findings. The `--frontmost` bypass bug above causes the wrong window to be captured, but the captured image still goes to the user-specified, ACL-protected destination; no data leaks to third parties. Case-sensitivity mismatches affect window selection but not title disclosure. All previously verified title-privacy invariants (sanitized labels, no title in error messages, no title in filenames) remain intact.

### UX

**[info] `plan_capture` declares a `label: str` parameter that is never read inside the function (`capture_screenshot.py:200–299`)**
The signature of `plan_capture` includes `label: str`, and the call site in `main()` supplies `label=labels[0] if labels else "capture"` (line 628). However, `label` is not referenced anywhere in the function body; all output-filename logic lives in `prepare_output_paths` / `unique_capture_path`, which are called before `plan_capture`. A reader of `plan_capture` may assume `label` influences the plan (e.g., that the output filename derives from it) when it does not. The dead parameter also appears in tests via `plan_capture(..., label="...", ...)` calls, which have no effect on the plan produced.
_Suggested fix:_ Remove the `label` parameter from `plan_capture`'s signature and drop the corresponding `label=` argument from the call site in `main()`. Update any test assertions that pass `label=` to `plan_capture`.

### Carry-overs (most critical unresolved, for visibility)

**[high, carry-over] Linux X11 named-window clipboard capture crashes with "internal error: missing output path" (`capture_screenshot.py`, first reported 2026-06-10)**
Still unresolved.

**[high, carry-over] Unhandled `CalledProcessError` from `subprocess.run(check=True)` propagates as raw Python traceback (`capture_screenshot.py:339,465`, first reported 2026-06-09)**
Still unresolved.

**[medium, carry-over] Clipboard temp file created in world-accessible `/tmp` rather than secured 0o700 request directory (`capture_screenshot.py:492`, first reported 2026-07-04)**
Still unresolved.

**[medium, carry-over] Whitespace-only or empty `--query` value silently expands capture scope to all visible windows (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[medium, carry-over] PowerShell parameter injection via leading-dash `--query` values (`capture_screenshot.py:_run_powershell_script`, first reported 2026-06-25)**
Still unresolved.

**[medium, carry-over] macOS `--allow-multiple-matches` + `--destination clipboard` silently discards all captures except the last (`capture_screenshot.py:225–231`, first reported 2026-06-11)**
Still unresolved.

**[low, carry-over] `copy_file_to_clipboard` subprocess calls have no `timeout=` (`capture_screenshot.py:468–478`, first reported 2026-07-05)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] Unquoted `args_file` path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved.

---

## 2026-07-08

### Security

**[medium, NEW] `resolve_linux_named_window` passes user query to `xdotool --name` without a `--` end-of-options separator (`capture_screenshot.py:392`)**

`xdotool search --name <pattern>` receives the query string as the final argument on the command list. Because `_escape_ere` only escapes POSIX ERE metacharacters and does not escape leading hyphens, a query passed as `--query=--someflag` (using argparse's `=` form to embed a leading dash) arrives at xdotool as a bare flag token (e.g., `xdotool search --name --onlyvisible`). Depending on the xdotool version and flag name, this either changes capture scope silently or produces an xdotool usage error that Python maps to `no_matching_window`. This parallels the macOS helper `--frontmost` injection found on 2026-07-07, but affects the Linux X11 path. The Python argparse definition (`action='append'`) means `--query --name` would fail argparse itself, but `--query=--name` succeeds and delivers the dash-prefixed string to `resolve_linux_named_window`.
_Suggested fix:_ Insert a `--` sentinel before the pattern in the xdotool invocation: `[xdotool, "search", "--name", "--", _escape_ere(query)]`. Verify first that the installed xdotool version honours `--` (most POSIX-compliant parsers do); if not, reject queries starting with `-` in `resolve_linux_named_window` before building the command.

**[low, NEW] `not_capturable_message` reflects the user query string into stderr without stripping control characters (`capture_screenshot.py:148–151`)**

The function constructs error messages such as `f"'{query}' is minimized — restore it and retry."` where `query` is taken directly from `args.query` without sanitization. A query containing ANSI escape sequences (e.g., `\x1b[31m`) would inject terminal colour codes into the stderr stream, potentially disrupting log aggregators, CI output renderers, or terminal emulators that interpret escape codes. While the user controls their own query and the impact is self-inflicted in interactive use, automated callers that pipe stderr into structured logging or display systems could be affected.
_Suggested fix:_ Strip non-printable characters before embedding the query in messages, e.g., `re.sub(r'[\x00-\x1f\x7f]', '', query)`, or simply replace any character outside printable ASCII/Unicode with `?`.

**[medium, carry-over] `--query` value `--frontmost` interpreted as a helper flag on macOS (`capture_screenshot.py:340–346`, `find_macos_window_id.m:41–48`, first reported 2026-07-07)**
Still unresolved.

**[medium, carry-over] PowerShell parameter injection via leading-dash `--query` values (`capture_screenshot.py:_run_powershell_script`, first reported 2026-06-25)**
Still unresolved.

**[medium, carry-over] Clipboard temp file created by `NamedTemporaryFile` lands in world-accessible `/tmp` (`capture_screenshot.py:492`, first reported 2026-07-04)**
Still unresolved.

**[low, carry-over] `Protect-Directory` applies ACL after directory creation, leaving a brief window with inherited permissions (`capture_screenshot.ps1:82–112`, first reported 2026-07-06)**
Still unresolved.

**[low, carry-over] Compiled macOS helper binary is not integrity-checked between compilation and execution (`capture_screenshot.py`, first reported 2026-06-26)**
Still unresolved.

**[low, carry-over] `Path.home()` raises `RuntimeError` when `$HOME` is unset (`capture_screenshot.py:_validate_output_root`, first reported 2026-06-29)**
Still unresolved.

**[low, carry-over] `Move-Item` TOCTOU between `New-CapturePath` allocation and rename (`capture_screenshot.ps1`, first reported 2026-06-30)**
Still unresolved.

**[low, carry-over] `Protect-Directory` does not harden intermediate parent directories created by `New-Item -Force` (`capture_screenshot.ps1`, first reported 2026-07-01)**
Still unresolved.

### Bugs & regressions

**[low, NEW] `execute_plan` desktop loop does not roll back successfully-written screenshots when a subsequent capture in the same request fails (`capture_screenshot.py:505–519`)**

The multi-window capture loop calls `os.replace(temp_output, output)` and `print(output)` on each iteration before moving to the next window. If `run_command` succeeds for the first window but fails for the second (e.g., the second window closed between captures, which raises `CalledProcessError` from `check=True`), the first screenshot remains on disk and has already been printed to stdout, but the process exits non-zero. The caller cannot distinguish a complete capture from a partial one via the exit code alone. No rollback of successfully-written files occurs. This is amplified by the existing high-severity unhandled `CalledProcessError` bug (2026-06-09): the failure manifests as a raw Python traceback rather than a structured error message, making it harder to detect the partial state programmatically.
_Suggested fix:_ Collect all `(command, output, temp_output)` tuples, attempt all captures writing to temp files first, then move all temp files to final destinations atomically as a second pass. On any failure in the first pass, clean up all temp files and exit cleanly before any final-destination file is written.

**[info, NEW] `_test_windows()` raises bare `json.JSONDecodeError` for malformed `CAPTURE_SCREENSHOT_TEST_WINDOWS` input instead of calling `die()` (`capture_screenshot.py:310`)**

`json.loads(raw)` is called without a try/except wrapper. A malformed JSON value in the environment variable (e.g., a truncated string or stray quote) produces an unhandled exception traceback rather than a structured `die()` exit with `EXIT_USAGE`. This only affects the testing/development path (`CAPTURE_SCREENSHOT_TEST_WINDOWS` is never set in production), but a developer mis-formatting the env-var value would see a confusing traceback rather than a clear usage error.
_Suggested fix:_ Wrap the `json.loads(raw)` call in `try/except json.JSONDecodeError` and re-raise via `die("CAPTURE_SCREENSHOT_TEST_WINDOWS: invalid JSON", EXIT_USAGE)`.

**[high, carry-over] Linux X11 named-window clipboard capture crashes with "internal error: missing output path" (`capture_screenshot.py`, first reported 2026-06-10)**
Still unresolved.

**[high, carry-over] Unhandled `CalledProcessError` from `subprocess.run(check=True)` propagates as raw Python traceback (`capture_screenshot.py:339,465`, first reported 2026-06-09)**
Still unresolved.

**[medium, carry-over] Case-insensitive matching on macOS vs. case-sensitive matching on Linux X11 for identical queries (`find_macos_window_id.m:10`, `capture_screenshot.py:134,392`, first reported 2026-07-07)**
Still unresolved.

**[medium, carry-over] Whitespace-only or empty `--query` value silently expands capture scope to all visible windows (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[medium, carry-over] macOS `--allow-multiple-matches` + `--destination clipboard` silently discards all captures except the last (`capture_screenshot.py:225–231`, first reported 2026-06-11)**
Still unresolved.

**[medium, carry-over] Windows dry-run path allocation may produce duplicate folder paths across same-second invocations (`capture_screenshot.ps1:Get-RequestFolderPath`, first reported 2026-06-23)**
Still unresolved.

**[low, carry-over] macOS fullscreen `screencapture` omits `-x` silence flag, playing an audible shutter while named-window captures are silent (`capture_screenshot.py:220`, first reported 2026-07-07)**
Still unresolved.

**[low, carry-over] Multiple `--query` values resolving to the same underlying window ID produce duplicate captures without warning (`capture_screenshot.py:585–595`, first reported 2026-07-06)**
Still unresolved.

**[low, carry-over] `copy_file_to_clipboard` subprocess calls have no `timeout=` (`capture_screenshot.py:468–478`, first reported 2026-07-05)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] `--query` flag value is silently discarded when `--target` is `fullscreen` or `active` (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[low, carry-over] `os.replace()` after atomic temp write can raise unhandled `OSError` on cross-device moves (`capture_screenshot.py`, first reported 2026-06-29)**
Still unresolved.

**[low, carry-over] PS1 dry-run evaluates `Get-WindowBounds` before dry-run guard fires (`capture_screenshot.ps1`, first reported 2026-06-27)**
Still unresolved.

**[low, carry-over] No test covers `_validate_output_root` rejecting paths outside `$HOME` (`tests/test_capture_screenshot.py`, first reported 2026-06-30)**
Still unresolved.

**[low, carry-over] `test_windows_delegates_to_powershell` does not assert `-OutputRoot` forwarding (`tests/test_capture_screenshot.py`, first reported 2026-06-26)**
Still unresolved.

**[low, carry-over] Unquoted `args_file` path in generated shell script in `test_windows_delegates_to_powershell` (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved.

### Data leaks

No new findings. The Linux xdotool leading-dash injection could redirect a capture to an unintended window, but the destination remains the user-controlled, ACL-protected output directory; no data reaches third parties. The `not_capturable_message` issue echoes the user's own query string back to the user, not any OS-retrieved window title; no title-privacy invariant is broken. All previously verified title-privacy invariants (sanitized labels, no title in error messages, no title in filenames) remain intact across macOS, Linux, and Windows paths.

### UX

**[info, NEW] `install.sh` clones from GitHub via HTTPS without commit-hash pinning or signature verification (`install.sh:27`)**

`git clone --quiet "$REPO" "$dest"` always clones the current HEAD of the default branch with no integrity anchor. A compromised or force-pushed upstream commit would be silently installed for all new users without any indication that the content changed. For a privacy-first utility distributed as a directly-executable agent skill, this is a notable supply-chain consideration.
_Suggested fix:_ Either document a specific commit SHA in the README that users can verify with `git log` after cloning, or add a post-clone `git verify-commit HEAD` step (if the repository signs releases), or at minimum add a note in the install output advising users to inspect the cloned scripts before running.

**[low, carry-over] No test asserts that whitespace-only `--query` triggers `EXIT_USAGE` (first reported 2026-07-01)**
Still unresolved.

**[low, carry-over] README "background/occluded capture" claim is not qualified for Linux X11 (first reported 2026-06-27)**
Still unresolved.

**[info, carry-over] `plan_capture` declares a `label: str` parameter that is never read inside the function (`capture_screenshot.py:200–299`, first reported 2026-07-07)**
Still unresolved.

**[info, carry-over] `detect_tools()` scans the full cross-platform tool list regardless of current platform (`capture_screenshot.py:606–619`, first reported 2026-07-06)**
Still unresolved.

**[info, carry-over] macOS helper binary is recompiled from source on every capture invocation (first reported 2026-06-24)**
Still unresolved.

**[info, carry-over] PS1 has no `-OutputRoot` home-directory containment check when invoked directly (first reported 2026-06-23)**
Still unresolved.

---

## 2026-07-09

### Security

No new findings. All previously documented security issues remain unresolved and are summarised in the carry-over block below.

---

### Bugs & regressions

**[low, NEW] PowerShell multi-query loop does not roll back successfully-captured screenshots when a later query fails (`capture_screenshot.ps1:380–401`)**

The `foreach ($queryText in $Query)` loop calls `Capture-ToDestination` and `Write-Output $path` for each matched handle before moving to the next query. If query N captures and prints successfully but query N+1 fails (e.g., `throw 'no matching on-screen window found'` or `exit 75` for a minimized window), query N's screenshot file remains on disk and its path has already been emitted to stdout. The script exits non-zero, but callers cannot distinguish a fully-completed multi-query capture from a partial one: both cases print some paths to stdout then exit with a non-zero code. This mirrors the Python finding from 2026-07-08 (`execute_plan` desktop loop), which covers the same gap in the Python path.
_Suggested fix:_ Mirror the Python approach: write all captures to temp paths first, verify all succeed, then move to final destinations and print paths. On any failure, clean up all temp files and exit cleanly before emitting any final-destination path to stdout.

---

### Data leaks

No new findings. All window-title privacy invariants continue to hold across macOS, Linux, and Windows code paths. The PS1 partial-capture bug above involves pixel data only; no window title metadata is written to stdout or any output file. All error messages continue to echo only user-supplied query strings, never OS-retrieved window titles.

---

### UX

**[low, NEW] `run_command` does not redirect screenshot tool stdout; verbose tool output can contaminate the structured output contract (`capture_screenshot.py:452–465`)**

`subprocess.run(args, check=True)` is invoked with no `stdout=` argument, so the called tool's stdout is inherited from the parent process. `capture_screenshot.py` emits a single file path (or `clipboard`) per capture on stdout; callers parse this line as the only output. If the underlying screenshot tool itself prints to stdout — for example, some `spectacle` builds emit `"Screenshot saved to /path"`, older `scrot` versions in verbose mode print the destination path, and certain `gnome-screenshot` builds print status — a spurious extra line appears on stdout before or after `print(output)`, breaking any caller that expects exactly one line per capture.
_Suggested fix:_ Pass `stdout=subprocess.DEVNULL` (or `stdout=subprocess.PIPE` and discard) in `run_command`, unless the tool is expected to write its capture to stdout (none of the current tools do). Add stderr forwarding explicitly if tool error messages should still surface.

**[low, NEW] `resolve_linux_named_window` spawns one `xprop`/`xwininfo` subprocess per matched window ID with no upper bound on subprocess count (`capture_screenshot.py:405`)**

After `xdotool search --name` returns a list of window IDs, `_linux_window_is_viewable` is called for every ID in a list comprehension:
```python
classified = [(wid, _linux_window_is_viewable(wid, tools)) for wid in ids]
```
`_linux_window_is_viewable` spawns up to two subprocesses per ID (`xprop` then `xwininfo` as fallback). A generic query (e.g., `--query "e"`) matching hundreds of open browser tabs causes proportional subprocess spawning before the `multiple_matches` guard can fire. In a desktop with many open windows this can cause noticeable latency; in adversarial input scenarios (user passes a single-character query) it could saturate the subprocess pool.
_Suggested fix:_ Apply an early limit — e.g., cap `ids` at 32 before entering `_linux_window_is_viewable` classification; return `multiple_matches` immediately if `ids` exceeds the cap. This eliminates O(N) subprocess spawning for clearly non-specific queries while preserving correct behaviour for typical usage.

---

### Carry-overs (critical unresolved, for visibility)

**[high, carry-over] Linux X11 named-window clipboard capture crashes with "internal error: missing output path" (`capture_screenshot.py`, first reported 2026-06-10)**
Still unresolved.

**[high, carry-over] Unhandled `CalledProcessError` from `subprocess.run(check=True)` propagates as raw Python traceback (`capture_screenshot.py:339,465`, first reported 2026-06-09)**
Still unresolved.

**[medium, carry-over] `--query` value `--frontmost` silently captures the frontmost window on macOS instead of searching by name (`capture_screenshot.py:340–346`, `find_macos_window_id.m:41–48`, first reported 2026-07-07)**
Still unresolved.

**[medium, carry-over] `resolve_linux_named_window` passes user query to `xdotool --name` without a `--` end-of-options separator, allowing leading-dash injection (`capture_screenshot.py:392`, first reported 2026-07-08)**
Still unresolved.

**[medium, carry-over] PowerShell parameter injection via leading-dash `--query` values forwarded from Python (`capture_screenshot.py:_run_powershell_script`, first reported 2026-06-25)**
Still unresolved.

**[medium, carry-over] Clipboard temp file created by `NamedTemporaryFile` lands in world-accessible `/tmp` (`capture_screenshot.py:492`, first reported 2026-07-04)**
Still unresolved.

**[medium, carry-over] Whitespace-only or empty `--query` silently expands capture scope to all visible windows (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[low, carry-over] `not_capturable_message` reflects user query into stderr without stripping control characters (`capture_screenshot.py:148–151`, first reported 2026-07-08)**
Still unresolved.

**[low, carry-over] `copy_file_to_clipboard` subprocess calls have no `timeout=` (`capture_screenshot.py:468–478`, first reported 2026-07-05)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] macOS fullscreen `screencapture` omits `-x` silence flag, playing audible shutter while named-window captures are silent (`capture_screenshot.py:220`, first reported 2026-07-07)**
Still unresolved.

**[low, carry-over] Unquoted `args_file` path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved.

---

## 2026-07-10

### Security

**[low, NEW] `ensure_private_directory` catches `PermissionError` only, leaving other `OSError` subclasses unhandled in a privacy-critical code path (`capture_screenshot.py:109`)**

`ensure_private_directory` wraps `path.chmod(0o700)` in:
```python
try:
    path.chmod(0o700)
except PermissionError:
    die("could not secure screenshots folder permissions", EXIT_PRIVACY)
```
`path.chmod()` can raise `OSError` subclasses beyond `PermissionError`: `OSError(EROFS)` on a read-only filesystem, `OSError(EPERM)` from a security policy (e.g., SELinux or AppArmor denial that is not mapped to EACCES), or `NotADirectoryError`. These propagate as unhandled Python exceptions — a raw traceback with implicit exit code 1 — rather than a structured `die()` message with `EXIT_PRIVACY`. This is the same gap reported for `secure_file()` on 2026-06-18 (line 117–121), but `ensure_private_directory` (line 109) was not covered by that finding. The function is called for both the output root and per-request subdirectory, making this path privacy-critical.
_Suggested fix:_ Broaden to `except OSError as e:` and call `die(f"could not secure screenshots folder permissions: {e.strerror}", EXIT_PRIVACY)`, consistent with the fix proposed for `secure_file()` on 2026-06-18.

---

### Bugs & regressions

No new findings. All known bugs are listed in the carry-over section below.

---

### Data leaks

No new findings. Window-title privacy invariants continue to hold across all three platform paths. All error messages echo only the user-supplied query or static text; no OS-retrieved window title reaches any output channel. The `ensure_private_directory` OSError gap (Security above) exposes only the structured error message, not screenshot content or title metadata.

---

### UX

**[info, NEW] `--allow-multiple-matches` with a broad query produces O(N) capture operations with no warning or soft cap (`capture_screenshot.py:225–231`, `execute_plan`)**

When `--allow-multiple-matches` is set and a short or generic query matches N windows, `plan_capture` builds N capture commands and `execute_plan` runs them sequentially. On macOS this means N `screencapture -l <id>` invocations each writing a PNG to disk; on Linux X11 it means N `import -window <id>` invocations. No count is previewed before capture begins, no warning is emitted when N exceeds a reasonable threshold, and no soft limit prevents a pathological query (e.g., `--query "e"` on a system with 60+ open windows) from saturating disk I/O or producing dozens of unexpected files. The 2026-07-09 entry documented unbounded *resolution-phase* xprop/xwininfo subprocess spawning on Linux; this is a distinct issue about unbounded *capture-phase* operations on all platforms.
_Suggested fix:_ After collecting `window_ids` in `main()` and before invoking `plan_capture`, print a warning to stderr when `len(window_ids)` exceeds a threshold (e.g., 10): `"warning: --allow-multiple-matches matched N windows — capturing all of them."` Optionally add a `--max-matches INT` flag to enforce a hard cap.

**[info, NEW] `install.sh` `$HOME`-unset risk from 2026-06-10 is mitigated by `set -u`; the remaining concern is an empty-string `$HOME` (`install.sh:2,36,41,47`)**

The 2026-06-10 entry warned that an unset `$HOME` causes `"$HOME/.claude/skills"` to expand to `"/.claude/skills"`. However, `install.sh` starts with `set -euo pipefail` (line 2); the `-u` flag causes bash to abort with "HOME: unbound variable" before any path is evaluated if `$HOME` is genuinely unset. The unset case is therefore already safe. The remaining concern is `HOME=""` (exported as an empty string, which `-u` does not flag): `"$HOME/.claude/skills"` evaluates to `"/.claude/skills"`, and on a machine where `/.claude/skills` happens to exist (e.g., a pre-built container image), `clone_if_missing` would attempt to write there — potentially as root on a system account. The remediation from 2026-06-10 (`[ -z "$HOME" ] && exit 1`) is correct but should use `[ -z "${HOME:-}" ]` to be safe under `set -u`, or test for both unset and empty: `[[ -z "${HOME-unset}" || "$HOME" == "unset" ]]`.
_Suggested fix:_ Replace the proposed guard with: `[[ -z "${HOME:-}" ]] && { echo "error: \$HOME is not set or is empty"; exit 1; }` near the top of the script.

---

### Carry-overs (critical unresolved, for visibility)

**[high, carry-over] Linux X11 named-window clipboard capture crashes with "internal error: missing output path" (`capture_screenshot.py`, first reported 2026-06-10)**
Still unresolved.

**[high, carry-over] Unhandled `CalledProcessError` from `subprocess.run(check=True)` propagates as raw Python traceback (`capture_screenshot.py:339,465`, first reported 2026-06-09)**
Still unresolved.

**[medium, carry-over] `--query` value `--frontmost` silently captures the frontmost window on macOS instead of searching by name (`capture_screenshot.py:340–346`, `find_macos_window_id.m:41–48`, first reported 2026-07-07)**
Still unresolved.

**[medium, carry-over] `resolve_linux_named_window` passes user query to `xdotool --name` without a `--` end-of-options separator, allowing leading-dash injection (`capture_screenshot.py:392`, first reported 2026-07-08)**
Still unresolved.

**[medium, carry-over] PowerShell parameter injection via leading-dash `--query` values forwarded from Python (`capture_screenshot.py:_run_powershell_script`, first reported 2026-06-25)**
Still unresolved.

**[medium, carry-over] Clipboard temp file created by `NamedTemporaryFile` lands in world-accessible `/tmp` (`capture_screenshot.py:492`, first reported 2026-07-04)**
Still unresolved.

**[medium, carry-over] Whitespace-only or empty `--query` silently expands capture scope to all visible windows (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[medium, carry-over] macOS `--allow-multiple-matches` + `--destination clipboard` silently discards all captures except the last (`capture_screenshot.py:225–231`, first reported 2026-06-11)**
Still unresolved.

**[low, carry-over] `not_capturable_message` reflects user query into stderr without stripping control characters (`capture_screenshot.py:148–151`, first reported 2026-07-08)**
Still unresolved.

**[low, carry-over] `copy_file_to_clipboard` subprocess calls have no `timeout=` (`capture_screenshot.py:468–478`, first reported 2026-07-05)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] macOS fullscreen `screencapture` omits `-x` silence flag, playing audible shutter while named-window captures are silent (`capture_screenshot.py:220`, first reported 2026-07-07)**
Still unresolved.

**[low, carry-over] Multiple `--query` values resolving to the same underlying window ID produce duplicate captures without warning (`capture_screenshot.py:585–595`, first reported 2026-07-06)**
Still unresolved.

**[low, carry-over] `run_command` does not redirect screenshot tool stdout; verbose tool output can contaminate the structured output contract (`capture_screenshot.py:452–465`, first reported 2026-07-09)**
Still unresolved.

**[low, carry-over] Unquoted `args_file` path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved.

---

## 2026-07-11

### Security

**[low, NEW] `_run_powershell_script` passes control to PowerShell with no timeout (`capture_screenshot.py` ~line 551)**

`subprocess.run(cmd).returncode` delegates the entire Windows capture to PowerShell without a `timeout=` argument. If PowerShell blocks (e.g., awaiting an OS permission dialog, a UAC prompt, or a frozen GUI subsystem), the Python parent hangs indefinitely — no watchdog, no SIGALRM equivalent. The `subprocess.CalledProcessError`/keyboard-interrupt path still works, but an automated or headless invocation has no escape hatch. This is distinct from the `copy_file_to_clipboard` timeout finding (2026-07-05), which covers Linux clipboard tools; the Windows delegation path was not previously flagged.
_Suggested fix:_ Add `timeout=60` (or a CLI-configurable value) to the `subprocess.run` call and handle `subprocess.TimeoutExpired` with a clean `die()` message.

**[low, NEW] `capture_screenshot.ps1` accepts arbitrary `$OutputRoot` with no home-containment check (`capture_screenshot.ps1`, param block)**

The Python orchestrator validates that `--output-root` is within the user's home directory (`_validate_output_root`) before delegating to PowerShell. However, SKILL.md documents the `.ps1` as a standalone fallback: `powershell.exe ... -File "...\capture_screenshot.ps1" -ConsentConfirmed -Destination desktop -Target fullscreen`. A caller who invokes the PowerShell script directly can pass any path as `-OutputRoot` (including `C:\Windows\System32\screenshots`) without any rejection. The user's own ACL prevents writes to protected system directories in the common case, but the constraint is silently unenforced at the PowerShell layer with no warning.
_Suggested fix:_ Add an explicit home-containment guard near the top of the `.ps1` after the `$ConsentConfirmed` check: compare the resolved `$OutputRoot` against `[Environment]::GetFolderPath('UserProfile')` and throw if it is outside.

---

### Bugs & regressions

**[low, NEW] `Get-WindowTitle` in PowerShell truncates window titles silently at 1024 characters (`capture_screenshot.ps1`, `Get-WindowTitle` function)**

`Get-WindowTitle` allocates a `[Text.StringBuilder]::new(1024)` and passes it to `GetWindowText`. Windows silently truncates any title longer than the buffer capacity. Modern browsers can display page titles well beyond 1024 characters (e.g., a URL-derived title). If a user's `--query` string matches only the portion of a title that was truncated away, `Find-WindowHandles` reports zero matches and the script exits with `no matching on-screen window found`. No warning is emitted about truncation.
_Suggested fix:_ Increase the buffer to 32 767 characters (the documented `GetWindowText` maximum on Windows) or use a two-pass approach: call `GetWindowTextLength` first, then allocate accordingly.

---

### Data leaks

No new findings. Window-title privacy invariants continue to hold across all three platform paths. The `Get-WindowTitle` truncation bug above does not cause title leakage — it causes missed matches — so no screenshot content or metadata is exposed by it.

---

### UX

**[low, NEW] `resolve_macos_with_helper` lets `subprocess.CalledProcessError` propagate uncaught if `clang` compilation fails (`capture_screenshot.py`, `resolve_macos_with_helper`)**

`subprocess.run([clang, "-framework", "ApplicationServices", str(helper_source), "-o", str(helper)], check=True)` raises `subprocess.CalledProcessError` if the compiler exits non-zero (e.g., ApplicationServices framework unavailable on a stripped macOS install, or the `.m` source has a parse error after a partial update). No `try/except` wraps this call, so the exception propagates through `resolve_macos_with_helper` → `main()` and produces a raw Python traceback — including full filesystem paths — rather than a structured `ResolutionResult` error message with an appropriate exit code. The return-type contract (`ResolutionResult`) is broken on this path.
_Suggested fix:_ Wrap the `subprocess.run` call in `try/except subprocess.CalledProcessError as e` and return `ResolutionResult(False, "helper_compile_failed", f"macOS window helper compilation failed: {e}")`.

---

### Carry-overs (unresolved from prior entries)

**[low, carry-over] `CGWindowID` stored in a signed `int` in `find_macos_window_id.m` (first reported 2026-06-11)**
Still unresolved.

**[medium, carry-over] Clipboard temp file created by `NamedTemporaryFile` lands in world-accessible `/tmp` (`capture_screenshot.py:492`, first reported 2026-07-04)**
Still unresolved.

**[medium, carry-over] Whitespace-only or empty `--query` silently expands capture scope to all visible windows (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[medium, carry-over] macOS `--allow-multiple-matches` + `--destination clipboard` silently discards all captures except the last (`capture_screenshot.py:225–231`, first reported 2026-06-11)**
Still unresolved.

**[low, carry-over] `not_capturable_message` reflects user query into stderr without stripping control characters (`capture_screenshot.py:148–151`, first reported 2026-07-08)**
Still unresolved.

**[low, carry-over] `copy_file_to_clipboard` subprocess calls have no `timeout=` (`capture_screenshot.py:468–478`, first reported 2026-07-05)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] macOS fullscreen `screencapture` omits `-x` silence flag, playing audible shutter while named-window captures are silent (`capture_screenshot.py:220`, first reported 2026-07-07)**
Still unresolved.

**[low, carry-over] Multiple `--query` values resolving to the same underlying window ID produce duplicate captures without warning (`capture_screenshot.py:585–595`, first reported 2026-07-06)**
Still unresolved.

**[low, carry-over] `run_command` does not redirect screenshot tool stdout; verbose tool output can contaminate the structured output contract (`capture_screenshot.py:452–465`, first reported 2026-07-09)**
Still unresolved.

**[low, carry-over] Unquoted `args_file` path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved.

**[low, carry-over] `ensure_private_directory` catches `PermissionError` only, leaving other `OSError` subclasses unhandled in a privacy-critical code path (`capture_screenshot.py:109`, first reported 2026-07-10)**
Still unresolved.

**[info, carry-over] `--allow-multiple-matches` with a broad query produces O(N) capture operations with no warning or soft cap (`capture_screenshot.py:225–231`, `execute_plan`, first reported 2026-07-10)**
Still unresolved.

**[info, carry-over] `install.sh` `HOME=""` risk: an exported empty-string `$HOME` passes `-u` guard and expands clone paths to `/.claude/skills/...` (`install.sh:2,36,41,47`, first reported 2026-07-10)**
Still unresolved.

---

## 2026-07-12

### Security

**[medium, NEW] PowerShell parameter-binding injection via leading-dash `--query` values (`capture_screenshot.py`, `_run_powershell_script`)**

`_run_powershell_script` appends each `--query` value verbatim as `["-Query", q]` to the PowerShell command list. If `q` starts with `-` and matches a named parameter in `capture_screenshot.ps1` — specifically `-OutputRoot`, `-DryRun`, or `-AllowMultipleMatches` — PowerShell's argument parser consumes it as that parameter rather than as the string value for `$Query`. A query of `-OutputRoot` followed by a subsequent token would bind that token to `$OutputRoot`, potentially overriding the home-validated path Python explicitly passes later. A query of `-DryRun` could silently suppress the actual capture. Because the query string derives from user-supplied window/app names, a user can craft a value that triggers this.
_Suggested fix:_ Add a guard in `_run_powershell_script` that rejects any `--query` value beginning with `-` (e.g. `if q.startswith("-"): die("query must not begin with '-'", EXIT_USAGE)`), or insert the PowerShell stop-parsing token `--%` immediately before the query arguments, or validate inside the `.ps1` that no `$Query` element starts with `-`.

**[info, NEW] TOCTOU between `_validate_output_root` and `ensure_private_directory` via intermediate-component symlink replacement (`capture_screenshot.py`, `_validate_output_root` / `ensure_private_directory`)**

`_validate_output_root` calls `path.resolve()` (which follows all symlinks) and confirms the canonical path is within the user's home. `ensure_private_directory` later checks `path.is_symlink()` only on the final path component. In the window between these two calls an attacker with write access to a parent directory could replace an intermediate component (e.g. swap `~/Desktop` for a link to `/etc`). The final-component `is_symlink()` check returns False for the not-yet-created leaf, `mkdir(parents=True)` follows the intermediate symlink, and the directory is created outside the home. Exploitation requires write access to directories the user already controls, limiting real-world impact.
_Suggested fix:_ After computing the safe canonical path, `mkdir()` against the resolved (canonical) path directly rather than the user-supplied path, so intermediate-component changes after `resolve()` have no effect.

---

### Bugs & regressions

**[low, NEW] `_linux_window_is_viewable` xwininfo parse may false-positive on a window title containing "isviewable" (`capture_screenshot.py`, `_linux_window_is_viewable`)**

```python
return "isviewable" in proc.stdout.lower().replace(" ", "")
```

`xwininfo` includes the window title on the first output line (e.g. `xwininfo: Window id: 0x1234 "IsViewable Dashboard"`). The check scans the full stdout after space removal. A window whose title happens to contain the substring "isviewable" would be classified as Map State = IsViewable regardless of its actual map state, potentially causing the script to attempt to capture a minimized or unmapped window.
_Suggested fix:_ Isolate the Map State line before checking: `m = re.search(r'map state:\s*(\S+)', proc.stdout, re.IGNORECASE)` and compare `m.group(1).lower() == "isviewable"` only if the match succeeds.

**[low, NEW] Unhandled `subprocess.CalledProcessError` from `copy_file_to_clipboard` produces a raw Python traceback (`capture_screenshot.py`, `copy_file_to_clipboard` / `execute_plan`)**

`copy_file_to_clipboard` calls `subprocess.run(..., check=True)` for `wl-copy`, `xclip`, and `xsel`. If any exits non-zero (e.g. no Wayland compositor, no X display, xclip crashes), `CalledProcessError` propagates uncaught through `execute_plan` and `main()`, emitting a raw Python traceback that includes full binary paths rather than a structured error message with a defined exit code. This is distinct from the existing timeout carry-over (process hangs) — this is the process-fails-immediately path.
_Suggested fix:_ Wrap the `subprocess.run` call in `copy_file_to_clipboard` in `try/except subprocess.CalledProcessError as e: die(f"clipboard tool failed ({e.returncode})", EXIT_UNAVAILABLE)`. Apply the same pattern to `run_command` for consistency.

---

### Data leaks

No new findings. Window-title privacy invariants remain intact across all platform paths; the xwininfo false-positive above risks a failed capture attempt, not a title disclosure.

---

### UX

**[low, NEW] `Find-WindowHandles` in PowerShell matches all visible windows when `$Needle` is an empty string (`capture_screenshot.ps1`, `Find-WindowHandles`)**

`[string].IndexOf("", [StringComparison]::OrdinalIgnoreCase)` always returns 0 (≥ 0), so an empty query string forwarded from Python causes `Find-WindowHandles` to collect every visible window. Python's `main()` verifies `args.query` has at least one element but does not reject the empty-string element, so `--query ""` reaches PowerShell unblocked. The result is either a "multiple matching windows found" error or, with `-AllowMultipleMatches`, a bulk capture of the entire desktop. This is the PowerShell-specific analogue of the existing whitespace-only `--query` carry-over on macOS/Linux.
_Suggested fix:_ Add `if ([string]::IsNullOrWhiteSpace($queryText)) { throw 'query must not be empty or whitespace' }` at the top of the `foreach ($queryText in $Query)` loop; also add a parallel guard in `main()` in `capture_screenshot.py`.

---

### Carry-overs (unresolved from prior entries)

**[low, carry-over] `CGWindowID` stored in a signed `int` in `find_macos_window_id.m` (first reported 2026-06-11)**
Still unresolved.

**[medium, carry-over] Clipboard temp file created by `NamedTemporaryFile` lands in world-accessible `/tmp` (`capture_screenshot.py`, `execute_plan`, first reported 2026-07-04)**
Still unresolved.

**[medium, carry-over] Whitespace-only or empty `--query` silently expands capture scope to all visible windows (`capture_screenshot.py`, first reported 2026-06-13)**
Still unresolved.

**[medium, carry-over] macOS `--allow-multiple-matches` + `--destination clipboard` silently discards all captures except the last (`capture_screenshot.py`, first reported 2026-06-11)**
Still unresolved.

**[low, carry-over] `not_capturable_message` reflects user query into stderr without stripping control characters (`capture_screenshot.py:148–151`, first reported 2026-07-08)**
Still unresolved.

**[low, carry-over] `copy_file_to_clipboard` subprocess calls have no `timeout=` (`capture_screenshot.py`, first reported 2026-07-05)**
Still unresolved.

**[low, carry-over] `private_temp_png` swallows non-`EEXIST` `OSError`s in the allocation loop (`capture_screenshot.py`, first reported 2026-07-03)**
Still unresolved.

**[low, carry-over] macOS fullscreen `screencapture` omits `-x` silence flag, playing audible shutter while named-window captures are silent (`capture_screenshot.py`, first reported 2026-07-07)**
Still unresolved.

**[low, carry-over] Multiple `--query` values resolving to the same underlying window ID produce duplicate captures without warning (`capture_screenshot.py`, first reported 2026-07-06)**
Still unresolved.

**[low, carry-over] `run_command` does not redirect screenshot tool stdout; verbose tool output can contaminate the structured output contract (`capture_screenshot.py`, first reported 2026-07-09)**
Still unresolved.

**[low, carry-over] Unquoted `args_file` path in `test_windows_delegates_to_powershell` generated shell script (`tests/test_capture_screenshot.py:309`, first reported 2026-06-17)**
Still unresolved.

**[low, carry-over] `ensure_private_directory` catches `PermissionError` only, leaving other `OSError` subclasses unhandled in a privacy-critical code path (`capture_screenshot.py`, first reported 2026-07-10)**
Still unresolved.

**[info, carry-over] `--allow-multiple-matches` with a broad query produces O(N) capture operations with no warning or soft cap (`capture_screenshot.py`, `execute_plan`, first reported 2026-07-10)**
Still unresolved.

**[info, carry-over] `install.sh` `HOME=""` risk: an exported empty-string `$HOME` passes `-u` guard and expands clone paths to `/.claude/skills/...` (`install.sh`, first reported 2026-07-10)**
Still unresolved.

**[low, carry-over] PowerShell script lacks home-containment validation when invoked directly, bypassing `_validate_output_root` (`capture_screenshot.ps1`, first reported 2026-07-11)**
Still unresolved.

**[low, carry-over] `Get-WindowTitle` truncates window titles at 1024 characters, causing missed matches for long titles (`capture_screenshot.ps1`, `Get-WindowTitle`, first reported 2026-07-11)**
Still unresolved.

**[low, carry-over] `resolve_macos_with_helper` lets `subprocess.CalledProcessError` propagate uncaught if `clang` compilation fails (`capture_screenshot.py`, `resolve_macos_with_helper`, first reported 2026-07-11)**
Still unresolved.

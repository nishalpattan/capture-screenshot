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

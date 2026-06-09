# Capture-Screenshot — Daily Review Log

This file is append-only. Each entry is headed `## YYYY-MM-DD` (UTC) and groups
findings under Security / Bugs & regressions / Data leaks / UX.

Severity levels: **critical** / **high** / **medium** / **low** / **info**

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

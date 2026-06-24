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
`capture_screenshot.py:465` a
nd all callers of `run_command`
`run_command` calls `subprocess.run(args, check=True)`. If any capture tool (e.g.
`screencapture`, `grim`, `gnome-screenshot`) exits non-zero, the
`subprocess.CalledProcessError` exception propagates uncaught through `execute_plan`
and `main`, producing a raw Python traceback on stderr. The rest of the codebase uses
`die()` for all error conditions. An uncaught `CalledProcessError` is both
inconsistent and exposes Python internals (file path, line number, repr of the failed
command) to the user.
_Suggested fix:_ Wrap `subprocess.run(args, check=True)` in `run_command` with
`try/except subprocess.CalledProcessError as exc` and call
`die(f"capture tool exited with code {exc.returncode}: {args[0]}", EXIT_UNAVAILABLE)`.

**[low] `execute_plan` has no guard against `output` being `None` when `{output}` is in the command**
`capture_screenshot.py:454–462` (`run_command`)
`run_command` checks `if part == "{output}" and output is None: die(...)`. This guard
is correct, but `execute_plan` passes `output=temp_output` (never `None`) when calling
`run_command` with a desktop command. If a future refactor accidentally calls
`run_command` with a `{output}` command and `output=None`, the guard catches it, but
the current call sites are safe. No active bug.

---

### Data leaks

**[info] `_validate_integer_ids` includes the raw helper output in its error message**
`capture_screenshot.py:92–95`
`die(f"invalid window id from {source}: {id_str!r}", EXIT_USAGE)` embeds `repr(id_str)`
from the helper's stdout into the error message. If the helper ever emits unexpected
output (e.g., a warning line mixed with IDs), that content appears on stderr. The
helper currently never emits non-integer stdout, so this is a defence-in-depth note
rather than an active leak.

---

### UX

**[info] No structured JSON output mode**
The script prints one path per line (or "clipboard") to stdout. Callers that want to
parse the result programmatically must split on newlines and handle the "clipboard"
sentinel. A `--output-format json` flag would make integration easier, though the
current line-oriented format is simple and POSIX-conventional.

**[info] `gnome-screenshot -w` introduces an implicit 1-second delay**
`capture_screenshot.py:plan_capture` (Linux active-window path)
`gnome-screenshot -w` waits ~1 s before capturing (by design, to let the user refocus
the window). This delay is undocumented in the skill's output and may confuse users
who expect an immediate capture.

---

## 2026-06-08

### Security

**[critical] Shell injection via unsanitised `--query` on the Linux path**
`capture_screenshot.py`, `resolve_linux_named_window`, line where `xdotool search --name` is called.
The query string supplied by the user via `--query` is passed directly to
`subprocess.run([xdotool, "search", "--name", query], ...)`.  Because `subprocess.run`
receives a list (not a shell string), there is no OS-level shell injection.  However,
`xdotool search --name` interprets its argument as an Extended Regular Expression
(ERE).  A malicious or malformed query containing ERE metacharacters (e.g. `.*`,
`(`, `[`, `\`) will be treated as a pattern rather than a literal string, causing
unintended window matches.  For instance, `--query '.*'` would match every window on
the desktop, potentially triggering `--allow-multiple-matches` captures of all open
windows and leaking their contents.
_Suggested fix:_ Escape the query before passing it to xdotool using a helper such as
`re.escape` (though `re.escape` targets Python regex, not POSIX ERE, so a dedicated
ERE-escape function is needed).  Alternatively, use `xdotool search --name` only after
validating that the query contains no ERE metacharacters, or switch to a case-insensitive
literal substring match via a different xdotool invocation.

**[high] TOCTOU race between `unique_capture_path` existence check and file creation**
`capture_screenshot.py`, `unique_capture_path` and `execute_plan`.
`unique_capture_path` calls `candidate.exists()` to find a free path, then returns
that path.  Between the check and the subsequent `os.replace(temp_output, output)` in
`execute_plan`, another process (or a parallel invocation of the skill) could create a
file at `candidate`.  The guard `if output.exists() or output.is_symlink(): die(...)`
in `execute_plan` mitigates this *for the final rename*, but a symlink attack is still
possible in the window between the `exists()` check in `unique_capture_path` and the
symlink check in `execute_plan`.  A local attacker who can write to the screenshots
folder could plant a symlink at the predicted path, potentially redirecting the
screenshot to an arbitrary file.  The `0o700` directory permission substantially
reduces the risk (other users cannot write to the folder), so the practical severity
is low in the intended deployment; it is elevated here because the scenario of a
compromised process running as the same user is realistic.
_Suggested fix:_ Use `O_CREAT | O_EXCL` (already done for the temp file) for the
*final* destination as well, or open the final path with `O_CREAT | O_EXCL` before
the rename to atomically claim it.

**[medium] `ensure_private_directory` chmod race (TOCTOU)**
`capture_screenshot.py:83–100` (`ensure_private_directory`)
`path.mkdir(mode=0o700, …)` is followed by `path.chmod(0o700)`.  On Linux the `mkdir`
syscall applies the mode *before* the umask, so the directory may be created with
broader permissions than intended if the umask is permissive (e.g. `0o022` produces
`0o755`).  The subsequent `chmod` corrects this, but there is a narrow window where
the directory is world-readable.
_Suggested fix:_ Call `os.umask(0)` around the `mkdir` call (saving and restoring the
original umask), or create the directory with `os.mkdir(path, 0o700)` after temporarily
clearing the umask.

**[medium] Windows PowerShell script path passed with `-File` but output root is not validated**
`capture_screenshot.py:_run_powershell_script`
The Python layer validates `output_root` is within the home directory before calling
the PowerShell script.  However, the PowerShell script itself (`capture_screenshot.ps1`)
accepts `-OutputRoot` directly and does not repeat the validation.  A caller who
invokes `capture_screenshot.ps1` directly (bypassing the Python wrapper) can write
screenshots to arbitrary paths.
_Suggested fix:_ Add a home-directory containment check at the top of
`capture_screenshot.ps1`, mirroring `_validate_output_root` in Python.

**[low] `_escape_ere` does not escape the hyphen character inside bracket expressions**
`capture_screenshot.py:107`
`_escape_ere` escapes `][\\.*+?{}()|^$` but not `-`.  In a POSIX ERE *bracket
expression* (e.g. `[a-z]`) a hyphen is a metacharacter.  If the user's query contains
a literal `-` that happens to be placed adjacent to other characters inside an ERE
bracket expression generated by xdotool, it could be misinterpreted.  Because the
query is passed as a *whole pattern* (not embedded inside `[…]`), `-` is not special
outside brackets in ERE, so this is low-severity in the current call pattern.
_Suggested fix:_ Escape `-` anyway for defence in depth, or document the assumption
that the query is never embedded inside a bracket expression.

**[low] Temporary directory for macOS helper compilation is in world-listable `/tmp`**
`capture_screenshot.py:resolve_macos_with_helper`
`tempfile.TemporaryDirectory(prefix="screenshot-window.")` creates a directory in
`/tmp`.  Although the directory itself is mode `0o700` (Python's default since 3.10),
its *name* is visible to any local user via `ls /tmp`, revealing that a screenshot
capture is in progress and approximately when.  The compiled helper binary inside is
protected, but the directory existence leaks timing metadata.
_Suggested fix:_ Create the temp directory under `~/.cache/capture-screenshot/` (mode
`0o700`) where the name is not visible to other users, matching the privacy model of
the per-request output directory.

**[info] `detect_tools` uses `shutil.which`, which honours `PATH` from the environment**
`capture_screenshot.py:116`
A user who controls `PATH` can inject a malicious binary named `screencapture`,
`grim`, etc.  Because `subprocess.run` is called with the *resolved* full path (not
relying on shell lookup at call time), and `shutil.which` returns the first match in
`PATH`, a crafted `PATH` could cause the skill to call a trojanised tool.  This is an
intended-user threat model issue (not a privilege-escalation risk), but worth noting
for deployments in shared or containerised environments.

---

### Bugs & regressions

**[medium] `execute_plan` does not handle the case where `len(plan.commands) > len(output_paths)`**
`capture_screenshot.py:execute_plan`
The guard `if len(plan.commands) != len(output_paths): die("internal error: command/output mismatch")` fires
correctly, but the error message gives no diagnostic context (which plan, which target,
how many commands vs paths).  Not a runtime bug, but makes debugging harder.

**[low] `prepare_output_paths` creates the output directory even on `--dry-run` when called with `create=True`**
`capture_screenshot.py:prepare_output_paths`
`create` defaults to `True`.  The `main` function passes `create=not args.dry_run`,
so this is guarded correctly in practice.  However, if a future caller forgets to pass
`create=False`, a dry-run will create real directories.

**[low] `sanitize_label` truncates at 80 characters and then strips trailing hyphens, potentially producing an empty string for a label that is all hyphens after truncation**
`capture_screenshot.py:sanitize_label`
After the 80-char slice, `.strip("-")` is applied and the result is returned, with
`or "capture"` as a fallback.  The fallback is correct, but a label like
`"--------------------------------------------------------------------------------"` (80 hyphens)
would produce `""` and silently fall back to `"capture"` without any warning.  In
practice this cannot arise from a real window title, but the silent fallback could mask
a sanitisation bug.

---

### Data leaks

**[medium] `run_command` exception traceback may print the full subprocess command, including window IDs**
As noted under Bugs & regressions, an unhandled `CalledProcessError` from `run_command`
would include `repr(args)` in the traceback, which contains the capture-tool invocation
with the window ID.  Window IDs are not sensitive per se, but they are internal
implementation details that should not appear in user-facing output.

**[info] `_test_windows` env-var hook bypasses the real window query on macOS**
`capture_screenshot.py:_test_windows`
`CAPTURE_SCREENSHOT_TEST_WINDOWS` is an undocumented environment variable that
substitutes a fake window list.  If set by accident in a production environment (e.g.,
carried over from a CI job), it silently alters behaviour.  The variable is checked in
`resolve_macos_with_helper` without any guard limiting its availability to test builds.

---

### UX

**[info] Error codes are defined as module-level constants but not documented for callers**
`capture_screenshot.py:27–31`
`EXIT_USAGE`, `EXIT_PRIVACY`, `EXIT_UNAVAILABLE`, `EXIT_NOT_CAPTURABLE` are defined
but not described in the `--help` output or README.  Agent callers that need to
distinguish "window minimised" from "tool missing" must read the source.

**[info] `--allow-multiple-matches` with `--destination clipboard` silently overwrites the clipboard once per window**
`capture_screenshot.py:execute_plan`, macOS clipboard branch
Each window in the plan writes to the clipboard, overwriting the previous one.  Only
the last window's screenshot ends up on the clipboard.  No warning is emitted.

---

## 2026-06-09

### Security

**[high] ERE injection confirmed exploitable via `xdotool search --name` with unescaped metacharacters**
`capture_screenshot.py`, `resolve_linux_named_window`
The 2026-06-08 entry flagged ERE metacharacter handling as a risk.  Confirmed today:
`xdotool search --name '.*'` matches all windows on the desktop.  Combined with
`--allow-multiple-matches`, a query of `'.*'` would capture every open window.  The
`_escape_ere` function added in the same commit correctly escapes all POSIX ERE
metacharacters (`][\\.*+?{}()|^$`) so that the query is treated as a literal string.
This was already fixed in the codebase before the review entry was written; the finding
is recorded here for completeness.  Status: **mitigated in the current codebase**.

**[medium] `private_temp_png` relies on `os.getpid()` for uniqueness, which is predictable**
`capture_screenshot.py:private_temp_png`
The temp filename is `{stem}.{pid}.{index:03d}.tmp.png`.  A local attacker who knows
the PID of the running capture process (trivially obtained via `ps`) can predict the
temp filename.  Because the file is created with `O_CREAT | O_EXCL` inside a `0o700`
directory, a race to pre-create the file would be blocked by the exclusive-create flag
(the skill would iterate to the next index).  The `0o700` directory means the attacker
cannot pre-create the file from another account.  Within the same user account, the
`O_EXCL` guard is the only defence.  If the temp directory ever becomes world-writable
(e.g., due to a misconfiguration), the predictable name becomes exploitable.
_Suggested fix:_ Add a random component to the temp filename (e.g., `os.urandom(4).hex()`)
so that even if the directory permissions are relaxed the filename cannot be predicted.

**[low] `ensure_private_directory` calls `path.stat()` after `path.chmod()` — a TOCTOU for the post-chmod verification**
`capture_screenshot.py:98–101`
After `path.chmod(0o700)`, the code reads `path.stat().st_mode` to verify the
permissions were applied.  Between the `chmod` and the `stat`, another process could
change the permissions again.  The verification would then pass on stale data.  The
practical risk is low (requires a local attacker with write access to the parent
directory), but the pattern is logically broken as a security check.
_Suggested fix:_ Use an `os.open`-based approach with `O_PATH` and `fchmod`/`fstat`
on the same file descriptor, eliminating the race.

---

### Bugs & regressions

**[medium] `resolve_macos_with_helper` does not propagate `subprocess.CalledProcessError` from the clang compilation step**
`capture_screenshot.py:resolve_macos_with_helper`
`subprocess.run([clang, …], check=True)` will raise `CalledProcessError` if
compilation fails (e.g., missing Xcode CLT, corrupted source file).  This exception is
not caught; it propagates through `main` as an unhandled exception, producing a Python
traceback rather than a clean `die()` message.  (Note: a separate entry in 2026-06-11
and today's review revisits this with additional detail.)

**[low] `plan_capture` returns `CapturePlan(False, "no_active_window", …)` for `target == "active"` with empty `window_ids` on Darwin, but `main` never calls `plan_capture` without first resolving the active window**
`capture_screenshot.py:plan_capture` (Darwin active branch)
The guard `if not window_ids: return CapturePlan(False, "no_active_window", …)` is
unreachable on Darwin when `target == "active"`, because `main` resolves the active
window ID via `resolve_macos_with_helper` before calling `plan_capture`, and dies on
failure.  Dead code.  Not a bug, but adds cognitive overhead.

---

### Data leaks

No new findings.  The `_escape_ere` function confirmed not to expose the query value
in error messages.  Window titles remain unexposed across all code paths reviewed.

---

### UX

**[info] `--query` values are silently ignored when `--target` is `fullscreen` or `active`**
`capture_screenshot.py:main`
If a user passes `--target fullscreen --query "Chrome"`, the query is accepted by
`argparse` but ignored without warning.  A user who mistakenly specifies a query with
a non-window target receives no feedback.
_Suggested fix:_ Validate that `args.query` is empty when `args.target != "window"`,
and call `die("--query is only valid with --target window", EXIT_USAGE)`.

---

## 2026-06-10

### Security

**[medium] `Protect-Directory` in PowerShell creates the directory before setting ACLs, leaving a brief world-accessible window**
`scripts/capture_screenshot.ps1:Protect-Directory`
`New-Item -ItemType Directory -Path $Path -Force` creates the directory with inherited
ACLs (typically SYSTEM + Administrators + current user on a default Windows install).
`Get-Acl` / `Set-Acl` are called immediately after, but there is a brief TOCTOU window
where another local process could enumerate the new directory or write into it before
the owner-only ACL is applied.
_Suggested fix:_ Use `[System.IO.Directory]::CreateDirectory(path, directorySecurity)`
with a pre-built `DirectorySecurity` object to set the ACL atomically at creation time.

**[low] PowerShell `Protect-File` does not verify the ACL was applied successfully**
`scripts/capture_screenshot.ps1:Protect-File`
`Set-Acl` is called without checking the return value or catching exceptions specific
to ACL-write failure (e.g., insufficient privilege on a network path).  If the ACL
application fails silently, the file retains inherited permissions.  `Set-Acl` in
PowerShell does throw on failure in `Stop` error mode (which is set globally), so in
practice this is caught — but the error message would be a raw PowerShell exception
rather than a structured `die`-style message.

**[low] Windows `New-TemporaryCapturePath` uses PID + index for uniqueness — predictable within same user session**
`scripts/capture_screenshot.ps1:New-TemporaryCapturePath`
The temp filename is `.{stem}.{PID}.{index:D3}.tmp.png`.  Within the same user
session, a racing process that knows the PID could predict the temp path.  The
`FileMode::CreateNew` + `FileShare::None` creation is atomic and immune to a file-pre-
creation race (the iterator skips taken names), but the predictability is a latent
concern if the directory ever becomes world-writable.
_Suggested fix:_ Add `[System.IO.Path]::GetRandomFileName()` to the temp name.

---

### Bugs & regressions

**[medium] `Copy-Rectangle` (PowerShell fullscreen/active) captures the screen buffer at the moment of the call, not the window's own pixels**
`scripts/capture_screenshot.ps1:Copy-Rectangle`
`Graphics.CopyFromScreen` copies whatever pixels are currently rendered at the given
screen coordinates.  If another window moves over the target between when the bounds
are obtained (`Get-WindowBounds`) and when `CopyFromScreen` runs, the captured image
contains the occluding window.  This is inherent to the `CopyFromScreen` API.  The
per-window `Copy-Window` path (using `PrintWindow`) correctly avoids this for named-
window and active-window targets; the fullscreen path (`Copy-Rectangle`) cannot avoid
it by nature.

**[low] `Test-BitmapAllBlack` returns `$true` for zero-dimension bitmaps**
`scripts/capture_screenshot.ps1:Test-BitmapAllBlack`
The early-return guard `if ($Bitmap.Width -le 0 -or $Bitmap.Height -le 0) { return $true }`
causes a zero-size bitmap to be flagged as all-black, which then emits a warning.
A zero-dimension bitmap cannot arise from `Copy-Window` (which throws on zero bounds
via `Get-WindowBounds`), but the guard is overly conservative.

---

### Data leaks

**[info] `Test-BitmapAllBlack` warning message includes the user's query label**
`scripts/capture_screenshot.ps1:Capture-ToDestination`
The warning `"warning: '$Label' rendered black via PrintWindow …"` includes `$Label`,
which is the sanitised form of the user's query (not the actual window title).  The
user's query is not sensitive in itself, but it confirms which application was targeted.
This is by design (matching the Python layer's convention of echoing query text, not
titles), and the label is already printed as part of the output path; recorded as
info-level for completeness.

---

### UX

**[info] `spectacle -b -n` (KDE) may open a transient notification even in background mode**
`capture_screenshot.py:plan_capture` (Linux desktop path, spectacle branch)
`spectacle -b -n` is intended to capture without GUI, but on some KDE versions it
still emits a D-Bus notification.  This can be surprising to users expecting a silent
capture.

---

## 2026-06-13

### Security

**[medium] `_validate_output_root` resolves `path` before checking containment, but `output_root` itself is not resolved before `ensure_private_directory` is called**
`capture_screenshot.py:_validate_output_root` and `prepare_output_paths`
`_validate_output_root` calls `path.resolve().relative_to(home)`.  If `output_root`
contains a `..` component that resolve would normalise away (e.g.,
`~/Desktop/../../../etc/screenshots`), `resolve()` canonicalises it correctly and the
check works.  However, `prepare_output_paths` passes the *unresolved* `output_root`
to `ensure_private_directory`, which calls `path.mkdir(parents=True)`.  If an
intermediate component of the path is a symlink added after the `_validate_output_root`
check, `mkdir` would follow it, potentially creating the directory outside the home.
The `is_symlink()` check in `ensure_private_directory` only guards the *leaf*
directory; it does not traverse and verify intermediate components.
_Suggested fix:_ After `_validate_output_root` passes, resolve the path and use the
canonical form for all subsequent directory operations.

**[low] `_run_powershell_script` does not validate `ps_script` is within `skill_dir`**
`capture_screenshot.py:_run_powershell_script`
`ps_script = skill_dir / "scripts" / "capture_screenshot.ps1"` is constructed by
joining trusted constants, so the path is controlled.  However, there is no assertion
that `ps_script.resolve()` is beneath `skill_dir.resolve()`.  If `skill_dir` itself
were a symlink pointing outside the intended directory (e.g., due to a malicious
install), the check would not catch it.  Low severity given that `skill_dir` is derived
from `Path(__file__).resolve().parents[1]` (already resolved).

---

### Bugs & regressions

**[medium] `allow_multiple=True` with multiple queries returns IDs from all queries interleaved, but `labels` and `window_ids` lists stay in sync only if each query resolves to the same number of IDs**
`capture_screenshot.py:main` (window target, multiple `--query` values)
`window_ids.extend(resolution.ids)` and `labels.extend([sanitize_label(query)] * len(resolution.ids))`
keep the two lists parallel, so `prepare_output_paths` and `plan_capture` receive
matching sequences.  This is correct.  However, `plan_capture` receives only
`label=labels[0]` (the first label) — the per-window labels are not available inside
`plan_capture`.  The `label` parameter is used only in error messages inside
`plan_capture`; the actual per-window output paths are determined by `prepare_output_paths`
outside it.  Not a bug today, but the mismatch in information flow is a maintenance
hazard.

**[low] On Linux with Wayland and no supported tool, `plan_capture` returns `missing_dependency_fullscreen` for `target=fullscreen/clipboard` even when grim is present but `wl-copy` is absent**
`capture_screenshot.py:plan_capture` (Linux clipboard fullscreen, `grim` present but `wl-copy` absent)
`if grim and wl_copy: …` is the first clipboard branch checked.  If `grim` is present
but `wl-copy` is absent, the branch is skipped and the function falls through to
`import_cmd and (xclip or xsel)`.  If `import` is also absent, the error message is
`"missing_dependency_fullscreen"` rather than a more specific
`"missing_dependency_wl_copy"`.  The user is told a generic "no tool found" message
when in fact the specific missing piece is `wl-copy`.
_Suggested fix:_ Check for `grim` alone first; if `grim` is present but `wl-copy` is
absent, return a targeted `missing_dependency_wl_copy` error.

**[low] `unique_capture_path` iterates up to index 999 before dying — the die message does not name the conflicting label**
`capture_screenshot.py:unique_capture_path`
The die message is a generic `"could not allocate a unique screenshot filename"`.
Adding the label and folder path to the message would help diagnose a runaway
duplicate-label scenario.

---

### Data leaks

**[info] `sanitize_label` strips `https?://` but not other URI schemes (e.g., `file://`, `ssh://`)**
`capture_screenshot.py:sanitize_label`
`re.sub(r"https?://", "", label)` removes `http://` and `https://` prefixes, but
`file:///home/user/secrets` would become `file:homeusersecretes` (the slashes are
converted to hyphens by the subsequent `[^a-z0-9]+` pass).  The `file:` prefix is not
stripped.  In practice, window titles rarely begin with `file://` URIs, but the
sanitisation is not URI-generic.

---

### UX

**[medium] `--query` silently discarded with `--target fullscreen` or `--target active`**
`capture_screenshot.py:main`
As noted in the 2026-06-09 entry, `--query` values are silently ignored for non-window
targets.  The fix has not been applied as of this review; carry-forward tracking note.

**[info] Install script (`install.sh`) does not verify a skills directory exists before offering installation**
`install.sh`
The script checks for `~/.claude/skills`, `~/.codex/skills`, and
`~/.config/opencode/skills`.  If none exist, it prints a manual-install message.  It
does not check whether the agent binaries themselves are installed, so a user with a
skills directory for a different agent might see an unexpected "already installed" message.

---

## 2026-06-12

### Security

**[medium] `_validate_output_root` does not guard `--dry-run` invocations in the original implementation**
`capture_screenshot.py:main`
Corrected in the current codebase: `_validate_output_root` is now called
unconditionally (before the `--dry-run` short-circuit), so the home-containment check
applies even when no files are written. Status: **already fixed**.  Recorded for
completeness.

**[low] `install.sh` clones over plain HTTPS without signature or checksum verification**
`install.sh:clone_if_missing`
`git clone "$REPO" "$dest"` fetches over HTTPS.  There is no GPG signature check on
the cloned content.  A compromised GitHub repository or a MITM that breaks TLS
(e.g., via a corporate proxy with custom CA) could deliver malicious scripts.  This is
inherent to the `git clone` install pattern; noted as an architectural limitation
rather than an implementation bug.

**[info] `CAPTURE_SCREENSHOT_TEST_PLATFORM` env var can redirect execution to the Windows PowerShell path on non-Windows hosts**
`capture_screenshot.py:main`, `_test_platform`
Setting `CAPTURE_SCREENSHOT_TEST_PLATFORM=Windows` on a Linux/macOS host causes
`_run_powershell_script` to be called.  If `powershell.exe` or `pwsh` happens to be
installed (e.g., PowerShell Core on Linux), it will attempt to run the PS1 script.
This is an intentional test hook, but an undocumented one; a misconfigured environment
could trigger unexpected behaviour in production.
_Suggested fix:_ Guard test hooks behind an explicit `CAPTURE_SCREENSHOT_UNSAFE_TEST_MODE=1`
variable or remove them from non-test builds.

---

### Bugs & regressions

**[medium] `resolve_linux_named_window` returns all IDs unfiltered when neither `xprop` nor `xwininfo` is available**
`capture_screenshot.py:resolve_linux_named_window`
When neither `xprop` nor `xwininfo` is in `tools`, `_linux_window_is_viewable` returns
`None` for every window, and the code keeps all IDs.  This means a minimised window
could be passed to `import -window {id}`, which may produce a black or empty image
without error.  The fallback behaviour degrades silently.
_Suggested fix:_ Emit an `info`-level warning to stderr when classification tools are
absent, so the user knows capture may include non-viewable windows.

**[low] `resolve_linux_named_window` does not limit the number of windows returned by `xdotool search`**
`capture_screenshot.py:resolve_linux_named_window`
`xdotool search` can return hundreds of window IDs for a broad query.  Without
`allow_multiple`, the multiple-match guard fires correctly.  With `allow_multiple`,
all IDs are accepted, potentially generating hundreds of screenshot files.  No
practical bound is enforced.

---

### Data leaks

No new findings.  All error and output paths reviewed continue to use sanitised labels
(never raw window titles).

---

### UX

**[low] `not_capturable_message` passes state `"unknown"` when the macOS C helper cannot distinguish minimised from off-Space**
`capture_screenshot.py:not_capturable_message`
The macOS helper emits `"unknown"` as the reason token when it cannot determine whether
a window is minimised or on another Space.  The user-facing message falls through to
the generic branch: "'{query}' exists but cannot be captured (minimized or off-screen)
— restore it and retry."  This is acceptable but could be improved if the helper
distinguished the two states.

---

## 2026-06-14

### Security

**[low] `run_command` silently drops extra `{output}` placeholders if a command tuple contains more than one**
`capture_screenshot.py:run_command`
`run_command` substitutes `{output}` and `{temp-output}` wherever they appear in the
command tuple.  If a command were constructed (by a future change to `plan_capture`)
with two `{output}` placeholders, both would be replaced with the same path, creating
two references to the output file in the same command.  This could cause tools to fail
or behave unexpectedly.  No current command has duplicate placeholders; this is a
latent correctness risk.

**[info] `shutil.which` on macOS may resolve to a Homebrew-installed tool that differs in behaviour from the system tool**
`capture_screenshot.py:detect_tools`
On macOS, `shutil.which("screencapture")` resolves to `/usr/sbin/screencapture` in
typical installs.  If a user has a Homebrew or MacPorts wrapper named `screencapture`
earlier in `PATH`, the wrapper is used instead.  The wrapper might not support `-l`
(window ID) or `-x` (no sound) flags, causing silent failures.  Low severity given
that `screencapture` is not a commonly wrapped tool.

---

### Bugs & regressions

**[medium] `execute_plan` for macOS clipboard with multiple windows calls `run_command` per window, but each run overwrites the clipboard — only the last window's screenshot survives**
`capture_screenshot.py:execute_plan` (Darwin clipboard, multiple window IDs)
`for command in plan.commands: run_command(command)` writes each window to the
clipboard sequentially.  Only the last write persists.  No warning is emitted.  The
2026-06-08 entry noted this under UX; here it is re-classified as a bug because the
user explicitly requested `--allow-multiple-matches` with clipboard destination and
receives silent data loss.
_Suggested fix:_ If `allow_multiple_matches` and `destination == "clipboard"`, either
reject the combination with a clear error, or composite the screenshots into a single
image before writing to the clipboard.

**[low] `prepare_output_paths` with `create=True` creates the request directory before `execute_plan` validates the plan**
`capture_screenshot.py:main`
`output_paths = prepare_output_paths(…, create=not args.dry_run)` runs before
`execute_plan` is called.  If `execute_plan` then fails immediately (e.g., plan is
not OK), the request directory (e.g.,
`~/Desktop/screenshots/06_14_2026_15_30_00/`) has already been created and remains
on disk empty.  Repeated failed captures accumulate empty directories.

---

### Data leaks

No new findings.

---

### UX

**[info] Empty request directories accumulate on disk after failed captures**
As noted in Bugs & regressions above: a failed `execute_plan` leaves an empty
timestamped directory under the screenshots root.  Repeated failures (e.g., missing
tool, window not found) leave traces on the filesystem without user notification.
_Suggested fix:_ Create the request directory inside `execute_plan`, after plan
validation, or delete it on failure.

---

## 2026-06-15

### Security

**[low] `private_temp_png` iterates index 0–999 and dies if all are taken, but does not randomise the index**
`capture_screenshot.py:private_temp_png`
As noted in the 2026-06-09 entry for `resolve_macos_with_helper`'s temp dir, the
`{pid}.{index}` naming is predictable.  Within a `0o700` directory the attack surface
is limited to same-user processes, but adding entropy to the filename is a defence-in-
depth improvement.

**[info] `find_macos_window_id.m` reads all windows with `kCGWindowListOptionAll`, which requires Screen Recording permission on macOS 14+**
`scripts/find_macos_window_id.m`
`CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)` returns an empty
array (not nil) when Screen Recording permission has not been granted, rather than
returning an error code.  The helper then finds no windows and exits with code 2
("no matching window"), which the Python layer surfaces as "No matching on-screen
window found" — a misleading message when the real cause is a missing permission.
_Suggested fix:_ Before the window enumeration, check the permission status using
`CGPreflightScreenCaptureAccess()` (available macOS 10.15+); if it returns false,
print a diagnostic to stderr and exit with a dedicated code that the Python layer
can map to a specific error message.

---

### Bugs & regressions

**[medium] macOS `--target active` with `--destination clipboard` does not use the `--frontmost` helper path for plan construction**
`capture_screenshot.py:main` and `plan_capture`
For `target == "active"` on Darwin, `main` calls `resolve_macos_with_helper("", False, True, skill_dir)`
to get the frontmost window ID, then extends `window_ids`.  `plan_capture` then
receives `target="active"` and `window_ids=[id]` and falls into the `if not window_ids`
guard (which does not fire) and then `for window_id in window_ids` to build
screencapture commands.  This is correct.  However, the macOS `screencapture -l`
command with a specific window ID and `-c` (clipboard) **requires** the window to be
on the current Space; a window on a different Space is on-screen but `screencapture -l`
may produce a black image.  This is an OS limitation, but the tool gives no warning.

**[low] `_linux_window_is_viewable` checks `"iconic"` in xprop output with a case-insensitive substring match**
`capture_screenshot.py:_linux_window_is_viewable`
`"iconic" not in proc.stdout.lower()` will flag any window whose xprop output contains
the string "iconic" in any context (e.g., an icon-path that includes the word "iconic").
In practice, `WM_STATE` output uses `window state: Iconic` as the canonical form, but
a window whose `WM_ICON_NAME` contains "Iconic" could be misclassified as minimised.
_Suggested fix:_ Parse the `WM_STATE` value more precisely, e.g., check that the line
starts with `window state:` before testing for `Iconic`.

---

### Data leaks

No new findings.

---

### UX

**[info] `screencapture` on macOS plays a shutter sound even with `-x` flag on some system configurations**
`capture_screenshot.py:plan_capture` (Darwin)
`screencapture -x` is documented to suppress the shutter sound, but on certain macOS
versions with "Play feedback when screenshot is taken" enabled in System Preferences,
the sound plays regardless.  This is an OS-level limitation and not fixable in the
script.

---

## 2026-06-16

### Security

**[low] `_linux_window_is_viewable` runs `xprop -id {window_id}` with IDs from `xdotool search` output without re-validating them**
`capture_screenshot.py:_linux_window_is_viewable`
`xdotool search` returns IDs validated by `_validate_integer_ids` (digits-only check),
so `xprop -id {window_id}` receives a numeric string.  Because `subprocess.run` uses a
list (no shell), there is no shell injection risk.  However, `xprop` itself could be a
malicious binary if `PATH` is untrusted (see the 2026-06-08 `shutil.which` note).
The per-tool `tools` dict is built from `detect_tools`, so the path is fixed at
detection time — an improvement over repeated `shutil.which` calls.

**[info] `find_macos_window_id.m` does not verify that `query_arg` is valid UTF-8 before calling `CFStringCreateWithCString`**
`scripts/find_macos_window_id.m`
`CFStringCreateWithCString(NULL, query_arg, kCFStringEncodingUTF8)` returns NULL if
`query_arg` is not valid UTF-8.  The code checks the return value (`if (!query)`)
and returns 64 on NULL, so this is handled correctly.  Recorded as info because the
error message to stderr on this path is empty (the check just `return 64`), which is
opaque to the user.
_Suggested fix:_ Print `"error: query is not valid UTF-8\n"` to stderr before returning 64.

---

### Bugs & regressions

**[medium] `Protect-Directory` in PowerShell does not remove pre-existing ACE entries before adding the owner-only rule**
`scripts/capture_screenshot.ps1:Protect-Directory`
`$acl.SetAccessRuleProtection($true, $false)` disables inheritance and removes
inherited entries, but *explicit* ACEs on a pre-existing directory are not purged
before `$acl.AddAccessRule($rule)` is called.  A directory that was previously
world-writable (or shared with other users) retains its explicit ACEs even after
`Protect-Directory` runs on it.
_Suggested fix:_ After `SetAccessRuleProtection`, iterate `$acl.Access` and call
`$acl.RemoveAccessRule($_)` for each existing rule before adding the owner-only ACE,
as is already done correctly in `Protect-File`.

**[low] `Copy-Window` disposes `$graphics` in a `finally` block but does not dispose `$bitmap` on `PrintWindow` failure before the `throw`**
`scripts/capture_screenshot.ps1:Copy-Window`
Inside the `try` block: if `PrintWindow` returns `$false`, `$bitmap.Dispose()` is
called and then `throw` is executed.  The `finally` block then calls
`$graphics.Dispose()`.  Because `$bitmap` was disposed before the throw, the `finally`
block does not double-dispose it.  However, the `$graphics` object holds a reference
to the already-disposed bitmap's HDC.  In practice, `ReleaseHdc` is called in its own
`finally` before `graphics.Dispose()`, so the sequencing is: `GetHdc` → `PrintWindow`
→ (failure) `ReleaseHdc` (in inner `finally`) → `bitmap.Dispose()` → `throw` →
`graphics.Dispose()` (outer `finally`).  This sequence is correct; noted because the
nested `finally` blocks are non-obvious.

---

### Data leaks

No new findings.  `Protect-Directory` ACE fix (above) is a security hardening item,
not a data-leak finding.

---

### UX

**[info] PowerShell error output on `throw` uses the exception message directly, which may include internal path information**
`scripts/capture_screenshot.ps1`
Several `throw 'message'` statements produce PowerShell `RuntimeException` objects.
In `$ErrorActionPreference = 'Stop'` mode these are fatal, but the message is printed
with full PowerShell exception formatting (including script path and line number) to
stderr.  This is consistent with other PowerShell scripts but differs from the Python
layer's `die()` convention of printing only the message.

---

## 2026-06-17

### Security

**[medium] `Protect-Directory` ACE purge gap confirmed: pre-existing explicit ACEs survive `SetAccessRuleProtection`**
`scripts/capture_screenshot.ps1:Protect-Directory` (revisit of 2026-06-16 finding)
The 2026-06-16 entry identified that explicit ACEs on an existing directory are not
removed.  Confirmed that `$acl.SetAccessRuleProtection($true, $false)` removes only
*inherited* ACEs; explicit ACEs require explicit removal.  The fix identified
(iterate + `RemoveAccessRule`) is correct.  The `Protect-File` function already
applies this fix correctly (iterates `$acl.Access` and removes all rules before adding
the owner rule), confirming the pattern is known within the codebase.  The gap applies
only to `Protect-Directory` when the directory pre-exists with foreign explicit ACEs
(e.g., if the user previously shared the screenshots folder).  Status: **unresolved**.

**[low] `resolve_macos_with_helper` compiles to a path inside a world-listable `/tmp` subdirectory**
`capture_screenshot.py:resolve_macos_with_helper`
`tempfile.TemporaryDirectory(prefix="screenshot-window.")` creates a directory in
`/tmp`.  While the directory itself is `0o700` (Python 3.10+ default), its *name*
(including the `screenshot-window.` prefix) is visible to all local users via `ls /tmp`
or filesystem event watchers (FSEvents on macOS, inotify on Linux).  The existence and
timing of window captures is thus metadata-leaked to other local users.  Repeated
across multiple captures, this creates an activity log visible to any local observer.
(Related to the 2026-06-08 entry; now specifically about the compilation temp dir in
addition to the capture temp dir.)

---

### Bugs & regressions

**[medium] `Protect-Directory` in PowerShell is called on the output root and the per-request subfolder, but if `Protect-Directory` on the subfolder fails, the output root's new ACLs have already been applied**
`scripts/capture_screenshot.ps1:New-RequestFolder`
`Protect-Directory -Path $OutputRoot` is called first, then
`Protect-Directory -Path $folder`.  If the second call throws (e.g., the subfolder
path is a reparse point added between the two calls), the function exits with an
exception but `$OutputRoot`'s ACLs have already been tightened.  This is actually
the desired outcome (a partial success is better than no hardening), but it means a
caller catching the exception cannot know whether `$OutputRoot` was secured.  Not a
correctness bug, but a subtle contract issue.

**[low] `resolve_linux_named_window` does not distinguish between xdotool returning zero results and xdotool failing (non-zero exit)**
`capture_screenshot.py:resolve_linux_named_window`
`if proc.returncode != 0: return ResolutionResult(False, "no_matching_window", …)`.
`xdotool search` exits with code 1 when no windows match *and* when an internal error
occurs (e.g., cannot connect to the X server).  Both cases produce the same
"no_matching_window" result, masking X11 connectivity issues.
_Suggested fix:_ Check stderr for error messages; if xdotool reports an X11 connection
failure, return a more specific error code (e.g., `"x11_unavailable"`).

---

### Data leaks

No new findings.

---

### UX

**[info] `not_capturable_message` for `state="unknown"` does not tell the user what actions to take beyond "restore it"**
`capture_screenshot.py:not_capturable_message`
The generic message `"'{query}' exists but cannot be captured (minimized or off-screen)
— restore it and retry."` does not explain that on macOS, "another Space" may be the
cause and switching Spaces is the remedy.  The `state="offscreen"` branch handles this
correctly; the `"unknown"` branch does not.
_Suggested fix:_ Update the `"unknown"` branch message to: `"'{query}' exists but
cannot be captured — it may be minimized or on another Space. Restore the window or
switch to its Space, then retry."`

---

## 2026-06-18

### Security

**[medium] `Protect-Directory` ACE purge gap resolved in current codebase**
`scripts/capture_screenshot.ps1:Protect-Directory`
Reviewing the current `main` branch: the `Protect-Directory` function now includes
`foreach ($rule in @($acl.Access)) { $acl.RemoveAccessRule($rule) | Out-Null }` after
`SetAccessRuleProtection`, purging all pre-existing explicit ACEs before adding the
owner-only rule.  This resolves the finding from 2026-06-16/17.  Status: **fixed**.

**[low] `capture_screenshot.ps1` does not validate `-OutputRoot` is within the user profile when invoked directly**
`scripts/capture_screenshot.ps1` (parameter block, top of script)
The Python orchestrator (`capture_screenshot.py:_validate_output_root`) enforces
home-directory containment before delegating to PowerShell, so the guard fires for
typical invocations.  However, a caller who runs `capture_screenshot.ps1` directly
(bypassing Python) can pass an arbitrary `-OutputRoot` (e.g., a network share or a
system path).  The script will create and ACL-secure whatever path is supplied without
error.  This was flagged in the 2026-06-08 and 2026-06-10 entries; noting here that
it remains unresolved on the current `main` branch.  Status: **unresolved**.

**[info] `find_macos_window_id.m` compiled binary lives in `/tmp` subdirectory visible via `ls /tmp`**
Previously flagged in 2026-06-08 and 2026-06-17.  Remains unresolved.

---

### Bugs & regressions

**[medium] `Copy-Rectangle` on Windows does not handle negative-coordinate windows (e.g., windows on a monitor to the left of the primary)**
`scripts/capture_screenshot.ps1:Copy-Rectangle`
`Graphics.CopyFromScreen` accepts negative `Left`/`Top` coordinates (valid for
multi-monitor setups where the primary monitor is not the leftmost).  However,
`Get-WindowBounds` checks `$width -le 0 -or $height -le 0` and throws on zero/negative
dimensions.  A window with `Left=-1920` (on the monitor to the left of primary) has
positive width and height, so it is not rejected.  `CopyFromScreen` with negative
source coordinates works correctly on Windows (the virtual screen coordinate system
allows negatives); this item is withdrawn — the code handles it correctly.
Status: **not a bug**.

**[low] `plan_capture` for Linux `target=active` returns `missing_dependency_active_window` even when `xdotool` is present (because gnome-screenshot is the only supported active-window tool)**
`capture_screenshot.py:plan_capture` (Linux active branch)
The Linux `active` branch only checks for `gnome-screenshot`.  If `gnome-screenshot`
is absent but `xdotool` is present, the error is `missing_dependency_active_window`.
`xdotool getactivewindow` + `import -window {id}` is a viable alternative that is
not attempted.  The user gets a misleading "no active-window tool" error when the
ingredients are available.
_Suggested fix:_ Add an `xdotool`+`import` fallback for active-window on X11.

**[medium] `test_windows_delegates_to_powershell` test uses an inline shell script with a path embedded in a printf command — shell-injection risk in test code**
`tests/test_capture_screenshot.py:test_windows_delegates_to_powershell`
The fake PowerShell script writes:
`f'#!/bin/sh\nprintf "%s\\n" "$@" > {args_file}\necho "fake/path.png"\n'`
`args_file` is a `Path` inside a `tempfile.TemporaryDirectory`.  If `args_file`
contains shell-special characters (e.g., spaces, `$`, backticks), the embedded path
in the heredoc-style script would cause unintended shell behaviour when `/bin/sh`
executes it.  `tempfile.TemporaryDirectory` typically produces paths under `/tmp`
without special characters, so the risk is negligible in practice, but the pattern is
fragile.
_Suggested fix:_ Write `args_file` path to an env variable and use `"$ARGS_FILE"`
in the script, or use `shlex.quote(str(args_file))` when embedding the path.

---

### Data leaks

No new findings.  Window title exclusion remains intact across all reviewed paths.

---

### UX

**[info] `execute_plan` prints output paths one per line, but on Windows the paths contain backslashes**
`capture_screenshot.py:execute_plan`
`print(output)` prints the `Path` object, which on Windows uses backslashes.  Callers
parsing the output with forward-slash assumptions (e.g., a shell script expecting
POSIX paths) would need to normalise separators.  This is expected Python/Windows
behaviour but worth documenting in SKILL.md.

---

## 2026-06-19

### Security

**[medium] `_test_windows` JSON parsing has no size guard — a very large `CAPTURE_SCREENSHOT_TEST_WINDOWS` value causes unbounded memory allocation**
`capture_screenshot.py:_test_windows`
`json.loads(raw)` on a very large string (e.g., a 100 MB environment variable set by a
hostile environment) would allocate proportional memory.  In practice, environment
variables are limited to a few MB on most OSes (Linux ARG_MAX / env size limits apply),
and the variable is a test hook; the risk is negligible.  Recorded for completeness.

**[low] `_validate_integer_ids` does not enforce an upper bound on window ID values**
`capture_screenshot.py:_validate_integer_ids`
`id_str.isdigit()` accepts arbitrarily large integers.  A misbehaving helper that emits
a 20-digit string would pass the check and be forwarded to `screencapture -l` or
`import -window`.  The tools would likely fail gracefully (invalid window ID), but the
unbounded integer is not explicitly bounded to `uint32_t` range (the actual CGWindowID
or X11 XID range).
_Suggested fix:_ Add `int(id_str) <= 2**32 - 1` to the validation, matching the
`uint32_t` range of both CGWindowID and X11 XID.

---

### Bugs & regressions

**[medium] `resolve_macos_with_helper` with `active=True` ignores `allow_multiple` — always passes `False` to `resolve_macos_window_ids` in the test path**
`capture_screenshot.py:resolve_macos_with_helper`
When `test_windows` is set and `active=False`, `resolve_macos_window_ids(query, test_windows, allow_multiple=allow_multiple)` is called.  When `active=True`, the test-windows path is skipped entirely (correct).  However, the live path (`subprocess.run(command, …)`) passes `--allow-multiple` only when `allow_multiple` is `True` and `active` is `False` (the `command.append("--frontmost")` branch runs `active=True` without `--allow-multiple`).  For `active=True`, `allow_multiple` is irrelevant (there is at most one frontmost window), so this is not a bug — but it is undocumented.

**[low] `_linux_window_is_viewable` uses `"iconic" not in proc.stdout.lower()` — fragile string match**
`capture_screenshot.py:_linux_window_is_viewable`
As noted in the 2026-06-15 entry.  Remains unresolved.  Carry-forward tracking note.

**[medium] `Copy-Window` on Windows does not fall back to `CopyFromScreen` when `PrintWindow` returns `$false`**
`scripts/capture_screenshot.ps1:Copy-Window`
`if (-not $ok) { $bitmap.Dispose(); throw 'PrintWindow failed to capture the window' }`
A `PrintWindow` failure (e.g., GPU/DWM-composited window that ignores the message)
throws and aborts the entire capture.  The `Test-BitmapAllBlack` check that follows
in `Capture-ToDestination` is never reached.  The user receives an exception rather
than a warning and a potentially usable (non-black) screenshot.
_Suggested fix:_ On `PrintWindow` failure, fall back to `Copy-Rectangle` on the same
bounds, emit a warning to stderr, and continue.

---

### Data leaks

No new findings.

---

### UX

**[info] `Capture-ToDestination` emits the black-bitmap warning to `[Console]::Error` with `$Label` (user's query) in the message**
`scripts/capture_screenshot.ps1:Capture-ToDestination`
`"warning: '$Label' rendered black via PrintWindow (GPU/Electron window); content may
be unavailable without bringing it forward."` uses `$Label` (the sanitised user query).
This is correct privacy-wise (not the window title), but the warning goes to stderr
mixed with other error output.  An agent parsing stderr would need to distinguish this
warning from fatal errors.  Adding a structured prefix (e.g., `warning:`) would help;
the warning already starts with `"warning:"` so this is already partially addressed.

---

## 2026-06-20

### Security

**[medium] `capture_screenshot.ps1` has no home-directory containment check for `-OutputRoot` (carry-forward, first reported 2026-06-08)**
Status: **still unresolved** as of this review.  No new technical information beyond
prior entries.

**[low] `find_macos_window_id.m` compiled binary temp-dir metadata leak (carry-forward, first reported 2026-06-17)**
Status: **still unresolved**.

**[medium] `test_windows_delegates_to_powershell` shell injection in test script (carry-forward, first reported 2026-06-18)**
`tests/test_capture_screenshot.py:test_windows_delegates_to_powershell`
The fake PowerShell script embeds `args_file` (a `Path`) directly in shell script
source.  If the temp path contained shell metacharacters, the embedded path would cause
unintended shell execution.  Remains unresolved.
_Suggested fix:_ Use `shlex.quote(str(args_file))` or pass the path via an environment
variable.

**[info] `CAPTURE_SCREENSHOT_TEST_PLATFORM` and `CAPTURE_SCREENSHOT_TEST_WINDOWS` are production env-var hooks with no documented way to disable them**
`capture_screenshot.py:_test_platform`, `_test_windows`
Both test hooks are checked unconditionally in `main` / `resolve_macos_with_helper`.
There is no `CAPTURE_SCREENSHOT_DISABLE_TEST_HOOKS=1` escape hatch.  In a CI/CD
pipeline that sets these variables for one job and leaks them into a child process
running the real capture script, behaviour would be silently altered.

---

### Bugs & regressions

**[medium] `Copy-Window` `PrintWindow` failure does not fall back to `CopyFromScreen` (carry-forward, first reported 2026-06-19)**
Status: **still unresolved**.

**[low] `_linux_window_is_viewable` uses fragile `"iconic"` substring match (carry-forward, first reported 2026-06-15)**
Status: **still unresolved**.

**[medium] `plan_capture` for Linux `target=active` has no `xdotool`+`import` fallback (carry-forward, first reported 2026-06-18)**
Status: **still unresolved**.

---

### Data leaks

No new findings.

---

### UX

**[info] `install.sh` has no `--dry-run` or `--help` flag**
`install.sh`
The installer unconditionally clones the repository into each detected skills
directory.  There is no way to preview what would be installed without running the
script.  A `--dry-run` flag and a `--help` message would improve usability.

**[info] `screencapture` on macOS does not emit a structured error when the target window disappears between resolution and capture**
`capture_screenshot.py:execute_plan` (Darwin path)
Between `resolve_macos_with_helper` (which queries the window list) and the actual
`screencapture -l {window_id}` call, the target window may close.  `screencapture`
exits with a non-zero code, which `subprocess.run(args, check=True)` raises as
`CalledProcessError` — an unhandled exception rather than a clean `die()` message.

---

## 2026-06-21

### Security

**[medium] `capture_screenshot.ps1` `-OutputRoot` path containment gap (carry-forward)**
Status: **still unresolved**.

**[low] `find_macos_window_id.m` temp-dir name visible in `/tmp` (carry-forward)**
Status: **still unresolved**.

**[medium] Test shell-injection in `test_windows_delegates_to_powershell` (carry-forward)**
Status: **still unresolved**.

**[low] `resolve_macos_with_helper`: uncaught `CalledProcessError` from clang compilation reveals install path**
`capture_screenshot.py:resolve_macos_with_helper`
This was noted in passing in the 2026-06-09 entry.  On this review pass, verifying the
current `main` branch: `subprocess.run([clang, …], check=True)` is still not wrapped
in a `try/except`.  A clang failure (e.g., Xcode CLT update removed a framework)
produces a Python traceback including the full path to `find_macos_window_id.m` and
the compile command on stderr.  The path reveals the skill installation directory.
_Suggested fix:_ Wrap in `try/except subprocess.CalledProcessError` and call
`die("macOS window helper failed to compile; ensure Xcode Command Line Tools are installed", EXIT_UNAVAILABLE)`.

---

### Bugs & regressions

**[medium] `Copy-Window` failure does not fall back (carry-forward, first reported 2026-06-19)**
Status: **still unresolved**.

**[low] Fragile `"iconic"` substring match in `_linux_window_is_viewable` (carry-forward)**
Status: **still unresolved**.

**[medium] Linux `active` target missing `xdotool`+`import` fallback (carry-forward)**
Status: **still unresolved**.

**[medium] `screencapture` failure when window closes between resolution and capture produces unhandled `CalledProcessError` (carry-forward)**
Status: **still unresolved**.

**[low] `find_macos_window_id.m` silently uses the last positional argument when multiple are supplied**
`scripts/find_macos_window_id.m:42–52`
In the argument-parsing loop, the `else` branch unconditionally overwrites `query_arg`
with the current `argv[i]`.  Passing two positional arguments silently discards the
first.  Since the Python caller controls the arguments, this cannot be exploited from
outside, but it makes the binary fragile to future refactors.
_Suggested fix:_ Detect multiple positional arguments and print a usage error with
`return 64`.

---

### Data leaks

No new findings.  Window titles remain excluded from all output.  The `query` string
passed to `_escape_ere` and then to xdotool is user-supplied text, not a window title,
and is not echoed in normal output.

---

### UX

**[info] No machine-readable output format (carry-forward)**
The line-oriented stdout format (path per line / "clipboard") is hard to parse when
paths contain spaces.  `--output-format json` remains unimplemented.

**[info] `gnome-screenshot -w` 1-second implicit delay (carry-forward, first reported 2026-06-11)**
Status: **still unresolved**.

**[medium] `plan_capture` for Linux active-window silently succeeds without `gnome-screenshot`, then `execute_plan` fails when the plan is `not ok` — but the error message cites "missing_dependency_active_window" without naming the missing tool**
`capture_screenshot.py:plan_capture` (Linux active branch)
`return CapturePlan(False, "missing_dependency_active_window", "No supported Linux active-window screenshot tool was found.")`
The message names no specific tool.  A user without `gnome-screenshot` might not know
which package to install.
_Suggested fix:_ List the missing tool(s) explicitly: "No supported Linux active-window
screenshot tool was found. Install gnome-screenshot (GNOME) or use xdotool+import (X11)."

---

## 2026-06-22

### Security

**[medium] `capture_screenshot.ps1` `-OutputRoot` containment gap (carry-forward)**
`scripts/capture_screenshot.ps1:6`
Status: **still unresolved** as of the current `main` branch.

**[low] Windows `Protect-Directory` TOCTOU between `New-Item` and `Set-Acl` (carry-forward, first reported 2026-06-10)**
`scripts/capture_screenshot.ps1:Protect-Directory`
The 2026-06-10 finding — directory created by `New-Item` with inherited ACLs, then
secured by `Set-Acl` a moment later — remains unresolved.
_Suggested fix:_ Use `[System.IO.Directory]::CreateDirectory(path, directorySecurity)`
to apply the ACL atomically at creation time.

**[low] Clipboard pipeline temp file created in world-searchable `/tmp` (carry-forward, first reported 2026-06-23 — see below)**
Noted in the 2026-06-23 entry below.

**[low] `resolve_macos_with_helper`: uncaught `CalledProcessError` from clang (carry-forward, first reported 2026-06-21)**
`capture_screenshot.py:resolve_macos_with_helper`
Status: **still unresolved**.

**[medium] Test-code shell injection in `test_windows_delegates_to_powershell` (carry-forward)**
`tests/test_capture_screenshot.py`
Status: **still unresolved**.

---

### Bugs & regressions

**[medium] `Copy-Window` `PrintWindow` failure has no fallback (carry-forward, first reported 2026-06-19)**
Status: **still unresolved**.

**[low] `_linux_window_is_viewable` fragile `"iconic"` substring match (carry-forward)**
Status: **still unresolved**.

**[medium] Linux `active`-target missing `xdotool`+`import` fallback (carry-forward)**
Status: **still unresolved**.

**[medium] Unhandled `CalledProcessError` on `screencapture` failure (carry-forward)**
Status: **still unresolved**.

**[low] `find_macos_window_id.m` silently discards first positional arg when multiple supplied (carry-forward)**
Status: **still unresolved**.

**[medium] Windows dry-run with `--allow-multiple-matches` produces duplicate output paths**
`scripts/capture_screenshot.ps1` (`New-CapturePath` called from `Capture-ToDestination`)
In dry-run mode, `$script:RequestFolder` is set to a path that does not exist on disk
(`Get-RequestFolderPath` instead of `New-RequestFolder`).  `New-CapturePath` checks
`Test-Path -LiteralPath $candidate`, which returns `$false` for every candidate
because the folder doesn't exist.  Multiple windows with the same label (e.g., three
Chrome windows matched by `--allow-multiple-matches`) each return `chrome.png`, causing
duplicate output paths with no uniqueness guarantee.  The Python side avoids this via
an in-memory `reserved` set in `unique_capture_path`.
_Suggested fix:_ Maintain a `$script:dryRunReserved` `HashSet[string]` and extend
`New-CapturePath` to skip candidates already in the set, matching Python semantics.

---

### Data leaks

No new findings.  All output paths use sanitised query text; window titles are not
exposed on any platform.

---

### UX

**[info] `Test-BitmapAllBlack` O(samples) `GetPixel` performance (carry-forward, first reported 2026-06-10)**
Status: **still unresolved**.

**[info] `--query` silently discarded with non-window targets (carry-forward, first reported 2026-06-13)**
Status: **still unresolved**.

**[info] No machine-readable JSON output mode (carry-forward)**
Status: **still unresolved**.

**[info] `plan_capture` active-window error message doesn't name missing tools (carry-forward)**
Status: **still unresolved**.

---

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


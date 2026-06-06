#!/usr/bin/env python3
"""Privacy-first screenshot helper for macOS and Linux.

The script intentionally uses only the Python standard library and OS tools.
It never installs dependencies and never falls back from a narrower capture
target to a broader one.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional


EXIT_USAGE = 64
EXIT_PRIVACY = 73
EXIT_UNAVAILABLE = 74


@dataclasses.dataclass(frozen=True)
class ResolutionResult:
    ok: bool
    code: str
    message: str
    ids: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class CapturePlan:
    ok: bool
    code: str
    message: str
    commands: tuple[tuple[str, ...], ...] = ()


def die(message: str, code: int = 1) -> None:
    raise SystemExit(f"{message}")


def validate_consent(consent_confirmed: bool) -> None:
    if not consent_confirmed:
        die("consent required: ask the user to approve capture and destination first", EXIT_PRIVACY)


def request_folder_name(now: Optional[dt.datetime] = None) -> str:
    return (now or dt.datetime.now()).strftime("%m_%d_%Y_%H_%M_%S")


def sanitize_label(value: Optional[str]) -> str:
    label = (value or "capture").strip().lower()
    label = re.sub(r"https?://", "", label)
    label = re.sub(r"[^a-z0-9]+", "-", label)
    label = re.sub(r"-+", "-", label).strip("-")
    return label[:80].strip("-") or "capture"


def _escape_ere(value: str) -> str:
    """Escape a string so it is treated as a literal by Extended Regular Expression engines."""
    return re.sub(r'([][\\.*+?{}()|^$])', r'\\\1', value)


def _validate_integer_ids(ids: tuple[str, ...], source: str) -> tuple[str, ...]:
    for id_str in ids:
        if not id_str.isdigit():
            die(f"invalid window id from {source}: {id_str!r}", EXIT_USAGE)
    return ids


def unique_capture_path(folder: Path, label: str, reserved: Optional[set[Path]] = None) -> Path:
    reserved = reserved or set()
    safe = sanitize_label(label)
    candidate = folder / f"{safe}.png"
    if not candidate.exists() and candidate not in reserved:
        return candidate
    for index in range(1, 1000):
        candidate = folder / f"{safe}-{index:03d}.png"
        if not candidate.exists() and candidate not in reserved:
            return candidate
    die("could not allocate a unique screenshot filename", EXIT_PRIVACY)


def ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        die("refusing to write to symlinked screenshots folder", EXIT_PRIVACY)
    if path.exists() and not path.is_dir():
        die("refusing to write screenshots into a non-directory path", EXIT_PRIVACY)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except PermissionError:
        die("could not secure screenshots folder permissions", EXIT_PRIVACY)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        die("screenshots folder permissions are not private", EXIT_PRIVACY)


def secure_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except PermissionError:
        die("could not secure screenshot file permissions", EXIT_PRIVACY)


def detect_tools(names: Iterable[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            found[name] = resolved
    return found


def _matches(query: str, value: str | None) -> bool:
    return bool(value) and query.casefold() in value.casefold()


def resolve_macos_window_ids(
    query: str,
    windows: list[dict[str, object]],
    allow_multiple: bool = False,
) -> ResolutionResult:
    matches: list[str] = []
    for window in windows:
        owner = str(window.get("owner", ""))
        title = str(window.get("title", ""))
        window_id = window.get("id")
        if window_id is None:
            continue
        if _matches(query, owner) or _matches(query, title):
            matches.append(str(window_id))
    if not matches:
        return ResolutionResult(False, "no_matching_window", "No matching on-screen window found.")
    if len(matches) > 1 and not allow_multiple:
        return ResolutionResult(
            False,
            "multiple_matches",
            "Multiple matching windows found; ask for a more specific target.",
        )
    return ResolutionResult(True, "ok", "ok", tuple(matches))


def _tool(tools: Optional[dict[str, str]], name: str) -> Optional[str]:
    if tools is None:
        return shutil.which(name)
    return tools.get(name)


def plan_capture(
    platform_name: str,
    target: str,
    destination: str,
    label: str,
    window_ids: Optional[list[str]] = None,
    session_type: Optional[str] = None,
    tools: Optional[dict[str, str]] = None,
) -> CapturePlan:
    if destination not in {"desktop", "clipboard"}:
        return CapturePlan(False, "invalid_destination", "destination must be desktop or clipboard")
    if target not in {"fullscreen", "active", "window"}:
        return CapturePlan(False, "invalid_target", "target must be fullscreen, active, or window")

    window_ids = window_ids or []
    if platform_name == "Darwin":
        screencapture = _tool(tools, "screencapture")
        if not screencapture:
            return CapturePlan(False, "missing_dependency_screencapture", "screencapture is unavailable")
        if target == "fullscreen":
            command = (screencapture, "-c") if destination == "clipboard" else (screencapture, "{output}")
            return CapturePlan(True, "ok", "ok", (command,))
        if not window_ids:
            code = "no_active_window" if target == "active" else "no_matching_window"
            return CapturePlan(False, code, "No matching on-screen window found.")
        commands = []
        for window_id in window_ids:
            if destination == "clipboard":
                commands.append((screencapture, "-x", "-l", str(window_id), "-c"))
            else:
                commands.append((screencapture, "-x", "-l", str(window_id), "{output}"))
        return CapturePlan(True, "ok", "ok", tuple(commands))

    if platform_name == "Linux":
        session = (session_type or os.environ.get("XDG_SESSION_TYPE") or "").lower()
        if target == "window" and session == "wayland":
            return CapturePlan(
                False,
                "unsupported_wayland_window_capture",
                "Wayland named-window capture is not safely available with bundled tools.",
            )
        gnome = _tool(tools, "gnome-screenshot")
        grim = _tool(tools, "grim")
        wl_copy = _tool(tools, "wl-copy")
        spectacle = _tool(tools, "spectacle")
        scrot = _tool(tools, "scrot")
        import_cmd = _tool(tools, "import")
        xdotool = _tool(tools, "xdotool")
        xclip = _tool(tools, "xclip")
        xsel = _tool(tools, "xsel")

        if target == "fullscreen":
            if destination == "clipboard":
                if gnome:
                    return CapturePlan(True, "ok", "ok", ((gnome, "-c"),))
                if grim and wl_copy:
                    return CapturePlan(True, "ok", "ok", ((grim, "{temp-output}"), (wl_copy, "--type", "image/png")))
                if import_cmd and (xclip or xsel):
                    clip = xclip or xsel
                    return CapturePlan(True, "ok", "ok", ((import_cmd, "-window", "root", "{temp-output}"), (clip,)))
            else:
                if gnome:
                    return CapturePlan(True, "ok", "ok", ((gnome, "-f", "{output}"),))
                if grim:
                    return CapturePlan(True, "ok", "ok", ((grim, "{output}"),))
                if spectacle:
                    return CapturePlan(True, "ok", "ok", ((spectacle, "-b", "-n", "-o", "{output}"),))
                if scrot:
                    return CapturePlan(True, "ok", "ok", ((scrot, "{output}"),))
                if import_cmd:
                    return CapturePlan(True, "ok", "ok", ((import_cmd, "-window", "root", "{output}"),))
            return CapturePlan(
                False,
                "missing_dependency_fullscreen",
                "No supported Linux full-screen screenshot tool was found.",
            )

        if target == "active":
            if gnome:
                if destination == "clipboard":
                    return CapturePlan(True, "ok", "ok", ((gnome, "-w", "-c"),))
                return CapturePlan(True, "ok", "ok", ((gnome, "-w", "-f", "{output}"),))
            return CapturePlan(
                False,
                "missing_dependency_active_window",
                "No supported Linux active-window screenshot tool was found.",
            )

        if xdotool and import_cmd and window_ids:
            commands = tuple((import_cmd, "-window", str(window_id), "{output}") for window_id in window_ids)
            return CapturePlan(True, "ok", "ok", commands)
        if xdotool and import_cmd:
            return CapturePlan(False, "no_matching_window", "No matching on-screen window found.")
        return CapturePlan(
            False,
            "missing_dependency_named_window",
            "No supported Linux named-window screenshot tool was found.",
        )

    return CapturePlan(False, "unsupported_platform", f"unsupported platform: {platform_name}")


def _test_platform() -> Optional[str]:
    return os.environ.get("CAPTURE_SCREENSHOT_TEST_PLATFORM")


def _test_windows() -> Optional[list[dict[str, object]]]:
    raw = os.environ.get("CAPTURE_SCREENSHOT_TEST_WINDOWS")
    if not raw:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        die("test window data must be a list", EXIT_USAGE)
    for entry in parsed:
        if not isinstance(entry, dict):
            die("test window data must be a list of dicts", EXIT_USAGE)
        if "id" in entry and not isinstance(entry["id"], int):
            die("test window id must be an integer", EXIT_USAGE)
    return parsed


def resolve_macos_with_helper(query: str, allow_multiple: bool, active: bool, skill_dir: Path) -> ResolutionResult:
    test_windows = _test_windows()
    if test_windows is not None and not active:
        return resolve_macos_window_ids(query, test_windows, allow_multiple=allow_multiple)

    helper_source = skill_dir / "scripts" / "find_macos_window_id.m"
    clang = shutil.which("clang")
    if not clang:
        return ResolutionResult(False, "missing_dependency_clang", "clang is required for macOS window lookup.")
    if not helper_source.exists():
        return ResolutionResult(False, "missing_helper", "macOS window lookup helper is missing.")

    with tempfile.TemporaryDirectory(prefix="screenshot-window.") as tmp:
        helper = Path(tmp) / "find_macos_window_id"
        subprocess.run([clang, "-framework", "ApplicationServices", str(helper_source), "-o", str(helper)], check=True)
        command = [str(helper)]
        if allow_multiple:
            command.append("--allow-multiple")
        if active:
            command.append("--frontmost")
        else:
            command.append(query)
        proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode == 0:
        ids = _validate_integer_ids(
            tuple(line.strip() for line in proc.stdout.splitlines() if line.strip()),
            "macOS window helper",
        )
        return ResolutionResult(True, "ok", "ok", ids)
    if proc.returncode == 2:
        return ResolutionResult(False, "no_matching_window", "No matching on-screen window found.")
    if proc.returncode == 3:
        return ResolutionResult(False, "multiple_matches", "Multiple matching windows found; ask for a more specific target.")
    return ResolutionResult(False, "window_query_failed", "Could not query the window list.")


def resolve_linux_named_window(query: str, allow_multiple: bool, tools: dict[str, str]) -> ResolutionResult:
    xdotool = tools.get("xdotool")
    if not xdotool:
        return ResolutionResult(False, "missing_dependency_xdotool", "xdotool is unavailable.")
    # xdotool --name uses ERE; escape the query so it matches literally.
    proc = subprocess.run([xdotool, "search", "--name", _escape_ere(query)], capture_output=True, text=True)
    if proc.returncode != 0:
        return ResolutionResult(False, "no_matching_window", "No matching on-screen window found.")
    ids = _validate_integer_ids(
        tuple(line.strip() for line in proc.stdout.splitlines() if line.strip()),
        "xdotool",
    )
    if not ids:
        return ResolutionResult(False, "no_matching_window", "No matching on-screen window found.")
    if len(ids) > 1 and not allow_multiple:
        return ResolutionResult(False, "multiple_matches", "Multiple matching windows found; ask for a more specific target.")
    return ResolutionResult(True, "ok", "ok", ids)


def prepare_output_paths(destination: str, output_root: Path, labels: list[str], create: bool = True) -> list[Path]:
    if destination != "desktop":
        return []
    if create:
        ensure_private_directory(output_root)
    request_dir = output_root / request_folder_name()
    if create:
        ensure_private_directory(request_dir)
    paths: list[Path] = []
    reserved: set[Path] = set()
    for label in labels:
        path = unique_capture_path(request_dir, label, reserved)
        reserved.add(path)
        paths.append(path)
    return paths


def private_temp_png(final_path: Path) -> Path:
    for index in range(1000):
        temp_path = final_path.parent / f".{final_path.stem}.{os.getpid()}.{index:03d}.tmp.png"
        try:
            descriptor = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        return temp_path
    die("could not allocate a private temporary screenshot file", EXIT_PRIVACY)


def run_command(command: tuple[str, ...], output: Optional[Path] = None, temp_output: Optional[Path] = None) -> None:
    args = []
    for part in command:
        if part == "{output}":
            if output is None:
                die("internal error: missing output path", EXIT_USAGE)
            args.append(str(output))
        elif part == "{temp-output}":
            if temp_output is None:
                die("internal error: missing temporary output path", EXIT_USAGE)
            args.append(str(temp_output))
        else:
            args.append(part)
    subprocess.run(args, check=True)


def copy_file_to_clipboard(path: Path, tool: str) -> None:
    data = path.read_bytes()
    name = Path(tool).name
    if name == "wl-copy":
        subprocess.run([tool, "--type", "image/png"], input=data, check=True)
    elif name == "xclip":
        subprocess.run([tool, "-selection", "clipboard", "-t", "image/png"], input=data, check=True)
    elif name == "xsel":
        subprocess.run([tool, "--clipboard", "--input"], input=data, check=True)
    else:
        die("unsupported clipboard tool", EXIT_UNAVAILABLE)


def execute_plan(plan: CapturePlan, output_paths: list[Path], destination: str, dry_run: bool) -> None:
    if not plan.ok:
        die(f"{plan.code}: {plan.message}", EXIT_UNAVAILABLE)
    if dry_run:
        for path in output_paths:
            print(path)
        if destination == "clipboard":
            print("clipboard")
        return

    if destination == "clipboard" and len(plan.commands) == 2 and "{temp-output}" in plan.commands[0]:
        with tempfile.NamedTemporaryFile(prefix="capture-screenshot.", suffix=".png") as tmp:
            temp_path = Path(tmp.name)
            run_command(plan.commands[0], temp_output=temp_path)
            copy_file_to_clipboard(temp_path, plan.commands[1][0])
        print("clipboard")
        return

    if destination == "clipboard":
        for command in plan.commands:
            run_command(command)
        print("clipboard")
        return

    if len(plan.commands) != len(output_paths):
        die("internal error: command/output mismatch", EXIT_USAGE)
    for command, output in zip(plan.commands, output_paths):
        temp_output = private_temp_png(output)
        try:
            run_command(command, output=temp_output)
            secure_file(temp_output)
            if output.exists() or output.is_symlink():
                die("refusing to overwrite an existing screenshot path", EXIT_PRIVACY)
            os.replace(temp_output, output)
        finally:
            if temp_output.exists():
                temp_output.unlink()
        secure_file(output)
        print(output)


def _validate_output_root(path: Path) -> None:
    home = Path.home().resolve()
    try:
        path.resolve().relative_to(home)
    except ValueError:
        die("output-root must be within the user home directory", EXIT_USAGE)


def _run_powershell_script(args: argparse.Namespace, skill_dir: Path) -> int:
    ps_script = skill_dir / "scripts" / "capture_screenshot.ps1"
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        die("PowerShell is required for Windows capture but was not found", EXIT_UNAVAILABLE)
    if not ps_script.exists():
        die("Windows capture script is missing", EXIT_UNAVAILABLE)
    cmd = [powershell, "-NoProfile", "-Sta", "-File", str(ps_script),
           "-ConsentConfirmed", "-Destination", args.destination, "-Target", args.target]
    for q in args.query:
        cmd += ["-Query", q]
    cmd += ["-OutputRoot", str(args.output_root)]
    if args.allow_multiple_matches:
        cmd.append("-AllowMultipleMatches")
    if args.dry_run:
        cmd.append("-DryRun")
    return subprocess.run(cmd).returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Privacy-first screenshot capture helper")
    parser.add_argument("--consent-confirmed", action="store_true", help="confirm user approved capture and destination")
    parser.add_argument("--destination", choices=("desktop", "clipboard"), required=True)
    parser.add_argument("--target", choices=("fullscreen", "active", "window"), required=True)
    parser.add_argument("--query", action="append", default=[], help="explicit app/window query; repeat for multiple windows")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "Desktop" / "screenshots")
    parser.add_argument("--allow-multiple-matches", action="store_true", help="capture every matching window for each query")
    parser.add_argument("--dry-run", action="store_true", help="plan capture without invoking screenshot tools")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    validate_consent(args.consent_confirmed)
    if args.destination == "desktop" and not args.dry_run:
        _validate_output_root(args.output_root)

    platform_name = _test_platform() or platform.system()
    skill_dir = Path(__file__).resolve().parents[1]

    if platform_name == "Windows":
        return _run_powershell_script(args, skill_dir)

    labels: list[str] = []
    window_ids: list[str] = []

    if args.target == "window":
        if not args.query:
            die("window target requires at least one --query", EXIT_USAGE)
        for query in args.query:
            if platform_name == "Darwin":
                resolution = resolve_macos_with_helper(query, args.allow_multiple_matches, False, skill_dir)
            elif platform_name == "Linux":
                tools = detect_tools(("xdotool", "import"))
                resolution = resolve_linux_named_window(query, args.allow_multiple_matches, tools)
            else:
                resolution = ResolutionResult(False, "unsupported_platform", f"unsupported platform: {platform_name}")
            if not resolution.ok:
                die(f"{resolution.code}: {resolution.message}", EXIT_UNAVAILABLE)
            window_ids.extend(resolution.ids)
            labels.extend([sanitize_label(query)] * len(resolution.ids))
    elif args.target == "active":
        labels = ["active-window"]
        if platform_name == "Darwin":
            resolution = resolve_macos_with_helper("", False, True, skill_dir)
            if not resolution.ok:
                die(f"{resolution.code}: {resolution.message}", EXIT_UNAVAILABLE)
            window_ids.extend(resolution.ids)
    else:
        labels = ["screen"]

    tools = detect_tools(
        (
            "screencapture",
            "gnome-screenshot",
            "grim",
            "wl-copy",
            "spectacle",
            "scrot",
            "import",
            "xdotool",
            "xclip",
            "xsel",
        )
    )
    if platform_name == "Darwin" and args.dry_run and _test_platform() == "Darwin":
        tools.setdefault("screencapture", "/usr/sbin/screencapture")

    output_paths = prepare_output_paths(args.destination, args.output_root, labels, create=not args.dry_run)
    plan = plan_capture(
        platform_name=platform_name,
        target=args.target,
        destination=args.destination,
        label=labels[0] if labels else "capture",
        window_ids=window_ids,
        session_type=os.environ.get("XDG_SESSION_TYPE"),
        tools=tools,
    )
    execute_plan(plan, output_paths, args.destination, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

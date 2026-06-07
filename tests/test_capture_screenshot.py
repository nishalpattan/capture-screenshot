import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capture_screenshot.py"
SKILL_MD = ROOT / "SKILL.md"


def load_module():
    spec = importlib.util.spec_from_file_location("capture_screenshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CaptureScreenshotUnitTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_requires_explicit_consent(self):
        with self.assertRaises(SystemExit) as cm:
            self.mod.validate_consent(False)
        self.assertEqual(cm.exception.code, self.mod.EXIT_PRIVACY)

    def test_request_folder_uses_required_timestamp_format(self):
        folder = self.mod.request_folder_name(self.mod.dt.datetime(2026, 6, 5, 16, 40, 12))
        self.assertEqual(folder, "06_05_2026_16_40_12")

    def test_sanitizes_app_label_without_window_title(self):
        label = self.mod.sanitize_label("ChatGPT - Customer Acme Roadmap https://private.example")
        self.assertEqual(label, "chatgpt-customer-acme-roadmap-private-example")
        self.assertNotIn("https", label)
        self.assertNotIn("/", label)

    def test_unique_capture_paths_use_label_and_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = self.mod.unique_capture_path(base, "terminal")
            first.touch()
            second = self.mod.unique_capture_path(base, "terminal")
            self.assertEqual(first.name, "terminal.png")
            self.assertEqual(second.name, "terminal-001.png")

    def test_prepare_output_paths_suffixes_duplicate_labels_in_one_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.mod.prepare_output_paths(
                "desktop",
                Path(tmp) / "screenshots",
                ["terminal", "terminal"],
            )
            self.assertEqual([path.name for path in paths], ["terminal.png", "terminal-001.png"])

    def test_desktop_folder_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "screenshots"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(SystemExit) as cm:
                self.mod.ensure_private_directory(link)
        self.assertEqual(cm.exception.code, self.mod.EXIT_PRIVACY)

    def test_private_temp_png_is_not_hidden(self):
        # macOS `screencapture` refuses to write to dot-prefixed (hidden) paths,
        # so the private temp file must not start with a leading dot.
        with tempfile.TemporaryDirectory() as tmp:
            final = Path(tmp) / "screen.png"
            temp = self.mod.private_temp_png(final)
            try:
                self.assertFalse(temp.name.startswith("."), msg=f"temp file is hidden: {temp.name}")
                self.assertTrue(temp.name.endswith(".tmp.png"))
                self.assertTrue(temp.exists())
                self.assertEqual(stat.S_IMODE(temp.stat().st_mode), 0o600)
            finally:
                if temp.exists():
                    temp.unlink()

    def test_desktop_folder_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "screenshots"
            self.mod.ensure_private_directory(target)
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode, 0o700)

    def test_macos_multiple_matches_fails_without_leaking_titles(self):
        result = self.mod.resolve_macos_window_ids(
            query="ChatGPT",
            windows=[
                {"id": 10, "owner": "ChatGPT", "title": "Private Customer A"},
                {"id": 11, "owner": "ChatGPT", "title": "Private Customer B"},
            ],
            allow_multiple=False,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "multiple_matches")
        self.assertNotIn("Private Customer", result.message)

    def test_macos_named_window_plan_does_not_fallback_to_fullscreen(self):
        plan = self.mod.plan_capture(
            platform_name="Darwin",
            target="window",
            destination="desktop",
            label="ChatGPT",
            window_ids=[],
            tools={"screencapture": "/usr/sbin/screencapture"},
        )
        self.assertFalse(plan.ok)
        self.assertEqual(plan.code, "no_matching_window")
        self.assertFalse(plan.commands)

    def test_linux_wayland_missing_tools_fails_closed(self):
        plan = self.mod.plan_capture(
            platform_name="Linux",
            target="fullscreen",
            destination="desktop",
            label="screen",
            session_type="wayland",
            tools={},
        )
        self.assertFalse(plan.ok)
        self.assertIn("missing_dependency", plan.code)
        self.assertFalse(plan.commands)

    def test_dry_run_output_has_no_window_title_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "screenshots"
            env = os.environ.copy()
            env["CAPTURE_SCREENSHOT_TEST_PLATFORM"] = "Darwin"
            env["CAPTURE_SCREENSHOT_TEST_WINDOWS"] = json.dumps(
                [{"id": 22, "owner": "ChatGPT", "title": "Secret Roadmap"}]
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--consent-confirmed",
                    "--destination",
                    "desktop",
                    "--target",
                    "window",
                    "--query",
                    "ChatGPT",
                    "--output-root",
                    str(output_root),
                    "--dry-run",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn("chatgpt.png", proc.stdout)
            self.assertNotIn("Secret Roadmap", proc.stdout)
            self.assertFalse(output_root.exists())

    def test_windows_delegates_to_powershell(self):
        with tempfile.TemporaryDirectory() as tmp:
            args_file = Path(tmp) / "captured_args.txt"
            fake_ps = Path(tmp) / "powershell.exe"
            # Write every argv entry on its own line so we can assert each flag.
            fake_ps.write_text(
                f'#!/bin/sh\nprintf "%s\\n" "$@" > {args_file}\necho "fake/path.png"\n'
            )
            fake_ps.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{tmp}:{env.get('PATH', '')}"
            env["CAPTURE_SCREENSHOT_TEST_PLATFORM"] = "Windows"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--consent-confirmed", "--destination", "desktop",
                 "--target", "fullscreen", "--dry-run"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("fake/path.png", proc.stdout)
            captured = args_file.read_text()
            for expected in ("-ConsentConfirmed", "-Destination", "desktop",
                             "-Target", "fullscreen", "-DryRun"):
                self.assertIn(expected, captured, msg=f"flag {expected!r} not forwarded to PowerShell")

    def test_windows_requires_powershell(self):
        env = os.environ.copy()
        env["CAPTURE_SCREENSHOT_TEST_PLATFORM"] = "Windows"
        env["PATH"] = ""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--consent-confirmed", "--destination", "desktop", "--target", "fullscreen"],
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("PowerShell", combined)

    def test_skill_notice_documents_privacy_consent_and_intended_use(self):
        text = SKILL_MD.read_text(encoding="utf-8").lower()
        required_phrases = [
            "privacy, consent, and intended use",
            "educational",
            "debugging",
            "documentation",
            "accessibility-support",
            "covert capture",
            "monitoring",
            "surveillance",
            "bypass os permissions",
            "without appropriate authorization",
            "does not send screenshots to an external service",
            "does not store data in an external database",
            "locally",
            "not legal advice",
        ]
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()

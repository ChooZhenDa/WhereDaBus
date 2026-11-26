"""Exercise the Windows entry points with Docker, HTTP and the browser stubbed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows launchers")
class PackagedLaunchers(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="wdb release ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for folder in ("scripts", "review", "data"):
            (self.root / folder).mkdir()
        for launcher in ("Setup.cmd", "Start.cmd", "Stop.cmd"):
            shutil.copyfile(PROJECT / launcher, self.root / launcher)
        self.metadata = self.root / "data" / "EXPORT_METADATA.json"
        self.metadata.write_text("{}")
        (self.root / "scripts" / "manage.cmd").write_text(
            "@echo off\necho LIVE %1 %2\nexit /b 17\n", encoding="ascii")
        (self.root / "scripts" / "ensure-docker.ps1").write_text(
            'Write-Host "Checking Docker"\nexit ([int]$env:MOCK_PREFLIGHT_EXIT)\n', encoding="ascii")
        # Both scripts run under Windows PowerShell in the same directory, so
        # the real runner also exercises its normal project and config paths.
        self.runner = self.root / "review" / "run.ps1"
        shutil.copyfile(PROJECT / "review" / "run.ps1", self.runner.with_name("actual.ps1"))
        self.runner.write_text("""
param([string]$Action, [switch]$NoBrowser)
function docker {
    $global:LASTEXITCODE = [int]$env:MOCK_DOCKER_EXIT
    if ($args -contains 'port') { '127.0.0.1:18080' }
}
function Invoke-RestMethod { 'healthy' }
function Start-Process { Write-Host "BROWSER $args" }
& "$PSScriptRoot\\actual.ps1" @PSBoundParameters
""", encoding="ascii")

    def launch(self, name, *args, docker_exit=0, preflight_exit=0):
        return subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "call", name, *args],
            cwd=self.root, input="\n", text=True, capture_output=True,
            env={**os.environ, "MOCK_DOCKER_EXIT": str(docker_exit),
                 "MOCK_PREFLIGHT_EXIT": str(preflight_exit)}, timeout=30)

    def test_packaged_start_and_stop(self):
        start = self.launch("Start.cmd")
        self.assertEqual(start.returncode, 0, start.stderr)
        self.assertIn("BROWSER http://127.0.0.1:18080/?review=morning#peak", start.stdout)
        self.assertTrue((self.root / "review" / ".env.review").is_file())
        quiet = self.launch("Start.cmd", "--no-browser")
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertIn("Morning crowding:", quiet.stdout)
        self.assertNotIn("BROWSER", quiet.stdout)
        stop = self.launch("Stop.cmd")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn("Review stopped.", stop.stdout)
        self.assertNotIn("BROWSER", stop.stdout)

    def test_unstarted_review_stop_and_failed_start(self):
        stop = self.launch("Stop.cmd")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIn("No review installation found.", stop.stdout)
        self.assertFalse((self.root / "review" / ".env.review").exists())
        failed = self.launch("Start.cmd", "--no-browser", docker_exit=1)
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("BROWSER", failed.stdout)

    def test_normal_configuration_or_incomplete_review_uses_original_commands(self):
        config = self.root / ".env"
        config.touch()
        for branch in ("configured", "no data marker", "no review runner"):
            with self.subTest(branch=branch):
                if branch == "no data marker":
                    config.unlink()
                    self.metadata.unlink()
                if branch == "no review runner":
                    self.metadata.touch()
                    self.runner.unlink()
                for action, args in (("Start", ["--no-browser"]), ("Stop", [])):
                    result = self.launch(action + ".cmd", *args)
                    self.assertEqual(result.returncode, 17, result.stderr)
                    self.assertIn(" ".join(["LIVE", action, *args]), result.stdout)

    def test_default_setup_starts_recorded_data_and_reuses_the_configuration(self):
        result = self.launch("Setup.cmd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Checking Docker", result.stdout)
        self.assertIn("BROWSER http://127.0.0.1:18080/?review=morning#peak", result.stdout)
        review_config = self.root / "review" / ".env.review"
        initial_config = review_config.read_bytes()
        repeat = self.launch("Setup.cmd", "--no-browser")
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertNotIn("BROWSER", repeat.stdout)
        self.assertIn("Morning crowding:", repeat.stdout)
        self.assertEqual(review_config.read_bytes(), initial_config)
        self.assertFalse((self.root / ".env").exists())
        failed = self.launch("Setup.cmd", "--no-browser", docker_exit=1)
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("BROWSER", failed.stdout)

    def test_setup_respects_live_override_and_existing_normal_install(self):
        for existing_config in (False, True):
            if existing_config:
                (self.root / ".env").touch()
                default = self.launch("Setup.cmd", "--no-browser")
                self.assertEqual(default.returncode, 17, default.stderr)
                self.assertIn("LIVE SetupRecorded --no-browser", default.stdout)
            for args in (["--live"], ["--live", "--no-browser"], ["--no-browser", "--live"]):
                with self.subTest(configured=existing_config, args=args):
                    result = self.launch("Setup.cmd", *args)
                    self.assertEqual(result.returncode, 17, result.stderr)
                    self.assertIn("Checking Docker", result.stdout)
                    expected = "LIVE Setup --no-browser" if "--no-browser" in args else "LIVE Setup "
                    self.assertIn(expected, result.stdout)
        self.assertFalse((self.root / "review" / ".env.review").exists())

    def test_unbundled_setup_keeps_the_original_configuration_flow(self):
        self.metadata.unlink()
        for existing_config in (False, True):
            if existing_config:
                (self.root / ".env").touch()
            result = self.launch("Setup.cmd", "--no-browser", preflight_exit=3)
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertNotIn("Checking Docker", result.stdout)
            self.assertIn("LIVE Setup --no-browser", result.stdout)

    def test_failed_docker_preflight_stops_setup_before_creating_a_stack(self):
        for args in ([], ["--live"]):
            result = self.launch("Setup.cmd", *args, preflight_exit=3)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("Checking Docker", result.stdout)
            self.assertNotIn("BROWSER", result.stdout)
            self.assertNotIn("LIVE", result.stdout)
            self.assertFalse((self.root / "review" / ".env.review").exists())

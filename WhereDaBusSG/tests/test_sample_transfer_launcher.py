"""Exercise the actual Windows launchers against an isolated Docker stand-in."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
FAKE_DOCKER = """
ConvertTo-Json -InputObject @($args) -Compress |
    Add-Content -LiteralPath $env:TRANSFER_TRACE -Encoding UTF8
if (($args -contains 'cp') -and ($args[-2] -like 'api:*.zip')) {
    [IO.File]::WriteAllText($args[-1], 'sample transfer test')
}
exit 0
"""


@unittest.skipUnless(os.name == "nt", "Requires Windows PowerShell and cmd.exe")
class SampleTransferLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cwd = Path(temporary.name)
        self.project = self.cwd / "project with spaces"
        (self.project / "scripts").mkdir(parents=True)
        for name in ("Restore-Samples.cmd", "Export-Samples.cmd", "scripts/sample-transfer.ps1"):
            shutil.copy2(PROJECT / name, self.project / name)
        (self.project / "backend").mkdir()
        (self.project / "backend/sample_transfer.py").touch()
        self.create_compose_files(self.project)
        self.archive = self.cwd / "backup with spaces.zip"
        self.trace = self.cwd / "docker.jsonl"
        fake_bin = self.cwd / "fake docker bin"
        fake_bin.mkdir()
        (fake_bin / "docker.ps1").write_text(FAKE_DOCKER, encoding="ascii")
        system32 = Path(os.environ["SystemRoot"]) / "System32"
        shell_dir = system32 / "WindowsPowerShell/v1.0"
        self.cmd = system32 / "cmd.exe"
        self.powershell = shell_dir / "powershell.exe"
        self.env = dict(os.environ)
        self.env["PATH"] = os.pathsep.join(map(str, (fake_bin, self.project, shell_dir, system32)))
        self.env["TRANSFER_TRACE"] = str(self.trace)

    def create_compose_files(self, project):
        (project / ".env").write_text("WDB_PROJECT=wheredabus-0000000000000000\n", encoding="ascii")
        (project / "compose.yaml").write_text("services: {}\n", encoding="ascii")

    def run_transfer(self, command, project):
        process = subprocess.run(command, cwd=self.cwd, env=self.env, capture_output=True,
                                 text=True, stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        calls = [json.loads(line) for line in self.trace.read_text(encoding="utf-8-sig").splitlines()]
        self.assertTrue(calls, "The Windows launcher never invoked Docker")
        for args in calls:
            self.assertEqual(Path(args[args.index("--file") + 1]), project / "compose.yaml")
            self.assertEqual(Path(args[args.index("--env-file") + 1]), project / ".env")
        return calls

    def test_restore_from_another_working_directory(self):
        self.archive.touch()
        calls = self.run_transfer([self.cmd, "/d", "/c", "Restore-Samples.cmd", self.archive],
                                  self.project)
        self.assertTrue(any("cp" in args and args[-2] == str(self.archive) for args in calls))
        self.assertTrue(any("exec" in args and args[-2] == "restore" for args in calls))

    def test_fresh_submission_points_to_review_before_requesting_an_archive(self):
        (self.project / ".env").unlink()
        result = subprocess.run([self.cmd, "/d", "/c", "Restore-Samples.cmd"],
                                cwd=self.cwd, env=self.env, text=True, capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Setup.cmd", result.stderr)
        self.assertFalse(self.trace.exists())

    def test_export_to_path_with_spaces(self):
        calls = self.run_transfer([self.cmd, "/d", "/c", "Export-Samples.cmd", self.archive],
                                  self.project)
        self.assertTrue(any("exec" in args and args[-2] == "export" for args in calls))
        self.assertEqual(self.archive.read_text(), "sample transfer test")

    def test_explicit_project_path_selects_the_target_installation(self):
        target = self.cwd / "other installation"
        target.mkdir()
        self.create_compose_files(target)
        self.archive.touch()
        script = self.project / "scripts/sample-transfer.ps1"
        self.run_transfer([self.powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           script, "-Action", "Restore", "-Archive", self.archive,
                           "-ProjectPath", target], target)


if __name__ == "__main__":
    unittest.main()

"""Check SetupRecorded routing in cmd.exe without starting Docker or a browser."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
DOCKER_STUB = """
using System;
using System.IO;

public class DockerStub {
    public static int Main(string[] args) {
        File.AppendAllText(Environment.GetEnvironmentVariable("SETUP_TRACE"),
            "docker\\t" + String.Join("\\t", args) + Environment.NewLine);
        if (args.Length > 1 && args[0] == "info") Console.WriteLine("linux");
        if (Array.IndexOf(args, "up") >= 0 && Environment.GetEnvironmentVariable("FAIL_START") == "1") return 1;
        if (Array.IndexOf(args, "backend.submission_network") >= 0) {
            if (Console.In.ReadToEnd().Trim() != "SELECT 1;") return 1;
            if (Environment.GetEnvironmentVariable("FAIL_SQL") == "1") return 1;
        }
        return 0;
    }
}
"""
TRANSFER_STUB = """
param([string]$Action, [string]$ProjectPath, [string]$Archive, [int]$CheckMorningPort)
Add-Content -LiteralPath $env:SETUP_TRACE -Encoding Ascii -Value ("transfer`t$Action`t$ProjectPath`t$Archive`t$CheckMorningPort")
if ($env:TRANSFER_EXIT) { exit ([int]$env:TRANSFER_EXIT) }
Write-Output '{"verified":5,"inserted":5,"alreadyPresent":0,"outsideRetention":0}'
exit 0
"""
REVIEW_STUB = """
param([string]$Action, [switch]$NoBrowser)
Add-Content -LiteralPath $env:SETUP_TRACE -Encoding Ascii -Value ("review`t$Action`t$NoBrowser")
exit 0
"""


@unittest.skipUnless(os.name == "nt", "Requires Windows cmd.exe and Windows PowerShell")
class ConfiguredSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sandbox = tempfile.TemporaryDirectory()
        cls.addClassCleanup(sandbox.cleanup)
        cls.bin = Path(sandbox.name)
        cls.system32 = Path(os.environ["SystemRoot"]) / "System32"
        cls.shell = cls.system32 / "WindowsPowerShell/v1.0"
        source = cls.bin / "DockerStub.cs"
        source.write_text(DOCKER_STUB, encoding="ascii")
        # cmd.exe transfers control to another batch file when called without CALL.
        # Use a real console executable so the production launcher's flow is unchanged.
        subprocess.run([cls.shell / "powershell.exe", "-NoProfile", "-Command",
                        "Add-Type -Path DockerStub.cs -OutputAssembly docker.exe "
                        "-OutputType ConsoleApplication -ErrorAction Stop"],
                       cwd=cls.bin, capture_output=True, text=True, check=True, timeout=30)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = Path(temporary.name) / "existing installation"
        scripts = self.app / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(PROJECT / "scripts/manage.cmd", scripts)
        (scripts / "sample-transfer.ps1").write_text(TRANSFER_STUB, encoding="ascii")
        (self.app / "review").mkdir()
        (self.app / "review/run.ps1").write_text(REVIEW_STUB, encoding="ascii")
        self.archive = self.app / "data/samples.zip"
        self.archive.parent.mkdir()
        self.archive.touch()
        (self.app / "data/postgresql.sql").write_text("SELECT 1;\n", encoding="ascii")
        (self.app / "compose.yaml").write_text("services: {}\n")
        self.project = "wheredabus-0123456789abcdef"
        self.port = "18191"
        self.settings = ("WDB_PROJECT=" + self.project + "\nAPP_PORT=" + self.port
                         + "\nLTA_ACCOUNT_KEY=fixture-only\n")
        (self.app / ".env").write_text(self.settings)
        (self.app / ".launcher-state").write_text("LAUNCH_PROJECT=" + self.project
            + "\nLAUNCH_PORT=" + self.port + "\nLAUNCH_HAS_KEY=1\n")
        self.trace = self.app / "trace.tsv"
        self.env = dict(os.environ, SETUP_TRACE=str(self.trace),
                        PATH=os.pathsep.join(map(str, (self.bin, self.shell, self.system32))))

    def run_setup(self):
        result = subprocess.run([self.system32 / "cmd.exe", "/d", "/c", "scripts\\manage.cmd",
                                 "SetupRecorded", "--no-browser"], cwd=self.app, env=self.env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual((self.app / ".env").read_text(), self.settings)
        calls = [line.split("\t") for line in self.trace.read_text().splitlines()] if self.trace.exists() else []
        return result, calls

    def test_reuses_configured_port_project_and_packaged_samples(self):
        result, calls = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config_calls = [call for call in calls if "scripts/install_config.py" in call]
        self.assertEqual([call[-1] for call in config_calls], ["check"])
        api_start = next(call for call in calls if "up" in call)
        self.assertIn(self.project, api_start)
        self.assertIn("api", api_start)
        sql_load = next(call for call in calls if "backend.submission_network" in call)
        self.assertIn(self.project, sql_load)
        transfers = [call for call in calls if call[0] == "transfer"]
        self.assertEqual(len(transfers), 1)
        _, action, project, archive, port = transfers[0]
        self.assertEqual(action.lower(), "restore")
        self.assertEqual(Path(project), self.app)
        self.assertEqual(Path(archive), self.archive)
        self.assertEqual(port, self.port)
        self.assertLess(calls.index(api_start), calls.index(sql_load))
        self.assertLess(calls.index(sql_load), calls.index(transfers[0]))
        self.assertFalse(any(call[0] == "review" for call in calls))
        self.assertFalse(any("sync-stops" in call or "sync-static" in call for call in calls))
        self.assertIn(f"http://localhost:{self.port}/?review=morning#peak", result.stdout)

    def test_restore_failure_does_not_open_the_recorded_result(self):
        self.env["TRANSFER_EXIT"] = "1"
        result, calls = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(call[0] == "transfer" for call in calls))
        self.assertFalse(any(call[0] == "review" for call in calls))
        self.assertNotIn("?review=morning#peak", result.stdout)

    def test_unavailable_live_morning_starts_archive_without_opening_browser(self):
        self.env["TRANSFER_EXIT"] = "3"
        result, calls = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[-1], ["review", "Start", "True"])
        self.assertTrue(any(call[0] == "transfer" and call[-1] == self.port for call in calls))
        self.assertNotIn(f"Open http://localhost:{self.port}/?review=morning#peak", result.stdout)

    def test_sql_load_failure_skips_sample_restore_and_review(self):
        self.env["FAIL_SQL"] = "1"
        result, calls = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("backend.submission_network" in call for call in calls))
        self.assertFalse(any(call[0] in ("transfer", "review") for call in calls))

    def test_api_start_failure_skips_restore(self):
        self.env["FAIL_START"] = "1"
        result, calls = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("up" in call for call in calls))
        self.assertFalse(any(call[0] == "transfer" for call in calls))


if __name__ == "__main__":
    unittest.main()

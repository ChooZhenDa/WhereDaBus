import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'ensure-docker.ps1'
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows Docker Desktop launcher')
class DockerStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.trace = self.root / 'calls.txt'
        self.fake = self.root / 'docker.cmd'
        self.env = dict(os.environ, PATH=str(self.root), LOCALAPPDATA=str(self.root),
                        ProgramFiles=str(self.root), DOCKER_TEST_ROOT=str(self.root))

    def run_check(self, mode):
        self.env['DOCKER_TEST_MODE'] = mode
        return subprocess.run(
            [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
             str(SCRIPT), '-TimeoutSeconds', '0'],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30,
        )

    def install_fake_cli(self):
        self.fake.write_text(
            '@echo off\n'
            'echo %*>>"%DOCKER_TEST_ROOT%\\calls.txt"\n'
            'if "%1"=="desktop" goto start\n'
            'if /i "%DOCKER_TEST_MODE%"=="windows" (echo windows& exit /b 0)\n'
            'if /i "%DOCKER_TEST_MODE%"=="ready" (echo linux& exit /b 0)\n'
            'if exist "%DOCKER_TEST_ROOT%\\running" (echo linux& exit /b 0)\n'
            'exit /b 1\n'
            ':start\n'
            'if /i "%DOCKER_TEST_MODE%"=="start" (type nul >"%DOCKER_TEST_ROOT%\\running"& exit /b 0)\n'
            'exit /b 1\n', encoding='ascii',
        )

    def test_ready_engine_returns_without_starting_desktop(self):
        self.install_fake_cli()
        result = self.run_check('ready')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.trace.read_text().splitlines(), ['info --format {{.OSType}}'])

    def test_windows_engine_explains_how_to_switch(self):
        self.install_fake_cli()
        result = self.run_check('windows')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Switch to Linux containers', result.stdout)
        self.assertNotIn('desktop start', self.trace.read_text())

    def test_missing_cli_shows_installation_help(self):
        result = self.run_check('missing')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Docker CLI was not found', result.stdout)
        self.assertIn('https://docs.docker.com/', result.stdout)

    def test_stopped_engine_starts_detached_and_rechecks(self):
        self.install_fake_cli()
        result = self.run_check('start')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.trace.read_text().splitlines(), [
            'info --format {{.OSType}}', 'desktop start --detach', 'info --format {{.OSType}}',
        ])

    def test_cli_only_installation_gives_desktop_setup_instructions(self):
        self.install_fake_cli()
        result = self.run_check('missing-desktop')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Open Docker Desktop from the Start menu', result.stdout)


if __name__ == '__main__':
    unittest.main()

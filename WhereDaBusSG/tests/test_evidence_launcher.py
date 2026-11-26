"""Windows launcher checks with a fake Docker command; never touch a daemon."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(os.name == 'nt', 'Native Windows PowerShell launcher checks')
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir()
        shutil.copy2(PROJECT/'scripts/evidence.ps1',self.root/'scripts/evidence.ps1')
        (self.root/'compose.yaml').write_text('services: {}')
        (self.root/'.env').write_text('WDB_PROJECT=wheredabus-0123456789abcdef\nAPP_PORT=18081\nSAMPLE_STOPS=22599,44181\nSAMPLE_SERVICES=\nLTA_ACCOUNT_KEY=local-test-placeholder\n')
        fake=self.root/'fake';fake.mkdir()
        (fake/'docker.cmd').write_text('@echo off\n"'+sys.executable+'" "%~dp0fake_docker.py" %*\n')
        (fake/'fake_docker.py').write_text('import os,sys,json\nif "exec" in sys.argv:\n status="incomplete" if os.getenv("FAKE_INCOMPLETE")=="1" else "completed"\n print(json.dumps({"evidenceVersion":1,"mode":sys.argv[-1],"status":status}))\n sys.exit(1 if status=="incomplete" else 0)\n')
        self.env=dict(os.environ,PATH=str(fake)+os.pathsep+os.environ['PATH'])
    def tearDown(self): self.temp.cleanup()
    def run_action(self,action):
        return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'scripts/evidence.ps1'),'-Action',action],env=self.env,text=True,capture_output=True)
    def test_invalid_stop_does_not_start(self):
        p=self.root/'.env';p.write_text(p.read_text().replace('22599,44181','bad'))
        r=self.run_action('Start')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('five-digit',r.stdout)
    def test_missing_env_fails_clearly(self):
        (self.root/'.env').unlink()
        r=self.run_action('Start')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('Setup.cmd',r.stdout)
    def test_valid_configuration_starts(self):
        r=self.run_action('Start')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('60 seconds',r.stdout)
    def test_collect_saves_three_reports(self):
        r=self.run_action('Collect')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        reports=list((self.root/'evidence').glob('*/*.json'))
        self.assertEqual(len(reports),3)
        self.assertEqual({json.loads(p.read_text())['mode'] for p in reports},{'audit','crud','benchmark'})
    def test_incomplete_is_saved_but_never_claimed_success(self):
        self.env['FAKE_INCOMPLETE']='1'
        r=self.run_action('Collect')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len(list((self.root/'evidence').glob('*/*.json'))),3)
        self.assertIn('incomplete stages',r.stdout)
if __name__=='__main__': unittest.main()


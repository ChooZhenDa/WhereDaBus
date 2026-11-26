"""Submission packaging checks with fake database exports, not real DB results."""
import json, os, shutil, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(os.name=='nt','Windows PowerShell submission utility')
class SubmissionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'project'
        shutil.copytree(PROJECT,self.root,ignore=shutil.ignore_patterns('__pycache__','node_modules','evidence','submission','.submission-build'))
        (self.root/'.env').write_text('WDB_PROJECT=wheredabus-0123456789abcdef\n')
        fake=Path(self.temp.name)/'fake';fake.mkdir()
        (fake/'docker.ps1').write_text("& '"+sys.executable.replace("'","''")+"' (Join-Path $PSScriptRoot 'fake_docker.py') @args\nexit $LASTEXITCODE\n")
        (fake/'fake_docker.py').write_text("""import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['FAKE_TRACE'],'a') as trace: trace.write(json.dumps(args)+'\\n')
if 'cp' in args:
 Path(args[-1]).write_text('-- SYNTHETIC PACKAGING TEST ONLY')
elif args and args[-1]=='arrival_observations':
 if os.getenv('FAKE_EMPTY')!='1': print(json.dumps({'source':'SYNTHETIC PACKAGING TEST'}))
elif any('counts = {}' in x for x in args):
 print(json.dumps({'bus_stops':3,'bus_services':2,'bus_routes':5,'import_runs':1}))
""")
        self.env=dict(os.environ,PATH=str(fake)+os.pathsep+os.environ['PATH'],FAKE_TRACE=str(self.root/'calls.jsonl'))
    def tearDown(self):self.temp.cleanup()
    def run_export(self):
        return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'submission-tools/Prepare-Submission.ps1'),'-ProjectPath',str(self.root),'-GroupId','51'],env=self.env,text=True,capture_output=True)
    def test_empty_history_is_rejected(self):
        self.env['FAKE_EMPTY']='1'
        r=self.run_export()
        self.assertNotEqual(r.returncode,0)
        self.assertIn('analytics history export is empty',r.stdout)
        self.assertNotIn('reserved generated submission path',r.stdout)
        self.assertFalse(list((self.root/'submission').glob('*.zip')))
    def test_previous_export_can_be_reexported(self):
        r=self.run_export()
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        files=list((self.root/'submission').glob('*.zip'))
        self.assertEqual(len(files),1)
        with zipfile.ZipFile(files[0]) as z:
            names={name.replace('\\','/'):name for name in z.namelist()}
            self.assertNotIn('WhereDaBusSG/.env',names)
            metadata=json.loads(z.read(names['WhereDaBusSG/data/EXPORT_METADATA.json']))
            self.assertEqual(metadata['mongoExportedDocuments']['arrival_observations'],1)
            self.assertEqual(z.read(names['WhereDaBusSG/data/postgresql.sql']),b'-- SYNTHETIC PACKAGING TEST ONLY')
if __name__=='__main__':unittest.main()


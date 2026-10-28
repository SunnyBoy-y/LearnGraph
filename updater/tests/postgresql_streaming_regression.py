"""Packaged recovery and shell branches on a small, already passed fixture."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from updater.tests.docker_isolation import require, validate_fixture
from updater.tests.postgresql_roundtrip import FixtureAgent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    root=args.root.resolve(); marker,_=validate_fixture(root)
    evidence=json.loads((root/'verification.json').read_text())
    require(evidence.get('passed') and evidence['project']==marker['project'],'passed owned fixture required')
    agent=FixtureAgent(root,'LearnGraph/UpdateFixture',marker['image'],['compose.json'])
    require(not agent.active(),'finish maintenance before recovery regression')
    source=Path(__file__).resolve().parents[2]/'backend/app'
    packaged=root/'packaged-app'
    require(not packaged.exists() and packaged.resolve().parent==root,'fresh contained packaged source required')
    require(all(not path.is_symlink() and path.resolve().is_relative_to(source.resolve()) for path in source.rglob('*')),'packaged source symlinks refused')
    shutil.copytree(source,packaged,ignore=shutil.ignore_patterns('__pycache__','.env*','*.db','*.sqlite*'))
    dockerfile,ignore=root/'Dockerfile',root/'.dockerignore'
    previous=dockerfile.read_bytes()
    old_ignore=ignore.read_bytes() if ignore.exists() else None
    image=marker['image']+':v1.0.0-stream-packaged'
    try:
        dockerfile.write_text('FROM '+marker['image']+':v1.0.0\nUSER root\nCOPY packaged-app /app/app\n',encoding='utf-8')
        ignore.write_text('*\n!Dockerfile\n!packaged-app\n!packaged-app/**\n',encoding='utf-8')
        agent.command(['build','-t',image,str(root)],240)
    finally:
        dockerfile.write_bytes(previous)
        if old_ignore is None: ignore.unlink(missing_ok=True)
        else: ignore.write_bytes(old_ignore)
        require(packaged.resolve().parent==root and not packaged.is_symlink(),'packaged cleanup escaped root')
        shutil.rmtree(packaged)
    for module,extra in [('postgresql_config_roundtrip',[]),('postgresql_key_roundtrip',['--image',image]),('shell_roundtrip',[])]:
        process=subprocess.run([sys.executable,'-B','-u','-m','updater.tests.'+module,'--root',str(root),*extra],capture_output=True,text=True,encoding='utf-8',timeout=1800)
        (root/('large-backup-'+module+'.log')).write_text(process.stdout+process.stderr,encoding='utf-8')
        require(process.returncode==0,'isolated '+module+' failed; inspect its private log')
        print(module+' passed',flush=True)
    print(json.dumps({'passed':True,'packaged_image':image}),flush=True)


if __name__=='__main__':main()

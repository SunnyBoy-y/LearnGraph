"""Exercise a >3 GB multipart upload on the final, updated isolated service."""
import argparse
import hashlib
import json
from pathlib import Path
import threading
import time

import httpx

from updater.tests.docker_isolation import require,validate_fixture
from updater.tests.postgresql_roundtrip import BACKUP_PASSWORD,PASSWORD,FixtureAgent

PROBE=r'''
import os,json,pwd
from pathlib import Path
if os.getuid()==0:
    runtime=pwd.getpwnam('learngraph')
    os.setgid(runtime.pw_gid);os.setuid(runtime.pw_uid)
files=[];readable=0
for process in Path('/proc').glob('[0-9]*'):
    try:
        paths=list((process/'fd').iterdir())
        if process.name!=str(os.getpid()) and process.stat().st_uid==os.getuid(): readable+=1
        for path in paths:
            try:
                name=os.readlink(path);size=path.stat().st_size
                if size>128*1024**2: files.append({'path':name,'bytes':size,'device':path.stat().st_dev})
            except (OSError,ValueError): pass
    except (OSError,ValueError): pass
print(json.dumps({'data_device':Path('/data/.backup-work').stat().st_dev,'files':files,'readable_peer_processes':readable}),flush=True)
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--wait',action='store_true')
    args=parser.parse_args()
    root=args.root.resolve();marker,_=validate_fixture(root)
    deadline=time.monotonic()+5400
    while True:
        try: complete=json.loads((root/'current-updater-large-verification.json').read_text())
        except (OSError,json.JSONDecodeError): complete={}
        if complete.get('passed'): break
        require(args.wait and time.monotonic()<deadline,'finish the final automatic update first')
        time.sleep(5)
    require(complete.get('project')==marker['project'] and complete.get('final_data_profile',{}).get('source_bytes',0)>=4*1024**3,'only the synthetic large fixture is allowed')
    archive=root/'current-code-streamed-site-backup.zip'
    require(archive.resolve().parent==root and archive.stat().st_size>3*1024**3,'expected the verified large encrypted artifact')
    verified=json.loads((root/'current-code-large-verification.json').read_text())
    require(verified.get('passed') and verified.get('project')==marker['project'],'the source archive must already have passed full upload validation')
    digest=hashlib.sha256()
    with archive.open('rb') as source:
        while block:=source.read(1024*1024): digest.update(block)
    require(digest.hexdigest()==verified['encrypted_archive']['sha256'],'the previously validated archive changed')
    agent=FixtureAgent(root,'LearnGraph/UpdateFixture',marker['image'],['compose.json'])
    require(not agent.active() and agent.current()['version']=='v1.1.0','final version must be idle')
    samples=[];stop=threading.Event()
    target=agent.containers()['app']['Id']
    # Packaged releases validate archived Settings before the staging service's
    # explicit version check. Establish that the earlier rejection is precisely
    # the release-version guard, without relaxing any application validation.
    version_probe="from app.core.config import Settings,get_settings; from pydantic import ValidationError; import json\nr={'current':get_settings().app_version,'archived':'v1.0.0'}\ntry: Settings(_env_file=None,app_version='v1.0.0')\nexcept ValidationError as e: r['rejected_fields']=[list(x['loc']) for x in e.errors(include_input=False,include_context=False)]\nprint(json.dumps(r))"
    version_guard=json.loads(agent.command(['exec',target,'learngraph-entrypoint','python','-c',version_probe]))
    require(version_guard=={'current':'v1.1.0','archived':'v1.0.0','rejected_fields':[['app_version']]},'release-version guard was not established')
    require(json.loads(agent.command(['exec',target,'python','-c',PROBE]))['readable_peer_processes']>0,'observer cannot inspect the runtime process; refuse before uploading')
    print('immutable archive hash, packaged-version guard and runtime FD visibility verified before uploading',flush=True)
    def observe():
        while not stop.wait(1):
            try: samples.append(json.loads(agent.command(['exec',target,'python','-c',PROBE])))
            except Exception as exc: samples.append({'error':type(exc).__name__})
    with httpx.Client(base_url=agent.api_url(),trust_env=False,timeout=3600) as client:
        response=client.post('/auth/login',json={'username':'admin','password':PASSWORD});response.raise_for_status();user=response.json()
        client.headers.update({'Authorization':'Bearer '+user['access_token'],'X-Workspace-ID':user['default_workspace_id']})
        monitor=threading.Thread(target=observe,daemon=True);monitor.start()
        try:
            with archive.open('rb') as source:
                response=client.post('/migrations/restore',data={'confirm':'true','current_password':PASSWORD,'backup_password':BACKUP_PASSWORD},files={'backup':('previous-version.zip',source,'application/zip')})
        finally: stop.set();monitor.join(40)
        # The archive was created at v1.0.0; the final application is v1.1.0.
        # This rejection occurs AFTER complete upload/decryption/validation and
        # proves version safety without applying another full database restore.
        try: error=response.json().get('error',{})
        except ValueError: error={}
        result={'status':response.status_code,'code':error.get('code'),'message':str(error.get('message',''))[:500],'version_guard':version_guard,'samples':samples}
        (root/'current-upload-last-attempt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        require((response.status_code,error.get('code')) in {(409,'site_restore_version_mismatch'),(422,'site_backup_manifest_invalid')},'old-version large backup was not safely rejected: '+str(result['status'])+' '+str(result['code']))
        seen=[entry for sample in samples for entry in sample.get('files',[]) if entry['path'].startswith('/data/.backup-work/lg-site-')]
        require(seen and all(entry['device']==sample['data_device'] for sample in samples for entry in sample.get('files',[]) if entry['path'].startswith('/data/.backup-work/lg-site-')),'large upload was not observed on the data volume')
        require(not any(entry['path'].startswith('/tmp/') for sample in samples for entry in sample.get('files',[])),'large upload used container /tmp')
        clean=json.loads(agent.command(['exec',target,'python','-c',"import json; from pathlib import Path; print(json.dumps([str(p) for p in Path('/data/.backup-work').glob('lg-site-*')]))"]))
        require(not clean,'rejected upload leaked a private plaintext workspace')
    report={'passed':True,'project':marker['project'],'uploaded_bytes':archive.stat().st_size,'status':response.status_code,'code':error['code'],'version_guard':version_guard,'previously_validated_archive_sha256':digest.hexdigest(),'data_volume_observed':True,'no_large_tmp_files':True,'workspace_cleanup':True,'samples':samples}
    (root/'current-upload-large-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('>3 GB upload used the private data volume; old-version restore was rejected and all workspaces were removed',flush=True)


if __name__=='__main__':main()

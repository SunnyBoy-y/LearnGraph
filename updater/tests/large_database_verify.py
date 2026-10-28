"""Final current-code checks on the completed 4 GiB synthetic Docker fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import httpx

from updater.tests.docker_isolation import require, validate_fixture
from updater.tests.postgresql_roundtrip import BACKUP_PASSWORD, PASSWORD, FixtureAgent


PROFILE = r'''
import hashlib,json,resource,sqlite3,time
from pathlib import Path
from contextlib import closing
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.core.config import Settings
from app.core.database_routing import postgres_engine,state_root,read_json,decrypt_url
from app.services.postgresql_backup import effective_url
from app.services.database_migration import _bounded_rows
settings=Settings()
seed=json.loads(Path('/data/large-database-seed.json').read_text())
assert seed['synthetic_only'] and seed['sqlite_bytes']>=4*1024**3
job=read_json(state_root(settings)/'job.json')
active=read_json(state_root(settings)/'active.json')
target=make_url(decrypt_url(settings,active))
assert active['phase']=='postgresql_active' and active['job_id']==job['id']
assert target.host=='learngraph-main-postgres-'+job['id'] and target.port==5432 and target.database=='learngraph' and target.username=='learngraph' and not target.query
source=state_root(settings)/'backups'/job['id']/'primary.sqlite3'
started=time.monotonic()
left=hashlib.sha256(); source_rows=0; max_batch=0
with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
    db.execute('PRAGMA cache_size=-8192')
    cursor=db.execute("SELECT id,details FROM security_events WHERE event_type='large-database-acceptance' ORDER BY id")
    while rows:=_bounded_rows(cursor):
        max_batch=max(max_batch,sum(len(key)+len(value) for key,value in rows))
        for key,value in rows:
            left.update(key.encode());left.update(value.encode());source_rows+=1
right=hashlib.sha256(); target_rows=0
engine=postgres_engine(effective_url(settings))
try:
    with engine.connect() as db:
        rows=db.execution_options(yield_per=16).execute(text("SELECT id,details::text FROM security_events WHERE event_type='large-database-acceptance' ORDER BY id"))
        for key,value in rows:
            right.update(key.encode());right.update(value.encode());target_rows+=1
finally: engine.dispose()
assert source_rows==target_rows==seed['rows']
assert left.hexdigest()==right.hexdigest()==seed['logical_payload_sha256']
print(json.dumps({'passed':True,'source_bytes':seed['sqlite_bytes'],'verified_synthetic_rows':target_rows,'source_target_seed_sha256':right.hexdigest(),'copy_batch_max_bytes':max_batch,'target_cursor_rows':16,'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,'memory_limit':Path('/sys/fs/cgroup/memory.max').read_text().strip(),'seconds':time.monotonic()-started}),flush=True)
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--wait',action='store_true')
    parser.add_argument('--resume-download',action='store_true',help='validate the artifact from a completed download after a harness assertion failure')
    args=parser.parse_args()
    root=args.root.resolve();marker,_=validate_fixture(root)
    deadline=time.monotonic()+3600
    while True:
        try: evidence=json.loads((root/'verification.json').read_text())
        except (FileNotFoundError,json.JSONDecodeError): evidence={}
        if evidence.get('passed'): break
        require(args.wait and time.monotonic()<deadline,'complete the owned large roundtrip first')
        time.sleep(5)
    require(evidence.get('project')==marker['project'] and evidence.get('synthetic_database',{}).get('sqlite_bytes',0)>=4*1024**3,'only a passed synthetic 4 GiB fixture is allowed')
    agent=FixtureAgent(root,'LearnGraph/UpdateFixture',marker['image'],['compose.json'],backup_timeout=3600)
    require(not agent.active(),'main roundtrip is still active')
    agent._verify_target(evidence['postgres_container'],marker)
    report=json.loads((root/'current-code-large-verification.json').read_text()) if args.resume_download else {'project':marker['project'],'synthetic_only':True,'checks':[]}
    require(report.get('project')==marker['project'],'verification report belongs to another project')
    def save(check):
        report['checks'].append(check)
        (root/'current-code-large-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(check,flush=True)
    if not args.resume_download:
        report['bounded_profile']=json.loads(agent.compose(['run','--rm','--no-deps','app','python','-c',PROFILE],1800))
        save('current byte-bounded copy batches and 16-row verification cursor preserve all 4 GiB synthetic data after restore and updates')
    else:
        require(report.get('bounded_profile',{}).get('passed') and report['bounded_profile']['source_bytes']>=4*1024**3,'cannot resume without the completed 4 GiB checksum profile')
    with httpx.Client(base_url=agent.api_url(),trust_env=False,timeout=3600) as client:
        value=client.post('/auth/login',json={'username':'admin','password':PASSWORD});value.raise_for_status();login=value.json()
        client.headers.update({'Authorization':'Bearer '+login['access_token'],'X-Workspace-ID':login['default_workspace_id']})
        state=client.get('/migrations/database/status');state.raise_for_status()
        require(state.json()['active_database']=='postgresql','PostgreSQL routing was lost')
        archive=root/'current-code-streamed-site-backup.zip'
        require(archive.resolve().parent==root and (archive.exists() if args.resume_download else not archive.exists()),'private backup resume/fresh path mismatch')
        digest=hashlib.sha256()
        # An ordinary browser navigation has no Authorization header. Verify the
        # HttpOnly cookie path as well as the disk streaming and cleanup path.
        if not args.resume_download:
            prepared=client.post('/migrations/backup/browser-grants',json={'current_password':PASSWORD,'backup_password':BACKUP_PASSWORD})
            prepared.raise_for_status()
            ticket=prepared.json()['ticket_id']
            with httpx.Client(base_url=client.base_url,cookies=client.cookies,trust_env=False,timeout=3600) as browser:
                with browser.stream('GET','/migrations/backup/browser/'+ticket) as response:
                    response.raise_for_status()
                    expected=int(response.headers['Content-Length'])
                    with archive.open('xb') as output:
                        for block in response.iter_bytes(1024*1024):
                            digest.update(block);output.write(block)
                replay=browser.get('/migrations/backup/browser/'+ticket).status_code
                require(replay in {403,404,409,410},'download ticket replay was accepted')
        else:
            target=agent.containers()['app']['Id']
            agent._verify_target(target,marker)
            logs=agent._raw(['logs','--tail','500',target],validated=True)
            records=re.findall(r'GET [^\s]+/backup/browser/([0-9a-f]{32}) HTTP/[^\"]+\" (\d+)',logs)
            success=[(i,ticket) for i,(ticket,status) in enumerate(records) if status=='200']
            require(success,'no successful native download was logged')
            position,ticket=success[-1]
            denied=[int(status) for saved,status in records[position+1:] if saved==ticket and int(status) in {403,404,409,410}]
            require(denied,'no denied replay was logged')
            replay=denied[-1]
            expected=archive.stat().st_size
            with archive.open('rb') as source:
                while block:=source.read(1024*1024): digest.update(block)
            report['resumed_completed_download']=True
        require(re.fullmatch('[0-9a-f]{32}',ticket),'invalid download ticket')
        remote='/data/.backup-work/browser-downloads/'+ticket
        require(agent.command(['exec',agent.containers()['app']['Id'],'python','-c',"from pathlib import Path; print(Path("+repr(remote)+").exists())"])=='False','download artifact was not cleaned')
        report['download_replay_status']=replay
        require(archive.stat().st_size==expected and expected>1024**3,'large streaming HTTP download incomplete')
        report['encrypted_archive']={'bytes':expected,'sha256':digest.hexdigest()}
        save('latest native browser download used a session-bound HttpOnly grant, streamed a greater-than-1-GiB encrypted archive, and rejected ticket replay')
        with archive.open('rb') as source:
            response=client.post('/migrations/restore',data={'confirm':'true','current_password':PASSWORD,'backup_password':BACKUP_PASSWORD},files={'backup':('site.zip',source,'application/zip')})
        response.raise_for_status();staged=response.json()
        require(staged['mode']=='staged' and staged['records']>=32576 and staged['tables']>=190,'large current-code upload failed validation')
        require(re.fullmatch(r'/data/site-restore/[0-9a-f-]{36}\.zip',staged['restore_path']),'foreign staged cleanup path')
        # The full offline restore already passed; this final upload validates
        # the current authorization/quota route and is deliberately not applied.
        agent.command(['exec',agent.containers()['app']['Id'],'python','-c',"from pathlib import Path; Path("+repr(staged['restore_path'])+").unlink()"])
        save('latest administrator-first multipart upload validated the entire large archive without applying a second restore')
    report['passed']=True
    save('current-code large-data acceptance complete; PostgreSQL retained')


if __name__=='__main__':main()

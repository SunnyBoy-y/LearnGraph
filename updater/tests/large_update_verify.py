"""Verify current updater staging paths with the real 4 GiB PostgreSQL fixture."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import httpx

from updater.agent import TERMINAL,handler
from updater.tests.docker_isolation import require,validate_fixture
from updater.tests.large_database_verify import PROFILE
from updater.tests.postgresql_roundtrip import FixtureAgent,PASSWORD,wait_until


class DataAgent(FixtureAgent):
    def command(self,args,timeout=60):
        if 'app.services.database_maintenance' in args and 'backup' in args:
            require('--output' in args,'native updater output missing')
            self.destination=args[args.index('--output')+1]
            require(self.destination.startswith('/data/.backup-work/learngraph-update-') and self.destination.endswith('.zip'),'native updater used an ephemeral backup destination')
        return super().command(args,timeout)

    def maintenance(self,container,operation,*args):
        result=super().maintenance(container,operation,*args)
        if operation=='backup':
            self.workspace=result['snapshot_workspace']
            require(self.workspace=='/data/database-migration/maintenance-work','native snapshot was not staged on the private data volume')
        return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--wait',action='store_true')
    args=parser.parse_args()
    root=args.root.resolve();marker,config=validate_fixture(root)
    deadline=time.monotonic()+5400
    while True:
        try: completed=json.loads((root/'current-code-large-verification.json').read_text())
        except (FileNotFoundError,json.JSONDecodeError): completed={}
        if completed.get('passed'): break
        require(args.wait and time.monotonic()<deadline,'complete the current-code 4 GiB backup verification first')
        time.sleep(5)
    require(completed.get('project')==marker['project'] and completed.get('synthetic_only') is True and completed.get('bounded_profile',{}).get('source_bytes',0)>=4*1024**3,'only the completed synthetic large fixture is allowed')
    require(config['services']['app']['environment'].get('LEARNGRAPH_UPDATE_AGENT_URL')=='http://host.docker.internal:18751','this final updater owns only its fixed test endpoint')
    agent=DataAgent(root,'LearnGraph/UpdateFixture',marker['image'],['compose.json'],timeout=100,backup_timeout=3600)
    require(not agent.active() and agent.current()['version']=='v1.0.0','owned baseline must be idle before final automatic update')
    server=ThreadingHTTPServer(('0.0.0.0',18751),handler(agent));server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    stop=threading.Event()
    try:
        with httpx.Client(base_url=agent.api_url(),trust_env=False,timeout=120) as client:
            response=client.post('/auth/login',json={'username':'admin','password':PASSWORD});response.raise_for_status();user=response.json()
            client.headers.update({'Authorization':'Bearer '+user['access_token'],'X-Workspace-ID':user['default_workspace_id']})
            # The preceding recovery fault-injection deliberately blacklisted
            # this fixture release. This independent retry owns the same local
            # image and must reset only its test failure, preserving all other
            # failed versions and the production retry policy itself.
            require('v1.1.0' in {job.get('target_version') for job in agent.state['jobs']},'expected the completed local release matrix')
            agent.state['failed_versions']=[value for value in agent.state['failed_versions'] if value!='v1.1.0']
            agent.state['releases']=[{'version':'v1.1.0'}];agent.save()
            before={job['id'] for job in agent.state['jobs']}
            client.post('/updates/policy',json={'auto_install':True}).raise_for_status()
            threading.Thread(target=agent.poll,args=(stop,1),daemon=True).start()
            def finished():
                return next((job for job in agent.state['jobs'] if job['id'] not in before and job['status'] in TERMINAL and not agent.active()),None)
            job=wait_until(finished,timeout=3600)
            stop.set()
            client.post('/updates/policy',json={'auto_install':False}).raise_for_status()
            require(job['status']=='succeeded' and job['backup']['engine']=='postgresql' and job['backup']['bytes']>1024**3,'large native automatic update failed')
            cleanup=json.loads(agent.command(['exec',agent.containers()['app']['Id'],'python','-c',"from pathlib import Path; import json; print(json.dumps({'reserved':Path('/data/database-migration/update.json').exists(),'native_archives':[str(p) for r in [Path('/data/.backup-work'),Path('/tmp')] for p in r.glob('learngraph-update-*.zip')],'snapshot_children':list(str(p) for p in Path('/data/database-migration/maintenance-work').iterdir())}))"]))
            require(not cleanup['reserved'] and not cleanup['native_archives'] and not cleanup['snapshot_children'],'large backup left a reservation or scratch file')
            require(client.get('/migrations/database/status').json()['active_database']=='postgresql','automatic update lost PostgreSQL routing')
        report={'project':marker['project'],'automatic_policy_triggered':True,'version':agent.current()['version'],'native_archive_bytes':job['backup']['bytes'],'destination':agent.destination,'snapshot_workspace':agent.workspace,'cleanup':cleanup}
        print('current updater automatically backed up >1 GiB on the data volume and upgraded all three services',flush=True)
        report['final_data_profile']=json.loads(agent.compose(['run','--rm','--no-deps','app','python','-c',PROFILE],1800))
        report['passed']=True
        (root/'current-updater-large-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('full 4 GiB source/target/seed hashes match after the final current-code automatic update',flush=True)
    finally:
        stop.set();server.shutdown();server.server_close()


if __name__=='__main__':main()

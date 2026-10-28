"""Read memory only from ownership-checked app and one-off recovery workers."""
import argparse
import json
from pathlib import Path
import shutil
import time

from updater.tests.docker_isolation import require, validate_fixture
from updater.tests.large_database_fixture import MEMORY
from updater.tests.postgresql_roundtrip import FixtureAgent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    marker,_=validate_fixture(root)
    agent=FixtureAgent(root,'LearnGraph/UpdateFixture',marker['image'],['compose.json'])
    output=root/'memory-all-app-workers.jsonl'
    require(output.resolve().parent==root,'monitor output escapes fixture')
    deadline=time.monotonic()+7200
    with output.open('a',encoding='utf-8') as handle:
        while time.monotonic()<deadline and not (root/'monitor-stop').exists():
            try:
                agent.verify_isolation()
                ids=agent._raw(['ps','-q','--filter','label=com.docker.compose.project='+marker['project'],'--filter','label=com.docker.compose.service=app']).splitlines()
                for target in ids:
                    owned=agent._verify_target(target,marker)
                    sample=json.loads(agent.command(['exec',target,'python','-c',MEMORY]))
                    sample.update(container=owned['Name'],oneoff=owned['Config']['Labels'].get('com.docker.compose.oneoff'),host_free_bytes=shutil.disk_usage(root).free)
                    handle.write(json.dumps(sample)+'\n');handle.flush()
            except Exception as exc:
                handle.write(json.dumps({'time':time.time(),'monitor_error':type(exc).__name__})+'\n');handle.flush()
            time.sleep(5)


if __name__=='__main__':main()

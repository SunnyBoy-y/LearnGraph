import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from sandboxd.runtime.supervisor import SUPERVISOR

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Container supervisor requires Linux process groups')


@pytest.mark.parametrize('mode',['timeout','cancel'])
def test_supervisor_stops_child_and_grandchild_without_stopping_sibling(tmp_path,mode):
    execution='ex_'+uuid.uuid4().hex
    marker=tmp_path/'late.txt'
    child=tmp_path/'child.py'
    child.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',"
        +repr("import time;from pathlib import Path;time.sleep(2);Path("+repr(str(marker))+ ").write_text('late')")+"],start_new_session=True)\ntime.sleep(10)\n")
    spec={'id':execution,'argv':[sys.executable,str(child)],'cwd':str(tmp_path),
          'timeout':0.5 if mode=='timeout' else 10,'limit':4096}
    root=Path('/tmp/learngraph-exec-'+execution)
    cancel=Path('/tmp/learngraph-cancel-'+execution)
    sibling=subprocess.Popen([sys.executable,'-c','import time;time.sleep(4)'])
    worker=subprocess.Popen([sys.executable,'-c',SUPERVISOR,json.dumps(spec)])
    try:
        deadline=time.monotonic()+3
        while not (root/'result.json').exists() and time.monotonic()<deadline:time.sleep(.02)
        if mode=='cancel':cancel.touch()
        worker.wait(timeout=3)
        value=json.loads((root/'result.json').read_text())
        assert value['status']==('timeout' if mode=='timeout' else 'cancelled')
        assert value['latency_ms']<1800
        assert sibling.poll() is None
        time.sleep(2)
        assert not marker.exists()
    finally:
        worker.kill() if worker.poll() is None else None
        sibling.terminate();sibling.wait(timeout=3)
        import shutil
        shutil.rmtree(root,ignore_errors=True);cancel.unlink(missing_ok=True)


def test_duplicate_dispatch_starts_once(tmp_path):
    execution='ex_'+uuid.uuid4().hex
    spec={'id':execution,'argv':[sys.executable,'-c',
          "from pathlib import Path;import time;p=Path('starts');p.open('a').write('1');time.sleep(.4)"],
          'cwd':str(tmp_path),'timeout':2,'limit':4096}
    workers=[subprocess.Popen([sys.executable,'-c',SUPERVISOR,json.dumps(spec)]) for _ in range(4)]
    for worker in workers:worker.wait(timeout=4)
    assert (tmp_path/'starts').read_text()=='1'
    import shutil
    shutil.rmtree('/tmp/learngraph-exec-'+execution)


def test_normal_completion_has_no_termination_grace_sleep(tmp_path):
    execution='ex_'+uuid.uuid4().hex
    spec={'id':execution,'argv':[sys.executable,'-c',"print('ok')"],
          'cwd':str(tmp_path),'timeout':2,'limit':4096}
    wrapper="import time; original=time.sleep; time.sleep=lambda seconds: (_ for _ in ()).throw(AssertionError('unnecessary termination grace')) if seconds==0.1 else original(seconds); exec("+repr(SUPERVISOR)+")"
    result=subprocess.run([sys.executable,'-c',wrapper,json.dumps(spec)],timeout=4)
    root=Path('/tmp/learngraph-exec-'+execution)
    value=json.loads((root/'result.json').read_text())
    try:
        assert result.returncode==0 and value['status']=='succeeded' and value['stdout']=='ok\n'
    finally:
        import shutil
        shutil.rmtree(root)


def test_late_detached_replay_cannot_execute_after_result_gc(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from sandboxd.runtime.docker import DockerRuntimeBackend
    from sandboxd.runtime.port import RuntimeHandle
    execution='ex_'+uuid.uuid4().hex
    spec={'id':execution,'argv':[sys.executable,'-c',"from pathlib import Path;Path('starts').open('a').write('1')"],
          'cwd':str(tmp_path),'timeout':2,'limit':4096}
    subprocess.run([sys.executable,'-c',SUPERVISOR,json.dumps(spec)],check=True,timeout=4)
    runtime=DockerRuntimeBackend(deployment_id='gc-test')
    client=SimpleNamespace(close=lambda:None)
    def execute(argv,**kwargs):return SimpleNamespace(exit_code=subprocess.run(argv,check=False).returncode)
    monkeypatch.setattr(runtime,'_client',lambda:client)
    monkeypatch.setattr(runtime,'_container',lambda *a:SimpleNamespace(exec_run=execute))
    runtime.forget_exec(RuntimeHandle('test','test'),execution)
    workers=[subprocess.Popen([sys.executable,'-c',SUPERVISOR,json.dumps(spec)]) for _ in range(4)]
    for worker in workers:assert worker.wait(timeout=4)==0
    assert (tmp_path/'starts').read_text()=='1'
    assert not Path('/tmp/learngraph-exec-'+execution).exists()
    import sqlite3
    with sqlite3.connect('/tmp/learngraph-completed.sqlite3') as db:
        db.execute('DELETE FROM completed WHERE execution_id=?',(execution,))

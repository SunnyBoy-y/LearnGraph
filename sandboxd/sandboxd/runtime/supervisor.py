"""Execution supervisor source shipped to the container through docker exec.

The watchdog belongs to the container, so API/daemon restarts cannot remove a
deadline. PID/PGID values are allocated and used in the same PID namespace.
"""
SUPERVISOR = r'''
import ctypes, json, os, selectors, signal, sqlite3, subprocess, sys, time
from pathlib import Path
spec = json.loads(sys.argv[1])
root = Path('/tmp/learngraph-exec-' + spec['id'])
cancel = Path('/tmp/learngraph-cancel-' + spec['id'])
try:
    root.mkdir(mode=0o700)
except FileExistsError:
    sys.exit(0)  # Concurrent/replayed dispatch owns exactly one launch.
# Result files may already have been garbage-collected while a previously
# accepted detached launch is still reaching Python. Keep compact completion
# tombstones independently of those files so that late launch cannot run again.
completed = Path('/tmp/learngraph-completed.sqlite3')
if completed.exists():
    with sqlite3.connect(str(completed), timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS completed (execution_id TEXT PRIMARY KEY)')
        already_finished = db.execute('SELECT 1 FROM completed WHERE execution_id=?', (spec['id'],)).fetchone()
    db.close()
    if already_finished:
        try: root.rmdir()
        except FileNotFoundError: pass
        sys.exit(0)
def write(value):
    temp = root / 'result.tmp'
    temp.write_text(json.dumps(value))
    temp.replace(root / 'result.json')
started = time.monotonic()
stdout = bytearray(); stderr = bytearray()
state = 'running'; timed_out = truncated = False
child = None
def descendants():
    parents = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal(): continue
        try:
            values = (entry/'stat').read_text().rsplit(')',1)[1].split()
            parents[int(entry.name)] = int(values[1])
        except (OSError, ValueError, IndexError): pass
    found = {os.getpid()}
    while True:
        expanded = found | {pid for pid,parent in parents.items() if parent in found}
        if expanded == found: return found - {os.getpid()}
        found = expanded
def terminate_tree():
    # A completed foreground command with no descendants needs no TERM grace
    # period. Background / escaped descendants must still be terminated.
    if child is None or (child.poll() is not None and not descendants()):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        # Individual descendants include children that called setsid themselves.
        for pid in descendants():
            try: os.kill(pid,sig)
            except ProcessLookupError: pass
        if child:
            try: os.killpg(child.pid,sig)
            except ProcessLookupError: pass
        if sig == signal.SIGTERM: time.sleep(0.1)
try:
    # Adopt double-forked children instead of losing them to PID 1.
    if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0) != 0:
        raise OSError('Cannot establish execution subreaper')
    write({'status':'launching'})
    if cancel.exists():
        state = 'cancelled'
    else:
        child = subprocess.Popen(spec['argv'], cwd=spec['cwd'], start_new_session=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        write({'status':'running', 'pid':child.pid})
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ, stdout)
        selector.register(child.stderr, selectors.EVENT_READ, stderr)
        while True:
            if cancel.exists(): state = 'cancelled'
            elif time.monotonic() - started >= spec['timeout']: state = 'timeout'; timed_out = True
            if state != 'running': break
            for key, _ in selector.select(timeout=0.03):
                block = os.read(key.fd, 65536)
                if not block: selector.unregister(key.fileobj)
                else: key.data.extend(block)
            if len(stdout) + len(stderr) > spec['limit']:
                truncated = True; state = 'failed'; break
            if child.poll() is not None:
                for key,_ in selector.select(timeout=0):
                    block=os.read(key.fd,65536)
                    if block: key.data.extend(block)
                state = 'succeeded' if child.returncode == 0 else 'failed'; break
        # Always remove descendants, including detached background commands in
        # this process group after their shell parent has exited.
        terminate_tree()
        child.wait(timeout=2)
        reap_deadline=time.monotonic()+2
        while True:
            while True:
                try:
                    if os.waitpid(-1,os.WNOHANG)[0] == 0: break
                except ChildProcessError: break
            remaining=descendants()
            if not remaining: break
            for pid in remaining:
                try: os.kill(pid,signal.SIGKILL)
                except ProcessLookupError: pass
            if time.monotonic()>=reap_deadline:
                raise TimeoutError('Execution descendants did not exit')
            time.sleep(0.02)
        selector.close()
    write({'status':state, 'exit_code':child.returncode if child else -1,
           'stdout':bytes(stdout[:spec['limit']]).decode('utf-8','replace'),
           'stderr':bytes(stderr[:spec['limit']]).decode('utf-8','replace'),
           'timed_out':timed_out, 'truncated':truncated,
           'latency_ms':int((time.monotonic()-started)*1000)})
except BaseException as exc:
    if child:
        try: terminate_tree(); child.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired): pass
    write({'status':'failed', 'exit_code':-1, 'stdout':'', 'stderr':'supervisor: '+type(exc).__name__,
           'timed_out':False,'truncated':False,'latency_ms':int((time.monotonic()-started)*1000)})
'''

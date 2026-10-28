"""Synthetic data only; executed inside an already ownership-checked fixture."""

SEED = r'''
import hashlib, json, os, sqlite3, time
from pathlib import Path
from contextlib import closing
target = 4 * 1024**3
source = Path('/data/learngraph.db')
if os.environ.get('LEARNGRAPH_ENV') != 'test' or os.environ.get('LEARNGRAPH_DATABASE_URL') != 'sqlite:////data/learngraph.db':
    raise RuntimeError('Synthetic generator requires the isolated SQLite fixture')
from app.services.backup_io import require_space
require_space(source.parent, 60 * 1024**3, reserve=10 * 1024**3)
started, digest, rows = time.monotonic(), hashlib.sha256(), 0
with closing(sqlite3.connect(source)) as db:
    if db.execute("SELECT count(*) FROM security_events WHERE event_type='large-database-acceptance'").fetchone()[0]:
        raise RuntimeError('Never adopt or overwrite an existing synthetic database')
    page_size = db.execute('PRAGMA page_size').fetchone()[0]
    db.execute('PRAGMA cache_size=-8192')
    while db.execute('PRAGMA page_count').fetchone()[0] * page_size < target:
        for _ in range(32):
            key = 'synthetic-' + str(rows).zfill(8)
            # Unique random text, not sparse files or repeated zero padding.
            payload = json.dumps({'synthetic': True, 'payload': os.urandom(65536).hex()}, separators=(',',':'))
            db.execute("INSERT INTO security_events(id,event_type,outcome,ip_address,user_agent,details,created_at) VALUES(?,'large-database-acceptance','success','','',?,'2026-10-18 00:00:00.000000')", (key,payload))
            digest.update(key.encode()); digest.update(payload.encode()); rows += 1
        db.commit()
        if rows % 2048 == 0:
            db.execute('PRAGMA wal_checkpoint(PASSIVE)')
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    result = {'synthetic_only':True,'rows':rows,'logical_payload_sha256':digest.hexdigest(),'sqlite_bytes':source.stat().st_size,'generation_seconds':round(time.monotonic()-started,2), 'integrity':db.execute('PRAGMA integrity_check').fetchone()[0], 'foreign_key_violations':len(db.execute('PRAGMA foreign_key_check').fetchall())}
Path('/data/large-database-seed.json').write_text(json.dumps(result))
print(json.dumps(result),flush=True)
'''

MEMORY = r'''
import json, os, time
from pathlib import Path
def status(path):
    values = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(':')
        if key in {'VmRSS','VmHWM'}:
            values[key] = int(value.strip().split()[0])*1024
    return values
processes = []
for path in Path('/proc').glob('[0-9]*/status'):
    try:
        values = status(path)
        if values: processes.append(values)
    except (OSError, ValueError): pass
root = Path('/sys/fs/cgroup')
result = {'time':time.time(),'rss_sum':sum(p.get('VmRSS',0) for p in processes),'process_hwm':max((p.get('VmHWM',0) for p in processes),default=0)}
for name in ['memory.current','memory.peak','memory.max','memory.events']:
    path = root/name
    if path.exists(): result[name] = path.read_text().strip()
import shutil
result['free_bytes'] = shutil.disk_usage('/data').free
print(json.dumps(result),flush=True)
'''

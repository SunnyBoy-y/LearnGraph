from pathlib import Path
import ast
from normalize_patch_literals import normalize
ROOT = Path(__file__).resolve().parents[1]
changes={}
def load(name):
    p=ROOT/name; raw=p.read_bytes(); changes[name]=(p,raw,'')
    return raw.decode().replace(chr(13)+chr(10),chr(10))
def rep(s,old,new):
    assert s.count(old)==1,(old[:100],s.count(old))
    return s.replace(old,new,1)
def put(name,s):
    p,raw,_=changes[name]; s=normalize(s); ast.parse(s); changes[name]=(p,raw,s)

s=load('app/services/memory_enhancement.py')
a=s.index('        except AppError as exc:',s.index('    with memory_service.atomic_write():'))
b=s.index('        drafts_created += 1',a)
s=s[:a]+'''        except AppError:
            raise  # The enclosing UoW rolls back every proposal and cursor.
'''+s[b:] if False else s
# Preserve usage before checking a source or lease that can have disappeared.
s=rep(s, '    from app.services.session_memory_deletion import lock_session_memory_sources\n\n    try:\n        lock_session_memory_sources(db, workspace.id, [session_id])', '''    db.commit()  # Actual provider usage survives source deletion / lost ownership.
    from app.services.session_memory_deletion import lock_session_memory_sources

    try:
        lock_session_memory_sources(db, workspace.id, [session_id])''')
s=s.replace('        attempt=1,','        attempt=current_lease().attempt if current_lease() else 1,')
put('app/services/memory_enhancement.py',s)

s=load('app/services/memory_outbox.py')
s=rep(s,'from dataclasses import dataclass','from dataclasses import dataclass\nfrom contextlib import contextmanager\nimport threading')
s=rep(s,'class OutboxHandler(Protocol):','class PermanentProjectionError(RuntimeError):\n    """Configuration failure that must not consume eight retry cycles."""\n\n\nclass OutboxHandler(Protocol):')
s=rep(s,'strict_leases: bool = False,','strict_leases: bool = True,')
s=rep(s,'        self.strict_leases = strict_leases','        self.strict_leases = True  # Retain parameter compatibility, not unsafe claims.')
s=rep(s,'''        claimed = self._claim(candidates, now)
        self.db.commit()

        succeeded = failed = dead_letter = ownership_lost = 0
        for claim in claimed:''','''        claimed = []
        self.db.commit()

        succeeded = failed = dead_letter = ownership_lost = 0
        for candidate_id in candidates:
            fresh_claims = self._claim([candidate_id], utc_now())
            self.db.commit()
            if not fresh_claims:
                continue
            claim = fresh_claims[0]
            claimed.append(claim)''')
s=rep(s,'                handler(item)','                with self._heartbeat(claim):\n                    handler(item)')
s=rep(s,'                outcome = self._mark_failed(claim, str(exc))','                self.db.rollback()\n                outcome = self._mark_failed(claim, str(exc), permanent=isinstance(exc, PermanentProjectionError))')
s=rep(s,'    def _claim(self, candidate_ids:', '''    @contextmanager
    def _heartbeat(self, claim):
        stop = threading.Event()
        bind = self.db.get_bind()
        def renew():
            while not stop.wait(max(0.2, self.lease_seconds / 3)):
                try:
                    with Session(bind) as heartbeat_db:
                        result = heartbeat_db.execute(update(MemoryProjectionOutbox).where(
                            self._claim_condition(claim),
                            MemoryProjectionOutbox.lease_until > utc_now(),
                        ).values(lease_until=utc_now()+timedelta(seconds=self.lease_seconds)))
                        heartbeat_db.commit()
                        if result.rowcount != 1:
                            return
                except Exception:
                    continue
        thread = threading.Thread(target=renew, daemon=True, name="memory-outbox-lease")
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2)

    def _claim(self, candidate_ids:''')
s=rep(s,'    def _mark_failed(self, claim: ClaimedOutboxItem, error: str) -> str | None:', '    def _mark_failed(self, claim: ClaimedOutboxItem, error: str, *, permanent: bool = False) -> str | None:')
s=rep(s,'        if attempts >= self.max_attempts:', '        if permanent or attempts >= self.max_attempts:')
put('app/services/memory_outbox.py',s)

s=load('app/services/memory_outbox_runtime.py')
s=rep(s,'import logging','import logging\nfrom uuid import uuid4')
s=rep(s,'from app.services.memory_outbox import MemoryOutboxWorker','from app.services.memory_outbox import MemoryOutboxWorker, PermanentProjectionError')
s=rep(s,'            raise RuntimeError(', '            raise PermanentProjectionError(')
s=rep(s,'worker_id="embedded-memory-outbox",','worker_id=f"embedded-memory-outbox:{uuid4()}",')
put('app/services/memory_outbox_runtime.py',s)

s=load('scripts/verify_memory_regressions.py')
s=rep(s,'class MemoryRegression(unittest.TestCase):\n    def setUp(self):', '''class MemoryRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_engine = create_engine('sqlite:////tmp/regression-schema.db')
        Base.metadata.create_all(cls.seed_engine)

    @classmethod
    def tearDownClass(cls):
        cls.seed_engine.dispose()

    def setUp(self):''')
s=rep(s,'        Base.metadata.create_all(self.engine)','''        source = self.seed_engine.raw_connection()
        target = self.engine.raw_connection()
        source.driver_connection.backup(target.driver_connection)
        source.close(); target.close()''')
put('scripts/verify_memory_regressions.py',s)

for p,raw,s in changes.values():
    assert p.read_bytes()==raw
for p,raw,s in changes.values():
    nl=chr(13)+chr(10) if bytes([13,10]) in raw else chr(10)
    p.write_bytes(s.replace(chr(10),nl).encode()); print('updated',p.relative_to(ROOT))

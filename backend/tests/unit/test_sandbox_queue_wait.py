from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base
from app.core.errors import AppError
from app.domain.models import ChatSession, SandboxInstance, SandboxSession, utc_now
from app.services.sandbox import SandboxAgentWorkspaceService
from app.services import sandbox_scheduler as scheduler


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'queue.db'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def configure_test_database(connection, _):
        connection.execute("PRAGMA synchronous=OFF")
    Base.metadata.create_all(engine)
    settings = get_settings().model_copy(update={
        "sandbox_workspace_root": str(tmp_path/'work'), "sandbox_instance_pooling_enabled": True,
        "sandbox_active_per_user": 1, "sandbox_default_max_instances_per_user": 1,
        "sandbox_host_minimum_free_disk_bytes": 0, "sandbox_queue_deadline_seconds": 3,
        "sandbox_wall_time_seconds": 2, "agent_tool_timeout_seconds": 1})
    backend = SimpleNamespace(host_capacity=lambda: (0, 0), observed_pressure=lambda: {})
    from app.providers.sandbox_registry import get_sandbox_backend_registry
    monkeypatch.setattr(get_sandbox_backend_registry(), "default", lambda *a, **k: backend)
    with Session(engine) as db:
        db.add(ChatSession(id="chat", workspace_id="ws", title="queue"))
        db.commit()
        yield db, settings, engine
    engine.dispose()


def pool(db, slots=2, active=1):
    instance = SandboxInstance(workspace_id="ws", owner_user_id="actor", runtime_profile="python-node",
        backend_resource_ref="real-pool", state="BUSY", active_executions=active, max_parallel_execs=slots)
    db.add(instance)
    db.flush()
    db.add(SandboxSession(id="logical", workspace_id="ws", owner_user_id="actor", chat_session_id="chat",
        backend_id="sandboxd", backend_session_ref="real-pool", lifecycle_state="RUNNING",
        manifest_hash="x"*64, expires_at=utc_now()+timedelta(hours=1)))
    db.commit()
    return instance


@pytest.mark.parametrize('status,terminal',[('succeeded','SUCCEEDED'),('timeout','FAILED'),('cancelled','CANCELLED')])
def test_recovered_completion_releases_one_slot_once_with_stale_worker(context,status,terminal):
    from app.domain.models import SandboxAgentCommand, SandboxJob
    db,settings,engine=context
    instance=pool(db,slots=2,active=2)
    job=SandboxJob(workspace_id='ws',owner_user_id='actor',chat_session_id='chat',status='RUNNING')
    db.add(job);db.flush()
    command=SandboxAgentCommand(workspace_id='ws',owner_user_id='actor',sandbox_session_id='logical',
        chat_session_id='chat',job_id=job.id,instance_id=instance.id,status='running',argv_digest='a'*64,
        resource_usage={'execution_id':'ex_'+'b'*32,'owner_session_id':instance.id,'backend_ref':'real-pool'})
    db.add(command);db.flush()
    logical=db.get(SandboxSession,'logical');logical.active_command_id=command.id
    logical.lease_token_hash='lease';logical.lease_expires_at=utc_now()+timedelta(minutes=5)
    db.commit()
    result={'status':status,'exit_code':0 if status=='succeeded' else -15,'stdout':'done','stderr':'',
        'timed_out':status=='timeout','truncated':False,'latency_ms':123}
    backend=SimpleNamespace(execution_result=lambda *args:result)
    def service(session):
        svc=object.__new__(SandboxAgentWorkspaceService);svc.db=session;svc.settings=settings
        svc._runtime_backend=lambda _:backend
        return svc
    with Session(engine,expire_on_commit=False) as stale_db:
        stale=stale_db.get(SandboxAgentCommand,command.id)
        stale_db.commit()
        service(db).reconcile_command(command)
        service(stale_db).reconcile_command(stale)
    db.refresh(instance);db.refresh(job);db.refresh(logical)
    assert instance.active_executions==1 and instance.state=='BUSY'
    assert job.status==terminal and job.finished_at
    assert logical.active_command_id is None and logical.lease_expires_at is None


def test_shared_pool_is_counted_once_and_free_slot_reused(context):
    db, settings, _ = context
    pool(db)
    assert scheduler.evaluate_capacity(db, settings, "actor", workspace_id="ws", runtime_kind="python-node")[0]
    assert not scheduler.evaluate_capacity(db, settings, "actor", workspace_id="other", runtime_kind="python-node")[0]


def test_old_pre_dispatch_command_does_not_keep_a_slot_or_lease(context):
    from app.domain.models import SandboxAgentCommand
    db,settings,_=context
    instance=pool(db,active=1)
    command=SandboxAgentCommand(workspace_id='ws',owner_user_id='actor',sandbox_session_id='logical',
        chat_session_id='chat',status='created',argv_digest='a'*64,updated_at=utc_now()-timedelta(hours=1))
    db.add(command);db.flush()
    logical=db.get(SandboxSession,'logical');logical.active_command_id=command.id
    logical.lease_token_hash='orphan';logical.lease_expires_at=utc_now()-timedelta(minutes=5)
    db.commit()
    scheduler.SandboxSchedulerService(db,settings).reconcile_commands()
    db.refresh(instance);db.refresh(command);db.refresh(logical)
    assert command.status=='failed' and command.error_class=='execution_not_started'
    assert instance.active_executions==0 and instance.state=='READY'
    assert logical.active_command_id is None and logical.lease_token_hash is None


def test_saturated_pool_and_different_profile_stay_bounded(context):
    db, settings, _ = context
    pool(db, slots=1)
    assert not scheduler.evaluate_capacity(db, settings, "actor", workspace_id="ws", runtime_kind="python-node")[0]
    assert not scheduler.evaluate_capacity(db, settings, "actor", workspace_id="ws", runtime_kind="python-node-browser")[0]


def test_atomic_slot_claim_cannot_overborrow_or_misrelease(context):
    db, settings, _ = context
    instance = pool(db, slots=1, active=0)
    service = object.__new__(SandboxAgentWorkspaceService)
    service.db, service.settings = db, settings
    logical = db.get(SandboxSession, "logical")
    service._borrow_instance(logical)
    with pytest.raises(AppError) as error:
        service._borrow_instance(logical)
    assert error.value.code == "sandbox_instance_saturated"
    db.refresh(instance)
    assert instance.active_executions == 1
    service._release_instance(instance.id)
    assert instance.active_executions == 0 and instance.state == "READY"


def test_wait_delivers_terminal_state_without_holding_write_lock(context, monkeypatch):
    db, settings, engine = context
    service = scheduler.SandboxSchedulerService(db, settings)
    job = service.submit_job(workspace_id="ws", owner_user_id="actor", chat_session_id="chat",
        kind="agent_command", payload={})
    monkeypatch.setattr(service, "schedule_once", lambda: None)
    def complete():
        time.sleep(.1)
        with Session(engine) as worker:
            row = worker.get(type(job), job.id)
            scheduler.SandboxSchedulerService(worker, settings)._finish(row, "SUCCEEDED", None)
            scheduler.notify_job_change()
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(complete)
        assert service.wait_job(job, 2).status == "SUCCEEDED"
        future.result()


def test_cancelled_queued_job_is_never_dispatched(context, monkeypatch):
    db, settings, _ = context
    service = scheduler.SandboxSchedulerService(db, settings)
    job = service.submit_job(workspace_id="ws", owner_user_id="actor", chat_session_id="chat",
        kind="agent_command", payload={})
    monkeypatch.setattr(service, "schedule_once", lambda: pytest.fail("cancelled job dispatched"))
    assert service.wait_job(job, 2, cancel_check=lambda: True).status == "CANCELLED"
    monkeypatch.setattr(service, "_execute_job", lambda *a: pytest.fail("cancelled job executed"))
    assert service._run_job(job) == "failed"


def test_queue_deadline_returns_expired_without_model_poll(context):
    db, settings, _ = context
    service = scheduler.SandboxSchedulerService(db, settings)
    job = service.submit_job(workspace_id="ws", owner_user_id="actor", chat_session_id="chat",
        kind="agent_command", payload={})
    job.deadline_at = utc_now()-timedelta(seconds=1)
    db.commit()
    assert service.wait_job(job, 2).status == "EXPIRED"


def test_foreground_tool_budget_includes_queue_but_explicit_background_stays_short(context):
    from app.services.chat import ChatService
    _, settings, _ = context
    chat = object.__new__(ChatService)
    chat.settings = settings
    assert chat._agent_tool_time_budget({"function":{"name":"sandbox_exec","arguments":'{}'}}) >= 20
    assert chat._agent_tool_time_budget({"function":{"name":"sandbox_exec","arguments":'{"yield_time_ms":0}'}}) == 1


def test_waiting_sandbox_does_not_block_unrelated_tool_executor():
    import threading
    from app.services.chat import _tool_executor_for_call
    release = threading.Event()
    pool = _tool_executor_for_call({"function":{"name":"sandbox_exec"}})
    held = pool.submit(lambda: release.wait(3))
    try:
        other = _tool_executor_for_call({"function":{"name":"search"}})
        assert other is not pool
        assert other.submit(lambda: "available").result(timeout=1) == "available"
    finally:
        release.set()
        held.result(timeout=2)


def test_startup_recovers_before_waiting_for_dead_process_advisory_lease(monkeypatch):
    import asyncio
    from app.core import scheduler as core_scheduler
    async def scenario():
        stop = asyncio.Event()
        called = []
        def recover():
            called.append("recovered")
            stop.set()
            return {}
        monkeypatch.setattr(scheduler, "run_scheduler_tick", recover)
        monkeypatch.setattr(core_scheduler, "_acquire_advisory_lock_in_worker",
            lambda *a, **kw: pytest.fail("Waited for a stale lock before startup recovery"))
        await core_scheduler.sandbox_execution_scheduler(stop, interval_seconds=1)
        assert called == ["recovered"]
    asyncio.run(scenario())


@pytest.mark.parametrize('backend_ref',[None,'real-pool'])
def test_error_instance_counter_is_repaired_without_resurrecting_runtime(context,backend_ref):
    db,settings,_=context
    instance=pool(db,active=2)
    instance.state='ERROR';instance.backend_resource_ref=backend_ref
    db.commit()
    scheduler.SandboxSchedulerService(db,settings).reconcile_commands()
    db.refresh(instance)
    assert instance.active_executions==0 and instance.state=='ERROR'


@pytest.mark.parametrize('high_pressure',[False,True])
def test_admission_uses_one_combined_host_snapshot_without_weakening_pressure_gate(context,monkeypatch,high_pressure):
    from app.providers.sandbox_registry import get_sandbox_backend_registry
    db,settings,_=context;calls=[]
    def sample():
        calls.append(1)
        return {'cpu_count':64,'memory_bytes':64*1024**3,
                'observed_memory_bytes':64*1024**3 if high_pressure else 0}
    def duplicate():pytest.fail('Repeated the combined capacity request')
    backend=SimpleNamespace(host_capacity_snapshot=sample,host_capacity=duplicate,observed_pressure=duplicate)
    monkeypatch.setattr(get_sandbox_backend_registry(),'default',lambda *a,**kw:backend)
    ok,reason,_=scheduler.evaluate_capacity(db,settings,'actor',workspace_id='ws',runtime_kind='python-node')
    assert calls==[1]
    assert ok is not high_pressure
    assert reason==('waiting_resource_pressure' if high_pressure else None)


def test_slot_release_wakes_only_this_users_capacity_waiters(context):
    from app.domain.models import SandboxJob
    db,settings,_=context
    instance=pool(db,slots=1,active=1)
    later=utc_now()+timedelta(seconds=30)
    rows=[]
    for owner,reason in [('actor','waiting_capacity'),('actor','backend_unavailable'),('other','waiting_capacity')]:
        row=SandboxJob(workspace_id='ws',owner_user_id=owner,chat_session_id='chat',status='QUEUED',reason=reason,available_at=later)
        db.add(row);rows.append(row)
    db.commit()
    service=object.__new__(SandboxAgentWorkspaceService);service.db=db;service.settings=settings
    service._release_instance(instance.id)
    for row in rows:db.refresh(row)
    assert rows[0].available_at.replace(tzinfo=None)<later.replace(tzinfo=None)-timedelta(seconds=20)
    assert all(r.available_at.replace(tzinfo=None)==later.replace(tzinfo=None) for r in rows[1:])

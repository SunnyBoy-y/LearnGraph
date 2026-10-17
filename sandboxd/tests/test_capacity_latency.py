from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time
import pytest
from sandboxd.controller import SandboxController
from sandboxd.runtime.docker import DockerRuntimeBackend

def test_capacity_refresh_is_single_flight_bounded_and_not_stale_on_failure(config,store,runtime,monkeypatch):
    clock=[10.0];calls=[]
    def capacity():
        calls.append(1);time.sleep(.02);return (8,10000)
    runtime.capacity=capacity
    runtime.observe=lambda:{'observed_memory_bytes':777,'active_containers':3}
    controller=SandboxController(config,store,runtime)
    monkeypatch.setattr('sandboxd.controller.time.monotonic',lambda:clock[0])
    with ThreadPoolExecutor(8) as pool: rows=list(pool.map(lambda _:controller.capacity(),range(16)))
    assert len(calls)==1
    assert all(r.observed_memory_bytes==777 and r.active_containers==3 for r in rows)
    clock[0]+=1.01;controller.capacity();assert len(calls)==2
    clock[0]+=1.01
    def broken():calls.append(1);raise RuntimeError('unavailable')
    runtime.capacity=broken
    for _ in range(2):
        with pytest.raises(RuntimeError):controller.capacity()
    assert len(calls)==4

def test_memory_pressure_does_not_wait_for_cpu_sampling_cycle(monkeypatch):
    calls=[]
    def stats(**kw):
        calls.append(kw);return {'memory_stats':{'usage':123}}
    client=SimpleNamespace(info=lambda:{'NCPU':8,'MemTotal':1000},
        containers=SimpleNamespace(list=lambda **kw:[SimpleNamespace(stats=stats)]),close=lambda:None)
    runtime=DockerRuntimeBackend(deployment_id='latency-test')
    monkeypatch.setattr(runtime,'_client',lambda:client)
    result=runtime.observe()
    assert calls==[{'stream':False,'one_shot':True}]
    assert result['observed_memory_bytes']==123 and result['active_containers']==1


def test_observation_totals_do_not_trigger_second_engine_info_lookup(config,store,runtime):
    def duplicate():raise AssertionError('Duplicate engine capacity read')
    runtime.capacity=duplicate
    runtime.observe=lambda:{'host_cpu_count':8,'host_memory_bytes':10000,'observed_memory_bytes':777}
    controller=SandboxController(config,store,runtime)
    out=controller.capacity()
    assert (out.cpu_count,out.memory_bytes,out.observed_memory_bytes)==(8,10000,777)

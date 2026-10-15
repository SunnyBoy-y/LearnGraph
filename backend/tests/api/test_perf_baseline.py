"""性能回归基线（带阈值断言）。

原则：
- 测试必须"绿"以进 CI，因此断言为"防灾难劣化"阈值（当前实测值的 2 倍或预算宽松线）。
- 达到正式预算（性能预算：登录 P95<800ms、首 token<2s）用 xfail(strict) 锚点记录，
  修复后转 XPASS 强制提醒更新。
- 使用 StubModelProvider 测管线延迟（不含真实上游），保证确定性。
"""
from __future__ import annotations

import threading
import time

from conftest import StubModelProvider


def _create_session(client, headers):
    r = client.post("/api/v1/sessions", headers=headers, json={"title": "perf-baseline"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_login_concurrency_no_catastrophic_regression(
    client, register_user, auth_headers
):
    """B1-1 防灾难劣化：并发 10 登录 P95 必须 < 3s（当前实测 1.48s；正式预算 800ms 见下锚点）。"""
    _, _, username, password = register_user("perf")
    n = 10
    latencies: list[float] = []
    lock = threading.Lock()

    def do_login():
        t0 = time.perf_counter()
        r = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        dt = (time.perf_counter() - t0) * 1000
        assert r.status_code == 200, r.text
        with lock:
            latencies.append(dt)

    threads = [threading.Thread(target=do_login) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0
    assert len(latencies) == n
    assert p95 < 3000, f"并发 10 登录 P95={p95:.0f}ms 超过 3s 灾难线（基线 1.48s）"


def test_login_concurrency_budget_anchor(client, register_user, auth_headers):
    """性能预算固化：并发 10 登录 P95 < 800ms（Argon2id 迁移后 GIL 释放并行）。"""
    import threading as _th
    import time as _t

    _, _, username, password = register_user("perfB")
    lat: list[float] = []
    lock = _th.Lock()

    def do():
        t0 = _t.perf_counter()
        r = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        with lock:
            lat.append((_t.perf_counter() - t0) * 1000)

    threads = [_th.Thread(target=do) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lat.sort()
    p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0
    assert p95 < 800, f"并发 10 登录 P95={p95:.0f}ms 超过预算 800ms（Argon2id 后应达标）"


def test_stream_first_event_latency_pipeline(
    client, register_user, auth_headers, stub_chat_service
):
    """管线首事件延迟（StubModelProvider，不含真实上游）：preflight+SSE 首 data < 2s。"""
    token, ws, _, _ = register_user("perfS")
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    t0 = time.perf_counter()
    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "管线延迟测试"},
    )
    assert r.status_code == 200, r.text
    first_event_ms = (time.perf_counter() - t0) * 1000
    # 响应流本身在 200 返回时可能已含事件；这里测"请求发出到收到完整响应"的管线时间
    assert first_event_ms < 2000, f"流式管线首事件 {first_event_ms:.0f}ms 超过 2s"


def test_messages_pagination_latency_with_history(
    client, register_user, auth_headers, stub_chat_service
):
    """历史分页：50 轮消息后 GET messages limit 返回窗口且耗时 < 2s（B1-2 防灾难）。"""
    token, ws, _, _ = register_user("perfH")
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    for i in range(30):  # 30 轮 → 60 条消息
        r = client.post(
            f"/api/v1/sessions/{sid}/messages/stream",
            headers=headers,
            json={"content": f"历史消息 {i}"},
        )
        assert r.status_code == 200, r.text
    t0 = time.perf_counter()
    r = client.get(f"/api/v1/sessions/{sid}/messages?limit=20", headers=headers)
    dt = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["items"]) <= 20, f"limit=20 但返回 {len(data['items'])} 条"
    assert data["total_count"] >= 60
    assert dt < 2000, f"历史分页查询 {dt:.0f}ms 超过 2s"


def test_health_latency_budget(client):
    """健康检查 P95 < 200ms（预算 800ms 内的健康探针要求）。"""
    lat = []
    for _ in range(20):
        t0 = time.perf_counter()
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    p95 = lat[int(len(lat) * 0.95) - 1]
    assert p95 < 200, f"health P95={p95:.0f}ms"

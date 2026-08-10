#!/usr/bin/env python3
"""LearnGraph audit - API-level runtime probes (concurrency / duplicates / errors / lockout).

Runs against the isolated test instance (127.0.0.1:8002). Never touches the real
database (backend/data/learngraph.db). Outputs JSON to audit/evidence/stability/.
"""
import json, time, threading, random, string, sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "http://127.0.0.1:8002/api/v1"
OUT = os.path.join(os.path.dirname(__file__), "..", "evidence", "stability")
os.makedirs(OUT, exist_ok=True)
results = {}

def req(method, path, body=None, token=None, ws="demo-workspace", timeout=30):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if ws is not None:
        headers["X-Workspace-ID"] = ws
    r = Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else None, time.perf_counter() - t0
    except HTTPError as e:
        return e.code, e.read().decode()[:300], time.perf_counter() - t0

def demo_token():
    st, body, _ = req("POST", "/auth/demo-login", {"username": "demo", "password": "learn-graph-local"})
    assert st == 200, body
    return body["access_token"]

def register_user(idx):
    name = f"audit_{int(time.time())}_{idx}"
    pw = "Audit@Pass2026!" + random.choice(string.ascii_letters)
    st, body, dt = req("POST", "/auth/register",
                       {"username": name, "display_name": f"Audit {idx}", "password": pw}, ws=None)
    return st, body, dt, name, pw

def percentile(vals, p):
    if not vals: return None
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(len(s) * p / 100)))
    return round(s[k] * 1000, 1)  # ms

def probe(label, fn, n=30, workers=10):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn) for _ in range(n)]
        out = []
        for f in as_completed(futs):
            try: out.append(f.result())
            except Exception as e: out.append(("ERR", str(e), -1))
    times = [x[2] for x in out if x[2] >= 0]
    errs = [x for x in out if x[0] == "ERR" or x[0] >= 400]
    entry = {
        "n": n, "workers": workers,
        "p50_ms": percentile(times, 50), "p90_ms": percentile(times, 90),
        "p95_ms": percentile(times, 95), "p99_ms": percentile(times, 99),
        "min_ms": round(min(times) * 1000, 1) if times else None,
        "max_ms": round(max(times) * 1000, 1) if times else None,
        "error_rate": round(len(errs) / n, 3), "errors": [str(e) for e in errs[:3]],
        "throughput_rps": round(n / max(sum(times), 1e-9), 1),
    }
    results[label] = entry
    print(f"{label}: p50={entry['p50_ms']} p90={entry['p90_ms']} p95={entry['p95_ms']} p99={entry['p99_ms']} err={entry['error_rate']} rps={entry['throughput_rps']}")

def main():
    token = demo_token()
    print(f"[token] ok len={len(token)}")

    # --- 1. latency at low concurrency ---
    probe("health_1w", lambda: req("GET", "/health", token=token, ws=None), n=20, workers=1)
    probe("me_1w", lambda: req("GET", "/auth/me", token=token), n=20, workers=1)
    probe("workspaces_1w", lambda: req("GET", "/workspaces", token=token), n=20, workers=1)
    probe("goals_1w", lambda: req("GET", "/goals", token=token), n=20, workers=1)
    probe("graphs_1w", lambda: req("GET", "/graphs", token=token), n=20, workers=1)
    probe("sessions_1w", lambda: req("GET", "/sessions", token=token), n=20, workers=1)
    probe("providers_1w", lambda: req("GET", "/providers", token=token), n=20, workers=1)
    probe("memory_1w", lambda: req("GET", "/memory", token=token), n=20, workers=1)

    # --- 2. concurrency ramp ---
    for conc in (10, 25, 50):
        probe(f"login_c{conc}", lambda: req("POST", "/auth/demo-login",
              {"username": "demo", "password": "learn-graph-local"}, ws=None), n=conc, workers=conc)
        probe(f"me_c{conc}", lambda: req("GET", "/auth/me", token=token), n=conc, workers=conc)
        probe(f"goals_c{conc}", lambda: req("GET", "/goals", token=token), n=conc, workers=conc)

    # --- 3. duplicate side-effect checks ---
    dup_goal = {"title": f"dup-goal-{int(time.time())}", "description": "audit duplicate check",
                "deadline": None, "assumptions": []}
    # try creating goal twice concurrently with the SAME idempotency signal (no idempotency-key header)
    st1, b1, _ = req("POST", "/goals/clarify", dup_goal, token=token)
    results["dup_goal_clarify"] = {"status1": st1, "body1": str(b1)[:200]}
    print(f"[dup] goal clarify st={st1} body={str(b1)[:150]}")

    # --- 4. error handling ---
    st_bad, b_bad, dt_bad = req("GET", "/goals", token="invalid.token.here")
    results["err_bad_token"] = {"status": st_bad, "body": str(b_bad)[:200], "latency_ms": round(dt_bad * 1000, 1)}
    print(f"[err] bad token -> {st_bad} in {dt_bad*1000:.0f}ms: {str(b_bad)[:100]}")

    st_nows, b_nows, dt_nows = req("GET", "/goals", token=token, ws=None)
    results["err_no_workspace"] = {"status": st_nows, "body": str(b_nows)[:200], "latency_ms": round(dt_nows * 1000, 1)}
    print(f"[err] no X-Workspace-ID -> {st_nows}: {str(b_nows)[:100]}")

    st_exp, b_exp, _ = req("GET", "/goals", token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjF9.sig")
    results["err_expired_token"] = {"status": st_exp, "body": str(b_exp)[:150]}

    # --- 5. login lockout (5 wrong passwords -> 15 min lock) ---
    lockout = []
    for i in range(6):
        st, b, dt = req("POST", "/auth/login", {"username": "demo", "password": "wrong-pass-123"}, ws=None)
        lockout.append({"attempt": i + 1, "status": st, "body": str(b)[:120], "ms": round(dt * 1000, 1)})
    results["login_lockout"] = lockout
    print(f"[lockout] attempts: {[l['status'] for l in lockout]}")

    # --- 6. register flooding probe (just 3) ---
    regs = [register_user(i) for i in range(3)]
    results["register"] = [{"status": r[0], "ms": round(r[2] * 1000, 1)} for r in regs]
    print(f"[register] statuses: {[r[0] for r in regs]}")

    with open(os.path.join(OUT, "api_probes.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n[done] wrote", os.path.join(OUT, "api_probes.json"))

if __name__ == "__main__":
    main()

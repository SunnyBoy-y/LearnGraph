#!/usr/bin/env python3
"""LearnGraph audit - streaming timing + idempotency probes (isolated instance).

Measures the full streaming path against DeepSeek (real model, ~2 calls) and
checks duplicate-creation side effects. Output: audit/evidence/stability/stream_probes.json
"""
import json, os, time, uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "http://127.0.0.1:8002/api/v1"
OUT = os.path.join(os.path.dirname(__file__), "..", "evidence", "stability")
os.makedirs(OUT, exist_ok=True)
results = {"streaming": {}, "duplicates": {}}

def req(method, path, body=None, token=None, ws="demo-workspace", headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    if ws is not None: h["X-Workspace-ID"] = ws
    if headers: h.update(headers)
    r = Request(f"{BASE}{path}", data=data, headers=h, method=method)
    t0 = time.perf_counter()
    try:
        with urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read()) if resp.headers.get("content-type", "").startswith("application/json") else resp.read(), time.perf_counter() - t0
    except HTTPError as e:
        return e.code, e.read().decode()[:400], time.perf_counter() - t0

def token():
    # demo account may be locked by the lockout probe; register a fresh account
    name = f"audit_stream_{int(time.time())}"
    pw = "Audit@Pass2026!x"
    st, b, _ = req("POST", "/auth/register", {"username": name, "display_name": "Audit Stream", "password": pw}, ws=None)
    if st != 201:
        st2, b2, _ = req("POST", "/auth/login", {"username": name, "password": pw}, ws=None)
        return b2["access_token"]
    return b["access_token"]

def stream_timing(tok, session_id, text, label, ws_id):
    """POST stream and time each stage."""
    events = []
    t_start = time.perf_counter()
    body = json.dumps({"content": text}).encode()
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {tok}",
         "X-Workspace-ID": ws_id, "Idempotency-Key": f"audit-{uuid.uuid4()}"}
    r = Request(f"{BASE}/sessions/{session_id}/messages/stream", data=body, headers=h, method="POST")
    t0 = time.perf_counter()
    first_byte = None; first_text = None; last_text = None; text_start = None
    buf = b""
    try:
        with urlopen(r, timeout=180) as resp:
            t_headers = time.perf_counter()
            while True:
                chunk = resp.read(4096)
                if not chunk: break
                if first_byte is None: first_byte = time.perf_counter()
                buf += chunk
                # parse SSE events incrementally
                while b"\n\n" in buf:
                    raw, buf = buf.split(b"\n\n", 1)
                    data_line = None
                    for ln in raw.decode("utf-8", "replace").split("\n"):
                        if ln.startswith("data:"):
                            data_line = ln[5:].strip()
                            break
                    if data_line:
                        try:
                            ev = json.loads(data_line)
                            ev["_t"] = time.perf_counter() - t_start
                            events.append(ev)
                            etype = ev.get("type", "")
                            if "text" in etype or etype == "part.delta":
                                if first_text is None: first_text = time.perf_counter()
                                last_text = time.perf_counter()
                        except Exception:
                            pass
        t_end = time.perf_counter()
    except Exception as e:
        results["streaming"][label] = {"error": str(e)}
        return
    # stage timings
    text_deltas = [e for e in events if e.get("type") in ("text.delta", "part.delta") or "text" in e.get("type", "")]
    intervals = []
    prev = None
    for e in events:
        if "text" in e.get("type", ""):
            t = e["_t"]
            if prev is not None: intervals.append(round((t - prev) * 1000, 1))
            prev = t
    intervals.sort()
    def pct(v, p):
        if not v: return None
        return v[min(len(v)-1, int(len(v)*p/100))]
    results["streaming"][label] = {
        "req_to_first_byte_ms": round((first_byte - t0) * 1000, 1) if first_byte else None,
        "req_to_headers_ms": round((t_headers - t0) * 1000, 1),
        "req_to_first_text_delta_ms": round((first_text - t0) * 1000, 1) if first_text else None,
        "first_to_last_text_ms": round((last_text - first_text) * 1000, 1) if (first_text and last_text) else None,
        "total_ms": round((t_end - t0) * 1000, 1),
        "event_count": len(events),
        "text_delta_count": len(text_deltas),
        "delta_interval_p50_ms": pct(intervals, 50), "delta_interval_p90_ms": pct(intervals, 90),
        "delta_interval_p99_ms": pct(intervals, 99),
        "event_types_sample": [e.get("type") for e in events[:12]],
        "terminal": [e.get("type") for e in events if e.get("type", "").endswith(".completed") or e.get("type", "").endswith(".failed")][:3],
    }
    print(f"[stream {label}] first_byte={results['streaming'][label]['req_to_first_byte_ms']}ms "
          f"first_text={results['streaming'][label]['req_to_first_text_delta_ms']}ms "
          f"total={results['streaming'][label]['total_ms']}ms events={len(events)}")

def token():
    # demo account may be locked by the lockout probe; register a fresh account
    name = f"audit_stream_{int(time.time())}"
    pw = "Audit@Pass2026!x"
    st, b, _ = req("POST", "/auth/register", {"username": name, "display_name": "Audit Stream", "password": pw}, ws=None)
    if st != 201:
        st2, b2, _ = req("POST", "/auth/login", {"username": name, "password": pw}, ws=None)
        tok = b2["access_token"]
    else:
        tok = b["access_token"]
    st3, ws_list, _ = req("GET", "/workspaces", token=tok, ws=None)
    ws_id = ws_list[0]["id"] if ws_list else None
    # copy the DeepSeek provider (workspace-scoped) into the new user's workspace
    try:
        import sqlite3, uuid as _uuid
        db = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "data", "audit_test.db")
        con = sqlite3.connect(db)
        src = "abd8294b-6cf4-4f31-ba5b-eaa80ca9cf87"
        newid = str(_uuid.uuid4())
        row = con.execute("SELECT * FROM provider_configs WHERE id=?", (src,)).fetchone()
        cols = [d[0] for d in con.execute("SELECT * FROM provider_configs LIMIT 0").description]
        if row:
            con.execute("DELETE FROM provider_configs WHERE id=?", (newid,))
            con.execute(f"INSERT INTO provider_configs ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                        [newid if c == 'id' else (ws_id if c == 'workspace_id' else row[cols.index(c)]) for c in cols])
        srow = con.execute("SELECT * FROM provider_secrets WHERE provider_id=?", (src,)).fetchone()
        scols = [d[0] for d in con.execute("SELECT * FROM provider_secrets LIMIT 0").description]
        if srow:
            con.execute("DELETE FROM provider_secrets WHERE provider_id=?", (newid,))
            con.execute(f"INSERT INTO provider_secrets ({','.join(scols)}) VALUES ({','.join('?'*len(scols))})",
                        [newid if c == 'provider_id' else (ws_id if c == 'workspace_id' else srow[scols.index(c)]) for c in scols])
        con.commit(); con.close()
        global _PROVIDER_ID
        _PROVIDER_ID = newid
    except Exception as e:
        print("[warn] provider copy failed:", e)
    return tok, ws_id

def main():
    tok, WS = token()
    def wreq(*a, **k):
        k["ws"] = k.get("ws", WS)
        return req(*a, **k)
    # --- stream 1: short factual ---
    st, s, _ = wreq("POST", "/sessions", {"title": "audit-stream-1"}, token=tok)
    sid = s["id"]
    stream_timing(tok, sid, "用一句话回答：什么是数据库索引？", "short_chat", WS)

    # --- stream 2: longer (agent tools disabled default; plain chat) ---
    st2, s2, _ = wreq("POST", "/sessions", {"title": "audit-stream-2"}, token=tok)
    stream_timing(tok, s2["id"], "请用三段话简要介绍快速排序算法的原理、复杂度与典型应用。", "medium_chat", WS)

    # --- duplicate session creation (same body, concurrent) ---
    dup_title = f"dup-session-{int(time.time())}"
    def create_dup():
        return req("POST", "/sessions", {"title": dup_title}, token=tok, ws=WS)[0:2]
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(create_dup); f2 = ex.submit(create_dup)
        r1 = f1.result(); r2 = f2.result()
    st_all, sessions, _ = wreq("GET", "/sessions", token=tok)
    matches = [s for s in sessions if s.get("title") == dup_title]
    results["duplicates"]["concurrent_session_create"] = {
        "statuses": [r1[0], r2[0]], "created_count": len(matches), "bodies": [str(r1[1])[:120], str(r2[1])[:120]]
    }
    print(f"[dup] session create statuses={results['duplicates']['concurrent_session_create']['statuses']} count={len(matches)}")

    # --- duplicate message stream with SAME Idempotency-Key (should dedupe) ---
    st3, s3, _ = wreq("POST", "/sessions", {"title": "audit-dup-msg"}, token=tok)
    sid3 = s3["id"]
    key = f"audit-idem-{uuid.uuid4()}"
    def send():
        return req("POST", f"/sessions/{sid3}/messages/stream",
                   {"content": "重复请求测试，请回复“收到”。"}, token=tok, ws=WS,
                   headers={"Idempotency-Key": key}, timeout=90)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(send); f2 = ex.submit(send)
        a = f1.result(); b = f2.result()
    st_msgs, msgs, _ = wreq("GET", f"/sessions/{sid3}/messages", token=tok)
    if isinstance(msgs, dict) and "items" in msgs:
        user_msgs = [m for m in msgs["items"] if m.get("role") == "user"]
        count = len(user_msgs)
    elif isinstance(msgs, list):
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        count = len(user_msgs)
    else:
        count = None
    results["duplicates"]["same_idempotency_key_stream"] = {
        "statuses": [a[0], b[0]], "user_message_count": count,
        "messages_resp_shape": str(msgs)[:200], "first_resp": str(a[1])[:150]
    }
    print(f"[dup] idem-key statuses={results['duplicates']['same_idempotency_key_stream']['statuses']} user_msgs={count}")

    with open(os.path.join(OUT, "stream_probes.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("[done] wrote stream_probes.json")

if __name__ == "__main__":
    main()

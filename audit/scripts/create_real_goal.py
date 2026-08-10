#!/usr/bin/env python3
"""Create a real goal via goals/clarify for user perfgo, then insert a 300-node graph via SQL."""
import json, time, uuid
from urllib.request import Request, urlopen
import urllib.error, sqlite3

BASE = "http://127.0.0.1:8002/api/v1"
def req(m, p, b=None, t=None, w=None, timeout=120):
    d = json.dumps(b).encode() if b is not None else None
    h = {"Content-Type": "application/json"}
    if t: h["Authorization"] = f"Bearer {t}"
    if w: h["X-Workspace-ID"] = w
    r = Request(f"{BASE}{p}", data=d, headers=h, method=m)
    try:
        with urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

name = f"perfgo3_{int(time.time())}"
pw = "Perfgo@Pass2026!x"
st, b = req("POST", "/auth/register", {"username": name, "display_name": "P", "password": pw}, w=None)
tok = b["access_token"]
st, w = req("GET", "/workspaces", t=tok)
ws = w[0]["id"]
con = sqlite3.connect("../../backend/data/audit_test.db")
src = "abd8294b-6cf4-4f31-ba5b-eaa80ca9cf87"
newid = str(uuid.uuid4())
cols = [d[0] for d in con.execute("SELECT * FROM provider_configs LIMIT 0").description]
row = con.execute("SELECT * FROM provider_configs WHERE id=?", (src,)).fetchone()
con.execute(f"INSERT INTO provider_configs ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            [newid if c == 'id' else (ws if c == 'workspace_id' else row[cols.index(c)]) for c in cols])
scols = [d[0] for d in con.execute("SELECT * FROM provider_secrets LIMIT 0").description]
srow = con.execute("SELECT * FROM provider_secrets WHERE provider_id=?", (src,)).fetchone()
con.execute(f"INSERT INTO provider_secrets ({','.join(scols)}) VALUES ({','.join('?'*len(scols))})",
            [newid if c == 'provider_id' else (ws if c == 'workspace_id' else srow[scols.index(c)]) for c in scols])
con.commit(); con.close()
print("user", name, "ws", ws, file=__import__('sys').stderr)

st, g = req("POST", "/goals/clarify", {"prompt": "我想学习数据库索引与查询优化"}, t=tok, w=ws)
print("clarify:", st, str(g)[:200])
if isinstance(g, dict) and "goal" in g:
    gid = g["goal"].get("id") or g["goal"].get("goal_id")
else:
    gid = (g.get("goal_id") or g.get("id")) if isinstance(g, dict) else None
print("goal_id:", gid)

if gid:
    # insert graph + 300 nodes + 299 edges (SQL, valid JSON columns default)
    import datetime
    now = datetime.datetime.now(datetime.UTC).isoformat()
    ggraph = str(uuid.uuid4()); P = f"pg{int(time.time())}"
    con = sqlite3.connect("../../backend/data/audit_test.db")
    con.execute("INSERT INTO graphs (id,goal_id,title,status,revision,created_at,updated_at,workspace_id) VALUES (?,?,?, 'active',1,?,?,?)",
                (ggraph, gid, "perf-graph-300", now, now, ws))
    for i in range(300):
        con.execute("INSERT INTO graph_nodes (id,graph_id,label,description,node_type,target_weight,mastery_stars,retrieval_state,evidence_state,attention_state,teaching_strategy,created_at,updated_at,workspace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{P}-{i}", ggraph, f"节点{i}", "", "knowledge", 1.0, i % 5, "fresh", "robust", "focused", "", now, now, ws))
    for i in range(299):
        con.execute("INSERT INTO graph_edges (id,graph_id,source_node_id,target_node_id,relation,created_at,updated_at,workspace_id) VALUES (?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), ggraph, f"{P}-{i}", f"{P}-{i+1}", "relates", now, now, ws))
    con.commit(); con.close()
    print("graph", ggraph, "goal", gid, "ws", ws)
    # verify goals + graph APIs
    st, gl = req("GET", "/goals", t=tok, w=ws)
    print("goals list:", st, str(gl)[:120])
    st, gr = req("GET", f"/graphs/{ggraph}", t=tok, w=ws)
    print("graph GET:", st, "nodes:", len(gr.get("nodes", [])) if isinstance(gr, dict) else gr)

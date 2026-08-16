"""Persistent in-container REPL kernel support for sandboxd.

The kernel is a pure-stdlib Python process started inside the sandbox
container with ``docker exec -d``.  It binds 127.0.0.1:0 inside the container,
writes ``{port, pid}`` to a port file under the chat workspace's
``.learngraph/`` directory, and serves one cell at a time over a JSON-lines
socket.  A watchdog exits the process when its port file disappears, so
explicit close and container teardown both terminate it.

Cell execution:
- the daemon writes the cell source to a workspace file (the daemon argv limit
  is 1024 chars per argument, so code never travels through argv);
- a one-shot client exec reads that file and sends it over the socket;
- the server ``exec``s the cell in a persistent namespace with stdout/stderr
  captured; the last top-level expression (if any) is ``eval``-ed for a
  ``result_repr``; a per-cell ``signal.alarm`` enforces the timeout.

Only the Python kernel is implemented in v1 (the schema restricts
``interpreter`` to ``"python"``).
"""

from __future__ import annotations

import json

# The server and client sources are embedded here and materialized into the
# workspace by the docker runtime at kernel start.  They must remain
# dependency-free (Python stdlib only) because the runner image pins wheels.

KERNEL_SERVER_SOURCE = r'''
import ast
import io
import json
import os
import select
import signal
import socket
import sys
import traceback

kernel_id = sys.argv[1]
port_file = sys.argv[2]
workspace_relative = sys.argv[3] if len(sys.argv) > 3 else "."

namespace = {"__name__": "__sandbox_kernel__"}
namespace.update({"kernel_id": kernel_id, "workspace": workspace_relative})


class CellTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise CellTimeout("cell exceeded its time budget")


def execute_cell(code, timeout_seconds):
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    timed_out = False
    result_repr = None
    # signal.alarm is POSIX-only; the production container is Linux. On
    # platforms without SIGALRM the exec wall-clock timeout still bounds the
    # client, and the cell timeout degrades to the daemon deadline.
    has_alarm = hasattr(signal, "SIGALRM")
    previous = None
    if has_alarm:
        previous = signal.signal(signal.SIGALRM, _timeout_handler)
    try:
        if has_alarm:
            signal.alarm(max(1, int(timeout_seconds or 60)))
        tree = ast.parse(code, filename="<cell>", mode="exec")
        body = list(tree.body)
        if len(body) == 1 and isinstance(body[0], ast.Expr):
            value = eval(compile(ast.Expression(body[0].value), "<cell>", "eval"), namespace)
            try:
                result_repr = repr(value)
            except Exception:
                result_repr = None
        elif body and isinstance(body[-1], ast.Expr):
            # IPython-style: run all statements, then eval the trailing
            # expression and capture its repr.
            prefix = ast.Module(body=body[:-1], type_ignores=[])
            exec(compile(prefix, "<cell>", "exec"), namespace)
            value = eval(compile(ast.Expression(body[-1].value), "<cell>", "eval"), namespace)
            try:
                result_repr = repr(value)
            except Exception:
                result_repr = None
        else:
            exec(compile(tree, "<cell>", "exec"), namespace)
    except CellTimeout:
        timed_out = True
        err.write("\n[cell timed out]\n")
    except BaseException:
        traceback.print_exc(file=err)
    finally:
        if has_alarm:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        sys.stdout, sys.stderr = old_out, old_err
    return {
        "ok": not timed_out and err.getvalue() == "",
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
        "result_repr": result_repr,
        "timed_out": timed_out,
    }


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    server.setblocking(False)
    port = server.getsockname()[1]
    with open(port_file, "w", encoding="utf-8") as handle:
        json.dump({"kernel_id": kernel_id, "port": port, "pid": os.getpid()}, handle)
    while True:
        if not os.path.exists(port_file):
            break
        try:
            readable, _, _ = select.select([server], [], [], 2.0)
        except (OSError, ValueError):
            break
        if not readable:
            continue
        try:
            conn, _addr = server.accept()
        except OSError:
            continue
        conn.settimeout(60)
        try:
            data = conn.recv(1 << 20)
            if not data:
                continue
            request = json.loads(data.decode("utf-8", errors="replace"))
            response = execute_cell(str(request.get("code") or ""), int(request.get("timeout_seconds") or 60))
            response["id"] = request.get("id")
            conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        except Exception:
            try:
                conn.sendall((json.dumps({"id": None, "ok": False, "stdout": "", "stderr": "kernel server error", "result_repr": None, "timed_out": False}) + "\n").encode("utf-8"))
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
'''

KERNEL_CLIENT_SOURCE = r'''
import json
import socket
import sys

port_file = sys.argv[1]
code_file = sys.argv[2]
cell_id = sys.argv[3] if len(sys.argv) > 3 else "cell"

with open(port_file, "r", encoding="utf-8") as handle:
    info = json.load(handle)
with open(code_file, "r", encoding="utf-8") as handle:
    code = handle.read()

sock = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=60)
request = {"id": cell_id, "code": code, "timeout_seconds": 0}
sock.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
buf = b""
while True:
    chunk = sock.recv(1 << 20)
    if not chunk:
        break
    buf += chunk
    if b"\n" in buf:
        break
sock.close()
try:
    response = json.loads(buf.decode("utf-8", errors="replace").split("\n", 1)[0])
except Exception:
    response = {"ok": False, "stdout": "", "stderr": buf.decode("utf-8", errors="replace"), "result_repr": None, "timed_out": False}
sys.stdout.write(json.dumps(response, ensure_ascii=False))
'''

"""Protocol regression tests using real long-lived stdio subprocesses, not echo mocks."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from app.core.errors import AppError
from app.providers.ports.mcp import MCPProtocolFailure
from app.providers.remote.mcp_http import StreamableHTTPMCPAdapter
from app.services.skill_sandbox_run import materialize_skill_package
from app.services.skill_script_runtime import run_skill_script, skill_script_tool_definition

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mcp_session_test", ROOT / "sandbox/mcp_session.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SERVER = r'''
import json, sys
initialized = False
for line in sys.stdin:
    m = json.loads(line)
    method = m.get('method')
    if method in {'server/discover','resources/templates/list'}:
        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'error':{'code':-32601,'message':'Method not found'}}),flush=True)
        continue
    if method == 'initialize':
        result = {'protocolVersion':'2025-11-25', 'serverInfo':{'name':'strict-test','version':'1'}, 'capabilities':{'tools':{}, 'resources':{}, 'prompts':{}}}
    elif method == 'notifications/initialized':
        initialized = True
        continue
    elif not initialized:
        raise RuntimeError('Missing MCP handshake')
    elif method == 'tools/list':
        result = {'tools':[{'name':'two' if m.get('params',{}).get('cursor') else 'one', 'inputSchema':{'type':'object'}}]}
        if not m.get('params',{}).get('cursor'): result['nextCursor'] = 'page2'
    elif method == 'resources/list': result = {'resources':[{'uri':'test://notes','name':'notes'}]}
    elif method == 'prompts/list': result = {'prompts':[{'name':'summarize'}]}
    elif method == 'tools/call': result = {'content':[{'type':'text','text':'real initialized call'}]}
    elif method == 'resources/read': result = {'contents':[{'uri':m['params']['uri'],'text':'notes'}]}
    elif method == 'prompts/get': result = {'messages':[{'role':'user','content':{'type':'text','text':'summarize'}}]}
    else: raise RuntimeError(method)
    print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}), flush=True)
'''


def test_real_stdio_discovery_and_pagination():
    response = module.execute_session([sys.executable, "-u", "-c", SERVER],
        {"jsonrpc":"2.0", "id":7, "method":"learngraph/discover"}, timeout=5)
    assert response['id'] == 7
    assert [t['name'] for t in response['result']['tools']] == ['one', 'two']
    assert response['result']['resources'][0]['uri'] == 'test://notes'
    assert response['result']['prompts'][0]['name'] == 'summarize'


@pytest.mark.parametrize('method,params,key', [
    ('tools/call', {'name':'one','arguments':{}}, 'content'),
    ('resources/read', {'uri':'test://notes'}, 'contents'),
    ('prompts/get', {'name':'summarize','arguments':{}}, 'messages'),
])
def test_real_stdio_call_does_not_wait_for_server_exit(method, params, key):
    response = module.execute_session([sys.executable, '-u', '-c', SERVER],
        {'jsonrpc':'2.0','id':1,'method':method,'params':params}, timeout=5)
    assert response['result'][key]


def test_stdio_rejects_wrong_response_id():
    server = "import sys,json; m=json.loads(sys.stdin.readline()); print(json.dumps({'jsonrpc':'2.0','id':'wrong','result':{}}),flush=True); sys.stdin.read()"
    with pytest.raises(ValueError, match='id'):
        module.execute_session([sys.executable, '-u', '-c', server], {'jsonrpc':'2.0','id':1,'method':'tools/call'}, timeout=3)


def test_stdio_timeout_is_bounded():
    server = "import sys; sys.stdin.read()"
    with pytest.raises(TimeoutError):
        module.execute_session([sys.executable, '-u', '-c', server], {'jsonrpc':'2.0','id':1,'method':'tools/call'}, timeout=0.1)


def adapter(monkeypatch, handler):
    monkeypatch.setattr('app.providers.remote.mcp_http.validate_mcp_http_endpoint', lambda value, **kwargs: value)
    obj = StreamableHTTPMCPAdapter('https://mcp.example/rpc', bearer_token=None, timeout_ms=1000, max_response_bytes=65536)
    monkeypatch.setattr(obj, '_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return obj


def test_http_resources_only_and_session_cleanup(monkeypatch):
    methods = []
    def handler(request):
        if request.method == 'DELETE':
            methods.append('DELETE')
            return httpx.Response(204)
        message = json.loads(request.content)
        methods.append(message['method'])
        if 'id' not in message:
            return httpx.Response(202)
        if message['method'] == 'server/discover':
            return httpx.Response(400,json={'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'Method not found'}})
        result = ({'protocolVersion':'2025-11-25','serverInfo':{'name':'resource-test'},'capabilities':{'resources':{}}}
                  if message['method'] == 'initialize' else {'resourceTemplates':[]} if message['method'] == 'resources/templates/list' else {'resources':[{'uri':'test://one','name':'one'}]})
        return httpx.Response(200, json={'jsonrpc':'2.0','id':message['id'],'result':result}, headers={'Mcp-Session-Id':'session-one'})
    result = adapter(monkeypatch, handler).probe()
    assert result.tools == []
    assert result.resources
    assert 'tools/list' not in methods
    assert methods[-1] == 'DELETE'


def test_http_rejects_repeated_cursor(monkeypatch):
    obj = adapter(monkeypatch, lambda _: httpx.Response(200))
    monkeypatch.setattr(obj, '_request', lambda *a, **kw: ({'tools':[], 'nextCursor':'same'}, None))
    with pytest.raises(MCPProtocolFailure, match='repeated'):
        obj._list_all(None, 'tools/list', 'tools', '2025-11-25', None)


def test_sse_returns_before_eof(monkeypatch):
    class LiveStream(httpx.SyncByteStream):
        def __init__(self, identifier): self.identifier = identifier
        def __iter__(self):
            yield b': keepalive\r\n\r\n'
            yield ('data: '+json.dumps({'jsonrpc':'2.0','id':self.identifier,'result':{'ok':True}})+'\r\n\r\n').encode()
            raise AssertionError('Client incorrectly waited for SSE EOF')
    def handler(request):
        identifier = json.loads(request.content)['id']
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=LiveStream(identifier))
    obj = adapter(monkeypatch, handler)
    with obj._client() as client:
        result, _ = obj._request(client, 'tools/call', {})
    assert result == {'ok':True}


@pytest.mark.parametrize('pooling', [False, True])
def test_skill_materialization_preserves_original_code_assets_and_binary(tmp_path, pooling):
    from subprocess import run
    package = [
        ('scripts/run.py', b"from pathlib import Path\nimport sys\nsys.path.insert(0, '.')\nfrom helper import label\nprint(label, Path('assets/data.bin').read_bytes().hex())\n"),
        ('helper.py', 'label = "技能原始代码"\n'.encode()),
        ('assets/data.bin', b'\x00\xff\x80'), ('package.json', b'{"type":"module"}'),
    ]
    writes = []
    def write(handle, path, data):
        writes.append((path, data))
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    backend = SimpleNamespace(write_agent_file=write)
    agent = SimpleNamespace(settings=SimpleNamespace(sandbox_agent_file_bytes=2097152),
        _require_chat_session=lambda _:None, _resolve_file_session=lambda *_:('session','handle'),
        _runtime_backend=lambda _:backend, _pooling_enabled=lambda _:pooling,
        _container_path=lambda chat,path:f'chats/{chat}/{path}', _touch_session=lambda _:None)
    materialize_skill_package(agent, 'chat', 'session', 'skills/skill/version', package)
    prefix = 'chats/chat/' if pooling else ''
    cwd = tmp_path / (prefix+'skills/skill/version')
    completed = run([sys.executable, 'scripts/run.py'], cwd=cwd, capture_output=True, timeout=5)
    assert completed.returncode == 0
    assert '00ff80' in completed.stdout.decode()
    assert writes[0][1] == package[0][1]
    assert (cwd/'assets/data.bin').read_bytes() == b'\x00\xff\x80'


def test_public_mcp_never_receives_host_bridge_token(monkeypatch):
    from app.services.mcp_skills import MCPAndSkillService
    from app.core.config import Settings
    manager = object.__new__(MCPAndSkillService)
    manager.settings = Settings(_env_file=None, deployment_profile='self_hosted_team', host_bridge_url='http://bridge.example:9999')
    manager._credential_secret = lambda _:None
    calls = []
    monkeypatch.setattr('app.services.mcp_skills.StreamableHTTPMCPAdapter', lambda endpoint, **kwargs: calls.append((endpoint,kwargs)))
    token = Mock(return_value='bridge-private-token')
    monkeypatch.setattr('app.services.mcp_skills.read_bridge_token', token)
    server = SimpleNamespace(transport='streamable_http',endpoint_url='https://public.example/mcp',server_key='public',timeout_ms=1000,max_result_bytes=10000)
    manager._adapter(server)
    assert calls[-1][1]['extra_headers'] == {}
    token.assert_not_called()
    assert server.endpoint_url == 'https://public.example/mcp'


def test_bridge_resolution_is_not_persisted_and_remains_repeatable(monkeypatch):
    from app.services.mcp_skills import MCPAndSkillService
    from app.core.config import Settings
    manager = object.__new__(MCPAndSkillService)
    manager.settings = Settings(_env_file=None, deployment_profile='self_hosted_team', host_bridge_url='http://bridge.example:9999')
    manager._credential_secret = lambda _:None
    calls = []
    monkeypatch.setattr('app.services.mcp_skills.StreamableHTTPMCPAdapter', lambda endpoint, **kwargs: calls.append((endpoint,kwargs)))
    monkeypatch.setattr('app.services.mcp_skills.read_bridge_token', lambda _: 'bridge-private-token')
    server = SimpleNamespace(transport='streamable_http',endpoint_url='http://127.0.0.1:8080/mcp',server_key='local',timeout_ms=1000,max_result_bytes=10000)
    manager._adapter(server); manager._adapter(server)
    assert server.endpoint_url == 'http://127.0.0.1:8080/mcp'
    assert calls[0] == calls[1]
    assert calls[0][1]['extra_headers']['X-LearnGraph-Host-Bridge-Token'] == 'bridge-private-token'


def test_skill_script_never_exposes_model_session_override():
    params = skill_script_tool_definition()['function']['parameters']
    assert 'chat_session_id' not in params['properties']
    runtime = SimpleNamespace(sandbox=None, sandbox_authorized=False, _failure=lambda code,message:code)
    assert run_skill_script(runtime, {}, chat_session_id='bound') == 'sandbox_agent_unavailable'

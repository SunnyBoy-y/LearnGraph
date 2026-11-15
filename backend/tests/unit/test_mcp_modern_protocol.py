from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import httpx
import pytest
from app.providers.mcp_metadata import MODERN_PROTOCOL_VERSION as VERSION, header_paths, header_value
from app.providers.ports.mcp import MCPProtocolFailure, MCPRemoteError
from app.providers.remote.mcp_http import StreamableHTTPMCPAdapter


def http_adapter(monkeypatch, handler):
    monkeypatch.setattr('app.providers.remote.mcp_http.validate_mcp_http_endpoint',lambda value,**kw:value)
    obj=StreamableHTTPMCPAdapter('https://example.test/mcp',bearer_token=None,timeout_ms=3000,max_response_bytes=65536)
    monkeypatch.setattr(obj,'_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    return obj


def test_modern_http_uses_discovery_and_per_request_metadata_and_routing_headers(monkeypatch):
    calls=[]
    def handler(request):
        assert request.method=='POST'
        message=json.loads(request.content);calls.append(message['method'])
        assert request.headers['MCP-Protocol-Version']==VERSION
        assert request.headers['Mcp-Method']==message['method']
        assert message['params']['_meta']['io.modelcontextprotocol/protocolVersion']==VERSION
        method=message['method']
        if method=='server/discover': result={'resultType':'complete','supportedVersions':[VERSION],'capabilities':{'tools':{}},'_meta':{'io.modelcontextprotocol/serverInfo':{'name':'modern'}}}
        elif method=='tools/list': result={'resultType':'complete','tools':[{'name':'query','inputSchema':{'type':'object','properties':{'region':{'type':'string','x-mcp-header':'Region'}}}}]}
        elif method=='tools/call':
            assert request.headers['Mcp-Name']=='query'
            assert request.headers['Mcp-Param-Region']==header_value('华东\n区域')
            result={'resultType':'complete','content':[{'type':'text','text':'modern-executed'}]}
        else: raise AssertionError(method)
        return httpx.Response(200,json={'jsonrpc':'2.0','id':message['id'],'result':result})
    result=http_adapter(monkeypatch,handler).call_tool('query',{'region':'华东\n区域'})
    assert result.result['content'][0]['text']=='modern-executed'
    assert calls==['server/discover','tools/list','tools/call']


def test_modern_error_never_downgrades_or_replays(monkeypatch):
    calls=[]
    def handler(request):
        message=json.loads(request.content);calls.append(message['method'])
        return httpx.Response(400,json={'jsonrpc':'2.0','id':message['id'],'error':{'code':-32022,'message':'unsupported','data':{'supported':['2030-01-01']}}})
    with pytest.raises(MCPRemoteError) as failure:
        http_adapter(monkeypatch,handler).call_tool('query',{})
    assert failure.value.rpc_code==-32022
    assert calls==['server/discover']


@pytest.mark.parametrize('schema',[
    {'type':'object','properties':{'a':{'type':'number','x-mcp-header':'A'}}},
    {'type':'object','properties':{'a':{'type':'string','x-mcp-header':'A\r\nInjected'}}},
    {'type':'object','properties':{'a':{'type':'string','x-mcp-header':'A'},'b':{'type':'string','x-mcp-header':'a'}}},
    {'type':'object','oneOf':[{'properties':{'a':{'type':'string','x-mcp-header':'A'}}}]},
])
def test_header_schema_rejects_injection_duplicates_and_ambiguous_paths(schema):
    with pytest.raises(MCPProtocolFailure):header_paths(schema)


def test_invalid_header_schema_only_excludes_invalid_tool(monkeypatch):
    obj=http_adapter(monkeypatch,lambda r:httpx.Response(200))
    monkeypatch.setattr(obj,'_request',lambda *a,**kw:({'tools':[
        {'name':'bad','inputSchema':{'type':'object','properties':{'a':{'type':'number','x-mcp-header':'A'}}}},
        {'name':'good','inputSchema':{'type':'object'}}]},None))
    assert [t['name'] for t in obj._list_all(None,'tools/list','tools',VERSION,None)]==['good']


def test_header_values_encode_control_unicode_padding_and_sentinel():
    for text in ['a\nb','中文',' padded ','=?base64?literal?=']:
        result=header_value(text)
        assert result.startswith('=?base64?') and result.endswith('?=')
        assert '\r' not in result and '\n' not in result
    assert header_value(True)=='true'
    assert header_value(42)=='42'
    with pytest.raises(MCPProtocolFailure):header_value(2**60)


def test_http_connections_pin_validated_ip_and_reject_dns_rebinding(monkeypatch):
    import socket
    import httpcore
    from app.providers.mcp_network import PinnedMCPBackend
    dialled=[]
    monkeypatch.setattr(httpcore.SyncBackend,'connect_tcp',lambda self,host,port,*a,**kw:dialled.append(host) or object())
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))])
    backend=PinnedMCPBackend('public.example')
    backend.connect_tcp('public.example',443,1)
    assert dialled==['1.1.1.1']
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(httpcore.ConnectError,match='private'):
        backend.connect_tcp('public.example',443,1)
    assert dialled==['1.1.1.1']
    PinnedMCPBackend('localhost').connect_tcp('localhost',443,1)
    assert dialled[-1]=='127.0.0.1'


def test_remote_credentials_are_redacted_even_when_echoed_in_text():
    from types import SimpleNamespace
    from app.services.mcp_result import redact_result
    manager=SimpleNamespace(_credential_secret=lambda server:'not-a-real-credential')
    value=redact_result(manager,SimpleNamespace(auth_reference='reference'),{'content':[{'text':'echo not-a-real-credential'}],'access_token':'other'})
    assert value=={'content':[{'text':'echo [REDACTED]'}],'access_token':'[REDACTED]'}


@pytest.mark.parametrize('modern',[True,False])
def test_real_http_transport_negotiates_and_executes_without_dns_race(modern):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_DELETE(self):
            calls.append('DELETE');self.send_response(204);self.end_headers()
        def do_POST(self):
            message=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            method=message['method'];calls.append(method)
            status=200
            if method=='server/discover' and not modern:
                status=400;value={'error':{'code':-32601,'message':'old server'}}
            elif method=='server/discover':
                value={'result':{'supportedVersions':[VERSION],'capabilities':{'tools':{}},'_meta':{'io.modelcontextprotocol/serverInfo':{'name':'real-http'}}}}
            elif method=='initialize':
                value={'result':{'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'real-http'}}}
            elif method=='notifications/initialized':
                self.send_response(202);self.end_headers();return
            elif method=='tools/list':value={'result':{'tools':[{'name':'proof','inputSchema':{'type':'object'}}]}}
            elif method=='tools/call':value={'result':{'content':[{'type':'text','text':'REAL-HTTP-EXECUTION'}]}}
            else:raise AssertionError(method)
            body=json.dumps({'jsonrpc':'2.0','id':message['id'],**value}).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            if not modern and method=='initialize':self.send_header('Mcp-Session-Id','real-session')
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        adapter=StreamableHTTPMCPAdapter(f'http://127.0.0.1:{server.server_port}/mcp',bearer_token=None,timeout_ms=3000,max_response_bytes=65536)
        result=adapter.call_tool('proof',{})
        assert result.result['content'][0]['text']=='REAL-HTTP-EXECUTION'
        assert calls.count('tools/call')==1
        assert ('initialize' in calls) is (not modern)
        assert ('DELETE' in calls) is (not modern)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_modern_stdio_has_no_initialize_and_carries_metadata():
    spec=importlib.util.spec_from_file_location('mcp_session_modern',Path(__file__).resolve().parents[2]/'sandbox/mcp_session.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    server=r'''
import sys,json
for line in sys.stdin:
 m=json.loads(line)
 assert m['params']['_meta']['io.modelcontextprotocol/protocolVersion']=='2026-07-28'
 if m['method']=='server/discover':r={'supportedVersions':['2026-07-28'],'capabilities':{'tools':{}},'_meta':{'io.modelcontextprotocol/serverInfo':{'name':'modern-stdio'}}}
 elif m['method']=='tools/call':r={'resultType':'complete','content':[{'type':'text','text':'MODERN-STDIO'}]}
 else:raise RuntimeError('unexpected handshake')
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
'''
    response=module.execute_session([sys.executable,'-u','-c',server],{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'query','arguments':{}}},timeout=5)
    assert response['result']['content'][0]['text']=='MODERN-STDIO'

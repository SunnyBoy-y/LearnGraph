"""Opt-in sandboxd/Docker proof for original Skill bytes and real MCP stdio.

All DBs, daemon state, containers and volumes are owned by this test run.
No production container, credentials or workspace is used.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import uuid
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("LEARNGRAPH_TEST_EXTENSIONS_DOCKER") != "1", reason="real Docker opt-in")]


@pytest.mark.parametrize("pooled", [False, True])
def test_original_skill_code_and_mcp_in_real_sandbox(tmp_path, monkeypatch, pooled):
    import docker
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "sandboxd"))
    from sandboxd.config import SandboxdConfig
    from sandboxd.main import create_app
    from app.core.config import get_settings
    from app.core.database import Base
    from app.core.security import Principal
    from app.domain.models import ChatSession, Workspace, User
    from app.domain.schemas.extensions import SkillPackageCreateRequest, PermissionDecisionRequest
    from app.providers.remote.sandboxd_backend import SandboxdBackend
    from app.providers.remote.sandboxd_client import SandboxdClient
    from app.providers.sandbox_registry import get_sandbox_backend_registry
    from app.services.agent_runtime import AgentToolRuntime
    from app.services.mcp_skills import MCPAndSkillService
    from app.services.sandbox import SandboxAgentWorkspaceService
    from app.services.skill_package import SkillPackageService
    from app.services.skill_sandbox_run import SkillSandboxRunService

    deployment = "extensions-test-"+uuid.uuid4().hex[:12]
    token = uuid.uuid4().hex
    docker_client = docker.from_env()
    image = docker_client.images.get(os.environ.get("LEARNGRAPH_TEST_EXTENSIONS_IMAGE", "learngraph-extension-test:local")).id
    config = SandboxdConfig(listen_host="127.0.0.1",port=0,token=token,admin_token=token,
        state_path=str(tmp_path/'daemon.db'),deployment_id=deployment,docker_host=None,
        runtime_backend="docker",runtime_image=image,egress_network_enabled=False,
        egress_proxy_url=None,egress_proxy_container=None,max_request_bytes=16*1048576,
        max_file_bytes=8*1048576,max_stdout_bytes=2*1048576,max_active=4,
        workspace_uid="65532:65532",reconcile_on_start=False,reconcile_grace_seconds=0,
        ttl_sweep_interval_seconds=60,network_label="com.learngraph.sandbox",
        seccomp_dir=str(Path(__file__).resolve().parents[2]/'sandbox'))
    settings = get_settings().model_copy(update={
        'storage_root':tmp_path/'storage','sandbox_workspace_root':str(tmp_path/'workspaces'),
        'sandbox_backend':'sandboxd','sandbox_image':image,'sandbox_browser_image':image,
        'sandboxd_deployment_id':deployment,'sandbox_instance_pooling_enabled':pooled,
        'sandbox_egress_enabled':False,'sandbox_host_minimum_free_disk_bytes':0,
        'sandbox_wall_time_seconds':30, 'sandbox_agent_enabled':True,
    })
    engine = create_engine(f"sqlite:///{(tmp_path/'backend.db').as_posix()}",connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    application = create_app(config,start_workers=False)
    with TestClient(application) as transport, factory() as db:
        client = SandboxdClient(url='http://testserver',token=token,admin_token=token,deployment_id=deployment,client=transport)
        client.install_runtime('python-node',image)
        backend = SandboxdBackend(settings,client=client)
        import app.core.database as database_module
        monkeypatch.setattr(database_module,'SessionLocal',factory)
        registry = get_sandbox_backend_registry()
        monkeypatch.setattr(registry,'default',lambda *a,**kw:backend)
        monkeypatch.setattr(registry,'for_backend_id',lambda *a,**kw:backend)
        workspace = Workspace(id='ws-extensions',tenant_id='tenant-test',owner_user_id='actor-test',name='extensions test')
        principal = Principal(user_id='actor-test',username='actor',tenant_id='tenant-test',session_id='test')
        db.add_all([workspace,User(id='actor-test',tenant_id='tenant-test',username='actor',username_normalized='actor',password_hash='unused'),
                    ChatSession(id='chat-extensions',workspace_id=workspace.id,title='Original skill execution')])
        db.commit()
        packages = SkillPackageService(db,workspace.id,principal.user_id,settings)
        skill = packages.create_package(SkillPackageCreateRequest(skill_key='real-code-proof',name='Original code proof',source='integration-test'))
        code = b"from pathlib import Path\nimport sys\nsys.path.insert(0, '.')\nfrom helper import label\ndata=Path('assets/raw.bin').read_bytes()\nPath('proof.txt').write_text(label+':'+data.hex())\nprint('ORIGINAL:'+label+':'+data.hex())\n"
        for path,body in [('scripts/run.py',code),('helper.py',b"label='package-helper'\n"),('assets/raw.bin',b'\x00\xff\x80'),('package.json',b'{"private":true}'),('scripts/run.mjs',b"import fs from 'node:fs'; console.log('ORIGINAL-NODE:'+fs.readFileSync('assets/raw.bin').toString('hex'));\n")]:
            packages._write_file_bytes(skill,path,body,invalidate=False)
        packages._recompute_package_state(skill);db.commit()
        extensions = MCPAndSkillService(db,workspace.id,principal.user_id,settings,workspace=workspace,principal=principal)
        extensions.authorize_skill(skill.id,PermissionDecisionRequest(decision='always',permissions=list(skill.required_permissions),reason='integration-test'))
        sandbox = SandboxAgentWorkspaceService(db,workspace.id,principal.user_id,settings,backend=backend,workspace=workspace,principal=principal)
        runtime = AgentToolRuntime(workspace_id=workspace.id,actor_id=principal.user_id,search_provider=None,
            extensions=extensions,sandbox=sandbox,sandbox_authorized=True,settings=settings)
        try:
            definitions = runtime.definitions(agent_mode_enabled=True,web_search_enabled=False)
            assert any(d['function']['name']=='lg_skill_run_script' for d in definitions)
            text,meta,_ = runtime.execute({'id':'skill-proof','function':{'name':'lg_skill_run_script',
                'arguments':json.dumps({'skill_key':skill.skill_key,'script_path':'scripts/run.py'})}},
                allowed_domains=[],chat_session_id='chat-extensions',disclosed_tool_names={'lg_skill_run_script'})
            result = json.loads(text)
            assert meta['status']=='completed',result
            assert result['status']=='succeeded',result
            assert 'ORIGINAL:package-helper:00ff80' in result['stdout_summary']
            service = SkillSandboxRunService(db,workspace.id,principal.user_id,settings)
            invocation = service.refresh_invocation(result['invocation_id'])
            assert invocation.status=='succeeded'
            output = sandbox.read_file(__import__('app.domain.schemas.sandbox',fromlist=['SandboxAgentFileReadRequest']).SandboxAgentFileReadRequest(
                chat_session_id='chat-extensions',path=result['package_root']+'/proof.txt',sandbox_session_id=result['sandbox_session_id']))
            assert output['content']=='package-helper:00ff80'
            node_text,node_meta,_ = runtime.execute({'id':'skill-node-proof','function':{'name':'lg_skill_run_script',
                'arguments':json.dumps({'skill_key':skill.skill_key,'script_path':'scripts/run.mjs'})}},
                allowed_domains=[],chat_session_id='chat-extensions',disclosed_tool_names={'lg_skill_run_script'})
            node_result = json.loads(node_text)
            assert node_meta['status']=='completed' and node_result['status']=='succeeded',node_result
            assert 'ORIGINAL-NODE:00ff80' in node_result['stdout_summary']
            # Prove the persistent schedule -> actual stored Skill -> sandbox job
            # -> reconciled terminal run path, not a mocked scheduler callback.
            from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate
            from app.domain.extension_models import ScheduledExtensionRun
            from app.services.scheduled_tasks import ScheduledTaskService, execute_run, reconcile_runs
            schedules = ScheduledTaskService(db,workspace,principal,settings)
            task = schedules.create(ScheduledTaskCreate(name='Original scheduled proof',task_type='skill',
                skill_id=skill.id,cron_expression='0 9 * * *',timezone='Asia/Shanghai',
                payload_json={'script_path':'scripts/run.py','chat_session_id':'chat-extensions'}))
            scheduled = schedules.claim(task.id)
            execute_run(task.id,scheduled.id,settings,factory)
            db.expire_all()
            scheduled = db.get(ScheduledExtensionRun,scheduled.id)
            assert scheduled.result_json.get('job_id'), scheduled.error_message
            sandbox.execute_agent_tool('sandbox_get_job',{'job_id':scheduled.result_json['job_id'],'wait_seconds':15},
                chat_session_id='chat-extensions',agent_authorized=True)
            reconcile_runs(db,settings=settings)
            db.expire_all()
            assert db.get(ScheduledExtensionRun,scheduled.id).status=='succeeded'
            assert schedules.require(task.id).lease_token is None
            # Run the actual fixed MCP task in the same disposable offline container.
            from app.providers.remote.mcp_stdio import DockerStdioMCPRunner
            runner = DockerStdioMCPRunner(settings.model_copy(update={'mcp_stdio_runner_enabled':True}))
            monkeypatch.setattr(runner,'_backend',lambda:backend)
            monkeypatch.setattr(runner,'_persist_runner_session',lambda **kw:None)
            monkeypatch.setattr(runner,'_mark_runner_session_terminated',lambda **kw:None)
            strict_server = "import sys,json\nready=False\nfor line in sys.stdin:\n m=json.loads(line)\n method=m['method']\n if method=='server/discover':\n  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'error':{'code':-32601,'message':'Method not found'}}),flush=True);continue\n if method=='initialize': r={'protocolVersion':'2025-11-25','serverInfo':{'name':'real-docker','version':'1'},'capabilities':{'tools':{}}}\n elif method=='notifications/initialized': ready=True;continue\n elif not ready: raise RuntimeError('not initialized')\n elif method=='tools/list': r={'tools':[{'name':'proof','inputSchema':{'type':'object'}}]}\n elif method=='tools/call': r={'content':[{'type':'text','text':'REAL-DOCKER-MCP'}]}\n else: raise RuntimeError(method)\n print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)\n"
            spec={'server_id':'mcp-proof','workspace_id':workspace.id,'command':['python','-u','-c',strict_server],
                  'image_digest':image,'protocol_version':'2025-11-25'}
            probe = runner.probe(spec)
            assert probe.tools[0]['name']=='proof'
            call = runner.invoke(spec,tool_name='proof',arguments={})
            assert call.result['content'][0]['text']=='REAL-DOCKER-MCP'
            print(json.dumps({'real_docker':True,'pooling':pooled,'original_bytes':True,'node_code':True,'binary_assets':True,
                'relative_helper':True,'script_output_file':True,'scheduled_skill_execution':True,'mcp_handshake_discovery_call':True}))
        finally:
            runtime.discard_streamed_writes()
            for record in application.state.store.list_sandboxes(deployment):
                if record.state!='DELETED': client.delete_sandbox(record.sandbox_id,record.session_id)
            assert not docker_client.containers.list(all=True,filters={'label':f'com.learngraph.deployment_id={deployment}'})
            assert not docker_client.volumes.list(filters={'label':f'com.learngraph.deployment_id={deployment}'})
    docker_client.close();engine.dispose()

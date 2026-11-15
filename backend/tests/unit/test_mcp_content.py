from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.domain.schemas.extensions import MCPServerCreateRequest, PermissionDecisionRequest
from app.providers.ports.mcp import MCPProbeResult, MCPToolCallResult
from app.services.mcp_content import content_tool_definitions, invoke_content
from app.services.mcp_skills import MCPAndSkillService


@pytest.fixture(scope='module')
def database(tmp_path_factory):
    root=tmp_path_factory.mktemp('content')
    engine=create_engine(f"sqlite:///{(root/'content.db').as_posix()}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine,expire_on_commit=False),Settings(_env_file=None,storage_root=root/'storage')
    engine.dispose()


@pytest.fixture()
def content(database,monkeypatch):
    import uuid
    factory,settings=database
    with factory() as db:
        manager=MCPAndSkillService(db,'ws-'+uuid.uuid4().hex[:8],'owner',settings)
        called=[]
        adapter=SimpleNamespace(
            probe=lambda:MCPProbeResult('2026-07-28',{'name':'content-server'},{'resources':{},'prompts':{}},[],
                [{'uri':'test://notes','name':'notes'},{'uri':'test://notes/{id}','uriTemplate':'test://notes/{id}','name':'templated'}],
                [{'name':'summary','arguments':[{'name':'topic','required':True}]}]),
            read_resource=lambda uri:called.append(('resource',uri)) or MCPToolCallResult({'contents':[{'uri':uri,'text':'notes'}]}),
            get_prompt=lambda name,args:called.append(('prompt',name,args)) or MCPToolCallResult({'messages':[{'role':'user','content':{'type':'text','text':'summarize'}}]}))
        monkeypatch.setattr(manager,'_adapter',lambda _:adapter)
        server=manager.create_server(MCPServerCreateRequest(server_key='content',display_name='Content',source='test',version='1',transport='streamable_http',endpoint_url='https://example.test/mcp',agent_auto_invoke=True,
            manifest={'identity':'content','requested_tools':[],'requested_resources':['test://notes','test://notes/{id}'],'requested_prompts':['summary'],'permissions':['network']}))
        snapshot=manager.refresh_server(server.id)
        manager.authorize_server(server.id,PermissionDecisionRequest(decision='always',permissions=server.required_permissions,reason='test'))
        yield SimpleNamespace(manager=manager,server=server,adapter=adapter,called=called,snapshot=snapshot)


def test_resource_and_prompt_only_servers_are_usable_and_discoverable(content):
    c=content
    catalog=c.manager.capability_descriptors()
    assert any(item['capability_id']=='mcp:content' and item['resource_uris'] for item in catalog)
    definitions={d['function']['name'] for d in content_tool_definitions(c.manager)}
    assert definitions=={'lg_mcp_read_resource','lg_mcp_get_prompt'}
    result=invoke_content(c.manager,c.server.id,'resource','test://notes')
    assert result.status=='succeeded'
    prompt=invoke_content(c.manager,c.server.id,'prompt','summary',{'topic':'graphs'})
    assert prompt.status=='succeeded'
    assert c.called==[('resource','test://notes'),('prompt','summary',{'topic':'graphs'})]


def test_reviewed_template_expands_with_rfc6570_without_arbitrary_fetch(content):
    result=invoke_content(content.manager,content.server.id,'resource','test://notes/{id}',{'id':'a b/中文'})
    assert result.status=='succeeded'
    assert content.called[-1]==('resource','test://notes/a%20b%2F%E4%B8%AD%E6%96%87')
    with pytest.raises(AppError,match='reviewed'):
        invoke_content(content.manager,content.server.id,'resource','https://unreviewed.test/secrets')
    with pytest.raises(AppError,match='exactly'):
        invoke_content(content.manager,content.server.id,'resource','test://notes/{id}',{})


def test_prompt_required_arguments_and_secrets_are_rejected(content):
    with pytest.raises(AppError,match='required'):
        invoke_content(content.manager,content.server.id,'prompt','summary',{})
    with pytest.raises(AppError,match='credentials'):
        invoke_content(content.manager,content.server.id,'prompt','summary',{'api_key':'not-real'})
    assert content.called==[]


def test_content_snapshot_changes_revoke_authorization(content):
    original=content.adapter.probe
    def changed():
        probe=original()
        return MCPProbeResult(probe.protocol_version,{'name':'changed'},probe.capabilities,probe.tools,probe.resources,probe.prompts)
    content.adapter.probe=changed
    with pytest.raises(AppError,match='authorization'):
        invoke_content(content.manager,content.server.id,'resource','test://notes')
    assert content.called==[]


def test_one_time_grant_cannot_be_consumed_twice(content):
    c=content
    grant=c.manager.authorize_server(c.server.id,PermissionDecisionRequest(decision='allow_once',permissions=c.server.required_permissions,reason='one time'))
    invoke_content(c.manager,c.server.id,'resource','test://notes')
    assert grant.status=='consumed'
    with pytest.raises(AppError,match='authorization'):
        invoke_content(c.manager,c.server.id,'resource','test://notes')
    assert len(c.called)==1

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import AuditEvent, ChatSession, Goal, Graph, Project, User, Workspace, WorkspaceSetting
from app.services.workflow import WorkflowService


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'folders.db').as_posix()}", connect_args={"check_same_thread": False, "timeout": 20})
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (User, Workspace, Goal, Graph, Project, ChatSession, WorkspaceSetting, AuditEvent)])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    principal = Principal(user_id='owner', username='owner', tenant_id='tenant', session_id='unit')
    with factory() as db:
        workspace = Workspace(id='ws', tenant_id='tenant', owner_user_id='owner', name='unit')
        db.add_all([User(id='owner', tenant_id='tenant', username='owner', username_normalized='owner', password_hash='unused'), workspace])
        db.flush()
        db.add(Goal(id='goal', workspace_id='ws', title='Goal', raw_prompt='Learn'))
        db.flush()
        db.add_all([Graph(id=id, workspace_id='ws', goal_id='goal', title='Same title', status='published') for id in ['g1', 'g2']])
        db.add(ChatSession(id='source', workspace_id='ws', title='Source', graph_id='g2', context_capsule={'current_node_id': 'old-node', 'draft': 'keep'}))
        db.commit()
        yield SimpleNamespace(db=db, factory=factory, workspace=workspace, principal=principal, service=WorkflowService(db, workspace, principal))
    engine.dispose()


def test_same_title_and_rename_do_not_change_folder_identity(env):
    first = env.service.open_graph_folder('g1')
    other = env.service.open_graph_folder('g2')
    assert first.id != other.id
    graph = env.db.get(Graph, 'g1'); graph.title = 'Renamed'; env.db.commit()
    assert env.service.open_graph_folder('g1').id == first.id
    assert env.db.scalar(select(func.count()).select_from(Project)) == 2
    source = env.db.get(ChatSession, 'source')
    assert source.graph_id == 'g2' and source.current_node_id == 'old-node'
    assert source.context_capsule['draft'] == 'keep'
    assert env.db.scalar(select(func.count()).select_from(ChatSession)) == 1


def test_legacy_folder_is_adopted_by_id_and_mapping_remains_stable(env):
    env.db.add(Project(id='legacy', workspace_id='ws', title='Custom folder', primary_graph_id='g1'))
    env.db.commit()
    assert env.service.open_graph_folder('g1').id == 'legacy'
    env.db.add(Project(id='another', workspace_id='ws', title='Same title', primary_graph_id='g1'))
    env.db.commit()
    assert env.service.open_graph_folder('g1').id == 'legacy'
    mapping = env.db.scalar(select(WorkspaceSetting).where(WorkspaceSetting.key == 'learning.graph-folder.g1'))
    assert mapping.value == 'legacy'


def test_same_title_unrelated_folder_is_never_rebound(env):
    env.db.add(Project(id='other', workspace_id='ws', title='Same title', primary_graph_id='g2'))
    env.db.commit()
    assert env.service.open_graph_folder('g1').id != 'other'
    assert env.db.get(Project, 'other').primary_graph_id == 'g2'


def test_reopening_archived_folder_restores_the_same_folder(env):
    folder = env.service.open_graph_folder('g1')
    env.service.archive_project(folder.id, True)
    reopened = env.service.open_graph_folder('g1')
    assert reopened.id == folder.id and reopened.status == 'active' and reopened.archived_at is None


def test_missing_or_foreign_graph_is_not_accessible(env):
    with pytest.raises(AppError, match='not found'):
        env.service.open_graph_folder('missing')
    foreign = Workspace(id='other', tenant_id='tenant', owner_user_id='owner', name='other')
    env.db.add(foreign); env.db.commit()
    with pytest.raises(AppError, match='not found'):
        WorkflowService(env.db, foreign, env.principal).open_graph_folder('g1')


def test_concurrent_first_open_has_one_folder_and_one_mapping(env):
    def open_folder(_):
        with env.factory() as db:
            return WorkflowService(db, db.get(Workspace, 'ws'), env.principal).open_graph_folder('g1').id
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(open_folder, range(3)))
    assert len(set(results)) == 1
    env.db.expire_all()
    assert env.db.scalar(select(func.count()).select_from(Project).where(Project.primary_graph_id == 'g1')) == 1
    assert env.db.scalar(select(func.count()).select_from(WorkspaceSetting).where(WorkspaceSetting.key == 'learning.graph-folder.g1')) == 1


def test_node_resume_filter_is_bounded_and_cursor_cannot_cross_nodes(env):
    from app.services.session_browser import SessionBrowser
    folder = env.service.open_graph_folder('g1')
    env.db.add_all([ChatSession(id=f'node-{i}', workspace_id='ws', title='Node learning', project_id=folder.id,
        graph_id='g1', context_capsule={'current_node_id': 'n1' if i < 3 else 'n2'}) for i in range(4)])
    env.db.commit()
    browser = SessionBrowser(env.db, env.workspace, env.principal)
    page = browser.page(project_id=folder.id, graph_id='g1', node_id='n1', roots_only=False, pinned_first=False, limit=2)
    assert len(page.items) == 2 and page.has_more
    assert all(env.db.get(ChatSession, item.id).current_node_id == 'n1' for item in page.items)
    with pytest.raises(AppError, match='cursor'):
        browser.page(project_id=folder.id, graph_id='g1', node_id='n2', roots_only=False, pinned_first=False, cursor=page.next_cursor)


def test_concurrent_repair_of_stale_mapping_has_one_replacement(env):
    original = env.service.open_graph_folder('g1')
    original.primary_graph_id = 'g2'
    env.db.commit()
    def open_folder(_):
        with env.factory() as db:
            return WorkflowService(db, db.get(Workspace, 'ws'), env.principal).open_graph_folder('g1').id
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(open_folder, range(3)))
    assert len(set(results)) == 1 and results[0] != original.id
    env.db.expire_all()
    assert env.db.scalar(select(func.count()).select_from(Project).where(Project.primary_graph_id == 'g1')) == 1

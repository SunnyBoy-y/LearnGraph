"""Exercise the real HTTP -> DB -> task inbox -> speaking-agent handoff."""
import pytest
import asyncio

from app.core.database import SessionLocal
from app.domain.models import VoiceSessionRecord, VoiceTaskLinkRecord, VoiceResultInboxRecord, SandboxAgentTask, SandboxAgentEvent, SandboxJob
from app.voice.coordinator_adapter import CoordinatorDelegationPort


def test_display_control_uses_durable_scene_and_silent_turn_without_audio_error(client, scene_call):
    from app.voice.conversation_control import load_conversation_policy
    from app.voice.journal import VoiceTurnJournal
    headers, voice, _ = scene_call
    async def run():
        journal = VoiceTurnJournal(voice["id"])
        await journal.user_typed("我要页面")
        await journal._emit("conversation.policy", payload={"quiet": False, "background_muted": True,
            "wait_for_scene": True, "mode": "display"})
        shown = await CoordinatorDelegationPort(voice["id"]).show_scene("scene-task", "visual-comparison")
        assert shown["status"] == "requested"
        await journal.assistant_completed(memory=False, silent_control=True)
        await journal.drain_persistence()
    asyncio.run(run())
    view = client.get(f"/api/v1/voice/sessions/{voice['id']}", headers=headers).json()
    assert view["scene_presentation"]["presentation_mode"] == "display"
    events = client.get(f"/api/v1/voice/sessions/{voice['id']}/events", headers=headers).json()["events"]
    assert any(e["type"] == "turn.finalized" and e["payload"].get("outcome") == "finalized" for e in events)
    assert not any(e["type"] == "processor.error" for e in events)
    assert load_conversation_policy(voice["id"])["background_muted"] is True


def test_post_claim_validation_can_read_delivered_result_and_release_it(client, scene_call):
    _, voice, result_id = scene_call
    port = CoordinatorDelegationPort(voice["id"])
    async def run():
        claim = await port.acknowledge_result("scene-task", delivery_state="delivered")
        assert claim["created"]
        assert await port.ready_results() == []
        current = await port.current_results()
        assert current[0]["result_id"] == result_id
        assert current[0]["status"] == "delivered"
        await port.acknowledge_result("scene-task", delivery_state="released")
        assert (await port.ready_results())[0]["status"] == "ready"
    asyncio.run(run())


def test_controls_preserve_task_topic_but_a_new_question_replaces_it(client, scene_call):
    from app.domain.models import VoiceTurnRecord
    headers, voice, _ = scene_call
    base = f"/api/v1/voice/sessions/{voice['id']}"
    first = client.post(base + "/turns/accept", headers=headers,
        json={"text": "展示你的能力", "client_message_id": "topic-demo"}).json()
    with SessionLocal() as db:
        from sqlalchemy import select
        link = db.scalar(select(VoiceTaskLinkRecord).where(VoiceTaskLinkRecord.voice_session_id == voice['id']))
        link.trigger_turn_id = first['turn_id']
        first_turn = db.get(VoiceTurnRecord, first['turn_id'])
        first_turn.status = 'finalized'
        db.commit()
    client.post(base + "/turns/accept", headers=headers,
        json={"text": "恢复自动播报", "client_message_id": "topic-control"})
    port = CoordinatorDelegationPort(voice['id'])
    assert port._ready_results_sync(10)[0]['topic_current'] is True
    with SessionLocal() as db:
        for turn in db.query(VoiceTurnRecord).filter(VoiceTurnRecord.voice_session_id == voice['id']):
            turn.status = 'finalized'
        db.commit()
    client.post(base + "/turns/accept", headers=headers,
        json={"text": "讲一个故事", "client_message_id": "topic-story"})
    assert port._ready_results_sync(10)[0]['topic_current'] is False


@pytest.fixture
def scene_call(client, register_user, auth_headers):
    token, workspace, _, _ = register_user("scene")
    headers = auth_headers(token, workspace)
    chat = client.post("/api/v1/sessions", headers=headers, json={"title": "Voice scene"}).json()["id"]
    response = client.post("/api/v1/voice/sessions", headers=headers, json={"chat_session_id": chat})
    assert response.status_code == 200, response.text
    voice = response.json()
    with SessionLocal() as db:
        session = db.get(VoiceSessionRecord, voice["id"])
        link = VoiceTaskLinkRecord(workspace_id=workspace, tenant_id=session.tenant_id,
            voice_session_id=session.id, chat_session_id=chat, subagent_id="scene-task",
            title="Compare methods", status="SUCCEEDED", requirement_version=1,
            latest_result_version=1, auto_delivery=False)
        db.add(link)
        db.flush()
        result = VoiceResultInboxRecord(workspace_id=workspace, tenant_id=session.tenant_id,
            voice_session_id=session.id, task_link_id=link.id, subagent_id=link.subagent_id,
            requirement_version=1, result_version=1, dedupe_key=f"scene:{session.id}",
            summary="A uses less memory", payload_json={"agent_result": {
                "short_answer": "A uses less memory", "findings": ["A stores fewer entries"],
                "limitations": ["Measured on the supplied example only"],
            }, "visuals": [{"protocol": "suip/0.1", "block_id": "comparison", "component": "table",
                "revision": 1, "state": "completed", "props": {"title": "Methods", "columns": [{"key": "memory", "label": "Memory"}]},
                "rows": [{"id": "a", "label": "A", "values": {"memory": 12}}]}]})
        db.add(result)
        db.commit()
        result_id = result.id
    return headers, voice, result_id


def test_scene_receipt_is_scoped_versioned_and_recovered_after_reconnect(client, scene_call, register_user, auth_headers):
    headers, voice, result_id = scene_call
    path = f"/api/v1/voice/sessions/{voice['id']}/results/{result_id}/scene"
    receipt = {"requirement_version": 1, "result_version": 1, "session_epoch": voice["session_epoch"],
               "status": "rendered", "anchors": ["summary", "finding-0", "visual-comparison"]}
    assert client.post(path, headers=headers, json=receipt).status_code == 200
    assert client.post(path, headers=headers, json=receipt).status_code == 200
    port = CoordinatorDelegationPort(voice["id"])
    ready = port._ready_results_sync(10)
    assert ready[0]["scene"]["status"] == "rendered"
    assert ready[0]["scene"]["anchors"] == receipt["anchors"]
    assert ready[0]["auto_delivery"] is False
    assert port._focus_scene_sync("scene-task", "finding-0")["status"] == "focused"
    events = client.get(f"/api/v1/voice/sessions/{voice['id']}/events", headers=headers).json()["events"]
    assert any(event["type"] == "scene.focus" and event["payload"]["anchor_id"] == "finding-0" for event in events)
    assert port._focus_scene_sync("scene-task", "unknown")["status"] == "unconfirmed"
    restored = client.get(f"/api/v1/voice/sessions/{voice['id']}", headers=headers).json()
    assert restored["results"][0]["payload"]["visuals"][0]["block_id"] == "comparison"
    assert client.post(path, headers=headers, json={**receipt, "session_epoch": voice["session_epoch"] + 1}).status_code == 409
    assert client.post(path, headers=headers, json={**receipt, "anchors": ["nonexistent"]}).status_code == 422
    token, workspace, _, _ = register_user("other_scene")
    assert client.post(path, headers=auth_headers(token, workspace), json=receipt).status_code == 404
    assert client.post(path, headers=headers, json={**receipt, "status": "failed", "anchors": []}).status_code == 200
    assert port._ready_results_sync(10)[0]["scene"]["status"] == "failed"
    assert port._focus_scene_sync("scene-task", "finding-0")["status"] == "requested"


def test_collapsed_scene_can_be_revealed_without_claiming_it_is_rendered(client, scene_call):
    headers, voice, result_id = scene_call
    port = CoordinatorDelegationPort(voice["id"])
    scene = port._ready_results_sync(10)[0]["scene"]
    assert scene["anchors"] == []
    assert "visual-comparison" in scene["available_anchors"]
    assert port._focus_scene_sync("scene-task", "visual-comparison")["status"] == "requested"
    view = client.get(f"/api/v1/voice/sessions/{voice['id']}", headers=headers).json()
    assert view["scene_presentation"]["anchor_id"] == "visual-comparison"
    assert view["scene_presentation"]["result_id"] == result_id
    assert port._ready_results_sync(10)[0]["scene"]["status"] == "unconfirmed"


def test_explicit_user_request_prepares_focus_before_model_inference(client, scene_call):
    from app.voice.task_context import load_task_context
    headers, voice, _ = scene_call
    with SessionLocal() as db:
        session = db.get(VoiceSessionRecord, voice['id'])
        db.add(SandboxAgentTask(workspace_id=session.workspace_id,owner_user_id=session.owner_user_id,
            chat_session_id=session.chat_session_id,task_id='scene-task',status='SUCCEEDED',title='Methods'))
        db.commit()
    query='请讲解刚才生成的交互表格。'
    accepted=client.post(f"/api/v1/voice/sessions/{voice['id']}/turns/accept",headers=headers,
        json={'text':query,'client_message_id':'scene-user-prepare'}).json()
    load_task_context(voice['id'],query)
    load_task_context(voice['id'],query)
    path=f"/api/v1/voice/sessions/{voice['id']}"
    presentation=client.get(path,headers=headers).json()['scene_presentation']
    assert presentation['anchor_id']=='visual-comparison'
    assert presentation['turn_id']==accepted['turn_id']
    events=client.get(path+'/events',headers=headers).json()['events']
    assert sum(event['type']=='scene.focus' for event in events)==1
    client.post(path+f"/turns/{accepted['turn_id']}/finalize",headers=headers,json={'text':'Finished'})
    load_task_context(voice['id'],query)
    assert client.get(path,headers=headers).json()['scene_presentation'] is None


@pytest.mark.parametrize("ending", ["turn.finalized", "turn.interrupted", "user.started", "session.closed", "session.reconnecting"])
def test_presentation_ends_with_playback_or_interruption_not_an_older_turn(client, scene_call, ending):
    from app.voice.events import emit_event
    headers, voice, result_id = scene_call
    emit_event(voice["id"], "scene.focus", turn_id="speaking-turn", payload={
        "task_id": "scene-task", "result_id": result_id, "result_version": 1,
        "requirement_version": 1, "anchor_id": "visual-comparison"})
    path = f"/api/v1/voice/sessions/{voice['id']}"
    emit_event(voice["id"], "turn.finalized", turn_id="older-turn", payload={"role": "assistant"})
    assert client.get(path, headers=headers).json()["scene_presentation"] is not None
    emit_event(voice["id"], "turn.finalized", turn_id="speaking-turn", payload={"role": "user"})
    assert client.get(path, headers=headers).json()["scene_presentation"] is not None
    emit_event(voice["id"], ending, turn_id="speaking-turn", payload={"role": "assistant"})
    assert client.get(path, headers=headers).json()["scene_presentation"] is None


def test_expired_media_owner_cannot_leave_a_result_temporarily_open(client, scene_call):
    from datetime import datetime,timedelta,timezone
    headers,voice,_=scene_call
    CoordinatorDelegationPort(voice['id'])._focus_scene_sync('scene-task','visual-comparison')
    with SessionLocal() as db:
        db.get(VoiceSessionRecord,voice['id']).runtime_owner_lease_until=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.commit()
    assert client.get(f"/api/v1/voice/sessions/{voice['id']}",headers=headers).json()['scene_presentation'] is None


def test_manual_delivery_reaches_runtime_and_can_only_be_adopted_once(client, scene_call):
    headers, voice, result_id = scene_call
    path = f"/api/v1/voice/sessions/{voice['id']}/results/{result_id}/deliver"
    response = client.post(path, headers=headers, json={"request_id": "user-listen-12345"})
    assert response.status_code == 200, response.text
    port = CoordinatorDelegationPort(voice["id"])
    rows = port._ready_results_sync(10)
    assert len(rows) == 1
    assert rows[0]["manual_delivery"] is True
    first = port._acknowledge_result_sync("scene-task", "delivered")
    second = port._acknowledge_result_sync("scene-task", "delivered")
    assert first["created"] is True
    assert second["created"] is False
    assert first["speech_id"] == response.json()["speech_id"]
    port._update_speech_delivery_sync(first["speech_id"], "started")
    port._update_speech_delivery_sync(first["speech_id"], "interrupted")
    with SessionLocal() as db:
        result = db.get(VoiceResultInboxRecord, result_id)
        assert result.status == "ready"
        assert result.payload_json["visuals"]  # interruption cannot delete artifacts
    retry = client.post(path, headers=headers, json={"request_id": "user-listen-retry-12345"})
    assert retry.status_code == 200, retry.text
    assert port._ready_results_sync(10)[0]["manual_delivery"] is True


def test_delivery_preference_persists_without_cancelling_work(client, scene_call):
    headers, voice, _ = scene_call
    path = f"/api/v1/voice/sessions/{voice['id']}/tasks/scene-task/delivery"
    response = client.post(path, headers=headers, json={"auto_delivery": True})
    assert response.status_code == 200, response.text
    assert response.json()["auto_delivery"] is True
    assert response.json()["status"] == "SUCCEEDED"
    restored = client.get(f"/api/v1/voice/sessions/{voice['id']}", headers=headers).json()
    assert restored["tasks"][0]["auto_delivery"] is True


def test_pause_resume_and_cancel_are_separate_from_audio_interrupt(client, scene_call):
    headers, voice, _ = scene_call
    with SessionLocal() as db:
        session = db.get(VoiceSessionRecord, voice["id"])
        task = SandboxAgentTask(workspace_id=session.workspace_id, owner_user_id=session.owner_user_id,
            chat_session_id=session.chat_session_id, task_id="scene-task", status="QUEUED", spec_json={"origin": "voice"})
        db.add(task)
        db.flush()
        job = SandboxJob(workspace_id=session.workspace_id, owner_user_id=session.owner_user_id,
            chat_session_id=session.chat_session_id, kind="subagent", payload_json={"task_id": task.id}, status="QUEUED")
        db.add(job)
        db.flush()
        task.latest_job_id = job.id
        db.commit()
        job_id, task_id = job.id, task.id
    base = f"/api/v1/voice/sessions/{voice['id']}/tasks/scene-task"
    paused = client.post(base + "/execution/pause", headers=headers, json={})
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    resumed = client.post(base + "/execution/resume", headers=headers, json={})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "queued"
    with SessionLocal() as db:
        assert db.get(SandboxJob, job_id).status == "QUEUED"
        assert not db.get(SandboxAgentTask, task_id).spec_json.get("voice_pause_requested")
    assert client.post(base + "/execution/pause", headers=headers, json={}).status_code == 200
    cancelled = client.post(base + "/cancel", headers=headers, json={})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"].lower() == "cancelled"
    assert client.post(base + "/execution/resume", headers=headers, json={}).status_code == 409


def test_superseded_result_cannot_be_adopted_or_reclaimed_even_with_same_request_id(client, scene_call):
    headers, voice, result_id = scene_call
    path = f"/api/v1/voice/sessions/{voice['id']}/results/{result_id}/deliver"
    assert client.post(path, headers=headers, json={"request_id": "user-listen-12345"}).status_code == 200
    with SessionLocal() as db:
        result = db.get(VoiceResultInboxRecord, result_id)
        db.get(VoiceTaskLinkRecord, result.task_link_id).requirement_version = 2
        db.commit()
    port = CoordinatorDelegationPort(voice["id"])
    assert port._ready_results_sync(10) == []
    with pytest.raises(Exception, match="no longer current"):
        port._acknowledge_result_sync("scene-task", "delivered")
    assert client.post(path, headers=headers, json={"request_id": "user-listen-12345"}).status_code == 409


def test_fast_agent_can_read_bounded_partial_scene_before_task_finishes(scene_call):
    _, voice, result_id = scene_call
    with SessionLocal() as db:
        session = db.get(VoiceSessionRecord, voice["id"])
        db.delete(db.get(VoiceResultInboxRecord, result_id))
        task = SandboxAgentTask(workspace_id=session.workspace_id, owner_user_id=session.owner_user_id,
            chat_session_id=session.chat_session_id, task_id="scene-task", status="RUNNING", spec_json={"requirement_version": 1})
        db.add(task)
        db.flush()
        db.add(SandboxAgentEvent(workspace_id=session.workspace_id, task_id=task.id, seq=1,
            event_type="artifact.upsert", payload={"requirement_version": 1, "snapshot": {
                "block_id": "partial", "component": "table", "props": {"title": "Partial comparison", "source_note": "Example input"},
                "rows": [{"id": f"row-{index}", "label": str(index), "values": {"method": "A"}} for index in range(10)],
            }}))
        db.commit()
    view = CoordinatorDelegationPort(voice["id"])._task_status_sync("scene-task")
    assert view["status"] == "running"
    assert view["partial_results"][0]["title"] == "Partial comparison"
    assert view["partial_results"][0]["evidence_status"] == "worker_reported"
    assert len(view["partial_results"][0]["rows"]) == 5
    assert view["scene"]["status"] == "unconfirmed"
    assert view["events"] == []
    with SessionLocal() as db:
        db.add(SandboxAgentEvent(workspace_id=session.workspace_id, task_id=task.id, seq=2,
            event_type="artifact.upsert", payload={"requirement_version": 1, "snapshot": {
                "block_id": "partial", "component": "table", "revision": 1, "state": "removed", "props": {"title": "Removed"},
            }}))
        db.commit()
    assert CoordinatorDelegationPort(voice["id"])._task_status_sync("scene-task")["partial_results"] == []

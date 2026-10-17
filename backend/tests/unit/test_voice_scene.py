from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.voice.scene import project_scene, scene_grounding, valid_visuals


def test_scene_has_stable_server_owned_anchors_and_omits_code():
    scene = project_scene({"agent_result": {"findings": ["First", "Second"]}, "visuals": [
        {"component": "html", "html": "<script>bad()</script>"},
        {"block_id": "comparison", "component": "table", "props": {"title": "Compare"}},
    ]}, "Summary")
    assert scene["anchors"] == ["summary", "finding-0", "finding-1", "visual-comparison"]
    assert len(scene["visuals"]) == 1


def test_only_fresh_matching_render_receipt_allows_visual_reference():
    payload = {"agent_result": {"findings": ["First"]}, "_scene_receipt": {
        "session_epoch": 2, "requirement_version": 3, "result_version": 1,
        "status": "rendered", "anchors": ["summary", "fake"],
        "at": datetime.now(timezone.utc).isoformat(),
    }}
    def check(**overrides):
        return scene_grounding(payload, **{"summary": "Ready", "session_epoch": 2,
                              "requirement_version": 3, "result_version": 1, **overrides})
    assert check()["anchors"] == ["summary"]
    assert check(session_epoch=3)["status"] == "unconfirmed"
    assert check(requirement_version=4)["anchors"] == []
    assert check(result_version=2)["anchors"] == []
    payload["_scene_receipt"]["at"] = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
    assert check()["status"] == "unconfirmed"
    payload["_scene_receipt"]["at"] = datetime.now(timezone.utc).isoformat()
    payload["_scene_receipt"]["status"] = "failed"
    assert check()["status"] == "failed"
    assert check()["anchors"] == []


def test_removed_and_invalid_visuals_do_not_get_anchors():
    assert valid_visuals([{"block_id": "gone", "component": "metric", "props": {"title": "X"}, "state": "removed"}]) == []


def test_completed_result_is_not_recaptured_after_sqlite_drops_timezone():
    from app.voice.coordinator import _task_fingerprint
    task = SimpleNamespace(task_id="task", status="SUCCEEDED", result_text="done", deliverables_json={},
                           finished_at=datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc))
    before = _task_fingerprint(task)
    task.finished_at = task.finished_at.replace(tzinfo=None)
    assert _task_fingerprint(task) == before

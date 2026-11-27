"""Turn-level sandbox file checkpoints: multi-turn preview, safe rewind & conflict skip."""
from __future__ import annotations

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.sandbox_checkpoints import SandboxCheckpointService
from app.services.session_workspace import SessionWorkspaceService


def _current_user_id(client, headers) -> str:
    resp = client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_session(client, headers) -> str:
    resp = client.post("/api/v1/sessions", headers=headers, json={"title": "Checkpoint Test"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_checkpoint_two_turn_preview_and_safe_rewind(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    user_id = _current_user_id(client, headers)
    session_id = _create_session(client, headers)
    settings = get_settings()

    with SessionLocal() as db:
        ws_files = SessionWorkspaceService(db, ws, user_id, settings)
        ck = SandboxCheckpointService(db, ws, user_id, settings)

        # Initial baseline file work/app.py before Turn 1
        ws_files.put_bytes(
            chat_session_id=session_id,
            path="work/app.py",
            data=b"print('v0')\n",
            role="work",
            source="agent_write",
        )

        # Turn 1: modifies work/app.py (v0 -> v1) and creates work/new.txt
        existed1, pre1, pre_sz1 = ck.capture_before_state(session_id, "work/app.py")
        view1 = ws_files.put_bytes(
            chat_session_id=session_id,
            path="work/app.py",
            data=b"print('v1')\n",
            role="work",
            source="agent_write",
        )
        ck.record_mutation(
            chat_session_id=session_id,
            turn_id="turn-1",
            path="work/app.py",
            existed_before=existed1,
            pre_blob_sha256=pre1,
            pre_size_bytes=pre_sz1,
            post_blob_sha256=view1["blob_sha256"],
            post_size_bytes=len(b"print('v1')\n"),
        )

        existed_new, pre_new, pre_sz_new = ck.capture_before_state(session_id, "work/new.txt")
        view_new = ws_files.put_bytes(
            chat_session_id=session_id,
            path="work/new.txt",
            data=b"created in turn 1",
            role="work",
            source="agent_write",
        )
        ck.record_mutation(
            chat_session_id=session_id,
            turn_id="turn-1",
            path="work/new.txt",
            existed_before=existed_new,
            pre_blob_sha256=pre_new,
            pre_size_bytes=pre_sz_new,
            post_blob_sha256=view_new["blob_sha256"],
            post_size_bytes=len(b"created in turn 1"),
        )

        # Turn 2: modifies work/app.py again (v1 -> v2)
        existed2, pre2, pre_sz2 = ck.capture_before_state(session_id, "work/app.py")
        view2 = ws_files.put_bytes(
            chat_session_id=session_id,
            path="work/app.py",
            data=b"print('v2')\n",
            role="work",
            source="agent_write",
        )
        ck.record_mutation(
            chat_session_id=session_id,
            turn_id="turn-2",
            path="work/app.py",
            existed_before=existed2,
            pre_blob_sha256=pre2,
            pre_size_bytes=pre_sz2,
            post_blob_sha256=view2["blob_sha256"],
            post_size_bytes=len(b"print('v2')\n"),
        )
        db.commit()

    # List checkpoints
    listed = client.get(f"/api/v1/sessions/{session_id}/checkpoints", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [item["turn_id"] for item in listed.json()["items"]] == ["turn-1", "turn-2"]

    # Preview rewind to before turn-1 (restores app.py -> v0 and deletes new.txt)
    preview = client.get(
        f"/api/v1/sessions/{session_id}/checkpoints/turn-1/preview",
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()
    assert preview_data["restore_count"] == 1
    assert preview_data["delete_count"] == 1
    assert preview_data["conflict_count"] == 0

    # Execute rewind with expected_current_hashes from preview
    rewound = client.post(
        f"/api/v1/sessions/{session_id}/checkpoints/turn-1/rewind",
        headers=headers,
        json={"expected_current_hashes": preview_data["expected_current_hashes"]},
    )
    assert rewound.status_code == 200, rewound.text
    result = rewound.json()
    assert result["restored_paths"] == ["work/app.py"]
    assert result["deleted_paths"] == ["work/new.txt"]
    assert result["conflicts"] == []

    # Verify workspace contents match pre-turn-1 state
    with SessionLocal() as db:
        ws_files = SessionWorkspaceService(db, ws, user_id, settings)
        restored_bytes = ws_files.materialize_bytes(session_id, "work/app.py")
        assert restored_bytes == b"print('v0')\n"
        entries = ws_files.list_entries(session_id)
        assert all(entry.path != "work/new.txt" for entry in entries)


def test_checkpoint_rewind_detects_post_preview_conflict_and_skips_modified_file(
    client, register_user, auth_headers
):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    user_id = _current_user_id(client, headers)
    session_id = _create_session(client, headers)
    settings = get_settings()

    with SessionLocal() as db:
        ws_files = SessionWorkspaceService(db, ws, user_id, settings)
        ck = SandboxCheckpointService(db, ws, user_id, settings)

        ws_files.put_bytes(
            chat_session_id=session_id,
            path="work/safe.txt",
            data=b"safe-v0",
            role="work",
            source="agent_write",
        )
        ws_files.put_bytes(
            chat_session_id=session_id,
            path="work/conflict.txt",
            data=b"conflict-v0",
            role="work",
            source="agent_write",
        )

        # Turn 1 touches both files
        for path, after in (("work/safe.txt", b"safe-v1"), ("work/conflict.txt", b"conflict-v1")):
            existed, pre_sha, pre_sz = ck.capture_before_state(session_id, path)
            view = ws_files.put_bytes(
                chat_session_id=session_id,
                path=path,
                data=after,
                role="work",
                source="agent_write",
            )
            ck.record_mutation(
                chat_session_id=session_id,
                turn_id="turn-conflict",
                path=path,
                existed_before=existed,
                pre_blob_sha256=pre_sha,
                pre_size_bytes=pre_sz,
                post_blob_sha256=view["blob_sha256"],
                post_size_bytes=len(after),
            )
        db.commit()

    # Take preview snapshot
    preview = client.get(
        f"/api/v1/sessions/{session_id}/checkpoints/turn-conflict/preview",
        headers=headers,
    ).json()
    assert preview["conflict_count"] == 0

    # Concurrent edit mutates work/conflict.txt AFTER preview
    with SessionLocal() as db:
        ws_files = SessionWorkspaceService(db, ws, user_id, settings)
        ws_files.put_bytes(
            chat_session_id=session_id,
            path="work/conflict.txt",
            data=b"manual-override-after-preview",
            role="work",
            source="agent_write",
        )
        db.commit()

    # Execute rewind with preview hashes: conflict.txt MUST be skipped, safe.txt restored
    result = client.post(
        f"/api/v1/sessions/{session_id}/checkpoints/turn-conflict/rewind",
        headers=headers,
        json={"expected_current_hashes": preview["expected_current_hashes"]},
    ).json()
    assert result["restored_paths"] == ["work/safe.txt"]
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["path"] == "work/conflict.txt"
    assert result["conflicts"][0]["conflict"] is True

    with SessionLocal() as db:
        ws_files = SessionWorkspaceService(db, ws, user_id, settings)
        assert ws_files.materialize_bytes(session_id, "work/safe.txt") == b"safe-v0"
        assert ws_files.materialize_bytes(session_id, "work/conflict.txt") == b"manual-override-after-preview"

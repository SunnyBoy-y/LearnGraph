from __future__ import annotations

from types import SimpleNamespace

from app.voice import coordinator_adapter
from app.voice.coordinator_adapter import CoordinatorDelegationPort
from app.voice.embedded_delegation import VoiceExerciseAnswerRequest


class _DB:
    def __init__(self, exercise):
        self.exercise = exercise

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def scalar(self, _statement):
        return self.exercise


def test_voice_multiple_choice_answer_is_normalized_before_shared_grading(
    monkeypatch,
):
    exercise = SimpleNamespace(
        id="exercise-1",
        workspace_id="workspace-1",
        node_id="node-1",
        question_type="multiple_choice",
        options=["Rust", "Python", "Go"],
    )
    db = _DB(exercise)
    context = SimpleNamespace(
        workspace_id="workspace-1",
        principal=SimpleNamespace(user_id="user-1"),
        workspace=SimpleNamespace(),
    )
    captured = {}

    class _Authz:
        def __init__(self, *_args):
            pass

        def can_access_bindings(self, *_args, **_kwargs):
            return True

    class _ExerciseService:
        def __init__(self, *_args, **_kwargs):
            pass

        def submit_answer(self, exercise_id, payload, *, attempt_index):
            captured["exercise_id"] = exercise_id
            captured["answer"] = payload.answer
            captured["attempt_index"] = attempt_index
            return SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "exercise_id": exercise_id,
                    "is_correct": True,
                    "evidence_id": "evidence-1",
                }
            )

    monkeypatch.setattr(
        coordinator_adapter,
        "load_session",
        lambda _voice_session_id: SimpleNamespace(
            workspace_id="workspace-1", owner_user_id="user-1"
        ),
    )
    monkeypatch.setattr(coordinator_adapter, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        coordinator_adapter,
        "build_background_workspace_context",
        lambda *_args, **_kwargs: context,
    )
    monkeypatch.setattr(coordinator_adapter, "AuthorizationService", _Authz)
    monkeypatch.setattr(coordinator_adapter, "ExerciseService", _ExerciseService)

    result = CoordinatorDelegationPort("voice-1")._submit_exercise_answer_sync(
        VoiceExerciseAnswerRequest(
            exercise_id="exercise-1", answer="A, C", attempt_index=2
        )
    )

    assert result["is_correct"] is True
    assert captured == {
        "exercise_id": "exercise-1",
        "answer": ["Rust", "Go"],
        "attempt_index": 2,
    }

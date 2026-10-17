from __future__ import annotations

import asyncio
from typing import Any

from pipecat.services.openai.llm import OpenAILLMService

from app.voice.embedded_bot import TutorAgent
from app.voice.embedded_delegation import DelegationRequest, VoiceExerciseAnswerRequest


class _Port:
    def __init__(self) -> None:
        self.request: DelegationRequest | None = None
        self.answer_request: VoiceExerciseAnswerRequest | None = None

    async def delegate(self, request: DelegationRequest):
        self.request = request
        return {"task_id": "task-42", "status": "accepted"}

    async def task_status(self, request):
        return {"task_id": request.task_id, "status": "running"}

    async def cancel_task(self, request):
        return {"task_id": request.task_id, "status": "cancel_requested"}

    async def revise_task(self, request):
        return {"task_id": request.task_id, "status": "accepted"}

    async def ready_results(self, *, limit: int = 10):
        return [
            {
                "task_id": "task-42",
                "title": "Research",
                "status": "ready",
                "summary": "verified answer",
            }
        ][:limit]

    async def acknowledge_result(self, task_id, *, delivery_state="delivered"):
        return {"task_id": task_id, "status": delivery_state, "created": True}

    async def submit_exercise_answer(self, request: VoiceExerciseAnswerRequest):
        self.answer_request = request
        return {
            "exercise_id": request.exercise_id,
            "is_correct": True,
            "evidence_id": "ev-1",
        }


class _Params:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    async def result_callback(self, value):
        self.results.append(dict(value))


def _llm() -> OpenAILLMService:
    return OpenAILLMService(api_key="test")


def test_tutor_delegation_tools_return_quick_receipt():
    async def run() -> None:
        port = _Port()
        agent = TutorAgent(
            llm=_llm(),
            voice_session_id="vs-1",
            delegation_port=port,
        )
        names = {getattr(item, "__name__", getattr(item, "name", "")) for item in agent.build_tools()}
        delegation = next(item for item in agent.build_tools() if getattr(item, "name", "") == "delegate_tool_task")
        assert delegation.properties["capability_id"]["enum"] == ["workspace_read", "learning_read", "practice_prepare",
            "learning_evidence", "graph_proposal", "artifact_create", "workspace_task"]
        assert "capability_id" in delegation.required
        assert {
            "delegate_research",
            "delegate_reasoning",
            "delegate_tool_task",
            "get_background_task",
            "get_ready_results",
            "cancel_background_task",
            "revise_background_task",
            "submit_voice_exercise_answer",
        }.issubset(names)

        params = _Params()
        await agent.delegate_research(params, query="latest docs", purpose="compare")
        assert params.results == [{"task_id": "task-42", "status": "accepted"}]
        assert port.request is not None
        assert port.request.query == "latest docs"
        assert port.request.voice_session_id == "vs-1"

        answer = _Params()
        await agent.submit_voice_exercise_answer(
            answer,
            exercise_id="exercise-1",
            answer="A",
            attempt_index=2,
        )
        assert answer.results == [
            {"exercise_id": "exercise-1", "is_correct": True, "evidence_id": "ev-1"}
        ]
        assert port.answer_request is not None
        assert port.answer_request.exercise_id == "exercise-1"
        assert port.answer_request.answer == "A"
        assert port.answer_request.attempt_index == 2

        completed = _Params()
        await agent.get_ready_results(completed)
        assert completed.results == [
            {
                "results": [
                    {
                        "task_id": "task-42",
                        "title": "Research",
                        "status": "ready",
                        "summary": "verified answer",
                        "source_count": 0,
                        "agent_result": {},
                    }
                ]
            }
        ]

    asyncio.run(run())


def test_tutor_hides_delegation_tools_without_port():
    agent = TutorAgent(llm=_llm(), voice_session_id="vs-1")
    assert agent.build_tools() == []

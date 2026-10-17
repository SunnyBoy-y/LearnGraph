from __future__ import annotations

import asyncio
from typing import Any

from pipecat.frames.frames import BotStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection

from app.voice.embedded_result_bridge import VoiceResultDeliveryProcessor


class _Port:
    def __init__(self, initial: list[dict[str, Any]] | None = None) -> None:
        self.results = list(initial or [])
        self.acknowledged: list[tuple[str, str]] = []
        self.speech_states: list[tuple[str, str]] = []
        self.created = True

    async def ready_results(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return list(self.results[:limit])

    async def acknowledge_result(
        self,
        task_id: str,
        *,
        delivery_state: str = "delivered",
    ) -> dict[str, Any]:
        self.acknowledged.append((task_id, delivery_state))
        return {
            "task_id": task_id,
            "status": delivery_state,
            "created": self.created if delivery_state == "delivered" else False,
            "speech_id": f"speech-{task_id}",
        }

    async def update_speech_delivery(self, speech_id: str, state: str):
        self.speech_states.append((speech_id, state))
        return {"speech_id": speech_id, "playback_status": state}


class _Agent:
    tool_call_active = False

    def __init__(self) -> None:
        self.frames: list[Any] = []

    async def queue_frame(self, frame: Any) -> None:
        self.frames.append(frame)


def _result(task_id: str, *, auto_delivery: bool = True) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "ready",
        "summary": "The answer is ready.",
        "auto_delivery": auto_delivery,
        "agent_result": {
            "short_answer": "The answer is ready.",
            "findings": ["finding"],
            "sources": [],
            "artifacts": [],
            "limitations": [],
        },
    }


def test_ready_result_is_delivered_once_when_floor_is_free() -> None:
    async def run() -> None:
        port = _Port()
        agent = _Agent()
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        port.results.append(_result("task-1"))
        await asyncio.sleep(0.45)
        await processor.stop()
        assert len(agent.frames) == 1
        assert port.acknowledged == [("task-1", "delivered")]

    asyncio.run(run())


def test_auto_delivery_can_be_paused_without_losing_result() -> None:
    async def run() -> None:
        port = _Port()
        agent = _Agent()
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        port.results.append(_result("task-1", auto_delivery=False))
        await asyncio.sleep(0.45)
        await processor.stop()
        assert agent.frames == []
        assert port.acknowledged == []

    asyncio.run(run())


def test_old_ready_result_is_not_replayed_on_reconnect() -> None:
    async def run() -> None:
        port = _Port([_result("old-task")])
        agent = _Agent()
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        await asyncio.sleep(0.45)
        await processor.stop()
        assert agent.frames == []
        assert port.acknowledged == []

    asyncio.run(run())


def test_failed_result_reaches_tutor_as_a_safe_failure_message() -> None:
    async def run() -> None:
        port = _Port()
        agent = _Agent()
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        port.results.append({
            "task_id": "task-failed",
            "status": "failed",
            "summary": "",
            "safe_error": "provider_timeout",
            "auto_delivery": True,
        })
        await asyncio.sleep(0.45)
        await processor.stop()
        assert len(agent.frames) == 1
        assert port.acknowledged == [("task-failed", "delivered")]

    asyncio.run(run())


def test_delivery_claim_is_created_before_the_tutor_frame_is_injected() -> None:
    async def run() -> None:
        port = _Port()
        agent = _Agent()
        queued_after_claim = False

        original_queue = agent.queue_frame

        async def queue_frame(frame: Any) -> None:
            nonlocal queued_after_claim
            queued_after_claim = port.acknowledged == [("task-1", "delivered")]
            await original_queue(frame)

        agent.queue_frame = queue_frame  # type: ignore[method-assign]
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        port.results.append(_result("task-1"))
        await asyncio.sleep(0.45)
        await processor.stop()
        assert queued_after_claim is True
        assert len(agent.frames) == 1

    asyncio.run(run())


def test_existing_delivery_claim_prevents_a_second_tutor_injection() -> None:
    async def run() -> None:
        port = _Port()
        port.created = False
        agent = _Agent()
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        port.results.append(_result("task-1"))
        await asyncio.sleep(0.45)
        await processor.stop()
        assert agent.frames == []
        assert port.acknowledged == [("task-1", "delivered")]

    asyncio.run(run())


def test_playback_boundary_advances_claimed_result_to_played() -> None:
    async def run() -> None:
        port = _Port()
        agent = _Agent()
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        port.results.append(_result("task-played"))
        await asyncio.sleep(0.45)
        await processor.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await processor.playback_ended()
        await processor.stop()
        assert port.speech_states == [
            ("speech-task-played", "enqueued"),
            ("speech-task-played", "started"),
            ("speech-task-played", "played"),
        ]

    asyncio.run(run())


def test_interruption_boundary_marks_active_result_interrupted() -> None:
    async def run() -> None:
        port = _Port()
        agent = _Agent()
        processor = VoiceResultDeliveryProcessor(
            agent=agent,
            result_port=port,
            poll_interval=0.25,
        )
        await processor.start()
        port.results.append(_result("task-interrupted"))
        await asyncio.sleep(0.45)
        # ``InterruptionFrame`` reaches this same boundary in the Pipecat
        # pipeline; call the boundary directly here to avoid creating a
        # detached downstream task in a unit test with no pipeline sink.
        await processor._finish_active_speech(interrupted=True)
        await processor.stop()
        assert port.speech_states == [
            ("speech-task-interrupted", "enqueued"),
            ("speech-task-interrupted", "interrupted"),
        ]

    asyncio.run(run())


def test_silent_or_old_topic_result_cannot_block_another_ready_task():
    async def run():
        port, agent = _Port(), _Agent()
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port, poll_interval=0.25)
        await processor.start()
        port.results = [_result("silent", auto_delivery=False), {**_result("old-topic"), "topic_current": False}, _result("current")]
        await asyncio.sleep(0.35)
        assert port.acknowledged == [("current", "delivered")]
        await processor.stop()
    asyncio.run(run())


def test_two_ready_tasks_cannot_overlap_while_first_audio_is_pending():
    async def run():
        port, agent = _Port(), _Agent()
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port, poll_interval=0.25)
        await processor.start()
        port.results = [_result("first"), _result("second")]
        await asyncio.sleep(0.35)
        processor._cooldown_until = 0
        await asyncio.sleep(0.35)
        assert len(agent.frames) == 1
        await processor.playback_ended()
        await asyncio.sleep(0.35)
        assert len(agent.frames) == 2
        await processor.stop()
    asyncio.run(run())


def test_interrupted_result_waits_for_explicit_request_even_after_reconnect():
    async def run():
        port, agent = _Port(), _Agent()
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port, poll_interval=0.25)
        await processor.start()
        port.results = [_result("first")]
        await asyncio.sleep(0.35)
        await processor.playback_ended(interrupted=True)
        processor._cooldown_until = 0
        await asyncio.sleep(0.35)
        assert len(agent.frames) == 1
        await processor.stop()
        port.results[0]["playback_status"] = "interrupted"
        restarted = VoiceResultDeliveryProcessor(agent=agent, result_port=port, poll_interval=0.25)
        await restarted.start()
        await asyncio.sleep(0.35)
        assert len(agent.frames) == 1
        # A user claim can wake even a silent task from before reconnect.
        port.results[0].update(manual_delivery=True, auto_delivery=False, topic_current=False, playback_status="claimed")
        await asyncio.sleep(0.35)
        assert len(agent.frames) == 2
        await restarted.stop()
    asyncio.run(run())


def test_tutor_packet_exposes_only_acknowledged_scene_anchors_and_preserves_limits():
    message = VoiceResultDeliveryProcessor._build_tutor_message({**_result("a"), "scene": {"status": "unconfirmed", "anchors": []}})
    assert '"status": "unconfirmed"' in message["content"]
    assert "不得声称页面已展示" in message["content"]
    assert "来源存在不等于结论已独立验证" in message["content"]

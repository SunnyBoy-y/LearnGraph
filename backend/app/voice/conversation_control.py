"""Session-owned conversation controls on the existing input/context/TTS path."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pipecat.frames.frames import (
    Frame, InterimTranscriptionFrame, LLMContextFrame, LLMMessagesAppendFrame,
    TextFrame, TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.voice.embedded_turn_intent import is_asr_filler, is_backchannel
from app.voice.turn_plan import TurnPlan, follows_topic, plan_turn


@dataclass(frozen=True)
class ConversationIntent:
    kind: str = "answer"
    text: str = ""
    controls: tuple[str, ...] = ()
    request_text: str = ""


def conversation_intent(text: str) -> ConversationIntent:
    plan = plan_turn(text)
    if text.strip().rstrip("。！!？?") in {"继续讲", "继续讲解", "继续说", "接着讲", "请你继续讲", "请你继续讲解"}:
        return ConversationIntent("resume_speech", request_text=text)
    return ConversationIntent(plan.kind, plan.verbatim or "", plan.controls, plan.request_text)


class VoiceConversationPolicy:
    def __init__(self, initial: dict[str, Any] | None = None):
        initial = initial or {}
        self.quiet = bool(initial.get("quiet"))
        self.background_muted = bool(initial.get("background_muted"))
        self.wait_for_scene = bool(initial.get("wait_for_scene"))
        self.concise = bool(initial.get("concise"))
        self.no_repeat = bool(initial.get("no_repeat", True))
        self.current_only = bool(initial.get("current_only"))
        self.mode = str(initial.get("mode") or ("silence" if self.quiet else "answer"))
        self.suppress_past_errors = bool(initial.get("suppress_past_errors", True))
        self.topic_id = str(initial.get("topic_id") or "")
        self.focus_task_ids = tuple(str(item) for item in initial.get("focus_task_ids", []) if isinstance(item, str))
        self.low_latency_mode = initial.get("low_latency_mode") is True
        self.request_text = ""
        self.plan = TurnPlan()
        self.revision = 0
        self.last_text = ""

    def snapshot(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in (
            "quiet", "background_muted", "wait_for_scene", "concise", "no_repeat", "current_only", "mode",
            "suppress_past_errors", "topic_id", "request_text", "focus_task_ids",
            "low_latency_mode",
        )}

    def accept(self, text: str, *, force: bool = False) -> ConversationIntent:
        intent = conversation_intent(text)
        if text == self.last_text and not force:
            return intent
        if intent.kind == "answer" and (is_asr_filler(text) or is_backchannel(text)):
            return intent
        self.last_text = text
        self.revision += 1
        self.plan = plan_turn(text)
        self.request_text = self.plan.request_text
        self.mode = intent.kind
        self.quiet = not bool(self.request_text or self.plan.verbatim is not None) and bool(set(intent.controls) & {"silence", "no_repeat", "current_only", "concise"})
        for control in intent.controls:
            if control in {"silence", "no_repeat", "current_only", "concise", "suppress_past_errors"}:
                self.background_muted = True
            self.no_repeat |= control == "no_repeat"
            self.current_only |= control == "current_only"
            self.concise |= control == "concise"
            self.suppress_past_errors |= control == "suppress_past_errors"
            if control == "display":
                self.wait_for_scene, self.background_muted = True, True
            elif control == "dismiss_scene":
                self.wait_for_scene, self.background_muted = False, True
            elif control == "auto_delivery":
                self.background_muted = False
        if self.request_text and (not self.topic_id or not follows_topic(self.request_text)):
            self.topic_id = f"topic_{uuid4().hex[:20]}"
            self.focus_task_ids = ()
        return intent

    @property
    def feedback_allowed(self) -> bool:
        return self.low_latency_mode and not self.quiet and self.mode == "answer"

    @property
    def background_allowed(self) -> bool:
        return not (self.quiet or self.background_muted or self.mode in {"verbatim", "display"})

    def allows_result(self, result: dict[str, Any]) -> bool:
        if self.quiet or self.mode in {"verbatim", "display"}:
            return False
        if not result.get("manual_delivery") and not self.background_allowed:
            return False
        if not self.wait_for_scene:
            return True
        scene = result.get("scene") or {}
        return scene.get("status") == "rendered" and bool(scene.get("anchors"))


class VoiceConversationProcessor(FrameProcessor):
    """Input controls are immediate; context controls bypass inference; output is fenced."""
    def __init__(self, policy: VoiceConversationPolicy, *, stage: str, journal=None,
                 interrupt=None, emit_response=None, show_scene=None, publish=None, floor=None):
        super().__init__()
        self.policy, self.stage, self.journal = policy, stage, journal
        self.interrupt, self.emit_response = interrupt, emit_response
        self.show_scene, self.publish, self.floor = show_scene, publish, floor
        self._output_events: dict[tuple, None] = {}
        self._input_events: dict[object, None] = {}

    async def _accept(self, text: str):
        before = self.policy.snapshot()
        intent = self.policy.accept(text)
        turn_id = getattr(self.journal, "current_turn_id", lambda: None)()
        if isinstance(turn_id, str) and intent.request_text and not follows_topic(intent.request_text):
            self.policy.topic_id = turn_id
        if self.policy.snapshot() != before and self.publish:
            await self.publish(self.policy.snapshot())
        return intent

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if self.stage == "input":
            text = ""
            if isinstance(frame, TranscriptionFrame) and not isinstance(frame, InterimTranscriptionFrame):
                if not (isinstance(frame.result, dict) and frame.result.get("superseded")):
                    text = frame.text
            elif isinstance(frame, LLMMessagesAppendFrame):
                text = next((m.get("content", "") for m in reversed(frame.messages or []) if m.get("role") == "user"), "")
            if isinstance(text, str) and text:
                identity = frame.metadata.get("voice_event_id") or frame.id
                if identity in self._input_events:
                    return
                self._input_events[identity] = None
                if len(self._input_events) > 256:
                    self._input_events.pop(next(iter(self._input_events)))
                # Update synchronously before provider/DB IO can release late speech.
                before = self.policy.snapshot()
                intent = self.policy.accept(text, force=True)
                stopping = bool(set(intent.controls) & {"silence", "no_repeat", "current_only", "concise", "display", "dismiss_scene", "suppress_past_errors"})
                if stopping and self.floor:
                    self.floor.interrupt()
                # RTVI interruption may cancel the current input frame task.
                # Persist its policy before that cancellation can discard it.
                if self.publish and self.policy.snapshot() != before:
                    await self.publish(self.policy.snapshot())
                if stopping and self.interrupt:
                    await self.interrupt()
        elif self.stage == "context" and isinstance(frame, LLMContextFrame):
            messages = frame.context.get_messages()
            user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
            text = user.get("content", "")
            if not isinstance(text, str):
                await self.push_frame(frame, direction)
                return
            origin = getattr(self.journal, "current_turn_origin", lambda: None)()
            # A result delivery has no new user input. Re-reading the previous
            # user control would silently consume this assistant-only turn.
            intent = ConversationIntent() if origin == "delivery" else await self._accept(text)
            if intent.kind in {"silence", "no_repeat", "current_only", "concise", "display", "dismiss_scene", "auto_delivery", "verbatim", "suppress_past_errors"}:
                if intent.kind == "verbatim" and self.emit_response:
                    await self.emit_response(intent.text)
                else:
                    if intent.kind == "display" and self.show_scene:
                        await self.show_scene()
                    if self.floor:
                        self.floor.interrupt()
                    if self.journal:
                        await self.journal.assistant_completed(memory=False, silent_control=True)
                return
            if self.policy.quiet:
                return
            # One replaceable policy message; never append a new copy each turn.
            marker = "[voice-conversation-policy]"
            rules = ("优先回答最新用户问题；历史任务只在与本轮相关时使用。继续做指继续执行现有任务，不重复派遣。"
                     "仅继续讲/继续讲解表示恢复播报，不把继续做解释为继续讲。"
                     "默认不主动提及用户历史错误，不把单次答错概括为能力标签。只有用户明确要求复盘时才引用有来源的旧错；教学直接解释正确做法。")
            if intent.controls and intent.request_text:
                rules += "控制偏好已由系统执行；本轮必须继续回答同一句中的有效请求：" + intent.request_text
            if self.policy.no_repeat:
                rules += "不要重述已说过的结论、任务受理或计划；用户明确要求复述或重新提问时正常回答。"
            if self.policy.concise:
                rules += "用户已要求简短：直接给关键答案，结束后不追加追问。"
            if self.policy.current_only:
                rules += "只处理当前问题；忽略不相关的旧任务提醒。"
            if self.policy.low_latency_mode:
                rules += "复杂任务的短应声由系统统一处理；直接输出实际内容，不重复添加受理开场。"
            messages[:] = [m for m in messages if not (m.get("role") == "system" and str(m.get("content", "")).startswith(marker))]
            index = min(1, next((i for i, message in enumerate(messages) if message.get('role') == 'user'), len(messages)))
            messages.insert(index, {"role": "system", "content": marker + rules})
        elif self.stage == "output" and isinstance(frame, TextFrame):
            if frame.metadata.get("voice_revision", self.policy.revision) != self.policy.revision:
                return
            if self.policy.quiet or self.policy.mode == "display":
                return
            if self.policy.mode == "verbatim" and not frame.metadata.get("voice_verbatim"):
                return
            # Network/frame replay is an identity problem. Do not deduplicate
            # ordinary streaming tokens by equal text ("哈" + "哈" is valid).
            identity = frame.metadata.get("voice_event_id") or frame.id
            key = (self.policy.revision, identity, frame.text)
            if key in self._output_events:
                return
            self._output_events[key] = None
            if len(self._output_events) > 256:
                self._output_events.pop(next(iter(self._output_events)))
        await self.push_frame(frame, direction)


def load_conversation_policy(session_id: str) -> dict[str, Any]:
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.domain.models import VoiceEventRecord
    with SessionLocal() as db:
        event = db.scalar(select(VoiceEventRecord).where(
            VoiceEventRecord.voice_session_id == session_id,
            VoiceEventRecord.event_type == "conversation.policy",
        ).order_by(VoiceEventRecord.event_seq.desc()).limit(1))
        return dict(event.payload or {}) if event else {}

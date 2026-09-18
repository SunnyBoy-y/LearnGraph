#
# LearnGraph 实时语音通话 —— Pipecat 官方 Workers 多-agent 拓扑
#
# 拓扑（对齐官方 examples/multi-worker/local-handoff）：
#   主 worker "learngraph"：transport.input → STT → 用户聚合器 → BusBridgeProcessor
#                            → TTS → generation gate → transport.output → 助手聚合器
#   子 worker "tutor"     ：LLMWorker（Pipeline([llm])，bridged=()），只跑 LLM；
#                            用户侧上下文经总线送进去，生成文本经总线送回来交主 worker 的 TTS 朗读。
# 好处：把「谁在说话 / 何时打断 / 音频进出」与「谁生成内容」解耦，
#       后台任务只通过 delegation port 产生结构化结果，不能接入 TTS。
#
# STT : 阿里 DashScope 实时 ASR（qwen3-asr-flash-realtime，Manual 模式）— 自研适配层
# LLM : 会话当前配置的模型（OpenAI 兼容通道）
# TTS : 火山引擎语音合成 2.0（双向流式，seed-tts-2.0）— 自研适配层
# 传输: SmallWebRTC（免 Daily key）
#

import asyncio
import contextlib
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.bus import BusBridgeProcessor
from pipecat.frames.frames import Frame, LLMRunFrame, MetricsFrame
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    TTFATMetricsData,
    TTFBMetricsData,
    TurnMetricsData,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import (
    PipelineParams,
    PipelineWorker,
    ProcessorUnusablePolicy,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.llm import LLMWorker
from pipecat.workers.llm.tool_decorator import tool
from pipecat.workers.runner import WorkerRunner
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)

from app.voice.caption_ledger import VoiceLedgerRelay
from app.voice.embedded_context import VoiceContextAdapter, VoiceContextState
from app.voice.coordinator_adapter import ensure_coordinator_delegation_port_factory
from app.voice.embedded_dashscope_stt import DashScopeSTTService
from app.voice.embedded_delegation import (
    DelegationKind,
    DelegationRequest,
    TaskControlRequest,
    VoiceDelegationPort,
    dispatch_delegation,
    dispatch_task_control,
    resolve_delegation_port,
)
from app.voice.embedded_generation import VoiceGenerationGate
from app.voice.embedded_result_bridge import VoiceResultDeliveryProcessor
from app.voice.embedded_timeline import insert_timeline_probes, timeline_enabled
from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy
from app.voice.embedded_volcengine_tts import VolcengineTTSService
from app.voice.journal import (
    STAGE_LLM,
    VOICE_TYPED_TURN_MESSAGE,
    VoiceControlWatchdog,
    VoiceJournalProcessor,
    VoiceTurnJournal,
    _stage_for_processor,
)
from app.providers.dialects import ThinkingOffVerdict, thinking_off_verdict
from app.providers.thinking_policy import ThinkingOff
from app.services.provider_capabilities import record_thinking_off_observation
from app.voice.policy import resolve_llm_thinking_off
from app.voice.runner_registry import (
    VoiceRunnerHandle,
    get_runner,
    register_runner,
    unregister_runner,
)

load_dotenv(override=True)

MAIN_WORKER_NAME = "learngraph"
TUTOR_WORKER_NAME = "tutor"
VOICE_INTERRUPT_MESSAGE = "learngraph-interrupt"
# RTVI 自定义客户端消息 ``{t: VOICE_PLAYBACK_MESSAGE, d: {...}}``：浏览器自述的
# 播放位置（§2.5）。它**只**用于回放进度与体验，绝不是"用户听完了"的持久判据。
VOICE_PLAYBACK_MESSAGE = "learngraph-playback"

# 口语回答预算（F08）。语音回答的成本主要在"听"：一段 60 秒的朗读会把用户锁在
# 麦克风外，而文字可以扫读。所以预算约束的是**默认详略**，不是内容完整性——它说明
# "什么时候该短"，任何情况下都不允许为了守住预算而截断事实、数字或必要的长代码。
DEFAULT_SPEECH_BUDGET_SENTENCES = "2-3"
DEFAULT_SPEECH_BUDGET_SECONDS = "15-25"

# 前台实时回合恒为「关闭思考」。当某个 Provider 方言/模型根本表达不出关闭（自定义
# 网关、能力快照缺失等）时，通话照常但代价必须可见：这条文案经 ``processor.notice``
# 落到前端提醒条上（不是 error：不降级、不强制文字输入、不静音麦克风）。
VOICE_THINKING_OFF_UNAVAILABLE_NOTICE = (
    "检测到模型 {model} 无法关闭思考，回复延迟会明显增加"
)

# 「关闭思考」发了字段却**没生效**：网关把未知字段丢掉、厂商改了默认值、方言表过期……
# 与上一条的区别：上一条是"压根表达不出"，这一条是"看起来表达了，实测仍在推理"。
# 真机事故（2026-09-18）：``enable_thinking=False`` 发给 DeepSeek 官方被忽略，
# 官方照样返回 398 个 reasoning token、首字晚 2.58 s，占端到端 3638 ms 的 71%，
# 而且**静默**——字段非空，所以"无法关闭思考"那条提醒发不出来。现在两段都有话：
# 会话开始用实测记录提醒（L2），运行中由 LatencyPercentileProcessor 用上游 usage 报警（L3）。
VOICE_THINKING_OFF_INEFFECTIVE_NOTICE_CODE = "thinking_off_ineffective"
VOICE_THINKING_OFF_INEFFECTIVE_NOTICE = (
    "检测到模型 {model} 的「关闭思考」未生效（实测仍在推理），回复延迟会明显增加"
)


#: 语音侧"关闭思考"实测结论的上报回调（L3，见 ``LatencyPercentileProcessor``）。
ThinkingOffReporter = Callable[[ThinkingOffVerdict], Awaitable[None]]


def speech_budget_instruction() -> str:
    """口语回答预算说明；句数与秒数可用环境变量覆盖（默认 2–3 句 / 15–25 秒）。"""
    sentences = (
        os.getenv("VOICE_SPEECH_BUDGET_SENTENCES", "").strip()
        or DEFAULT_SPEECH_BUDGET_SENTENCES
    )
    seconds = (
        os.getenv("VOICE_SPEECH_BUDGET_SECONDS", "").strip()
        or DEFAULT_SPEECH_BUDGET_SECONDS
    )
    return (
        f"默认把口头回答控制在 {sentences} 句、大约 {seconds} 秒以内；"
        "用户明确要求深入讲解时不受这个预算限制，继续展开。"
        "任何情况下都不得为了控制长度而省略关键事实、数字、结论，"
        "也不得截断必要的事实陈述或长代码——预算只约束默认详略，"
        "不约束内容的完整与正确。"
    )


_BASE_SYSTEM_INSTRUCTION = (
    "你是一个友好的中文语音导师。你的回答会被直接朗读出来，所以请使用自然、"
    "口语化的中文，避免 emoji、特殊符号、列表、Markdown 等无法朗读的内容。"
    "回答要简洁、亲切，像真人对话一样。遇到需要联网研究、深度分析或工具执行的"
    "请求时，调用相应的后台任务工具，先快速确认受理，然后继续和用户实时对话；"
    "不要等待后台任务完成，也不要把后台原始结果逐字念出。"
)

SYSTEM_INSTRUCTION = _BASE_SYSTEM_INSTRUCTION + speech_budget_instruction()


def load_session_row(voice_session_id: str):
    """Read durable session state for the control watchdog."""
    from app.voice.events import load_session

    return load_session(voice_session_id)


@dataclass(frozen=True, slots=True)
class _VoiceProviderConfig:
    """Resolved, non-persistent configuration for one voice pipeline."""

    asr: object | None = None
    llm: object | None = None
    tts: object | None = None
    session: object | None = None


def _resolve_voice_provider_config(session_id: str) -> _VoiceProviderConfig:
    """Resolve same workspace providers without request-scoped objects."""
    if not session_id:
        return _VoiceProviderConfig()
    try:
        from app.core.config import get_settings
        from app.core.database import SessionLocal
        from app.providers.factory import (
            model_provider_for_workspace,
            realtime_asr_provider_for_workspace,
            tts_provider_for_workspace,
        )
        from app.voice.events import load_session as load_voice_session

        handle = load_voice_session(session_id)
        if handle is None:
            logger.warning(
                "Voice session {} was not found while resolving Providers", session_id
            )
            return _VoiceProviderConfig()
        with SessionLocal() as db:
            settings = get_settings()
            asr = realtime_asr_provider_for_workspace(
                db, handle.workspace_id, settings
            )
            tts = tts_provider_for_workspace(db, handle.workspace_id, settings)
            llm = model_provider_for_workspace(
                db,
                handle.workspace_id,
                settings,
                model_id=handle.model_id,
                provider_id=handle.provider_id,
                # 前台实时回合恒为 off（见 policy.resolve_llm_thinking_off）：先按 "off"
                # 解析，这样"能否关闭思考"在目录层就已经问过一次。
                thinking_mode="off",
            )
            if not getattr(llm, "available", False):
                # 目录里声明"只支持思考"的模型在 off 下会被拒（thinking_required）。
                # 前台依然要提供服务——口径是"照常服务 + 前端提醒延迟增加"——所以退回该
                # 模型自己的默认档位再解析一次；run_bot 那边会因为表达不出关闭而发提醒。
                # 真正不可服务的（模型被删、Provider 被停）两次都拿不到，照旧带真实原因拒绝。
                retry = model_provider_for_workspace(
                    db,
                    handle.workspace_id,
                    settings,
                    model_id=handle.model_id,
                    provider_id=handle.provider_id,
                    thinking_mode=None,
                )
                if getattr(retry, "available", False):
                    llm = retry
        logger.info(
            "Voice providers: llm={}/{} thinking=off(forced) "
            "session_thinking_max={} asr={} tts={}",
            getattr(llm, "provider_id", None),
            getattr(llm, "model_id", None),
            handle.max_thinking_mode,
            getattr(asr, "model_id", None),
            getattr(tts, "model_id", None),
        )
        return _VoiceProviderConfig(asr=asr, llm=llm, tts=tts, session=handle)
    except Exception:
        logger.exception(
            "Failed to resolve workspace Providers for voice session %s", session_id
        )
        return _VoiceProviderConfig()


def context_change_report(
    trigger_payload: Mapping[str, Any] | None,
    *,
    repointed: bool,
    effective_model_id: str | None,
    applied_epoch: int,
) -> dict[str, Any]:
    """Build the worker's ``context.updated`` acknowledgement.

    Three things share this event type: a model-switch request from the control
    plane (``reason="model_switch"``), a context-snapshot write
    (``reason="context_snapshot"``, no model involved) and this report.  Only
    ``origin="pipeline"`` says "this was already applied by the running
    pipeline" -- which is exactly what ``VoiceControlWatchdog`` skips so it does
    not answer its own answer, one event per second for the rest of the call.

    The trigger's fields are carried through on purpose: the client needs the
    request (``model_id``/``reason``) next to what is really in force
    (``effective_model_id``) and whether it took effect (``repointed``).
    """
    return {
        **dict(trigger_payload or {}),
        "origin": "pipeline",
        "repointed": repointed,
        "applied_epoch": applied_epoch,
        "effective_model_id": effective_model_id,
        "applies_to": "next_turn",
        "certainty": "repointed" if repointed else "next_connection",
    }


class LatencyPercentileProcessor(FrameProcessor):
    """Rolling latency percentile logger: LLM TTFB + 本机回合判定模型。

    ``TurnMetricsData`` 只在这里落账：RTVIObserver 的 ``metrics`` 报文只认
    TTFB/TTFA/TTFAT/Processing/Usage 那几类，本机 Smart Turn 的推理耗时不在其中，
    所以真机读数的唯一落点是这行日志。它同时也是"这点耗时到底加没加进端到端"的
    判据 —— DashScope 的 commit 在 VAD 停止时就发了，识别往返与本机推理并行，
    只有推理慢过识别往返时它才会真正压在回合边界上。

    L3（本类新增的第二个职责）：**"关闭思考"到底生效没有**的落账点。pipecat 白送两条
    互相独立的证据——``TTFATMetricsData.thinking_time``（首包到首个正文字之间的空隙；
    思考 token 不产出任何帧，只能在时间轴上看见）与 usage 里的 ``reasoning_tokens``
    （厂商自己承认产出了思考 token）。意图 = off 却观察到思考 ⇒ 违约：交给注入的回调
    去写日志、发前端提醒、并把实测结论回写 provider 快照（L2）。

    为什么必须在这一层：这是整条链上**唯一不依赖"方言表对不对"**的检查。厂商偷改默认值、
    网关丢掉未知字段、表过期——都只能靠"意图 vs 实测"发现。fail-loud，不 fail-closed：
    通话照常进行，只是不再允许它静默。
    """

    WINDOW = 50

    def __init__(
        self,
        *,
        thinking_off_intent: bool = False,
        on_thinking_off_verdict: "ThinkingOffReporter | None" = None,
    ):
        super().__init__()
        self._samples: list[float] = []
        # (推理耗时 ms, 是否判定回合结束)。后者也要看：Silero 的 stop_secs 判停
        # 常常落在句子中间，那些"假停顿"同样会各跑一次模型。
        self._turn: list[tuple[float, bool]] = []
        # 本轮会话是否真的要求了"关闭思考"（只有要求了才谈得上违约）。
        self._thinking_off_intent = bool(thinking_off_intent)
        self._on_thinking_off_verdict = on_thinking_off_verdict
        # 违规与通过各只上报一次：第一轮就足以说明问题，不必把日志/DB 刷满。
        self._thinking_off_flagged = False
        self._thinking_off_confirmed = False

    def _percentile(self, sorted_vals: list[float], p: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = (len(sorted_vals) - 1) * p
        lo = int(idx)
        hi = min(lo + 1, len(sorted_vals) - 1)
        frac = idx - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    def _report(self) -> None:
        window = self._samples[-self.WINDOW :]
        sv = sorted(window)
        p90_ms = self._percentile(sv, 0.90) * 1000
        p95_ms = self._percentile(sv, 0.95) * 1000
        p99_ms = self._percentile(sv, 0.99) * 1000
        logger.info(
            "TTFB latency (window={}): p90={:.0f}ms p95={:.0f}ms "
            "p99={:.0f}ms (min={:.0f}ms max={:.0f}ms)",
            len(window),
            p90_ms,
            p95_ms,
            p99_ms,
            sv[0] * 1000,
            sv[-1] * 1000,
        )

    def _report_turn(self) -> None:
        window = self._turn[-self.WINDOW :]
        sv = sorted(ms for ms, _ in window)
        complete = sum(1 for _, done in window if done)
        logger.info(
            "Smart turn latency (window={}): p50={:.0f}ms p90={:.0f}ms "
            "p99={:.0f}ms (min={:.0f}ms max={:.0f}ms, turn_complete={}/{})",
            len(window),
            self._percentile(sv, 0.50),
            self._percentile(sv, 0.90),
            self._percentile(sv, 0.99),
            sv[0],
            sv[-1],
            complete,
            len(window),
        )

    async def _check_thinking_off(
        self,
        *,
        reasoning_tokens: int | None = None,
        thinking_time_ms: float | None = None,
    ) -> None:
        """L3：把本轮"意图 vs 实测"交给共享判据，再由注入的回调落账。"""

        if not self._thinking_off_intent or self._on_thinking_off_verdict is None:
            return
        verdict = thinking_off_verdict(
            intent_off=True,
            reasoning_tokens=reasoning_tokens,
            thinking_time_ms=thinking_time_ms,
        )
        if verdict is None:
            return
        if verdict.violated:
            if self._thinking_off_flagged:
                return
            self._thinking_off_flagged = True
        else:
            if self._thinking_off_confirmed:
                return
            self._thinking_off_confirmed = True
        try:
            await self._on_thinking_off_verdict(verdict)
        except Exception:
            # 一条观测记录绝不允许打断通话。
            logger.warning("thinking-off verdict reporter failed", exc_info=True)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            got_ttfb = False
            got_turn = False
            for data in frame.data:
                if isinstance(data, TTFBMetricsData) and data.value is not None:
                    self._samples.append(float(data.value))
                    got_ttfb = True
                elif isinstance(data, TurnMetricsData):
                    self._turn.append(
                        (float(data.e2e_processing_time_ms), bool(data.is_complete))
                    )
                    got_turn = True
                elif isinstance(data, TTFATMetricsData):
                    # 思考 token 不产出任何帧，只能在时间轴上看见：这就是那段时间。
                    thinking = getattr(data, "thinking_time", None)
                    if thinking is not None:
                        await self._check_thinking_off(
                            thinking_time_ms=float(thinking) * 1000.0
                        )
                elif isinstance(data, LLMUsageMetricsData):
                    usage = getattr(data, "value", None)
                    await self._check_thinking_off(
                        reasoning_tokens=int(
                            getattr(usage, "reasoning_tokens", 0) or 0
                        )
                    )
            if got_ttfb:
                if len(self._samples) >= 3:
                    self._report()
                else:
                    logger.info(
                        "TTFB samples so far: {} (need >=3 to compute p90/p95/p99)",
                        len(self._samples),
                    )
            if got_turn:
                if len(self._turn) >= 3:
                    self._report_turn()
                else:
                    logger.info(
                        "Smart turn samples so far: {} (need >=3 to compute p50/p90/p99)",
                        len(self._turn),
                    )
        await self.push_frame(frame, direction)


class TutorAgent(LLMWorker):
    """Foreground tutor: the only agent allowed to write to TTS.

    Delegation tools enqueue work through a narrow port and return a receipt.
    They are registered as asynchronous, non-interruption-cancelled tools so a
    user barge-in never cancels the durable background task itself.
    """

    def __init__(
        self,
        *,
        llm: OpenAILLMService,
        name: str = TUTOR_WORKER_NAME,
        voice_session_id: str = "",
        delegation_port: VoiceDelegationPort | None = None,
    ):
        self._voice_session_id = voice_session_id
        self._delegation_port = delegation_port
        super().__init__(name, llm=llm, bridged=(), active=True)

    def build_tools(self) -> list:
        # Do not advertise delegation tools when no coordinator is installed.
        # This prevents a model from appearing to accept work that has no
        # durable backend.
        if self._delegation_port is None:
            return []
        return super().build_tools()

    async def _delegate(
        self,
        params: FunctionCallParams,
        *,
        kind: DelegationKind,
        query: str,
        purpose: str = "",
        context: str = "",
    ) -> None:
        result = await dispatch_delegation(
            self._delegation_port,
            DelegationRequest(
                kind=kind,
                query=str(query or "").strip(),
                purpose=str(purpose or "").strip(),
                context=str(context or "").strip(),
                voice_session_id=self._voice_session_id,
            ),
        )
        await params.result_callback(result)

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def delegate_research(
        self,
        params: FunctionCallParams,
        query: str,
        purpose: str = "",
        context: str = "",
    ) -> None:
        """Queue a web research task and return immediately with a task receipt."""
        await self._delegate(
            params,
            kind=DelegationKind.RESEARCH,
            query=query,
            purpose=purpose,
            context=context,
        )

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def delegate_reasoning(
        self,
        params: FunctionCallParams,
        query: str,
        purpose: str = "",
        context: str = "",
    ) -> None:
        """Queue deep reasoning and return immediately with a task receipt."""
        await self._delegate(
            params,
            kind=DelegationKind.REASONING,
            query=query,
            purpose=purpose,
            context=context,
        )

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def delegate_tool_task(
        self,
        params: FunctionCallParams,
        query: str,
        purpose: str = "",
        context: str = "",
    ) -> None:
        """Queue an authorized tool task and return immediately."""
        await self._delegate(
            params,
            kind=DelegationKind.TOOL,
            query=query,
            purpose=purpose,
            context=context,
        )

    async def _control(
        self,
        params: FunctionCallParams,
        *,
        operation: str,
        task_id: str,
        instruction: str = "",
    ) -> None:
        result = await dispatch_task_control(
            self._delegation_port,
            operation=operation,
            request=TaskControlRequest(
                task_id=str(task_id or "").strip(),
                instruction=str(instruction or "").strip(),
            ),
        )
        await params.result_callback(result)

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def get_background_task(self, params: FunctionCallParams, task_id: str) -> None:
        """Read a task brief without waiting for task completion."""
        await self._control(params, operation="status", task_id=task_id)

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def get_ready_results(
        self, params: FunctionCallParams, limit: int = 5
    ) -> None:
        """Read completed background results when the user asks to hear them."""
        if self._delegation_port is None:
            await params.result_callback({"results": []})
            return
        rows = await self._delegation_port.ready_results(
            limit=max(1, min(int(limit), 10))
        )
        results: list[dict[str, Any]] = []
        task_ids: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "").casefold()
            if status == "stale":
                continue
            task_id = str(row.get("task_id") or row.get("subagent_id") or "")
            if not task_id:
                continue
            task_ids.append(task_id)
            results.append(
                {
                    "task_id": task_id,
                    "title": str(row.get("title") or ""),
                    "status": status,
                    "summary": str(row.get("summary") or row.get("safe_error") or ""),
                    "source_count": int(row.get("source_count") or 0),
                    "agent_result": row.get("agent_result") or {},
                }
            )
        await params.result_callback({"results": results})
        for task_id in task_ids:
            try:
                await self._delegation_port.acknowledge_result(
                    task_id,
                    delivery_state="delivered",
                )
            except Exception:
                logger.debug("manual voice result acknowledgement failed", exc_info=True)

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def cancel_background_task(
        self, params: FunctionCallParams, task_id: str
    ) -> None:
        """Request cancellation of a specific background task."""
        await self._control(params, operation="cancel", task_id=task_id)

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def set_background_delivery(
        self, params: FunctionCallParams, task_id: str, auto_report: bool
    ) -> None:
        """Pause or resume automatic spoken reporting for one task."""
        await self._control(
            params,
            operation="delivery",
            task_id=task_id,
            instruction="auto" if auto_report else "manual",
        )

    @tool(cancel_on_interruption=False, timeout_secs=5)
    async def revise_background_task(
        self,
        params: FunctionCallParams,
        task_id: str,
        instruction: str,
    ) -> None:
        """Create a new requirement revision for a specific task."""
        await self._control(
            params,
            operation="revise",
            task_id=task_id,
            instruction=instruction,
        )


transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


def _apply_context_state(
    context: LLMContext,
    adapter: VoiceContextAdapter,
    state: VoiceContextState,
) -> None:
    adapter.apply(context, state)


def build_pipeline_steps(
    *,
    transport_input: FrameProcessor,
    stt: FrameProcessor,
    user_journal: FrameProcessor | None,
    user_aggregator: FrameProcessor,
    bridge: FrameProcessor,
    assistant_journal: FrameProcessor | None,
    tts: FrameProcessor,
    latency: FrameProcessor,
    transport_output: FrameProcessor,
    assistant_aggregator: FrameProcessor,
    result_delivery: FrameProcessor | None = None,
    generation_gate: FrameProcessor | None = None,
    ledger_relay: FrameProcessor | None = None,
) -> list[FrameProcessor]:
    """Assemble the main worker's pipeline in one testable place."""
    steps: list[FrameProcessor] = [transport_input, stt]
    if user_journal is not None:
        steps.append(user_journal)
    steps.extend([user_aggregator, bridge])
    if result_delivery is not None:
        steps.append(result_delivery)
    if assistant_journal is not None:
        steps.append(assistant_journal)
    steps.append(tts)
    if generation_gate is not None:
        steps.append(generation_gate)
    steps.append(latency)
    steps.append(transport_output)
    # The ledger relay must sit **strictly downstream of the transport**: only there
    # do frames arrive after the audio ahead of them has actually been written out,
    # which is what makes "this sentence started/finished playing" a fact rather
    # than an estimate (see app/voice/caption_ledger.py).
    if ledger_relay is not None:
        steps.append(ledger_relay)
    steps.append(assistant_aggregator)
    return steps


def bind_journal_to_tap(
    journal: VoiceTurnJournal, assistant_journal: FrameProcessor
) -> None:
    """Attach the journal's outbound channel to the assistant observation tap.

    Two things hang off this one binding, and both are silent when missing -- the
    call is a plain attribute assignment, so nothing fails until a call is due:

    * ``_publish`` forwards every durable envelope to the client over RTVI, which
      is what turns a persisted event into a live subtitle.
    * ``_retry_hook`` re-runs a generation that produced no text. It pushes
      ``LLMRunFrame`` **upstream** from the assistant tap, which sits immediately
      downstream of the bus bridge, so the frame walks back to the bridge and on
      to the tutor worker's LLM. Without the hook ``llm_failed`` can only give up:
      a provider hiccup ends the turn instead of retrying it.

    Kept as a named function so the wiring itself is assertable -- it was once
    dropped by an unrelated rewrite of this module and no test noticed, because
    every existing test exercised the journal, not the binding.
    """

    async def _publish(envelope: dict[str, Any]) -> None:
        try:
            await assistant_journal.push_frame(
                RTVIServerMessageFrame(
                    data={"type": "voice-event", "event": envelope}
                ),
                FrameDirection.DOWNSTREAM,
            )
        except Exception:
            logger.debug("voice event publish failed", exc_info=True)

    async def _rerun_generation() -> None:
        """Re-run the LLM on the existing context after a failed attempt."""
        await assistant_journal.push_frame(LLMRunFrame(), FrameDirection.UPSTREAM)

    journal._publish = _publish
    journal._retry_hook = _rerun_generation


async def route_pipeline_error(journal: VoiceTurnJournal, frame: Any) -> None:
    """Send one failed pipeline frame to the recovery path that owns its stage.

    An LLM failure is not merely reported: the turn is still open and still has
    no answer, so it takes the retry/failure path, which either re-runs the
    generation or finalizes the turn with a retryable reason. Every other stage
    is a plain report -- ASR and TTS own their own reconnect and retry loops and
    must not be double-driven from here.
    """
    stage = _stage_for_processor(getattr(frame, "processor", None))
    message = str(getattr(frame, "error", "") or "pipeline error")
    if stage == STAGE_LLM:
        await journal.llm_failed(message)
        return
    await journal.processor_error(
        stage,
        message,
        retryable=not bool(getattr(frame, "fatal", False)),
    )


# 客户端打断 id 的保留窗口。它只需要覆盖"重连重放 + 用户连点"这段几秒到几十秒的
# 窗口，不需要在整个通话期间记账。
MAX_TRACKED_INTERRUPT_IDS = 64


class InterruptIdDeduplicator:
    """同一个 ``interruptId`` 只打断一次。

    为什么需要：客户端在数据通道上重发打断（重连重放、用户连点"停止播报"，或
    数据通道与 HTTP 兜底同时到达）时，每一次处理都会推进 generation 闸门。多余的
    那一次会把**已经开始朗读的下一轮回答**判成上一代的残留音频而丢弃 —— 表现就是
    "打断一次之后导师变哑巴"。因此按客户端给的 id 去重，而不是按到达次数。
    """

    def __init__(self, max_ids: int = MAX_TRACKED_INTERRUPT_IDS) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_ids = max(1, int(max_ids))

    def accept(self, interrupt_id: str) -> bool:
        """第一次见到这个 id 返回 True；重复的 id 返回 False。"""
        key = str(interrupt_id or "").strip()
        if not key:
            # 不带 id 的旧客户端无法去重：只能按"每次都是新的打断"处理，否则
            # 真正的第二次打断会被误吞。
            return True
        if key in self._seen:
            return False
        self._seen[key] = None
        while len(self._seen) > self._max_ids:
            self._seen.popitem(last=False)
        return True


def _int_or_none(value: Any) -> int | None:
    """Best-effort int for a client-supplied field; garbage becomes ``None``."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def handle_learngraph_client_message(
    *,
    message: Any,
    journal: VoiceTurnJournal | None,
    generation_gate: VoiceGenerationGate,
    interrupt_bot: Callable[[], Awaitable[None]],
    interrupts: InterruptIdDeduplicator,
) -> None:
    """Route the browser's ``learngraph-*`` RTVI messages, the call's control plane.

    Kept as a module-level function rather than an inline closure so the routing
    itself is assertable: this is the only place where a client message becomes a
    journal write or a barge-in, and both stay silent when a branch is dropped.

    Three message types, one rule each:

    * ``learngraph-typed-turn`` -- announce the idempotency key of an utterance the
      control plane already opened a turn for.
    * ``learngraph-playback`` -- the browser's own playback position.  **体验用**:
      it feeds the replay cursor and audit only.  It must never take part in
      finalize/memory 判定 -- the browser can be muted, in a background tab or
      throttled, so "played" is not "heard".
    * ``learngraph-interrupt`` -- barge-in, deduplicated by ``interruptId``.
    """
    message_type = str(getattr(message, "type", "") or "")
    payload = getattr(message, "data", None)
    data = payload if isinstance(payload, dict) else {}

    if message_type == VOICE_TYPED_TURN_MESSAGE:
        # The browser announces the idempotency key it is about to type with
        # *before* ``send-text`` on the same ordered data channel. Without it the
        # worker cannot tell which turn the control plane already opened for this
        # utterance, so one typed message would become the client's turn plus a
        # worker-opened one.
        if journal is not None:
            journal.expect_typed_turn(
                str(data.get("client_message_id") or ""),
                str(data.get("text") or ""),
            )
        return

    if message_type == VOICE_PLAYBACK_MESSAGE:
        if journal is not None:
            await journal.playback_ack(
                sentence_seq=_int_or_none(data.get("sentence_seq")),
                played_ms=_int_or_none(data.get("played_ms")) or 0,
                phase=str(data.get("phase") or ""),
                generation_id=_int_or_none(data.get("generation_id")),
            )
        return

    if message_type != VOICE_INTERRUPT_MESSAGE:
        return

    interrupt_id = str(data.get("interruptId") or "")
    if not interrupts.accept(interrupt_id):
        logger.debug("Duplicate barge-in ignored: interruptId={}", interrupt_id)
        return
    logger.info("Client requested barge-in over RTVI; interrupting the bot")
    generation_gate.invalidate()
    await interrupt_bot()


def _voice_peer_connection(runner_args: Any) -> Any:
    """Return the aiortc peer connection behind the runner arguments."""

    connection = getattr(runner_args, "webrtc_connection", None)
    return getattr(connection, "pc", None)


def _candidate_address(candidate: Any) -> str | None:
    host = getattr(candidate, "host", None)
    if not host:
        return None
    port = getattr(candidate, "port", None)
    return f"{host}:{port}" if port else str(host)


def describe_ice_path(pc: Any) -> dict[str, Any] | None:
    """Describe the ICE candidate pair a connected peer actually settled on.

    Walks aiortc -> aioice internals deliberately: neither layer exposes the
    selected pair, and without it there is no way to answer "did this call go
    direct or through the relay?" after the fact.  Returns ``None`` while the
    pair is still undecided.
    """

    if pc is None:
        return None
    try:
        connection = pc.sctp.transport.transport._connection
    except Exception:
        return None
    pairs = list(getattr(connection, "_check_list", None) or [])
    if not pairs:
        return None
    selected = next((item for item in pairs if getattr(item, "nominated", False)), None)
    if selected is None:
        selected = next(
            (
                item
                for item in pairs
                if str(getattr(item, "state", "")).upper().endswith("SUCCEEDED")
            ),
            None,
        )
    if selected is None:
        return None
    local = getattr(selected, "local_candidate", None)
    remote = getattr(selected, "remote_candidate", None)
    local_type = str(getattr(local, "type", "") or "")
    remote_type = str(getattr(remote, "type", "") or "")
    # The candidate's own ``transport`` is the SDP string ("udp"); the pair's
    # ``protocol`` is an aioice enum whose repr is worthless in an event payload.
    protocol = str(getattr(local, "transport", "") or "").strip()
    if protocol not in {"udp", "tcp"}:
        protocol = "udp"
    return {
        "local_type": local_type or None,
        "remote_type": remote_type or None,
        "local_address": _candidate_address(local),
        "remote_address": _candidate_address(remote),
        "protocol": protocol,
        # "relayed" is the one bit that matters to an operator: a relayed call
        # costs TURN bandwidth, an unrelayed one does not.
        "relayed": "relay" in {local_type, remote_type},
        "label": f"{local_type or '?'}↔{remote_type or '?'}",
    }


async def report_ice_path(
    journal: VoiceTurnJournal,
    pc: Any,
    *,
    attempts: int = 10,
    interval: float = 0.5,
) -> dict[str, Any] | None:
    """Emit ``session.ice`` as soon as the selected pair is known."""

    for _ in range(max(1, attempts)):
        path = describe_ice_path(pc)
        if path is not None:
            try:
                await journal.session_ice(path)
            except Exception:
                logger.debug("session.ice emit failed", exc_info=True)
            return path
        await asyncio.sleep(interval)
    return None


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting ChatGPT-style CN bot")
    if timeline_enabled():
        logger.info(
            "VOICE_TIMELINE_DEBUG 已开启：本轮通话将输出 [VT] 单点计时时间线"
            "（锚点=VAD 判定用户开口）"
        )

    session_id = str(getattr(runner_args, "session_id", "") or "")
    providers = _resolve_voice_provider_config(session_id)
    asr_provider = providers.asr if getattr(providers.asr, "available", False) else None
    llm_provider = providers.llm if getattr(providers.llm, "available", False) else None
    tts_provider = providers.tts if getattr(providers.tts, "available", False) else None

    from app.core.config import get_settings

    settings = get_settings()
    journal = (
        VoiceTurnJournal(
            session_id,
            idle_timeout_secs=settings.voice_turn_idle_timeout,
            retry_attempts=settings.voice_llm_retry_attempts,
            retry_base_delay=settings.voice_llm_retry_base_delay,
            retry_max_delay=settings.voice_llm_retry_max_delay,
        )
        if session_id
        else None
    )
    context_adapter = (
        VoiceContextAdapter(session_id, SYSTEM_INSTRUCTION) if session_id else None
    )
    context_state = (
        await asyncio.to_thread(context_adapter.load)
        if context_adapter is not None
        else VoiceContextState(None, ({"role": "system", "content": SYSTEM_INSTRUCTION},))
    )
    last_context_version = context_state.version

    stt = DashScopeSTTService(
        api_key=str(
            getattr(asr_provider, "api_key", None)
            or os.getenv("DASHSCOPE_API_KEY", "")
        ),
        journal=journal,
        settings=DashScopeSTTService.Settings(
            model=str(
                getattr(asr_provider, "model_id", None)
                or os.getenv("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash-realtime")
            ),
            ws_url=str(
                getattr(asr_provider, "base_url", None)
                or os.getenv(
                    "DASHSCOPE_ASR_WS_URL",
                    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                )
            ),
            silence_ms=int(
                getattr(asr_provider, "silence_ms", None)
                or os.getenv("DASHSCOPE_ASR_SILENCE_MS", "400")
            ),
        ),
    )

    llm_api_key = str(
        getattr(llm_provider, "api_key", None)
        or os.getenv("VOICE_LLM_API_KEY", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
    )
    if not llm_api_key.strip():
        # ``_resolve_voice_provider_config`` returns an unusable Provider object
        # (rather than raising) when the session's pinned model is gone from the
        # Provider catalog or the Provider was disabled.  Collapsing that into
        # ``llm_provider = None`` used to hand an empty key to the OpenAI client,
        # which then failed deep inside the SDK with a misleading "Missing
        # credentials" -- and because that happened before the runner started, the
        # whole pipeline (ASR + LLM + TTS) never came up.  The user saw a call that
        # "connected" and then stayed silent forever.  Report the real reason on
        # the durable channel and fail loudly instead.
        reason = (
            str(getattr(providers.llm, "reason", "") or "").strip()
            or "语音会话没有解析到可用的模型 Provider"
        )
        logger.error("Voice pipeline cannot start: {}", reason)
        if journal is not None:
            await journal.processor_error(
                STAGE_LLM, reason, retryable=False, degraded=True
            )
        raise RuntimeError(f"Voice LLM provider unavailable: {reason}")

    llm_base_url = str(
        getattr(llm_provider, "base_url", None)
        or os.getenv("VOICE_LLM_BASE_URL", "")
        or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    )
    llm_model = str(
        getattr(llm_provider, "model_id", None)
        or os.getenv("VOICE_LLM_MODEL", "")
        or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    )
    # 前台恒 off：pipecat 的 OpenAI 服务自己没有思考开关，``Settings.extra`` 是唯一能
    # 落到请求体上的通道，所以这里必须把厂商自己的"关闭"字段显式拼出来——而且只能放
    # 进 ``extra_body``（见 ``LlmThinkingOff.settings_extra``：摊在顶层会被 SDK 的签名
    # 校验拦下，每一轮都抛 TypeError，整通没有回答）。
    thinking_off = resolve_llm_thinking_off(
        provider_type=getattr(llm_provider, "provider_type", None),
        base_url=llm_base_url,
        model_id=llm_model,
        capabilities=getattr(llm_provider, "capabilities", None),
    )
    if thinking_off.expressible:
        logger.info(
            "Voice LLM thinking pinned off: mechanism={} fields={}",
            thinking_off.mechanism,
            thinking_off.fields,
        )
    else:
        # 有一个方言表达不出"关闭思考"（例如自定义网关上的模型，或能力快照缺失）。
        # 口径：**照常服务**（这个模型上就按厂商默认走，通常是思考开启），但必须
        # 让用户在界面上看到代价——首字延迟会明显变长。走 ``processor.notice``
        # 而不是 ``processor.error``：通话不降级、不强制文字输入、麦克风不静音。
        notice = VOICE_THINKING_OFF_UNAVAILABLE_NOTICE.format(model=llm_model)
        logger.warning("Voice LLM thinking cannot be disabled: {}", notice)
        if journal is not None:
            await journal.notice(STAGE_LLM, "thinking_off_unavailable", notice)

    if thinking_off.expressible and thinking_off.suspect:
        # L2：上一次真机实测已经证明这套机制关不掉（或认出了厂商源、却只能靠通用兜底
        # 字段）。会话开始就把代价说清楚，而不是等用户听出慢两秒。
        ineffective_notice = VOICE_THINKING_OFF_INEFFECTIVE_NOTICE.format(model=llm_model)
        logger.warning(
            "Voice LLM thinking off looks ineffective: mechanism={} source={} reason={}",
            thinking_off.mechanism,
            thinking_off.source,
            thinking_off.reason or "-",
        )
        if journal is not None:
            await journal.notice(
                STAGE_LLM,
                VOICE_THINKING_OFF_INEFFECTIVE_NOTICE_CODE,
                ineffective_notice,
            )

    async def report_thinking_off_verdict(verdict: ThinkingOffVerdict) -> None:
        """L3 的落账点：意图 vs 实测不符就喊出来，并把结论回写 provider 快照（L2）。"""

        if verdict.violated:
            logger.warning(
                "Voice thinking=off was ignored by the upstream: {} "
                "(model={}, mechanism={}, source={})",
                verdict.detail,
                llm_model,
                thinking_off.mechanism or "-",
                thinking_off.source,
            )
            if journal is not None:
                await journal.notice(
                    STAGE_LLM,
                    VOICE_THINKING_OFF_INEFFECTIVE_NOTICE_CODE,
                    VOICE_THINKING_OFF_INEFFECTIVE_NOTICE.format(model=llm_model),
                )
        else:
            logger.info(
                "Voice thinking=off honoured: {} (model={})", verdict.detail, llm_model
            )
        # 写库是"真机结论 → 数据"的闭环（L2）：下一次解析会优先采用它。
        # 线程里做，且失败只留日志——观测绝不能拖慢或打断通话。
        await asyncio.to_thread(
            record_thinking_off_observation,
            provider_id=getattr(llm_provider, "provider_id", None),
            model_id=llm_model,
            verified=not verdict.violated,
            mechanism=thinking_off.mechanism,
            surface="voice",
            reasoning_tokens=verdict.reasoning_tokens,
            thinking_time_ms=verdict.thinking_time_ms,
            detail=verdict.detail,
        )

    llm = OpenAILLMService(
        api_key=llm_api_key,
        base_url=llm_base_url,
        settings=OpenAILLMService.Settings(
            model=llm_model,
            system_instruction=SYSTEM_INSTRUCTION,
            # 表达不出关闭时就是空 dict：宁可不发字段（= 该模型按自己的默认走），
            # 也不能把别的方言的字段塞给它——那会直接让上游 400、整通没有回答。
            extra=thinking_off.settings_extra,
        ),
    )

    tts = VolcengineTTSService(
        api_key=str(
            getattr(tts_provider, "api_key", None)
            or os.getenv("VOLC_TTS_API_KEY", "")
        ),
        journal=journal,
        # 句子身份里的 generation_id 必须来自**同一个**闸门实例：它是"这一句属于
        # 哪一代音频"的唯一权威，前端据此丢弃被打断那一代的迟到 marker。
        generation_source=lambda: generation_gate.generation,
        settings=VolcengineTTSService.Settings(
            voice_type=str(
                getattr(getattr(tts_provider, "options", None), "voice_type", None)
                or os.getenv(
                    "VOLC_TTS_VOICE_TYPE",
                    "ICL_uranus_zh_female_heainainai_tob",
                )
            ),
            model=str(
                getattr(tts_provider, "model_id", None)
                or os.getenv("VOLC_TTS_MODEL", "seed-tts-2.0-standard")
            ),
            emotion=str(
                getattr(getattr(tts_provider, "options", None), "emotion", None)
                or os.getenv("VOLC_TTS_EMOTION", "")
            ),
            endpoint=str(
                getattr(tts_provider, "base_url", None)
                or os.getenv(
                    "VOLC_TTS_ENDPOINT",
                    "wss://openspeech.bytedance.com/api/v3/tts/bidirection",
                )
            ),
            resource_id=str(
                getattr(tts_provider, "resource_id", None)
                or os.getenv("VOLC_TTS_RESOURCE_ID", "")
            ),
        ),
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    from app.core.config import get_settings

    settings = get_settings()
    vad_params = (
        VADParams(stop_secs=settings.voice_vad_stop_secs)
        if settings.voice_vad_stop_secs
        else None
    )
    smart_turn_overrides = {
        key: value
        for key, value in (
            ("stop_secs", settings.voice_smart_turn_stop_secs),
            ("pre_speech_ms", settings.voice_smart_turn_pre_speech_ms),
        )
        if value
    }
    stop_strategies = (
        [
            TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=LocalSmartTurnAnalyzerV3(
                    params=SmartTurnParams(**smart_turn_overrides)
                )
            )
        ]
        if smart_turn_overrides
        else None
    )
    if vad_params or smart_turn_overrides:
        logger.info(
            "Voice turn tuning: vad_stop_secs={} smart_turn={} turn_stop_timeout={}",
            settings.voice_vad_stop_secs,
            smart_turn_overrides or "pipecat-default",
            settings.voice_turn_stop_timeout,
        )

    initial_messages = [dict(message) for message in context_state.messages]
    if not initial_messages:
        initial_messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    context = LLMContext(messages=initial_messages)
    turn_strategy = AdaptiveUserTurnStartStrategy()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=vad_params),
            user_turn_stop_timeout=settings.voice_turn_stop_timeout,
            user_turn_strategies=UserTurnStrategies(
                start=[turn_strategy],
                stop=stop_strategies,
            ),
        ),
    )

    latency = LatencyPercentileProcessor(
        # 只有"真的要求了关闭思考"才谈得上违约。表达不出时上游按自己的默认走，
        # 那种情况上面的 notice 已经说清楚了，不该在这里重复报警。
        thinking_off_intent=thinking_off.expressible,
        on_thinking_off_verdict=report_thinking_off_verdict,
    )
    generation_gate = VoiceGenerationGate()
    bridge = BusBridgeProcessor(
        bus=runner.bus,
        worker_name=MAIN_WORKER_NAME,
        name=f"{MAIN_WORKER_NAME}::BusBridge",
    )

    user_journal = VoiceJournalProcessor(journal, role="user") if journal else None
    assistant_journal = (
        VoiceJournalProcessor(journal, role="assistant") if journal else None
    )

    ensure_coordinator_delegation_port_factory()
    delegation_port = resolve_delegation_port(session_id) if session_id else None
    agent = TutorAgent(
        llm=llm,
        voice_session_id=session_id,
        delegation_port=delegation_port,
    )
    result_delivery = (
        VoiceResultDeliveryProcessor(agent=agent, result_port=delegation_port)
        if delegation_port is not None
        else None
    )

    pipeline_steps = build_pipeline_steps(
        transport_input=transport.input(),
        stt=stt,
        user_journal=user_journal,
        user_aggregator=user_aggregator,
        bridge=bridge,
        assistant_journal=assistant_journal,
        tts=tts,
        result_delivery=result_delivery,
        generation_gate=generation_gate,
        latency=latency,
        transport_output=transport.output(),
        assistant_aggregator=assistant_aggregator,
        # 句级账本的写入者：坐在输出传输下游，只在"这一句的音频真的写出去了"之后
        # 才落账（未听到的文本因此进不了账本、转录与记忆）。
        ledger_relay=VoiceLedgerRelay(journal=journal) if journal is not None else None,
    )
    pipeline = Pipeline(insert_timeline_probes(pipeline_steps))

    worker = PipelineWorker(
        pipeline,
        name=MAIN_WORKER_NAME,
        # 字幕交给官方的句级路径：``AggregatedTextFrame`` → ``bot-output{new}``（整句先
        # 到、灰着），``TTSTextFrame`` → ``bot-output{completed}``（这一句播完时点亮）。
        # 前者在开口前被 observer 扣住，后者随音频队列被真实播放节奏放行，前端不需要
        # 任何播放时钟。``bot-tts-text`` 只是排障用的旁路，保持关闭以免多一路流量。
        rtvi_observer_params=RTVIObserverParams(
            bot_output_enabled=True,
            bot_tts_enabled=False,
            # 把服务端 Silero 的 VAD 起止（`vad-user-started-speaking` /
            # `vad-user-stopped-speaking`）也送给前端：延迟面板要"用户声音只用 VAD"的
            # 原始信号 —— 它是模型判定，且由 user aggregator 直接广播，独立于回合策略
            # 与回合终结（pipecat 的 message docstring 原话）。前端只消费 stop 那条
            # 作标注（"人声停止"≠"话说完了"），开轮权仍归账本的 `user.started`。
            vad_user_speaking_enabled=True,
        ),
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        processor_unusable_policy=ProcessorUnusablePolicy.CONTINUE,
    )

    if journal is not None and assistant_journal is not None:
        bind_journal_to_tap(journal, assistant_journal)

    await runner.add_workers(agent, worker)

    current_task = asyncio.current_task()
    handle = VoiceRunnerHandle(
        voice_session_id=session_id,
        chat_session_id=str(getattr(providers.session, "chat_session_id", "") or ""),
        owner_user_id=str(getattr(providers.session, "owner_user_id", "") or ""),
        processors=[stt, tts, generation_gate, worker, agent] + ([result_delivery] if result_delivery is not None else []),
        transports=[transport],
        task=current_task,
    )

    async def _stop_runner(reason: str) -> None:
        await runner.cancel(reason)

    handle.stop = _stop_runner
    if session_id:
        register_runner(handle)

    close_reason = "runner_finished"
    disconnect_emitted = False
    watchdog: VoiceControlWatchdog | None = None
    ice_path_task: asyncio.Task[Any] | None = None

    try:
        rtvi = worker.rtvi
    except Exception:
        rtvi = None

    if rtvi is not None:
        # 每个语音会话一份打断去重表：去重窗口只需覆盖同一次打断在数据通道上的重发，
        # 跨会话共享既没有必要，也会把 id 空间搅在一起。
        interrupts = InterruptIdDeduplicator()

        @rtvi.event_handler("on_client_message")
        async def on_client_message(rtvi_processor, message):
            await handle_learngraph_client_message(
                message=message,
                journal=journal,
                generation_gate=generation_gate,
                interrupt_bot=rtvi_processor.interrupt_bot,
                interrupts=interrupts,
            )

        async def _fast_interrupt() -> None:
            generation_gate.invalidate()
            await rtvi.interrupt_bot()

        turn_strategy.set_interrupt_callback(_fast_interrupt)

    if journal is not None and session_id:

        async def _on_interrupt(_event: Mapping[str, Any]) -> None:
            generation_gate.invalidate()
            if rtvi is not None:
                await rtvi.interrupt_bot()

        async def _on_model_changed(event: Mapping[str, Any]) -> None:
            nonlocal context_state, last_context_version
            if context_adapter is None:
                return
            handle_now = await asyncio.to_thread(load_session_row, session_id)
            if handle_now is None or handle_now.status != "active":
                return

            refreshed = await asyncio.to_thread(context_adapter.load)
            if refreshed.version != last_context_version:
                _apply_context_state(context, context_adapter, refreshed)
                context_state = refreshed
                last_context_version = refreshed.version
                logger.info(
                    "Voice context applied at next turn boundary: version={} source={}",
                    refreshed.version,
                    refreshed.source,
                )

            repointed = False
            llm_service = getattr(agent, "llm", None)
            model = handle_now.model_id or getattr(llm_provider, "model_id", None)
            # 换模型必须连"关闭思考"的字段一起换：该方言会随 Provider/模型变
            # （DeepSeek 是 thinking:{type}，DashScope 是 enable_thinking）。
            pin = await asyncio.to_thread(_resolve_voice_provider_config, session_id)
            pin_llm = pin.llm if getattr(pin.llm, "available", False) else None
            pinned_model = str(
                getattr(pin_llm, "model_id", None) or model or ""
            )
            off = resolve_llm_thinking_off(
                provider_type=getattr(pin_llm, "provider_type", None),
                base_url=str(
                    getattr(pin_llm, "base_url", None)
                    or os.getenv("VOICE_LLM_BASE_URL", "")
                    or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
                ),
                model_id=pinned_model,
                capabilities=getattr(pin_llm, "capabilities", None),
            )
            if llm_service is not None and pinned_model:
                try:
                    settings_obj = getattr(llm_service, "_settings", None)
                    if settings_obj is not None and hasattr(settings_obj, "model"):
                        settings_obj.model = pinned_model
                        if pin_llm is not None:
                            # 新方言表达不出关闭时置空——把上一家的字段继续发过去会让
                            # 新上游 400，整通没有回答，比"这次换模型仍在思考"更糟。
                            # 解析不出新 Provider（被停用/删除）时**不动** extra：正在跑
                            # 的那条链路用的仍是它自己方言的字段。
                            settings_obj.extra = off.settings_extra
                        repointed = True
                except Exception:
                    logger.debug("voice model re-pin failed", exc_info=True)
            await journal.context_updated(
                context_change_report(
                    event.get("payload"),
                    repointed=repointed,
                    effective_model_id=pinned_model or model,
                    applied_epoch=handle_now.session_epoch,
                ),
                # 一条请求只该有一条回执：request_id 以触发事件为键，重复处理
                # （游标回退、两个 worker 先后读到同一条）只会命中同一行。
                request_id=f"context.report:{event.get('event_id') or handle_now.session_epoch}",
            )
            if llm_service is not None and pin_llm is not None and not off.expressible:
                # 注意顺序：提醒必须在 context.updated **之后**发。客户端收到"换模型
                # 成功"的回执时会先清掉上一家模型的提示，再按本条建立新提示——反过来的
                # 话，刚发出的提醒会被那条清空覆盖掉。
                notice = VOICE_THINKING_OFF_UNAVAILABLE_NOTICE.format(
                    model=pinned_model or model or "未知"
                )
                logger.warning("Voice model switch: {}", notice)
                if journal is not None:
                    await journal.notice(
                        STAGE_LLM, "thinking_off_unavailable", notice
                    )


        async def _on_close(reason: str) -> None:
            nonlocal close_reason
            close_reason = reason or "session_closed"
            logger.info("voice session %s closing: %s", session_id, close_reason)
            generation_gate.invalidate()
            if journal is not None:
                # Settle the open exchange *before* the loop is cancelled: a call
                # that ends mid-answer (or mid-typing) must not leave a turn
                # ``accepted`` forever, which is what kept the question out of the
                # transcript and out of memory.
                with contextlib.suppress(Exception):
                    await journal.close_open_turns_on_session_end()
            await runner.cancel(close_reason)

        watchdog = VoiceControlWatchdog(
            voice_session_id=session_id,
            on_interrupt=_on_interrupt,
            on_model_changed=_on_model_changed,
            on_close=_on_close,
            on_reconcile=(
                journal.reconcile_stale_turns if journal is not None else None
            ),
        )

        async def _cleanup_watchdog() -> None:
            await watchdog.stop()

        watchdog.cleanup = _cleanup_watchdog  # type: ignore[method-assign]
        handle.register_processor(watchdog)
        watchdog.start()

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker_ref, frame):
        if journal is None:
            return
        await route_pipeline_error(journal, frame)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        nonlocal ice_path_task
        logger.info("Client connected")
        if result_delivery is not None:
            await result_delivery.start()
        if journal is not None:
            epoch = getattr(providers.session, "session_epoch", None)
            await journal.session_ready(epoch=epoch)
            # The selected candidate pair is only decided a moment after the
            # transport reports the peer as connected, so this is a bounded
            # background poll rather than a read at connect time.
            ice_path_task = asyncio.create_task(
                report_ice_path(journal, _voice_peer_connection(runner_args))
            )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        nonlocal disconnect_emitted
        logger.info("Client disconnected")
        if disconnect_emitted:
            return
        disconnect_emitted = True
        if session_id and journal is not None:
            try:
                from app.voice.events import mark_session_reconnecting

                await asyncio.to_thread(mark_session_reconnecting, session_id)
                await journal.session_reconnecting(attempt=1, delay_ms=0)
            except Exception:
                logger.debug("voice reconnect marker failed", exc_info=True)
        if result_delivery is not None:
            await result_delivery.stop()
        await runner.cancel("client_disconnected")

    try:
        await runner.run()
    finally:
        if ice_path_task is not None and not ice_path_task.done():
            ice_path_task.cancel()
        if result_delivery is not None:
            await result_delivery.stop()
        if watchdog is not None:
            await watchdog.stop()
        if session_id and get_runner(session_id) is handle:
            unregister_runner(session_id)
        if journal is not None and session_id:
            state = await asyncio.to_thread(load_session_row, session_id)
            if state is not None and not state.active:
                await journal.session_closed(reason=close_reason)
            elif state is not None and not disconnect_emitted:
                await journal.session_reconnecting(attempt=1, delay_ms=0)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point compatible with Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    try:
        await run_bot(transport, runner_args)
    except asyncio.CancelledError:
        # A cancelled bot is a normal ending, not a failure.  This coroutine is the
        # ASGI background task of ``POST /api/v1/voice/sessions/{id}/api/offer``, so
        # the two things that cancel it are both deliberate:
        #
        # * a reconnect replaced this pipeline -- ``runner_registry`` cancels the
        #   registered task once the replacement has taken over the session;
        # * uvicorn is shutting down and cancels its pending background tasks
        #   (``Waiting for background tasks to complete``).
        #
        # Letting the CancelledError escape made uvicorn print a full
        # ``ERROR: Exception in ASGI application`` traceback for both of them, which
        # buried the errors that do matter.  The teardown itself already ran: it
        # lives in ``run_bot``'s ``finally``.
        logger.debug("Voice bot task cancelled; pipeline already torn down")


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()

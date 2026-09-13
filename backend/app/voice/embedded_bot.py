#
# LearnGraph 实时语音通话 —— Pipecat 官方 Workers 多-agent 拓扑
#
# 拓扑（对齐官方 examples/multi-worker/local-handoff）：
#   主 worker "learngraph"：transport.input → STT → 用户聚合器 → BusBridgeProcessor
#                            → TTS → transport.output → 助手聚合器
#   子 worker "tutor"     ：LLMWorker（Pipeline([llm])，bridged=()），只跑 LLM；
#                            用户侧上下文经总线送进去，生成文本经总线送回来交主 worker 的 TTS 朗读。
# 好处：把「谁在说话 / 何时打断 / 音频进出」与「谁生成内容」解耦，
#       后续加检索或长任务 sidecar worker 只需 add_workers + @tool，不必碰音频管线。
#
# STT : 阿里 DashScope 实时 ASR（qwen3-asr-flash-realtime，Manual 模式）— 自研适配层
# LLM : 会话当前配置的模型（OpenAI 兼容通道）
# TTS : 火山引擎语音合成 2.0（双向流式，seed-tts-2.0）— 自研适配层
# 传输: SmallWebRTC（免 Daily key）
#

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.bus import BusBridgeProcessor
from pipecat.frames.frames import (
    Frame,
    MetricsFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
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
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.llm import LLMWorker
from pipecat.workers.runner import WorkerRunner

from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)

from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy
from app.voice.embedded_dashscope_stt import DashScopeSTTService
from app.voice.embedded_timeline import insert_timeline_probes, timeline_enabled
from app.voice.embedded_volcengine_tts import VolcengineTTSService

load_dotenv(override=True)

# 主 worker 名：BusBridgeProcessor 靠它把总线上的帧流与这条管线对上。
MAIN_WORKER_NAME = "learngraph"
# 子 agent 名：将来加检索/任务 worker 时，用 activate_worker(name, ...) 做交接。
TUTOR_WORKER_NAME = "tutor"
# 前端手动打断用的 RTVI 自定义消息类型（须与
# frontend/src/features/voice/voice-session-controller.tsx 的
# VOICE_INTERRUPT_MESSAGE 保持一致）。
VOICE_INTERRUPT_MESSAGE = "learngraph-interrupt"

SYSTEM_INSTRUCTION = (
    "你是一个友好的中文语音助手。你的回答会被直接朗读出来，"
    "所以请使用自然、口语化的中文，避免 emoji、特殊符号、列表、Markdown 等无法朗读的内容。"
    "回答要简洁、亲切，像真人对话一样。"
)


@dataclass(frozen=True, slots=True)
class _VoiceProviderConfig:
    """Resolved, non-persistent configuration for one voice pipeline."""

    asr: object | None = None
    llm: object | None = None
    tts: object | None = None


def _resolve_voice_provider_config(session_id: str) -> _VoiceProviderConfig:
    """Resolve the same workspace Providers used by the normal chat API.

    The embedded runner is launched as a background task, so it cannot rely on
    the request-scoped dependency objects from the HTTP route.  Resolve the
    in-memory voice session to its workspace, then use the normal factory
    selectors.  The returned adapter objects contain decrypted secrets only in
    process memory and are never serialized to the browser or logs.
    """

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
        from app.voice.service import VoiceSessionService

        with VoiceSessionService._lock:
            session = VoiceSessionService._sessions.get(session_id)
        if session is None:
            logger.warning("Voice session {} was not found while resolving Providers", session_id)
            return _VoiceProviderConfig()
        with SessionLocal() as db:
            settings = get_settings()
            return _VoiceProviderConfig(
                # Pin every adapter to the selection captured when the voice
                # session was created.  Falling back to the workspace default
                # here made a voice call silently use a different model than
                # the chat session requested.
                asr=realtime_asr_provider_for_workspace(
                    db, session.workspace_id, settings,
                ),
                llm=model_provider_for_workspace(
                    db, session.workspace_id, settings,
                    model_id=session.model_id,
                    provider_id=session.provider_id,
                ),
                tts=tts_provider_for_workspace(db, session.workspace_id, settings),
            )
    except Exception:
        logger.exception("Failed to resolve workspace Providers for voice session %s", session_id)
        return _VoiceProviderConfig()


class LatencyPercentileProcessor(FrameProcessor):
    """滚动收集每轮 LLM 的 TTFB（首字节延迟），计算 p90 / p95 / p99 并打印到日志。

    挂载在 pipeline 中，监听 MetricsFrame，从中取出 TTFBMetricsData 的 value（秒）。
    按最近 WINDOW 条样本滚动计算百分位，阈值不满足时打印仅样本数提示。
    非 MetricsFrame 原样透传。
    """

    WINDOW = 50  # 滚动窗口：最近 50 条 TTFB 样本

    def __init__(self):
        super().__init__()
        self._samples: list[float] = []  # 秒

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
            "TTFB latency (window={}): p90={:.0f}ms p95={:.0f}ms p99={:.0f}ms (min={:.0f}ms max={:.0f}ms)",
            len(window),
            p90_ms,
            p95_ms,
            p99_ms,
            sv[0] * 1000,
            sv[-1] * 1000,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, MetricsFrame):
            # 每轮可能有多条 TTFB（多服务），全部收集
            got_ttfb = False
            for data in frame.data:
                if isinstance(data, TTFBMetricsData) and data.value is not None:
                    self._samples.append(float(data.value))
                    got_ttfb = True
            if got_ttfb:
                if len(self._samples) >= 3:
                    self._report()
                else:
                    logger.info(
                        "TTFB samples so far: {} (need >=3 to compute p90/p95/p99)",
                        len(self._samples),
                    )

        await self.push_frame(frame, direction)


class TutorAgent(LLMWorker):
    """语音导师 agent —— 只跑 LLM，音频进出全部交给主 worker。

    - ``bridged=()``：让 PipelineWorker 用总线边缘处理器包住这条管线；用户侧
      上下文从主 worker 经总线进来，生成的文本帧再回主 worker 由 TTS 朗读。
    - ``active=True``：单 agent 场景常驻待命。（官方 local-handoff 是多 agent
      轮值，故默认 ``active=False``，由 ``activate_worker`` 接管；我们不轮值。）
    - `@tool` 装饰的方法会被自动收集并注册给 LLM（沿 MRO 收集，子类覆盖优先）；
      长任务工具用 ``@tool(cancel_on_interruption=False)``，避免被用户插话打断，
      这也正是后续「派子代理跑搜索」要挂的钩子。
    - ``defer_tool_frames`` 保持默认 True：工具执行期间入队的帧延后到工具全部
      结束再放行，避免 TTS 在工具还没返回时就开始念陈旧内容。
    """

    def __init__(self, *, llm: OpenAILLMService, name: str = TUTOR_WORKER_NAME):
        super().__init__(
            name,
            llm=llm,
            bridged=(),
            active=True,
        )


transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting ChatGPT-style CN bot")
    if timeline_enabled():
        logger.info(
            "VOICE_TIMELINE_DEBUG 已开启：本轮通话将输出 [VT] 单点计时时间线"
            "（锚点=VAD 判定用户开口）"
        )

    providers = _resolve_voice_provider_config(
        str(getattr(runner_args, "session_id", "") or "")
    )
    asr_provider = providers.asr if getattr(providers.asr, "available", False) else None
    llm_provider = providers.llm if getattr(providers.llm, "available", False) else None
    tts_provider = providers.tts if getattr(providers.tts, "available", False) else None

    stt = DashScopeSTTService(
        api_key=str(
            getattr(asr_provider, "api_key", None)
            or os.getenv("DASHSCOPE_API_KEY", "")
        ),
        settings=DashScopeSTTService.Settings(
            model=str(
                getattr(asr_provider, "model_id", None)
                or os.getenv("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash-realtime")
            ),
            ws_url=str(
                getattr(asr_provider, "base_url", None)
                or os.getenv("DASHSCOPE_ASR_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
            ),
            silence_ms=int(
                getattr(asr_provider, "silence_ms", None)
                or os.getenv("DASHSCOPE_ASR_SILENCE_MS", "400")
            ),
        ),
    )

    # 会话当前配置的模型 → 统一走 OpenAI 兼容通道（2026-09-11 拍板）。
    # base_url / api_key / model 三项全部取自 workspace Provider 解析结果
    # （已按会话创建时钉住的 provider_id/model_id 解析），环境变量仅作本地直连兜底。
    llm = OpenAILLMService(
        api_key=str(
            getattr(llm_provider, "api_key", None)
            or os.getenv("VOICE_LLM_API_KEY", "")
            or os.getenv("DEEPSEEK_API_KEY", "")
        ),
        base_url=str(
            getattr(llm_provider, "base_url", None)
            or os.getenv("VOICE_LLM_BASE_URL", "")
            or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        ),
        settings=OpenAILLMService.Settings(
            model=str(
                getattr(llm_provider, "model_id", None)
                or os.getenv("VOICE_LLM_MODEL", "")
                or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
            ),
            system_instruction=SYSTEM_INSTRUCTION,
        ),
    )

    tts = VolcengineTTSService(
        api_key=str(
            getattr(tts_provider, "api_key", None)
            or os.getenv("VOLC_TTS_API_KEY", "")
        ),
        settings=VolcengineTTSService.Settings(
            voice_type=str(
                getattr(getattr(tts_provider, "options", None), "voice_type", None)
                or os.getenv("VOLC_TTS_VOICE_TYPE", "ICL_uranus_zh_female_heainainai_tob")
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
                or os.getenv("VOLC_TTS_ENDPOINT", "wss://openspeech.bytedance.com/api/v3/tts/bidirection")
            ),
            resource_id=str(
                getattr(tts_provider, "resource_id", None)
                or os.getenv("VOLC_TTS_RESOURCE_ID", "")
            ),
        ),
    )

    # runner 必须先建：BusBridgeProcessor 需要 runner.bus（进程内 AsyncQueueBus）。
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)

    # 回合边界参数（A1 拍板纳入 Settings，2026-09-12 起真正生效）：
    # 三个 Smart Turn / VAD 旋钮默认 None = 交给 Pipecat 常量（库是唯一真源，
    # 且 core/config.py 不得 import 可选的 pipecat extra）；显式设值才覆盖。
    # 它们直接决定 §6.5.2 里"闭嘴 → 回合边界"的耗时，因此必须可调，
    # 否则用户报的 EOU 延迟只能靠改代码收敛。
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

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=vad_params),
            # 硬上限：没有任何 stop 策略命中时，回合最多开多久。
            user_turn_stop_timeout=settings.voice_turn_stop_timeout,
            # 保留既有回合策略：start 仍是自研策略（附和词过滤 + 真实打断），
            # stop 缺省时 —— UserTurnStrategies.__post_init__ 会回填
            # TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3())，
            # 即 Smart Turn v3 端点检测（实测确认，见设计文档 §4.1/§6.3）；
            # 只有在设置里显式给了 Smart Turn 参数时才换成显式实例。
            user_turn_strategies=UserTurnStrategies(
                start=[AdaptiveUserTurnStartStrategy()],
                stop=stop_strategies,
            ),
        ),
    )

    latency = LatencyPercentileProcessor()

    # 主 worker 与子 agent 之间的帧通道：用户侧上下文下行给子 worker，
    # 子 worker 生成的文本上行回来，交给这里的 TTS 朗读。
    bridge = BusBridgeProcessor(
        bus=runner.bus,
        worker_name=MAIN_WORKER_NAME,
        name=f"{MAIN_WORKER_NAME}::BusBridge",
    )

    # 排障用单点计时（VOICE_TIMELINE_DEBUG=1）：把「用户闭嘴 → 出文字 → 出
    # 声音」拆成可分段的日志时间线；关闭时原样返回，不额外挂处理器。
    pipeline_steps = [
        transport.input(),
        stt,
        user_aggregator,
        bridge,
        tts,
        latency,
        transport.output(),
        assistant_aggregator,
    ]
    pipeline = Pipeline(insert_timeline_probes(pipeline_steps))

    worker = PipelineWorker(
        pipeline,
        name=MAIN_WORKER_NAME,
        # The default RTVI observer queues and flushes every bot-output segment
        # when the first audio frame starts. That makes a fast LLM response appear
        # in full before the browser has played it. Sentence markers emitted by
        # VolcengineTTSService are the sole assistant caption source instead.
        rtvi_observer_params=RTVIObserverParams(
            bot_output_enabled=False,
            bot_tts_enabled=False,
        ),
        params=PipelineParams(
            # 显式声明，与 DashScope ASR(16k) / 火山 TTS(24k) 对齐，
            # 不依赖 Pipecat 默认值将来是否变化。
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        # 官方范例的取值：某个 processor 变为不可用时直接收束管线，
        # 而不是留在半死不活的状态继续跑。
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    agent = TutorAgent(llm=llm)

    # 子 agent 先注册、主 worker 后注册（与官方 local-handoff 同序）。
    await runner.add_workers(agent, worker)

    # 客户端手动打断：走同一条 RTVI data channel，把一键打断落到这条管线自己的
    # 打断路径上（与 VAD 自动打断完全同路），不依赖 HTTP 路由与进程内注册表。
    # 浏览器在 data channel 上发 RTVI client-message：
    #   {label:"rtvi-ai", type:"client-message", data:{t:VOICE_INTERRUPT_MESSAGE}}
    try:
        rtvi = worker.rtvi
    except Exception:  # RTVI 被显式关闭时访问器会抛错；此时没有手动打断通道
        rtvi = None

    if rtvi is not None:

        @rtvi.event_handler("on_client_message")
        async def on_client_message(rtvi_processor, message):
            if getattr(message, "type", "") != VOICE_INTERRUPT_MESSAGE:
                return
            logger.info("Client requested barge-in over RTVI; interrupting the bot")
            await rtvi_processor.interrupt_bot()

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # 不自动打招呼：保持「用户先开口」的既有交互。
        # 子 agent 常驻 active=True，桥接帧一到即可作答。
        logger.info("Client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Main bot entry point compatible with Pipecat Cloud."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()

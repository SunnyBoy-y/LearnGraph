#
# ChatGPT 风格中文语音助手（Pipecat 全国内服务版 · 语音球前端）
#
# STT : 阿里 DashScope 实时 ASR（qwen3-asr-flash-realtime）
# LLM : DeepSeek（deepseek-chat）
# TTS : 火山引擎语音合成 2.0（双向流式，seed-tts-2.0）
# 传输: SmallWebRTC（免 Daily key）
# 前端: frontend/index.html（呼吸语音球，纯音频）
#

import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    MetricsFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from pipecat.turns.user_turn_strategies import UserTurnStrategies

from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy
from app.voice.embedded_dashscope_stt import DashScopeSTTService
from app.voice.embedded_volcengine_tts import VolcengineTTSService

load_dotenv(override=True)

SYSTEM_INSTRUCTION = (
    "你是一个友好的中文语音助手。你的回答会被直接朗读出来，"
    "所以请使用自然、口语化的中文，避免 emoji、特殊符号、列表、Markdown 等无法朗读的内容。"
    "回答要简洁、亲切，像真人对话一样。"
)


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


transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting ChatGPT-style CN bot")

    stt = DashScopeSTTService(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        settings=DashScopeSTTService.Settings(
            model=os.getenv("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash-realtime"),
            ws_url=os.getenv("DASHSCOPE_ASR_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"),
            silence_ms=int(os.getenv("DASHSCOPE_ASR_SILENCE_MS", "400")),
        ),
    )

    llm = DeepSeekLLMService(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        settings=DeepSeekLLMService.Settings(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            system_instruction=SYSTEM_INSTRUCTION,
        ),
    )

    tts = VolcengineTTSService(
        api_key=os.getenv("VOLC_TTS_API_KEY", ""),
        settings=VolcengineTTSService.Settings(
            voice_type=os.getenv("VOLC_TTS_VOICE_TYPE", "ICL_uranus_zh_female_heainainai_tob"),
            model=os.getenv("VOLC_TTS_MODEL", "seed-tts-2.0-standard"),
            emotion=os.getenv("VOLC_TTS_EMOTION", ""),
        ),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[AdaptiveUserTurnStartStrategy()],
            ),
        ),
    )

    latency = LatencyPercentileProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            latency,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
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

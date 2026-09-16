"""DashScope 实时语音识别（qwen3-asr-flash-realtime）— Pipecat STTService 适配。

协议（阿里云百炼 Qwen-ASR-Realtime，与忆伴 Agent/src/voice/stt.py 同源）：
  -> session.update（turn_detection=null，即 Manual 模式）
  -> input_audio_buffer.append(base64 16k PCM)
  -> input_audio_buffer.commit（由本机回合结束信号触发）
  <- conversation.item.input_audio_transcription.text（partial，两种模式均下发）
  <- conversation.item.input_audio_transcription.completed（final）
  -> session.finish（关闭前；服务端回 session.finished 后才断开）

**Manual 模式是必须的**：官方文档对 ``input_audio_buffer.commit`` 明确标注
"禁用场景：VAD 模式"。旧实现把 session 配成 ``server_vad`` 却又在回合结束
时发 commit —— 该事件会被忽略，本机回合结束信号永远无法提前 finalize。
因此通话链路必须关闭云端 VAD，回合边界完全交给本机 Silero VAD + Smart Turn v3。

**由此 commit 成为硬依赖**：Manual 模式没有任何自动断句，必须由
``UserStoppedSpeakingFrame``（Pipecat 用户聚合器在停策略触发时广播）驱动
commit，否则永远不会产生 final 转录。``DASHSCOPE_ASR_COMMIT_ON_EOU=0``
仅用于排障，开启状态下会让本服务不产生任何 final。

**连接生命周期与"有人在说话"解耦（2026-09-16 硬化）**：上游会话此前完全跟随
通话存活——静音、长时间没人说话、乃至本机 VAD 根本没生效时，都会整通占着一条
实时会话，而 Manual 模式只会把静音音频一路攒进云端缓冲（本仓库只约束了单次
append ≤ 15 MiB，总缓冲无上限）。本服务现在遵守三条契约：

1. **空闲退役**：连续 ``DASHSCOPE_ASR_IDLE_TIMEOUT_SECS``（默认 60s）既无本机
   语音活动、又无任何上游事件时，主动 ``session.finish`` + 断开上游。退役不是
   降级：下一次真正的语音活动会触发懒重连（``_ensure_upstream``），握手期间的
   音频帧在管线里排队而不是被丢弃。注意这里的尺度是**麦克风**而不是"通话"：
   助手连续朗读 90s 期间用户一直没说话，同样会退役（这正是没人对着麦克风说话的
   时候，也是不该继续占着实时会话的时候）；用户开口时付一小段握手延迟，本地
   VAD/打断链路不依赖这条 socket，所以插话依然即时生效。
2. **语音门闩**：只有"当前确实有语音活动"（本机 VAD 帧或 PCM 能量）才向上游
   append / 触发重连。静音帧既不再无脑灌进云端缓冲，也不会把 4 次退避的重连
   循环压在音频处理路径上（旧行为：``_ws is None`` 时每一帧都重连一轮）。
3. **能量兜底**：本机 VAD 帧缺席时，PCM 能量仍会置位 ``_speech_seen`` 并触发
   commit。修复前"VAD 不产生帧"等于"这个人说的话永远出不了 final 且没有任何
   报错"——因为 commit 与 partial **都**只认 VAD 帧。

本适配层把该 WebSocket 客户端包成 Pipecat 的 STTService：
  run_stt(audio) 仅喂入音频；后台 read loop 收到结果后 push TranscriptionFrame。
"""

from __future__ import annotations

import array
import asyncio
import base64
import contextlib
import json
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import websockets
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.settings import STTSettings
from pipecat.services.stt_latency import DEFAULT_TTFS_P99
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from app.voice.embedded_timeline import timeline_mark


# interim 字幕单条长度上限：partial 的 ``text`` 是整段单调增长的假设文本，
# 回声/长段累积可到数千字，而 interim 只用于实时字幕，超长截尾即可。
_INTERIM_MAX_CHARS = 120

# ---- 上游连接生命周期与语音门闩的默认值（均可用环境变量覆盖）------------------
#
# 空闲退役：本机既无语音活动、也没有任何上游事件时的静默上限。流式 ASR 按会话
# 时长计费，而 Manual 模式下静音只会把云端缓冲撑大，所以"没人说话就别占着会话"。
# 0 = 关闭该行为（退回"连接跟随通话存活"的旧语义，仅用于排障）。
DEFAULT_IDLE_TIMEOUT_SECS = 60.0
# 能量门：16k 单声道 PCM 的 RMS（满量程 1.0）。正常说话 0.02~0.1，安静房间底噪
# 0.001~0.005，故 0.006（≈ -44 dBFS）低到能接住正常音量的小声说话，又高到不会
# 被底噪长期点亮。
DEFAULT_ENERGY_RMS = 0.006
# "有信号"门：只决定"这一帧值不值得送去云端"。比语音门松一档（≈ -56 dBFS），
# 足够滤掉纯静音/数字静音，又不会把音量偏小的说话挡在云端的识别器门外。
DEFAULT_SIGNAL_RMS = 0.0015
# 能量"开口/收口"的持续时间阈值，与 Pipecat VAD 的 stop_secs 同量级。
DEFAULT_ENERGY_ONSET_SECS = 0.06
DEFAULT_ENERGY_STOP_SECS = 0.4
# 空闲看门狗检查周期。
IDLE_CHECK_INTERVAL_SECS = 1.0


def _env_float(name: str, default: float) -> float:
    """Read a float knob; a malformed value must not take the call down."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(f"{name}={raw!r} 不是数字，退回默认值 {default}")
        return default


def _pcm16_rms(audio: bytes) -> float:
    """RMS of a little-endian PCM16 mono chunk, normalized to full scale.

    Dependency-free on purpose: ``audioop`` was removed in 3.13 and this only has
    to answer "is anybody talking right now?". It is the fallback that keeps a
    dead/missing local VAD from silencing the whole call (see module docstring).
    """
    usable = len(audio) - (len(audio) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(bytes(audio[:usable]))
    if sys.byteorder == "big":
        samples.byteswap()
    total = 0
    for value in samples:
        total += value * value
    return (total / len(samples)) ** 0.5 / 32768.0


@dataclass
class DashScopeSTTSettings(STTSettings):
    ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    # DashScope 侧语言代码（ISO 639-1，如 zh / yue / en）。
    # 单独建字段而不复用基类的 ``language``：基类会把字符串转成 ``Language``
    # 枚举再映射为服务侧代码，而 DashScope 只认原始 ISO 代码（v1.1 已修过
    # zh-CN→zh 同类问题），直接下发可避免二次转换。
    asr_language: str = "zh"
    # 保留字段：Manual 模式下云端 VAD 已关闭，该值不再被使用，
    # 仅为兼容既有调用方（embedded_bot.py 仍会传 silence_ms）。
    silence_ms: int = 400
    api_key: str = ""


@dataclass(slots=True)
class _PendingCommit:
    """One client commit awaiting exactly one provider final."""

    event_id: str
    trigger: str
    sent_at: float
    connection_generation: int
    utterance_generation: int
    # A pause followed by more speech is not the end of the user's turn.  The
    # provider final for this commit must still contribute text, but it must not
    # be marked finalized or the Smart Turn strategy can release the turn before
    # the continuation has even been committed.
    superseded: bool = False


class DashScopeSTTService(STTService):
    """DashScope Qwen 实时 ASR（qwen3-asr-flash-realtime）服务。"""

    Settings = DashScopeSTTSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str = "",
        settings: DashScopeSTTSettings | None = None,
        ttfs_p99_latency: float | None = DEFAULT_TTFS_P99,
        journal: Any = None,
        **kwargs,
    ):
        if settings is None:
            settings = self.Settings(model="qwen3-asr-flash-realtime")
        settings.api_key = api_key
        # 基类会把 str 语言转成 Language 枚举再映射；DashScope 只认原始 ISO
        # 代码，故仍清空基类字段，改由 settings.asr_language 直接下发。
        settings.language = None
        # 显式给出 TTFS（"说完话 → 收到 final"的 P99 延迟），供
        # TurnAnalyzerUserTurnStopStrategy 的 wait_for_transcript 安全网使用。
        # 不传时基类会退回 DEFAULT_TTFS_P99 并打 warning；0 表示"不再等待"，
        # 会导致 Smart Turn 一判完就放行、句中文本被截断，故绝不设 0。
        super().__init__(
            settings=settings,
            ttfs_p99_latency=ttfs_p99_latency,
            **kwargs,
        )
        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._session_finished = asyncio.Event()
        self._event_id = 0
        # 回合结束主动 commit：Manual 模式下这是唯一的 finalize 触发手段
        # （云端 VAD 已关闭）。关闭它等价于让本服务不产生任何 final 转录。
        self._commit_on_eou = os.getenv("DASHSCOPE_ASR_COMMIT_ON_EOU", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        if not self._commit_on_eou:
            logger.warning(
                f"{self}: DASHSCOPE_ASR_COMMIT_ON_EOU 已关闭，但 Manual 模式下"
                " commit 是唯一的 finalize 触发手段——本服务将不会产生 final 转录。"
            )
        # 本机 VAD 自上次 commit 以来是否真的听到过语音。DashScope 在
        # Manual 模式下对"纯静音缓冲"的 commit 会稳定返回幻觉 final（实测
        # 连续 5 次静音 commit 均返回「嗯。」），该幻觉会被回合层当作真实
        # 用户发言，形成「EOU→commit→幻觉→新回合→EOU」自激循环。因此
        # commit 与 final 都必须以"本机确实听到语音"为前提。
        self._speech_seen = False
        # 上一次上报的 partial 全文（用于成对去重：DashScope 对同一次更新常
        # 连发两条相同文本的事件，一条带尾标点；也用于识别假设重置）。
        self._last_partial_text = ""
        # 一个布尔量无法表达两个合法 commit 同时在途，也无法区分迟到 final
        # 属于哪一个连接/utterance。 使用 FIFO 队列保存 commit 身份。
        self._pending_commits: deque[_PendingCommit] = deque()
        self._connection_generation = 0
        self._utterance_generation = 0
        # commit 的触发点（2026-09-12 单点计时定位后修正）：
        #   "1"（默认）→ 本机 VAD 判定停止（VADUserStoppedSpeakingFrame）即 commit；
        #   "0"        → 等聚合器广播 UserStoppedSpeakingFrame 再 commit（旧行为）。
        #
        # 旧行为把 DashScope 的 commit→final 往返串行压在「回合已经结束」之后，
        # 而聚合器的 stop 策略（Smart Turn v3）在 wait_for_transcript=True 下
        # 必须"有文本 + （final 已到 或 p99 安全网超时）"才放行，于是构成
        # 「策略等文本、文本等策略」的循环依赖，只能靠 p99 安全网超时兜底：
        # 实测每轮固定多等 ttfs_p99 − stop_secs = 1.0 − 0.2 ≈ 0.8s，之后才
        # commit，再串行等 commit 往返。VAD 停止即 commit 让这段往返与
        # Smart Turn 推理并行，final 一到就能立刻结束回合。
        self._commit_on_vad_stop = os.getenv(
            "DASHSCOPE_ASR_COMMIT_ON_VAD_STOP", "1"
        ).lower() not in ("0", "false", "no", "off")
        # Optional durable journal (ASR-stage errors are reported through it) and
        # bounded reconnect bookkeeping for the upstream websocket.
        self._journal = journal
        self._reconnect_attempt = 0
        self._reconnect_lock = asyncio.Lock()
        self._closed = False
        # ---------------------------------------------------------------- knobs
        # 上游连接生命周期与语音门闩（见模块 docstring 的"连接生命周期"一节）。
        self._idle_timeout_secs = _env_float(
            "DASHSCOPE_ASR_IDLE_TIMEOUT_SECS", DEFAULT_IDLE_TIMEOUT_SECS
        )
        self._energy_rms = _env_float("DASHSCOPE_ASR_ENERGY_RMS", DEFAULT_ENERGY_RMS)
        # "有信号"门比语音门松一档（默认 ≈ -56 dBFS）：它只决定"这一帧值不值得送去
        # 云端"，不决定"要不要 commit"——权威判定留给云端的识别器，别把音量偏小的
        # 说话挡在门外。
        self._signal_rms = _env_float("DASHSCOPE_ASR_SIGNAL_RMS", DEFAULT_SIGNAL_RMS)
        self._energy_onset_secs = _env_float(
            "DASHSCOPE_ASR_ENERGY_ONSET_SECS", DEFAULT_ENERGY_ONSET_SECS
        )
        self._energy_stop_secs = _env_float(
            "DASHSCOPE_ASR_ENERGY_STOP_SECS", DEFAULT_ENERGY_STOP_SECS
        )
        if self._idle_timeout_secs <= 0:
            logger.warning(
                f"{self}: DASHSCOPE_ASR_IDLE_TIMEOUT_SECS=0 —— 上游会话将随通话"
                "整通保持（静音期间也会占着实时会话并攒大云端缓冲）。"
            )
        # 单调时钟：决定"该不该重连"（有语音活动）与"该不该退役"（双向都静）。
        self._last_activity_at = time.monotonic()
        self._last_speech_at = self._last_activity_at
        # 信号尾巴从"从未有过信号"开始：否则通话开头两秒的静音会被当成尾巴送上去。
        self._last_signal_at = float("-inf")
        self._last_upstream_event_at = self._last_activity_at
        # 能量 VAD-lite 状态：VAD 帧缺席时的兜底门闩。
        self._energy_speech = False
        self._energy_speech_secs = 0.0
        self._energy_silence_secs = 0.0
        # 可观测性：本机 VAD 是否曾经出过帧。能量判定到语音、而 VAD 从未出现，
        # 说明"这名用户说的话本来会永远出不来"——每次通话只告警一次，避免把一条
        # 真实故障刷成噪声。
        self._vad_seen = False
        self._notified_vad_missing = False
        self._energy_speech_episodes = 0
        self._idle_task: asyncio.Task | None = None

    def can_generate_metrics(self) -> bool:
        return True

    # ------------------------------------------------------------- inspection

    def last_audio_activity_at(self) -> float | None:
        """Monotonic stamp of the last audio frame this service processed.

        Consumed by the session idle reaper (``app/voice/reaper.py``): a live call
        whose transcript has not been written yet looks idle in the database, and
        the only evidence to the contrary lives in this process.
        """
        return self._last_activity_at

    def upstream_connected(self) -> bool:
        """Whether an upstream ASR session is currently held."""
        return self._upstream_alive()

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        await self._connect()
        if self._idle_timeout_secs > 0:
            # 上游会话不再跟着通话存活：没人说话时就别占着它（见模块 docstring）。
            # 看门狗只退役上游会话，从不结束通话本身。
            self._idle_task = self.create_task(self._idle_watchdog())

    # ---------------------------------------------------------------- upstream

    def _upstream_alive(self) -> bool:
        ws = self._ws
        return ws is not None and not getattr(ws, "closed", False)

    def _speech_active(self, window: float = 2.0) -> bool:
        """现在（或刚刚）是否有语音活动？

        同一个判定同时驱动两个相反的决策——"值不值得新开一条上游连接"和"该不该
        退役当前这条"——所以两者不可能互相矛盾。旧实现恰好是矛盾的：静音帧每一帧
        都触发一轮重连，却从来没有任何东西会因为安静而退役。
        """
        if self._energy_speech or self._speech_seen:
            return True
        return (time.monotonic() - self._last_speech_at) <= window

    async def _detach_upstream(
        self, reason: str, *, notify: bool, expected_ws: Any = None
    ) -> bool:
        """把当前上游连接摘下来并关掉，幂等。

        所有拆卸路径都汇到这里，为的是没有一条能留下"没人负责的连接"：
        ``run_stt`` 的发送失败分支旧实现只把引用置空（旧 socket 与它的读任务会
        一直活着——TCP 层还有 20s 一次的 ping 保活），而重连又可能把 ``_ws`` /
        ``_reader_task`` 直接覆盖，于是 cleanup 只能关掉最新那一条。

        ``expected_ws`` 是"只许拆这一条"的断言：一个正在死去的旧读者无权拆掉刚被
        重连建起来的新连接。调用方负责串行化（见 ``_retire_upstream``）。
        """
        ws = self._ws
        if expected_ws is not None and ws is not expected_ws:
            # 当前连接已经不是"报告死亡的那一条"：它属于别人，不动它。
            return False
        reader = self._reader_task
        if ws is None and reader is None:
            return False
        self._ws = None
        self._reader_task = None
        # 退役的连接不能再交付任何东西，包括在途 commit 的迟到 final。
        self._connection_generation += 1
        self._pending_commits.clear()
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            with contextlib.suppress(BaseException):
                await reader
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        if notify:
            logger.info(f"{self}: upstream ASR retired ({reason})")
            timeline_mark("stt", "上游 ASR 已退役", reason)
        return True

    async def _retire_upstream(
        self,
        reason: str,
        *,
        notify: bool = True,
        wait: float | None = None,
        expected_ws: Any = None,
    ) -> bool:
        """``_detach_upstream`` under the reconnect lock."""
        if wait is not None:
            try:
                await asyncio.wait_for(self._reconnect_lock.acquire(), timeout=wait)
            except (asyncio.TimeoutError, TimeoutError):
                # 有重连卡在握手或退避里。调用方（收尾）已经置位 ``_closed``，所以
                # 那条重连就算建成功也会自己关掉；这里做不受锁约束的尽力清理，总比
                # 让收尾堵在一个 15s 的 open_timeout 后面好。
                logger.warning(
                    f"{self}: 重连在 {wait}s 内没有落定，跳过锁直接退役 ({reason})"
                )
                return await self._detach_upstream(
                    reason, notify=notify, expected_ws=expected_ws
                )
            try:
                return await self._detach_upstream(
                    reason, notify=notify, expected_ws=expected_ws
                )
            finally:
                self._reconnect_lock.release()
        async with self._reconnect_lock:
            return await self._detach_upstream(
                reason, notify=notify, expected_ws=expected_ws
            )

    async def _ensure_upstream(self, *, reason: str = "lost") -> bool:
        """Reuse the live ASR websocket, or rebuild it with bounded backoff.

        In Manual mode ``commit`` is the only way to obtain a final transcript,
        so a dead socket does not merely lose audio: the current turn can never
        close.  The socket is therefore rebuilt rather than abandoned, and the
        session keeps running if the rebuild fails.

        Only ever called when there IS speech to carry (see ``run_stt``): the old
        code called it for every silent frame, which ran a 4-attempt backoff loop
        inside the audio path over and over while the user was muted.
        """
        if self._closed:
            return False
        if self._upstream_alive():
            return True
        async with self._reconnect_lock:
            if self._closed:
                return False
            if self._upstream_alive():
                return True
            # 旧的残留必须先清干净再建新的：否则两条连接会同时活着，而只有一条
            # 有主人（cleanup 只关得掉最新那条）。
            await self._detach_upstream(f"{reason}:replaced", notify=False)
            from app.voice.journal import backoff_delay

            attempts = 4
            for attempt in range(1, attempts + 1):
                try:
                    await self._connect()
                    self._reconnect_attempt = 0
                    logger.info(f"{self}: ASR upstream reconnected ({reason})")
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    if self._closed:
                        # 收尾已经开始了（``_connect`` 会拒绝建连）：别再用退避拖住
                        # 收尾流程。
                        return False
                    delay = backoff_delay(attempt, base=0.5, cap=8.0)
                    self._reconnect_attempt = attempt
                    if self._journal is not None:
                        await self._journal.retry_scheduled(
                            "asr",
                            attempt=attempt,
                            delay_ms=int(delay * 1000),
                            reason=str(exc)[:200],
                        )
                    logger.warning(f"{self}: ASR 上游重连第 {attempt} 次失败: {exc}")
                    await asyncio.sleep(delay)
            self._reconnect_attempt = attempts
            if self._journal is not None:
                await self._journal.processor_error(
                    "asr",
                    "ASR 上游连接无法恢复，已降级为文本模式",
                    retryable=False,
                    degraded=True,
                )
            return False

    async def _connect(self) -> None:
        if self._closed:
            # 收尾已经开始（或已完成）：绝不能在收尾之后再建一条没人负责的连接。
            raise RuntimeError("ASR service is closing")
        url = f"{self._settings.ws_url}?model={self._settings.model}"
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        if self._ws is not None or self._reader_task is not None:
            # 一条连接只能有一个主人。
            await self._detach_upstream("reconnect", notify=False)
        ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=16 * 1024 * 1024,
            open_timeout=15,
        )
        if self._closed:
            # 握手与收尾赛跑：cleanup() 已经跑过（那时它看到的 `_ws` 还是 None），
            # 于是这条刚建好的连接没有任何人会关它。就地关掉，别留孤儿会话。
            with contextlib.suppress(Exception):
                await ws.close()
            raise RuntimeError("ASR service closing during connect")
        self._ws = ws
        self._event_id = 0
        self._connection_generation += 1
        self._session_finished.clear()
        # 新会话 = 新的活动时钟：空闲退役的判据必须从这里重新计时。
        now = time.monotonic()
        self._last_activity_at = now
        self._last_speech_at = now
        self._last_upstream_event_at = now
        try:
            await ws.send(json.dumps(self._build_session_update(), ensure_ascii=False))
        except Exception:
            # 连 session.update 都发不出去，这条会话没有意义，不能留成半开的连接。
            self._ws = None
            with contextlib.suppress(Exception):
                await ws.close()
            raise
        self._reader_task = asyncio.create_task(self._read_loop(ws))
        logger.info(f"{self}: connected to {self._settings.ws_url}")

    def _build_session_update(self) -> dict:
        """构建 session.update —— **Manual 模式**（``turn_detection=null``）。

        官方语义：``turn_detection`` 是 VAD 模式的开关；设为 ``null`` 即关闭
        云端 VAD 并启用 Manual 模式。Manual 模式下服务端不做断句，回合边界
        完全由本机 Silero VAD + Smart Turn v3 决定，并由
        ``input_audio_buffer.commit`` 手动触发识别——这正是双工通话要的语义。

        （旧实现发的是 ``server_vad``，而该模式下 commit 属"禁用场景"，
        导致回合结束时的 commit 被忽略、无法提前 finalize。）
        """
        return {
            "event_id": self._next_event_id("session"),
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "input_audio_transcription": {
                    "model": self._settings.model,
                    "language": self._settings.asr_language or "zh",
                },
                # None = 关闭云端 VAD，进入 Manual 模式（commit 才合法）。
                "turn_detection": None,
            },
        }

    def _next_event_id(self, kind: str) -> str:
        self._event_id += 1
        return f"evt_{kind}_{self._event_id}"

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """喂入一帧音频：先过两级能量门，再决定"发 / 重连 / 丢弃"。

        * **信号门**（很低，≈ -56 dBFS）：纯静音帧不再无脑 append。旧行为把整段
          静音灌进云端缓冲（设计文档只约束单次 append ≤ 15 MiB，总缓冲无上限）。
        * **语音门**（≈ -44 dBFS）：只有真的像说话才值得为它开一条上游会话，也就
          不会再把 4 次退避的重连循环压在音频路径上（旧行为：`_ws is None` 时每一帧
          都重连一轮）。

        两级门都只看本机是否听到声音，与上游状态无关；信号门比语音门松一档，是为了
        不把"音量偏小但云端能识别"的音频挡在门外——权威判定留给云端的识别器。
        """
        if self._closed:
            yield None
            return
        signal, speaking = await self._note_audio(audio)
        if not (signal or self._recent_signal()):
            # 纯静音：不进云端缓冲，也就不需要任何连接。
            yield None
            return
        if not self._upstream_alive():
            if not (speaking or self._speech_active()):
                # 有一点点信号，但还没到"像说话"：不值得为它握手。
                yield None
                return
            await self._ensure_upstream(reason="speech")
        ws = self._ws
        if ws is not None:
            try:
                await ws.send(
                    json.dumps(
                        {
                            "event_id": self._next_event_id("audio"),
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(audio).decode("ascii"),
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{self}: send audio failed: {exc}")
                # 旧实现只把 ``_ws`` 置空：旧 socket 与它的读任务会一直活着（TCP
                # 层还有 20s 一次的 ping 保活），而 cleanup 只关得掉最新那条。
                await self._retire_upstream("send_failed")
        yield None

    def _recent_signal(self, window: float = 2.0) -> bool:
        """最近是否出现过任何高于信号门的输入（用于给句尾留一小段尾巴）。"""
        return (time.monotonic() - self._last_signal_at) <= window

    async def _note_audio(self, audio: bytes) -> tuple[bool, bool]:
        """更新活动时钟与能量 VAD-lite 状态；返回 ``(有信号, 像说话)``。

        能量门是 VAD 帧缺席时的兜底，并同时决定"该不该重连"——所以"我们听到了
        说话"与"我们有地方把话发出去"不可能互相矛盾。修复前，本机 VAD 一旦不产生
        帧，``_speech_seen`` 就永远是 False，commit 与 partial 被自己的门闩全部丢掉：
        用户说什么都没反应，而且没有任何报错。
        """
        now = time.monotonic()
        self._last_activity_at = now
        # PCM16 单声道 @16k：时长 = 字节数 / 2 / 16000。
        frame_secs = len(audio) / 2 / 16000.0
        rms = _pcm16_rms(audio)
        if rms >= self._signal_rms:
            self._last_signal_at = now
        loud = rms >= self._energy_rms
        if loud:
            self._energy_silence_secs = 0.0
            self._last_speech_at = now
            self._energy_speech_secs += frame_secs
            if (
                not self._energy_speech
                and self._energy_speech_secs >= self._energy_onset_secs
            ):
                self._energy_speech = True
                self._energy_speech_episodes += 1
                self._speech_seen = True
                # 与 VAD 开口同语义：句中停顿的提交会被标 superseded（文本保留，
                # 但不许它让 Smart Turn 提前放行本回合）。
                self._mark_pending_commits_superseded()
                timeline_mark("stt", "能量判定开口")
                await self._note_vad_missing()
        else:
            self._energy_speech_secs = 0.0
            if self._energy_speech:
                self._energy_silence_secs += frame_secs
                if self._energy_silence_secs >= self._energy_stop_secs:
                    self._energy_speech = False
                    timeline_mark("stt", "能量判定收口")
                    # 与 VAD 停止帧同一条 commit 路径：谁先到谁提交，`_speech_seen`
                    # 门闩保证一次语音只提交一次。
                    await self._send_commit("energy-stop")
        return rms >= self._signal_rms, loud or self._energy_speech

    async def _note_vad_missing(self) -> None:
        """能量判定到语音、而整通没出现过任何 VAD 帧 —— 报告一次。

        延迟到第 3 段语音才报：VAD 帧由下游聚合器广播回来，本来就比音频帧晚一帧
        到达，第一段就报会稳定误报。这条告警不降级、不吓用户 —— 能量兜底已经在替
        VAD 干活，用户仍然听得懂、也被听得懂。
        """
        if self._vad_seen or self._notified_vad_missing:
            return
        if self._energy_speech_episodes < 3:
            return
        self._notified_vad_missing = True
        message = (
            "本机 VAD 连续 3 段语音没有产生任何帧；已切换到 PCM 能量兜底"
            "（commit 与 partial 仍可正常工作）"
        )
        logger.warning(f"{self}: {message}")
        if self._journal is not None:
            await self._journal.notice("asr", "vad_frames_missing", message)

    async def _idle_watchdog(self) -> None:
        """空闲就退役上游会话；从不结束通话本身。

        要求"双向都静"：既无本机语音活动，也无任何上游事件。在途 commit、仍在被
        转写的音频都会把会话留住 —— 在那里退役会丢掉回合正等着的那条 final。
        """
        while not self._closed:
            await asyncio.sleep(IDLE_CHECK_INTERVAL_SECS)
            if self._closed:
                return
            if self._ws is None:
                continue
            quiet_for = time.monotonic() - max(
                self._last_speech_at, self._last_upstream_event_at
            )
            if quiet_for < self._idle_timeout_secs:
                continue
            if self._pending_commits or self._speech_seen or self._energy_speech:
                continue
            # 优雅退役：先 session.finish，让云端有机会把最后一段的 final 吐出来
            # （此刻管线仍在运行），再断开。这里没有等它回 session.finished ——
            # 空闲意味着缓冲区本来就是空的。
            await self._send_session_finish(timeout=0.75)
            await self._retire_upstream(f"idle_{int(quiet_for)}s")

    async def _note_upstream_lost(self, ws: Any, reason: str, *, clean: bool) -> None:
        """我们自己还持有这条 socket 时它结束了：记录 + 让下一次语音重建。"""
        if self._ws is not ws:
            # 已经被自己退役/替换过了：这条死亡不该再报一次，更不该去动新的连接。
            return
        if clean:
            logger.warning(f"{self}: ASR 上游正常关闭（{reason}）")
        else:
            logger.warning(f"{self}: {reason}")
        await self._retire_upstream("lost", notify=False, expected_ws=ws)
        if self._journal is None:
            return
        if clean:
            # 正常关闭不是故障：写一条 notice（客户端会忽略）而不是
            # processor.error —— 后者会让用户看到一条吓人的红色错误，而实际上
            # "下一次说话会自动重连"。
            await self._journal.notice("asr", "upstream_closed", reason)
        else:
            await self._journal.processor_error(
                "asr", reason, retryable=True, attempt=self._reconnect_attempt
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """在基类 STT 逻辑之上，监听本机 EOU 信号主动 commit。

        默认在 ``VADUserStoppedSpeakingFrame``（本机 Silero 的静音判定）到达时
        提前 commit，让 DashScope 的识别往返与 Smart Turn 推理并行；只有在
        ``DASHSCOPE_ASR_COMMIT_ON_VAD_STOP=0`` 时才退回等待
        ``UserStoppedSpeakingFrame``（Silero + Smart Turn 的完整回合边界）。
        Manual 模式下服务端不会自行断句，因此 commit 是产生 final 转录的唯一
        触发点。
        """
        await super().process_frame(frame, direction)
        now = time.monotonic()
        # 本机 VAD 判定用户开口：标记"这一轮确实有语音"，供 commit 门闩使用。
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._vad_seen = True
            self._speech_seen = True
            self._last_speech_at = now
            if not self._upstream_alive():
                # VAD 开口是最早的"要说话了"信号：上游若因空闲被退役，这里先预热
                # 重连，省掉把握手耗在音频路径上的那一拍。
                self.create_task(self._ensure_upstream(reason="vad-start"))
            # A short pause followed by more speech is still one user turn.
            # Keep the old commit so its text is not lost, but mark it as
            # superseded: its final may be appended to the current context, yet
            # it must not satisfy TurnAnalyzer's "finalized transcript" gate.
            self._mark_pending_commits_superseded()
        # commit 触发点：默认在 VAD 判定停止时（最早的可提交时刻），旧行为则
        # 等聚合器广播 UserStoppedSpeakingFrame（回合已经结束之后）。
        if isinstance(frame, VADUserStoppedSpeakingFrame) and self._commit_on_vad_stop:
            timeline_mark("stt", "收到 VAD 停止帧")
            await self._send_commit("vad-stop")
        if isinstance(frame, UserStoppedSpeakingFrame):
            # This is a real turn boundary, unlike VADUserStoppedSpeakingFrame:
            # Smart Turn has decided that the user is done. Any still-unresolved
            # commit from the previous turn must not consume the next turn's
            # final, and its late final must not reopen the turn.
            self._utterance_generation += 1
            self._pending_commits.clear()
            if not self._commit_on_vad_stop:
                timeline_mark("stt", "收到回合结束帧")
                await self._send_commit("turn-stopped")

    def _mark_pending_commits_superseded(self) -> None:
        for commit in self._pending_commits:
            commit.superseded = True

    async def _send_commit(self, trigger: str) -> None:
        """向 DashScope 发送 input_audio_buffer.commit，强制立即出 final。

        只有在本机 VAD（或能量兜底）本回合确实听到过语音时才 commit：静音缓冲的
        commit 会拿到幻觉 final（见 ``__init__`` 的说明），进而自激出无限空回合。
        ``trigger`` 仅用于单点计时日志，标明这次 commit 是被谁触发的。
        """
        if self._closed or not self._upstream_alive():
            return
        if not self._commit_on_eou:
            return
        if not self._speech_seen:
            logger.debug(f"{self}: skipped commit on EOU (no local speech this turn)")
            return
        event_id = self._next_event_id("commit")
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": event_id,
                        "type": "input_audio_buffer.commit",
                    }
                )
            )
            self._pending_commits.append(
                _PendingCommit(
                    event_id=event_id,
                    trigger=trigger,
                    sent_at=time.time(),
                    connection_generation=self._connection_generation,
                    utterance_generation=self._utterance_generation,
                )
            )
            # commit 是上游流量：在它的 final 回来之前不许退役这条会话。
            self._last_upstream_event_at = time.monotonic()
            logger.debug(f"{self}: committed audio buffer on EOU")
            timeline_mark("stt", "commit 已发出", f"trigger={trigger}")
            self._speech_seen = False
            # 新段落开始：下一条 partial 属于新文本，不再与上一段去重。
            self._last_partial_text = ""
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: commit failed: {exc}")
            await self._retire_upstream("commit_failed")

    async def _read_loop(self, ws: Any) -> None:
        """读取上游事件。

        两点与旧实现不同，都来自"连接会静默死亡"这一实测结论：

        * 只有"我们自己还持有这条 socket"时，它的结束才需要处理。被替换/退役的
          连接是 ``_detach_upstream`` 先摘引用再关的，这里的 ``self._ws is ws``
          判据保证同一个死亡不会被报告两次。
        * ``websockets`` 的 ``__aiter__`` 会把**正常关闭**（1000/1001）吞掉并让
          迭代器正常返回（``websockets/asyncio/connection.py:242-246``）。旧实现
          只处理了调用抛异常的路径，于是服务端优雅关会话时留下一个"已关闭但引用
          还在"的 ``_ws``：静音期间没有音频进来就不会重连，UI 也没有任何提示。
        """
        seen_types: set = set()
        try:
            async for raw in ws:
                # 任何上游事件都算"这条会话正在干活"，空闲退役据此让路。
                self._last_upstream_event_at = time.monotonic()
                payload = self._parse(raw)
                etype = str(payload.get("type") or "")
                if etype not in seen_types:
                    seen_types.add(etype)
                    logger.info("[DashScopeEvt] 首次见 type={!r}", etype)
                if etype == "session.finished":
                    # 收尾握手：cleanup() 等这个事件到齐后再断开连线。
                    self._session_finished.set()
                if "transcription" in etype:
                    # 只记事件类型与文本长度，不记转录正文（日志脱敏）。
                    logger.debug(
                        "[DashScopeEvt] type={!r} text_len={}",
                        etype,
                        len(str(payload.get("text") or payload.get("transcript") or "")),
                    )
                if etype == "conversation.item.input_audio_transcription.text":
                    # partial 假设文本：新版协议放 ``text``（整段单调增长），
                    # 旧版放 ``stash``（text 恒空），两个字段都读。
                    text = str(payload.get("text") or payload.get("stash") or "").strip()
                    # 仅在"本回合确实有语音"时上报 partial，避免静音期间的
                    # 服务端幻觉 partial 触发一次空回合。能量兜底期间也算有语音，
                    # 否则 VAD 缺席时实时字幕会一句话都不显示。
                    if text and (self._speech_seen or self._energy_speech):
                        # 成对下发（同一次更新两条相同文本）只报第一条；
                        # 假设重置（新句）视为新文本。
                        if text != self._last_partial_text:
                            self._last_partial_text = text
                            # 回声/长段累积会把假设文本撑到数千字（实测
                            # 4964+ 且逐字增长），interim 只用于实时字幕，
                            # 截断到尾部即可，权威文本以 final 为准。
                            display = text[-_INTERIM_MAX_CHARS:] if len(text) > _INTERIM_MAX_CHARS else text
                            await self.push_frame(
                                InterimTranscriptionFrame(
                                    display, self._user_id, time_now_iso8601()
                                )
                            )
                elif etype == "conversation.item.input_audio_transcription.completed":
                    await self._handle_final(payload)
                elif etype in ("error", "asr.error"):
                    message = str(
                        payload.get("message") or payload.get("error") or "asr error"
                    )
                    logger.warning(f"{self}: asr error: {message}")
                    if self._journal is not None:
                        # Surface the stage failure instead of logging only: a
                        # silent ASR error is indistinguishable from "the user
                        # said nothing", which is the worst failure mode for a
                        # voice UI.
                        await self._journal.processor_error(
                            "asr",
                            message,
                            retryable=True,
                            attempt=self._reconnect_attempt,
                        )
        except asyncio.CancelledError:
            raise
        except ConnectionClosed as exc:
            await self._note_upstream_lost(
                ws,
                f"ASR 读取循环结束: {exc}",
                clean=isinstance(exc, ConnectionClosedOK),
            )
        except Exception as exc:  # noqa: BLE001
            await self._note_upstream_lost(ws, f"ASR 读取循环中断: {exc}", clean=False)
        else:
            # 迭代正常结束 = 服务端优雅关会话（``__aiter__`` 吞掉了
            # ``ConnectionClosedOK``）。旧实现什么都没做，于是留下一个"已关闭但
            # 引用还在"的 handle：静音期间没有音频就不会重连，UI 也没有任何提示。
            await self._note_upstream_lost(ws, "ASR 上游已关闭", clean=True)

    async def _handle_final(self, payload: dict) -> None:
        """Accept one final per outstanding commit, in FIFO order.

        A boolean in-flight flag loses the second of two legal commits and can
        attach a late final to a newer utterance. Pending commits therefore
        carry both connection and utterance generations.
        """
        text = str(payload.get("transcript") or payload.get("text") or "").strip()
        commit: _PendingCommit | None = None
        while self._pending_commits:
            candidate = self._pending_commits.popleft()
            if (
                candidate.connection_generation != self._connection_generation
                or candidate.utterance_generation != self._utterance_generation
            ):
                logger.debug(
                    "[DashScopeEvt] dropping stale final (connection={}, utterance={})",
                    candidate.connection_generation,
                    candidate.utterance_generation,
                )
                continue
            commit = candidate
            break
        if commit is None:
            logger.debug(
                "[DashScopeEvt] dropping unsolicited final (text_len={})",
                len(text),
            )
            return
        timeline_mark(
            "stt",
            "commit→final 往返",
            f"{time.time() - commit.sent_at:.3f}s text_len={len(text)}",
        )
        if not text:
            return
        await self.emit_stt_usage_metrics()
        await self.push_frame(
            TranscriptionFrame(
                text,
                self._user_id,
                time_now_iso8601(),
                result={
                    "commit_id": commit.event_id,
                    "commit_trigger": commit.trigger,
                    "connection_generation": commit.connection_generation,
                    "utterance_generation": commit.utterance_generation,
                    "superseded": commit.superseded,
                },
                # A superseded final belongs to an earlier pause in the same
                # turn: keep its text, but do not let it trigger end-of-turn.
                finalized=not commit.superseded,
            )
        )

    @staticmethod
    def _parse(raw: Any) -> dict:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    async def cleanup(self):
        # 优雅收尾：先发 session.finish 并等尾部 final（此刻管线仍在运行，
        # 最后一条 TranscriptionFrame 还能正常推送出去），再拆 reader 与连接，
        # 最后交给基类收尾。顺序颠倒会丢掉尾句的 final。
        #
        # ``_closed`` 必须在任何 await 之前置位（``_connect`` 会检查它），拆卸则走
        # 带锁的 ``_retire_upstream``：旧实现直接读写 ``_ws`` / ``_reader_task``，
        # 而一场正在进行的重连可以在这个窗口里建出一条新的连接——它随后既不在
        # ``_ws`` 里、也没人能关掉它（孤儿会话，靠 20s 一次的 ping 一直活着）。
        self._closed = True
        self._connection_generation += 1
        self._pending_commits.clear()
        idle_task, self._idle_task = self._idle_task, None
        if idle_task is not None and not idle_task.done():
            idle_task.cancel()
            with contextlib.suppress(BaseException):
                await idle_task
        await self._send_session_finish()
        await self._retire_upstream("cleanup", notify=False, wait=6.0)
        await super().cleanup()

    async def _send_session_finish(self, timeout: float = 1.5) -> None:
        """发送 session.finish 并等待 session.finished。

        官方流程：若已检测到语音，服务端先发
        ``conversation.item.input_audio_transcription.completed``（尾句 final），
        再发 ``session.finished``；客户端收到后须主动断开。
        """
        if not self._upstream_alive():
            return
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": self._next_event_id("finish"),
                        "type": "session.finish",
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{self}: session.finish 发送失败: {exc}")
            return
        try:
            await asyncio.wait_for(self._session_finished.wait(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{self}: {timeout}s 内未收到 session.finished: {exc}")

"""DashScope 实时语音识别（qwen3-asr-flash-realtime）— Pipecat STTService 适配。

协议（阿里云百炼 Qwen-ASR-Realtime，与忆伴 Agent/src/voice/stt.py 同源）：
  -> session.update（turn_detection=null → Manual；server_vad → 云端 VAD）
  -> input_audio_buffer.append(base64 16k PCM)
  -> input_audio_buffer.commit（Manual：由本机"这句说完"信号触发）
  -> 追加一段静音 PCM（云端 VAD：同样的触发，见下）
  <- input_audio_buffer.speech_started / speech_stopped（云端 VAD 的段边界）
  <- conversation.item.input_audio_transcription.text（partial，两种模式均下发）
  <- conversation.item.input_audio_transcription.completed（final）
  -> session.finish（关闭前；服务端回 session.finished 后才断开）

**两种断句模式，默认云端 VAD（2026-09-18 起）**：

* **云端 VAD（默认，``DASHSCOPE_ASR_TURN_DETECTION=server_vad``）**：由云端自己判断
  "这段音频算不算语音、在哪里结束"，因此环境噪音不再被当成一句"嗯"送进转录并打断
  对话（`threshold`/`silence_duration_ms` 见 ``_build_session_update``）。代价是段尾
  必须由我们补：本机为了省带宽把静音挡在门外（见下面第 2 条"语音门闩"），云端因此
  **永远看不到"静音"这个结束信号**，于是本地判定"这句说完了"时主动补一段 ≥
  ``silence_duration_ms`` 的静音 PCM，云端 VAD 立即 finish 本段并下发 final。
  但本机能量尾巴（``DASHSCOPE_ASR_ENERGY_STOP_SECS``，默认 0.4s，与云端
  ``silence_duration_ms`` 等长）期间送出去的正是"安静帧"，云端往往**先我们一步**
  自己 ``committed``；那时再补静音就是往空缓冲里灌静音——拿不到 final、多付一段
  计费，还会让"补了几段／回收了几段"的账永久拉偏（``cloud_vad_no_final`` 误报的
  来源，2026-09-18 修）。现在的规则是：服务端的 ``committed`` 会复位本地"自上次交接
  以来听到过语音"的门闩（等价于我们自己的 commit/补静音完成了一次交接），因此 EOU
  时不会再补；真需要补时也只在"服务端手里还有未提交音频"时才发（见
  ``_finish_segment``）；而"补静音后没有 final"只有在**这条连接从未见过
  ``committed``** 时才算真故障（见 ``_check_pad_stall``）。
* **Manual（``=none``）**：官方文档对 ``input_audio_buffer.commit`` 标注
  "禁用场景：VAD 模式"，所以 Manual 下 commit 既是唯一的 finalize 手段，也是产生
  final 的唯一触发点；回合边界完全交给本机 Silero VAD + Smart Turn v3。

两种模式由 ``_finish_segment()`` 统一分发（Manual → commit；云端 VAD → 补静音），
调用点、门闩与"任何已收到的 final 都必须变成文本"的契约完全一致。
``DASHSCOPE_ASR_COMMIT_ON_EOU=0`` 仅用于排障，开启状态下 Manual 模式不会有任何
final 转录。

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
   循环压在音频处理路径上（旧行为：``_ws is None`` 时每一帧都重连一轮）。云端 VAD
   模式下这条门闩更严格：**任何静音都不外送**（Manual 模式仍保留"最后一个信号之后
   2s 内继续送"的句尾尾巴）。理由见模块开头——送静音等于把段尾判定权交给云端。
3. **能量兜底**：本机 VAD 帧缺席时，PCM 能量仍会置位 ``_speech_seen`` 并触发
   commit。修复前"VAD 不产生帧"等于"这个人说的话永远出不了 final 且没有任何
   报错"——因为 commit 与 partial **都**只认 VAD 帧。

**回合边界不丢文本（2026-09-17 修复"说两轮后麦克风就哑了"）**：这是一条比上面
三条更硬的契约——**任何一条已经收到的 final 都必须变成文本**，即使它回来得太晚、
已经越过回合边界。旧实现在 ``UserStoppedSpeakingFrame`` 处清空在途 commit 队列，
而 commit 是在 VAD 停止时发的、final 要 0.5~1s 才回：一旦 final 输给 p99 安全网
（回合先结束），它回来时就找不到配对、被判为 "unsolicited" 整条丢掉。丢掉的正是
用户刚说的那句话，而且**每一轮都丢**——用户看到的正是"说两轮之后麦克风死了"。
现在回合边界只把在途 commit 标 ``superseded``（文本保留、``finalized=False`` 不许
它结束/重开回合），队列直到超时才清理；丢 final 也从 DEBUG 升级为 WARNING +
durable notice，杜绝"用户说了话、服务端一句日志都没有"。

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
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import websockets
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
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
from app.voice.embedded_turn_intent import is_backchannel


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
# commit 门的最小语音量：自上次 commit 以来至少听到这么多语音，才值得向云端提交。
# 与能量"开口"阈值同量级——够长到不会被 VAD 停止之后的一两帧残留噪声点亮（那会
# 拿到幻觉 final），又短到不会把一句很短的"嗯"挡掉。
_MIN_COMMIT_SPEECH_SECS = DEFAULT_ENERGY_ONSET_SECS
# 一条 commit 超过这么久没有 final 回来，就当作服务端不会回了。回合结束不再清空
# 队列（见 ``process_frame`` 中 ``UserStoppedSpeakingFrame`` 的处理），留着这类
# 僵尸记录会让下一个 final 被错配到它身上。
_PENDING_COMMIT_MAX_AGE_SECS = 20.0
# 云端 VAD（``turn_detection=server_vad``）的默认参数：官方推荐值。
DEFAULT_VAD_SILENCE_MS = 400.0
# 官方对 ``silence_duration_ms`` 的取值范围。
VAD_SILENCE_RANGE_MS = (200.0, 6000.0)
DEFAULT_VAD_THRESHOLD = 0.0
# 补静音在 ``silence_duration_ms`` 之上留的余量：云端必须从"最后那段静音"里判出段尾，
# 刚好等于阈值时边界太脆（采样/分片对齐），多给 200ms 让判定稳定落在我们这一侧。
DEFAULT_SEGMENT_SILENCE_MARGIN_MS = 200.0
# 补静音的分片长度：单条 append 保持小而密，避免与真实音频帧的节奏差太远。
SEGMENT_SILENCE_CHUNK_MS = 100.0
# 云端的 final 没在这么久内回来，就认为"补静音没能让它收尾"（配置没生效/服务端不
# 支持 VAD/网络抖动），报一条 notice——不能让它静默退化成另一种哑麦。
DEFAULT_PAD_FINAL_GRACE_SECS = 2.5
# "丢掉的 final" 的上报节流。用户可见的"我说了话却什么都没发生"必须在 durable log
# 里留下痕迹，但一条已经坏掉的会话不该把日志刷满。
_FINAL_DROP_NOTICE_INTERVAL_SECS = 60.0


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
    # 断句模式：``server_vad``（云端 VAD，默认）| ``none``（Manual，见模块 docstring）。
    turn_detection: str = "server_vad"
    # 云端 VAD 判定"这一段说完了"所需的静音时长（毫秒）：官方默认 800、推荐 400、
    # 范围 [200, 6000]。Manual 模式下该字段不被使用（但 ``embedded_bot.py`` 照旧
    # 从 provider 配置/``DASHSCOPE_ASR_SILENCE_MS`` 透传，默认 400）。
    silence_ms: int = 400
    # 云端 VAD 的语音灵敏度门限：官方默认 0.2、推荐 0.0（最灵敏，不吞音量偏小的说话）。
    vad_threshold: float = 0.0
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
        self._raw_event_logging = os.getenv(
            "DASHSCOPE_ASR_LOG_RAW_EVENTS", "0"
        ).lower() in ("1", "true", "yes", "on")
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
                f"{self}: DASHSCOPE_ASR_COMMIT_ON_EOU 已关闭，本服务不会产生 final 转录"
                "（Manual 模式下 commit 是唯一的 finalize 手段，云端 VAD 模式下则是补静音）。"
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
        # DashScope identifies one completed transcription by ``item_id``.
        # Keep a bounded idempotency ledger so a repeated terminal event cannot
        # consume the next commit and duplicate the user's question.
        self._seen_final_items: OrderedDict[str, None] = OrderedDict()
        self._connection_generation = 0
        self._utterance_generation = 0
        # 自上一次 commit 以来，本机听到的语音时长（秒）。这是 commit 门与
        # partial 门共用的判据：``_speech_seen`` 在每次 commit 之后复位，单看它
        # 无法表达"commit 之后本回合又说了话"——旧实现因此会出现"字幕照常滚动、
        # commit 永远缺席"的状态（用户看得见自己的话，回合却再也不结束）。
        # 反过来，只按"现在有没有能量"判断又会让 VAD 停止后的一两帧残留噪声
        # 触发一次对静音缓冲的 commit，拿到幻觉 final。
        self._uncommitted_speech_secs = 0.0
        # 被丢弃的 final 计数 + 最后一次上报时间。丢话以前只有一行 DEBUG 日志：
        # 用户看到的是"我说了话但什么都没发生"，而服务端日志一片安静。
        self._dropped_finals = 0
        self._last_final_drop_notice_at = float("-inf")
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
        # ------------------------------------------------- 断句模式（默认云端 VAD）
        requested_mode = (
            os.getenv("DASHSCOPE_ASR_TURN_DETECTION")
            or settings.turn_detection
            or "server_vad"
        ).strip().lower()
        if requested_mode not in ("server_vad", "none"):
            logger.warning(
                f"{self}: DASHSCOPE_ASR_TURN_DETECTION={requested_mode!r} 无法识别，"
                "回落到 server_vad（none = Manual 模式）"
            )
            requested_mode = "server_vad"
        self._turn_detection_mode = requested_mode
        self._cloud_vad_enabled = requested_mode == "server_vad"
        raw_silence = _env_float("DASHSCOPE_ASR_VAD_SILENCE_MS", settings.silence_ms or DEFAULT_VAD_SILENCE_MS)
        low, high = VAD_SILENCE_RANGE_MS
        self._vad_silence_ms = min(max(raw_silence, low), high)
        if self._vad_silence_ms != raw_silence:
            logger.warning(
                f"{self}: silence_duration_ms={raw_silence:.0f} 超出官方范围"
                f" [{low:.0f}, {high:.0f}]，已收敛为 {self._vad_silence_ms:.0f}"
            )
        self._vad_threshold = min(
            max(_env_float("DASHSCOPE_ASR_VAD_THRESHOLD", settings.vad_threshold), -1.0),
            1.0,
        )
        # 本机"这句说完了"之后补的静音长度：必须 ≥ silence_duration_ms，否则云端判不出段尾。
        self._segment_silence_ms = _env_float(
            "DASHSCOPE_ASR_SEGMENT_SILENCE_MS",
            self._vad_silence_ms + DEFAULT_SEGMENT_SILENCE_MARGIN_MS,
        )
        if self._segment_silence_ms < self._vad_silence_ms:
            logger.warning(
                f"{self}: DASHSCOPE_ASR_SEGMENT_SILENCE_MS="
                f"{self._segment_silence_ms:.0f} 小于 silence_duration_ms="
                f"{self._vad_silence_ms:.0f} —— 云端无法判出段尾，已抬到后者"
            )
            self._segment_silence_ms = self._vad_silence_ms
        self._segments_finished = 0
        # 云端 VAD 模式的自诊断：补了静音却迟迟等不到 final 时必须留痕（见
        # ``_check_pad_stall``）——否则"配置没生效/服务端不支持 VAD"会退化成
        # 又一种"用户说了话却什么都没有"的哑麦，且没有任何日志。
        self._segments_padded = 0
        self._segments_resolved = 0
        self._pad_stall_notified = False
        self._pad_stall_deadline: float | None = None
        # "云端自己在判段"的证据，以及"服务端手里还有多少没提交的音频"——补静音该不该
        # 发，由这两个数决定（每条连接各一份，见 ``_connect``）。
        self._server_commits = 0
        self._audio_chunks_since_server_commit = 0
        self._pad_final_grace_secs = _env_float(
            "DASHSCOPE_ASR_PAD_FINAL_GRACE_SECS", DEFAULT_PAD_FINAL_GRACE_SECS
        )
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
        # --------------------------------------------------- 背声词门闩（见
        # ``_is_backchannel_filler``）。``_bot_speaking`` 跟着输出传输广播的
        # BotStarted/StoppedSpeakingFrame 走：那两个帧会沿管线上行经过本服务，
        # 所以这里的判据与回合策略、与账本的"机器人在出声"完全同源。
        self._bot_speaking = False
        self._suppressed_backchannels = 0

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
        self._seen_final_items.clear()
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
        # 证据按连接重置：新连接要重新被接受 server_vad 参数，旧连接的 committed 不能
        # 替它背书（上一版把这两个数当全局用，重连后会把真故障说成误报）。
        self._server_commits = 0
        self._audio_chunks_since_server_commit = 0
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
        """构建 session.update —— 云端 VAD（默认）或 Manual（见模块 docstring）。

        ``turn_detection`` 是 VAD 模式的开关：``null`` 关闭云端 VAD 进入 Manual
        （官方语义），给一个 ``{"type": "server_vad", ...}`` 则开启云端 VAD。

        开启云端 VAD 的目的**不是**把断句权交出去（本机 Silero VAD + Smart Turn v3
        仍是回合边界的唯一决策者），而是借云端的语音/非语音判定把环境噪音挡在转录
        之外——Manual 模式下任何够响的杂音都会被本机能量门送去识别，用户会看到
        莫名其妙的"嗯"把对话打断。段尾则由 ``_pad_silence()`` 在本机判定说完时补，
        所以云端只负责"这段算不算语音"，不负责"回合在哪结束"。
        """
        turn_detection: dict | None = None
        if self._cloud_vad_enabled:
            turn_detection = {
                "type": "server_vad",
                # 官方默认 0.2；0.0 最灵敏（不吞音量偏小的说话），噪音过滤交给识别器。
                "threshold": self._vad_threshold,
                # 官方默认 800、推荐 400：这就是"我们补多长静音它才肯收尾"的阈值。
                "silence_duration_ms": int(round(self._vad_silence_ms)),
            }
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
                "turn_detection": turn_detection,
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
        if self._cloud_vad_enabled:
            # 云端 VAD 模式：静音一律不外送。
            #
            # 云端一旦看到累积 ≥ ``silence_duration_ms`` 的静音就会自己切段，而本机
            # VAD 允许的句中停顿（``stop_secs``）恰恰比它长——把静音送上去等于把
            # "半句被切"的权力交出去（这正是当初改用 Manual 的原因）。这里改成
            # 只送"本地判定正在说话"的帧，段尾由 ``_pad_silence()`` 在本机真的判定
            # 说完时精确补上，云端因此只在我们要它收尾的时候收尾。
            #
            # ``speaking`` 已包含两件事：这一帧本身够响，或本机仍处于能量判定的
            # 说话状态（含 ``_energy_stop_secs`` 的尾巴），所以句尾不会被削掉。
            keep = speaking
        else:
            keep = signal or self._recent_signal()
        if not keep:
            # 静音：不进云端缓冲，也就不需要任何连接。
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
                # 服务端手里因此多了一段没提交的音频：补静音才有意义。
                self._audio_chunks_since_server_commit += 1
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
            # 未提交语音量：commit 的真判据（见 ``_speech_since_commit``）。一个
            # commit 只把"截至此刻的那段音频"变成文本；此后继续说的话必须重新
            # 攒够时长，才值得再提交一次。
            self._uncommitted_speech_secs += frame_secs
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
                    # 与 VAD 停止帧同一条收尾路径：谁先到谁收尾，`_speech_since_commit`
                    # 门闩保证一次语音只收尾一次。
                    await self._finish_segment("energy-stop")
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
            # 与"退役"无关的独立检查：补静音有没有被服务端回应（见 _check_pad_stall）。
            await self._check_pad_stall()
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
        # 机器人是否正在出声/正在作答：背声词门闩的判据（见 _is_backchannel_filler）。
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
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
            await self._finish_segment("vad-stop")
        if isinstance(frame, UserStoppedSpeakingFrame):
            # This is a real turn boundary, unlike VADUserStoppedSpeakingFrame:
            # Smart Turn has decided that the user is done. Bumping the utterance
            # generation detaches the commits still in flight from the *next*
            # turn -- but it must not throw their text away. A commit sent at VAD
            # stop lands 0.5-1s later; when that final loses the race against the
            # p99 safety net, the stop frame arrives first and the old code
            # cleared the queue here, so the final came back "unsolicited" and
            # was dropped whole. The next turn then committed on top of an
            # already-finalized buffer, and the call went permanently silent:
            # the "speak two turns and the mic is dead" bug. Keep them queued and
            # mark them superseded instead -- their text still reaches the
            # aggregator, while ``finalized=False`` keeps them from ending or
            # reopening a turn.
            self._utterance_generation += 1
            self._mark_pending_commits_superseded()
            if not self._commit_on_vad_stop:
                timeline_mark("stt", "收到回合结束帧")
                await self._finish_segment("turn-stopped")

    def _mark_pending_commits_superseded(self) -> None:
        for commit in self._pending_commits:
            commit.superseded = True

    def _speech_since_commit(self) -> bool:
        """本机自上一次 commit 以来，是否确实听到过语音？

        commit 门与 partial 门必须读同一个判据。旧实现把 partial 门写成
        ``_speech_seen or _energy_speech``、commit 门只写 ``_speech_seen``，而
        ``_speech_seen`` 在每次 commit 后复位——于是"本回合 commit 过一次、之后
        又说了话"时，字幕照常滚动而 commit 永远缺席：用户看得见自己的话，回合却
        再也不结束，整通电话就此哑掉。
        """
        return (
            self._speech_seen
            or self._uncommitted_speech_secs >= _MIN_COMMIT_SPEECH_SECS
        )

    def _prune_pending_commits(self) -> None:
        """丢掉太久没有 final 的 commit。

        ``UserStoppedSpeakingFrame`` 不再清空队列（见 ``process_frame``），所以
        队列里可能留下一条"服务端始终没回 final"的记录；不清理它，下一个 final
        会被按 FIFO 错配到它身上。超过 ``_PENDING_COMMIT_MAX_AGE_SECS`` 即视为
        已丢失——这条记录也不再有任何可配对的 final 会来。
        """
        cutoff = time.time() - _PENDING_COMMIT_MAX_AGE_SECS
        while self._pending_commits and self._pending_commits[0].sent_at < cutoff:
            stale = self._pending_commits.popleft()
            logger.warning(
                f"{self}: dropping commit {stale.event_id} (trigger={stale.trigger})"
                f" after {_PENDING_COMMIT_MAX_AGE_SECS:.0f}s without a final"
            )
            self._dropped_finals += 1

    async def _note_final_dropped(self, *, text_len: int, reason: str) -> None:
        """丢掉的 final 必须可见。

        以前这两条路径只写一行 DEBUG：用户看到的是"我说了话但什么都没发生"，
        而服务端日志里连一条 WARNING 都没有，排障时无从下手。现在每次都记
        WARNING，并按 ``_FINAL_DROP_NOTICE_INTERVAL_SECS`` 节流上报一条 durable
        notice（同一会话里的第一次必定上报）。
        """
        self._dropped_finals += 1
        logger.warning(
            f"{self}: dropped ASR final without a matching commit"
            f" (reason={reason}, text_len={text_len}, total_dropped={self._dropped_finals})"
        )
        if self._journal is None:
            return
        now = time.monotonic()
        if now - self._last_final_drop_notice_at < _FINAL_DROP_NOTICE_INTERVAL_SECS:
            return
        self._last_final_drop_notice_at = now
        await self._journal.notice(
            "asr",
            "final_without_commit",
            f"收到 {self._dropped_finals} 条没有配对 commit 的 final"
            f"（最近一条 reason={reason}, text_len={text_len}），这些文本无法进入转录",
        )

    def _assistant_reply_in_flight(self) -> bool:
        """助手是否还欠着这一句的回答（== 用户此刻多半只是在回应）。

        本地判据是 ``_bot_speaking``（输出传输开始写音频）；账本还多知道一段窗口：
        用户的话已经有归属、回合还没落定——从 LLM 出第一个字到第一帧音频真的写出去
        之间有一到几秒（实测 1~3s），而那正是用户最容易应一声「嗯」的空档。
        """
        if self._bot_speaking:
            return True
        checker = getattr(self._journal, "assistant_reply_in_flight", None)
        if checker is None:
            return False
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - 过滤是尽力而为，绝不能让 ASR 读循环挂掉
            logger.debug(f"{self}: assistant reply query failed", exc_info=True)
            return False

    def _is_backchannel_filler(self, text: str) -> bool:
        """整句都是背声词（「嗯。」「哦。」「对。」…）且助手正在作答 → 不入管线。

        背声词是"我在听"的回应，不是一次发言。放它过去会做三件错事：起一个用户
        回合、把正在播的回答打断（实测 ``嗯。`` 13ms 后 turn.interrupted，随后这
        句背声词还被当成一个问题回答）、并污染下一轮的用户消息。因此在文本出生的
        这一层就掐掉：账本、上屏（RTVI ``user-transcription``）、聚合器、记忆都
        不会再看到它。

        反过来，"助手在作答时才算"是刻意的：助手没在作答、回合也已落定时，孤零零
        一个「嗯」很可能**就是**回答（"听懂了吗？"→"嗯"），那种情况必须照常放行。
        """
        if not text or not is_backchannel(text):
            return False
        if not self._assistant_reply_in_flight():
            return False
        self._suppressed_backchannels += 1
        logger.debug(
            f"{self}: suppressed backchannel while assistant is replying"
            f" (text_len={len(text)}, total={self._suppressed_backchannels})"
        )
        timeline_mark("stt", "背声词滤除", f"text_len={len(text)}")
        return True

    async def _finish_segment(self, trigger: str) -> None:
        """本机判定"这一句说完了" → 让上游把当前这一段 finalize 出来。

        两种断句模式的手段不同、语义相同（见模块 docstring）：

        * **Manual**：``input_audio_buffer.commit``——该模式下这是唯一的 finalize
          手段，也是产生 final 的唯一触发点。
        * **云端 VAD**：补一段静音 PCM。官方文档把 ``commit`` 标注为"禁用场景：VAD
          模式"，发了会被忽略；而云端的段尾判定读的就是**收到的静音**，本机又把静音
          挡在门外（``run_stt`` 的两个门），所以必须由我们主动补上这个"没有语音了"的
          信号，它才会 finish 本段。

        两者都只在"自上次收尾以来本机确实听到语音"（``_speech_since_commit``）时执行：
        对一段没有语音的缓冲收尾，Manual 会拿到幻觉 final，云端 VAD 则是白发一段静音
        （只多付计费）。
        """
        if self._closed or not self._upstream_alive():
            return
        if not self._commit_on_eou:
            return
        if not self._speech_since_commit():
            logger.debug(f"{self}: skipped segment finish on EOU (no local speech this turn)")
            return
        if self._cloud_vad_enabled:
            if self._server_commits and not self._audio_chunks_since_server_commit:
                # 云端已经自己收尾了这一段，此后我们没再送过音频：补静音落进空缓冲，
                # 等不到 final，只会多付一段静音计费，并把"补了几段/回收了几段"的账
                # 永久拉偏（那正是 cloud_vad_no_final 误报的来源）。
                #
                # 正常路径上这一步已经被上一行的 ``_speech_since_commit()`` 挡住了
                # （服务端的 committed 会复位本地门闩）；这条判据是补刀：本地 VAD 报
                # 了"开口"、我们却因为音量门没把任何音频送上去时（门闩为真、计数为 0），
                # 补静音同样只会白发一段。
                logger.debug(
                    f"{self}: skipped pad on EOU (云端已自行收尾这一段，"
                    f"commits={self._server_commits})"
                )
                timeline_mark("stt", "云端已收尾，跳过补静音")
                return
            await self._pad_silence(trigger)
            return
        await self._send_commit(trigger)

    async def _pad_silence(self, trigger: str) -> None:
        """向云端追加一段静音 PCM：云端 VAD 模式下的"段尾信号"。

        长度 = ``_segment_silence_ms``（默认 ``silence_duration_ms`` + 200ms 余量），
        按 ``SEGMENT_SILENCE_CHUNK_MS`` 分片追加。追加完成后与 commit 成功一样复位
        门闩：本段已经交给服务端了，此后用户再说话必须重新攒够语音量才值得再收尾一次。

        计费说明：静音帧同样计入 STT 的音频时长（每回合多付约 0.6s），这是"让云端
        自己收尾"的代价；换来的是噪音不再被当成一句话打断对话。
        """
        ws = self._ws
        if ws is None:
            return
        step_ms = SEGMENT_SILENCE_CHUNK_MS
        total_ms = float(self._segment_silence_ms)
        silence = bytes(int(16000 * step_ms / 1000.0) * 2)  # 16k 单声道 PCM16 全零
        encoded = base64.b64encode(silence).decode("ascii")
        sent_ms = 0.0
        try:
            while sent_ms < total_ms:
                await ws.send(
                    json.dumps(
                        {
                            "event_id": self._next_event_id("silence"),
                            "type": "input_audio_buffer.append",
                            "audio": encoded,
                        }
                    )
                )
                sent_ms += step_ms
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: pad silence failed: {exc}")
            await self._retire_upstream("pad_failed")
            return
        # 补静音是上游流量：在它触发的 final 回来之前不许退役这条会话。
        self._last_upstream_event_at = time.monotonic()
        self._speech_seen = False
        self._uncommitted_speech_secs = 0.0
        self._last_partial_text = ""
        self._segments_finished += 1
        self._segments_padded += 1
        if self._pad_stall_deadline is None:
            # 补了静音就要有一个 final 来回应它；没人回应就报出来（见 _check_pad_stall）。
            self._pad_stall_deadline = time.monotonic() + self._pad_final_grace_secs
        logger.debug(
            f"{self}: padded {total_ms:.0f}ms silence to finish segment "
            f"#{self._segments_finished} (trigger={trigger})"
        )
        timeline_mark("stt", "补静音收尾", f"{total_ms:.0f}ms trigger={trigger}")

    async def _check_pad_stall(self) -> None:
        """补静音之后迟迟没有 final → 报一条 notice（由空闲看门狗每秒驱动一次）。

        这是新模式唯一的失败面：如果服务端不支持 ``turn_detection=server_vad``、
        参数被拒、或者补的静音不足以让它判出段尾，用户侧的表现就是"说话没有任何反应"，
        而那正是我们花了两轮才修掉的哑麦。宁可吵一次，也不能静默。
        """
        deadline = self._pad_stall_deadline
        if deadline is None or time.monotonic() < deadline:
            return
        self._pad_stall_deadline = None
        if self._segments_resolved >= self._segments_padded or self._pad_stall_notified:
            return
        if self._server_commits:
            # 这条连接里云端确实在判段（见过 ``input_audio_buffer.committed``）：本告警
            # 的措辞（"云端 VAD 可能未生效"）就不成立，剩下的只是某次补静音落在已提交的
            # 空缓冲上——那是我们的账没对上，不是用户的故障，只该进 DEBUG。
            logger.debug(
                f"{self}: pad produced no final within "
                f"{self._pad_final_grace_secs:.1f}s, but cloud VAD already committed "
                f"{self._server_commits} time(s) on this connection"
            )
            return
        self._pad_stall_notified = True
        message = (
            f"补静音后 {self._pad_final_grace_secs:.1f}s 内没有收到 final"
            f"（已补 {self._segments_padded} 段、收到 {self._segments_resolved} 段），"
            "云端 VAD 可能未生效；可临时设 DASHSCOPE_ASR_TURN_DETECTION=none 回退 Manual"
        )
        logger.warning(f"{self}: {message}")
        timeline_mark("stt", "补静音后无 final")
        if self._journal is not None:
            await self._journal.notice("asr", "cloud_vad_no_final", message)

    async def _send_commit(self, trigger: str) -> None:
        """向 DashScope 发送 input_audio_buffer.commit，强制立即出 final（仅 Manual）。

        只有在本机 VAD（或能量兜底）自上次 commit 以来确实听到过语音时才 commit：
        静音缓冲的 commit 会拿到幻觉 final（见 ``__init__`` 的说明），进而自激出
        无限空回合。``trigger`` 仅用于单点计时日志，标明这次 commit 是被谁触发的。
        """
        if self._closed or not self._upstream_alive():
            return
        if not self._commit_on_eou:
            return
        if not self._speech_since_commit():
            logger.debug(f"{self}: skipped commit on EOU (no local speech this turn)")
            return
        self._prune_pending_commits()
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
            # 已提交的语音不再计入"未提交语音量"：此后若用户接着说，必须重新攒够
            # ``_MIN_COMMIT_SPEECH_SECS`` 才允许再提交一次（避免对残留噪声提交）。
            self._uncommitted_speech_secs = 0.0
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
                if self._raw_event_logging:
                    logger.info("[DashScopeRaw] %s", json.dumps(payload, ensure_ascii=False, default=str))
                if etype not in seen_types:
                    seen_types.add(etype)
                    logger.info("[DashScopeEvt] 首次见 type={!r}", etype)
                if etype == "session.finished":
                    # 收尾握手：cleanup() 等这个事件到齐后再断开连线。
                    self._session_finished.set()
                if etype in (
                    "input_audio_buffer.speech_started",
                    "input_audio_buffer.speech_stopped",
                    "input_audio_buffer.committed",
                ):
                    # 云端 VAD 的段边界与提交回执。排障时用它确认"云端确实在判段"，
                    # 而不是只看我们自己补的静音；首次出现已由上面的 info 记过一次。
                    timeline_mark("stt", f"云端{etype.rsplit('.', 1)[-1]}")
                    if etype == "input_audio_buffer.committed":
                        # 服务端自己收尾了这一段：它既是"云端 VAD 真的在判段"的权威证据，
                        # 也让"还有多少音频没被提交"归零——此后没再送音频就不该再补静音。
                        self._server_commits += 1
                        self._audio_chunks_since_server_commit = 0
                        # 服务端已经把这一批音频收下了，等价于我们自己的 commit/补静音完成
                        # 过一次"交接"：本地"自上次交接以来听到过语音"的门闩必须随之复位。
                        # 不复位就还会在 EOU 时再补一段谁也回应不了的静音——真机上
                        # ``committed`` 比我们第一次补静音早 6–113ms，那一段永远等不到
                        # final，补/收的账差 1，于是 ``cloud_vad_no_final`` 误报。
                        self._speech_seen = False
                        self._uncommitted_speech_secs = 0.0
                        self._last_partial_text = ""
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
                    # 否则 VAD 缺席时实时字幕会一句话都不显示。判据与 commit 门
                    # 共用（``_speech_since_commit``）——两门不一致就会出现
                    # "字幕在动、回合永远不结束"。
                    if text and (self._energy_speech or self._speech_since_commit()):
                        # 成对下发（同一次更新两条相同文本）只报第一条；
                        # 假设重置（新句）视为新文本。
                        if text != self._last_partial_text:
                            self._last_partial_text = text
                            # 回声/长段累积会把假设文本撑到数千字（实测
                            # 4964+ 且逐字增长），interim 只用于实时字幕，
                            # 截断到尾部即可，权威文本以 final 为准。
                            display = text[-_INTERIM_MAX_CHARS:] if len(text) > _INTERIM_MAX_CHARS else text
                            if not self._is_backchannel_filler(display):
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
        item_id = str(payload.get("item_id") or "").strip()
        event_id = str(payload.get("event_id") or "").strip()
        final_identity = item_id or event_id
        if final_identity and final_identity in self._seen_final_items:
            logger.debug("[DashScopeEvt] duplicate final ignored identity=%s", final_identity)
            return
        if final_identity:
            self._seen_final_items[final_identity] = None
            while len(self._seen_final_items) > 512:
                self._seen_final_items.popitem(last=False)
        self._prune_pending_commits()
        # 任何一个 final 都算"回应了最近一次补静音"：清掉等待与告警状态。
        # （Manual 模式 ``_segments_padded`` 恒为 0，这里等价于空操作。）
        self._segments_resolved = min(self._segments_padded, self._segments_resolved + 1)
        self._pad_stall_notified = False
        self._pad_stall_deadline = None
        commit: _PendingCommit | None = None
        while self._pending_commits:
            candidate = self._pending_commits.popleft()
            if candidate.connection_generation != self._connection_generation:
                # 上一个连接的 commit：它的音频与新连接无关，丢弃（重连后属正常）。
                logger.debug(
                    "[DashScopeEvt] dropping stale final (connection={}, utterance={})",
                    candidate.connection_generation,
                    candidate.utterance_generation,
                )
                continue
            if candidate.utterance_generation != self._utterance_generation:
                # 回合边界已经翻页（本机 VAD 开口 / Smart Turn 判定结束），但这
                # 条 commit 的音频就是刚刚那一轮说的话：文本必须留下，只是不许
                # 它结束或重开一个回合。旧实现在这里直接丢掉，于是"说话"和
                # "有回合"是两回事——用户说两轮之后麦克风就"死"了。
                candidate.superseded = True
            commit = candidate
            break
        if commit is None and self._cloud_vad_enabled:
            # 云端 VAD 模式：这一段是服务端自己判出来的，本来就没有配对的 commit——
            # 这是**正常路径**（语义等价于 Manual 下那条 commit 的 final），不是丢文本。
            # 文本照常进转录，并允许它结束本回合；若按 Manual 的规则在这里丢弃，
            # "说两轮后哑"就会以另一种形式回来（用户说了话，服务端一句日志都不留）。
            timeline_mark("stt", "云端 VAD 收尾", f"text_len={len(text)}")
            if not text:
                return
            # 用量指标如实上报（识别确实发生了），过滤只决定文本去不去管线。
            await self.emit_stt_usage_metrics()
            if self._is_backchannel_filler(text):
                return
            await self.push_frame(
                TranscriptionFrame(
                    text,
                    self._user_id,
                    time_now_iso8601(),
                    result={
                        "commit_id": None,
                        "item_id": item_id or None,
                        "event_id": event_id or None,
                        "commit_trigger": "server_vad",
                        "connection_generation": self._connection_generation,
                        "utterance_generation": self._utterance_generation,
                        "superseded": False,
                    },
                    finalized=True,
                )
            )
            return
        if commit is None:
            await self._note_final_dropped(text_len=len(text), reason="unsolicited")
            return
        timeline_mark(
            "stt",
            "commit→final 往返",
            f"{time.time() - commit.sent_at:.3f}s text_len={len(text)}",
        )
        if not text:
            return
        # 用量指标如实上报（识别确实发生了），过滤只决定文本去不去管线。
        await self.emit_stt_usage_metrics()
        if self._is_backchannel_filler(text):
            return
        await self.push_frame(
            TranscriptionFrame(
                text,
                self._user_id,
                time_now_iso8601(),
                result={
                    "commit_id": commit.event_id,
                    "item_id": item_id or None,
                    "event_id": event_id or None,
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

"""离线验证 ``LatencyPercentileProcessor`` 收的**本机回合判定模型**读数。

为什么要单独钉住它：``TurnMetricsData`` 到不了 RTVI 的 ``metrics`` 报文
（``RTVIObserver._handle_metrics`` 只认 TTFB/TTFA/TTFAT/Processing/Usage 那几类），
所以这行日志是 Smart Turn 真机耗时的唯一落点 —— 字段名、窗口与阈值口径值得有测试。

覆盖范围：

* 单样本走 "need >=3" 分支，且不污染 TTFB 的采样窗口；
* 满 3 个样本后给出 p50/p90/p99 + min/max，并把 ``is_complete`` 汇总成
  ``turn_complete=k/n``（假停顿也算样本，这是要看的另一半信息）；
* 滚动窗口按 ``WINDOW`` 截断；
* ``MetricsFrame`` 仍原样转发下游 —— 这个 processor 只是旁听，不许吞帧；
* 原有的 TTFB 日志与窗口不受影响。

**未覆盖**：帧是否真的从 ``user_aggregator`` 里的回合策略走到这个 processor。那需要
真机跑一轮语音（本地无 provider 配置，跑不出真实回合），这里只断言它自身的契约；
投递顺序只做静态推断：``build_pipeline_steps`` 里 ``latency`` 在 ``user_aggregator``
之后，而策略的 ``push_frame`` 默认走 DOWNSTREAM。
"""

from __future__ import annotations

import asyncio

import pytest
from loguru import logger
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import TTFBMetricsData, TurnMetricsData
from pipecat.processors.frame_processor import FrameDirection

from app.voice.embedded_bot import LatencyPercentileProcessor


class _Recorder(LatencyPercentileProcessor):
    """记录转发行为的子类；``push_frame`` 是显式转发点。"""

    def __init__(self):
        super().__init__()
        self.forwarded: list[tuple[str, object]] = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):  # type: ignore[override]
        self.forwarded.append((type(frame).__name__, frame))
        # 不往下传：这里没有下游邻居，也不需要。


@pytest.fixture
def logs():
    """把 loguru 的 INFO 抓成列表，测试结束后摘掉 handler。"""
    messages: list[str] = []
    handler_id = logger.add(messages.append, level="INFO", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(handler_id)


def _turn(ms: float, complete: bool, probability: float = 0.9) -> MetricsFrame:
    return MetricsFrame(
        data=[
            TurnMetricsData(
                processor="BaseSmartTurn",
                is_complete=complete,
                probability=probability,
                e2e_processing_time_ms=ms,
            )
        ]
    )


def _ttfb(value: float) -> MetricsFrame:
    return MetricsFrame(data=[TTFBMetricsData(processor="llm", model="m", value=value)])


def _feed(proc: LatencyPercentileProcessor, frames: list[MetricsFrame]) -> None:
    async def run():
        for frame in frames:
            await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

    asyncio.run(run())


def test_single_sample_waits_for_three_and_keeps_ttfb_window_clean(logs):
    proc = _Recorder()
    _feed(proc, [_turn(41.0, True)])

    assert len(proc._turn) == 1
    # TTFB 与回合模型是两个独立窗口：收回合样本不许往 TTFB 里塞东西。
    assert proc._samples == []
    assert any("Smart turn samples so far: 1" in m for m in logs)
    assert not any("Smart turn latency" in m for m in logs)


def test_three_samples_report_percentiles_and_completion_rate(logs):
    proc = _Recorder()
    _feed(proc, [_turn(38.0, True), _turn(41.0, True), _turn(43.0, True)])

    report = [m for m in logs if "Smart turn latency" in m]
    assert len(report) == 1, logs
    line = report[0]
    assert "window=3" in line
    assert "p50=41ms" in line
    assert "min=38ms max=43ms" in line
    # 全部判成回合结束：分母必须也跟着涨，否则这两个数字对不上账。
    assert "turn_complete=3/3" in line


def test_fake_stop_counts_as_sample_but_not_as_complete(logs):
    """Silero 的 stop_secs 判停常落在句中，那些假停顿同样跑一次模型。"""
    proc = _Recorder()
    _feed(
        proc,
        [
            _turn(38.0, True),
            _turn(41.0, True),
            _turn(120.0, False),
            _turn(43.0, True),
        ],
    )

    line = [m for m in logs if "Smart turn latency" in m][-1]
    assert "window=4" in line
    assert "turn_complete=3/4" in line
    # 假停顿那次是慢样本，必须落在 max 上：读数不能被"只统计成功回合"吞掉。
    assert "max=120ms" in line


def test_window_is_rolling(logs):
    proc = _Recorder()
    _feed(proc, [_turn(40.0, True) for _ in range(proc.WINDOW + 5)])

    assert len(proc._turn) == proc.WINDOW + 5  # 采样不丢，只是报窗口滚动
    assert "window=50" in [m for m in logs if "Smart turn latency" in m][-1]


def test_metrics_frame_is_forwarded_and_ttfb_still_works(logs):
    proc = _Recorder()
    _feed(proc, [_turn(40.0, True), _ttfb(0.5), _ttfb(0.6), _ttfb(0.7)])

    # 旁听归旁听，帧必须原样继续往下走。
    assert [kind for kind, _ in proc.forwarded] == ["MetricsFrame"] * 4
    assert len(proc._samples) == 3
    # TTFB 的老行为逐字保留：前两个样本提示、第三个起出百分位。
    assert any("TTFB samples so far: 1" in m for m in logs)
    assert any("TTFB latency" in m for m in logs)

"""Opt-in whole-turn PCM retention and a non-blocking ASR review sidecar.

This reviewer never emits transcription/control frames or invokes the LLM.
An opt-in reviewed aggregator may consume its future at a semantic turn boundary.
"""
from __future__ import annotations

import asyncio
import array
import base64
import hashlib
import io
import json
import math
import sys
import time
import wave
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from loguru import logger

PCM_BYTES_PER_SECOND = 32_000  # PCM16 mono, 16 kHz


def pcm_peak_dbfs(pcm: bytes) -> float | None:
    samples = array.array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(min(samples, default=0)), abs(max(samples, default=0))) / 32768
    return 20 * math.log10(peak) if peak else None


def canonical_zero_padding(pcm: bytes) -> bytes:
    """Keep every nonzero PCM16 sample and every internal gap, exactly.

    Only external exact zeros are replaced by 240ms leading / 400ms trailing
    zeros. Align byte-level zero scans outward so samples such as 0x0100 and
    0x0001 cannot lose a byte. This is request packaging, never capture gating.
    """
    start = (len(pcm) - len(pcm.lstrip(b"\0"))) // 2 * 2
    end = len(pcm.rstrip(b"\0"))
    end += end % 2
    return b"\0" * 7680 + pcm[start:end] + b"\0" * 12800


@dataclass(frozen=True)
class TurnRecording:
    sequence: int
    pcm: bytes
    total_bytes: int
    overflow: bool = False
    revision: int = 0


class TurnAudioBuffer:
    """Keep gaps and weak syllables until Smart Turn's actual turn boundary.

    Before onset only preroll is retained. Overlong turns are explicitly marked
    unusable for review, rather than silently submitting a truncated sentence.
    """
    def __init__(self, *, preroll_bytes: int = 7680, max_bytes: int = 60 * PCM_BYTES_PER_SECOND):
        self.preroll_bytes = max(0, preroll_bytes)
        self.max_bytes = max(640, max_bytes)
        self.active = False
        self.overflow = False
        self.total_bytes = 0
        self._sequence = 0
        self._pending = bytearray()
        self._pcm = bytearray()

    @property
    def preserve(self) -> bool:
        return self.active and not self.overflow

    @property
    def current_sequence(self) -> int | None:
        return self._sequence + 1 if self.active else None

    def begin(self) -> None:
        if self.active:
            return
        self.active = True
        prefix = bytes(self._pending)
        self._pending.clear()
        self.append(prefix)

    def append(self, audio: bytes) -> None:
        if not self.active:
            if self.preroll_bytes:
                self._pending.extend(audio)
                del self._pending[:-self.preroll_bytes]
            return
        self.total_bytes += len(audio)
        if self.total_bytes > self.max_bytes:
            self.overflow = True
            self._pcm.clear()
        elif not self.overflow:
            self._pcm.extend(audio)

    def snapshot(self, *, revision: int = 0) -> TurnRecording | None:
        """Immutable prefix for speculative review; never closes retention."""
        if not self.active:
            return None
        return TurnRecording(self.current_sequence, bytes(self._pcm), self.total_bytes, self.overflow, revision)

    def finish(self, *, revision: int = 0) -> TurnRecording | None:
        if not self.active:
            return None
        self._sequence += 1
        result = TurnRecording(self._sequence, bytes(self._pcm), self.total_bytes, self.overflow, revision)
        self.active = self.overflow = False
        self.total_bytes = 0
        self._pcm.clear()
        self._pending.clear()
        return result

    def clear(self) -> None:
        self.finish()
        self._pending.clear()


def review_url(realtime_url: str, *, api_format: str = "openai") -> str:
    """Reuse the configured DashScope region/workspace, never switch regions."""
    parsed = urlsplit(realtime_url)
    host = parsed.hostname or ""
    if host not in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"} and not host.endswith(".maas.aliyuncs.com"):
        raise ValueError("Set DASHSCOPE_ASR_REVIEW_URL for a custom ASR endpoint")
    if api_format == "openai":
        path = "/compatible-mode/v1/chat/completions"
    elif api_format == "dashscope_flash":
        path = "/api/v1/services/aigc/multimodal-generation/generation"
    else:
        raise ValueError("Unsupported ASR review API format")
    return f"https://{parsed.netloc}{path}"


def _request_body(pcm: bytes, model: str, language: str, *, api_format: str = "openai") -> bytes:
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(pcm)
    messages = [{"role": "user", "content": [{"type": "input_audio", "input_audio": {
            "data": "data:audio/wav;base64," + base64.b64encode(data.getvalue()).decode("ascii"),
        }}]}]
    if api_format == "dashscope_flash":
        return json.dumps({"model": model, "input": {"messages": messages}, "parameters": {
            "format": "wav", "sample_rate": "16000", "language_hints": [language],
        }}).encode("utf8")
    if api_format != "openai":
        raise ValueError("Unsupported ASR review API format")
    return json.dumps({
        "model": model, "messages": messages,
        "stream": False,
        "asr_options": {"language": language, "enable_itn": False},
    }).encode("utf8")


class WholeTurnASRReviewer:
    """Bounded async sidecar, with results held locally for experiment inspection."""
    def __init__(self, *, api_key: str, url: str, model: str = "qwen3-asr-flash",
                 language: str = "zh", timeout: float = 5.0, max_pending: int = 2,
                 weak_model: str | None = None, weak_url: str | None = None,
                 weak_peak_dbfs: float = -24.0,
                 canonical_weak_audio: bool = False,
                 client: httpx.AsyncClient | None = None):
        self._api_key, self._url = api_key, url
        self.model, self.language = model, language
        if weak_model and not weak_url:
            raise ValueError("A weak-speech ASR review endpoint is required")
        self.weak_model, self._weak_url = weak_model, weak_url
        self.canonical_weak_audio = canonical_weak_audio
        self.weak_peak_dbfs = max(-80., min(-6., weak_peak_dbfs))
        self.timeout = max(.05, min(30., timeout))
        self.max_pending = max(1, min(4, max_pending))
        self.results: deque[dict] = deque(maxlen=32)
        self._tasks: set[asyncio.Task] = set()
        self._client = client
        self._owns_client = client is None
        self._closed = False

    def submit(self, turn: TurnRecording) -> asyncio.Future:
        if self._closed:
            return self._completed({"sequence": turn.sequence, "status": "closed"})
        if turn.overflow or not turn.pcm:
            result = self._record(turn, "overflow" if turn.overflow else "empty")
            return self._completed(result)
        if sum(not task.done() for task in self._tasks) >= self.max_pending:
            return self._completed(self._record(turn, "capacity"))
        quality = asyncio.get_running_loop().create_future() if self.canonical_weak_audio else None
        task = asyncio.create_task(self._run(turn, time.perf_counter(), quality), name=f"asr-review-{turn.sequence}")
        if quality is not None:
            task.review_quality = quality
            def finish_quality(_):
                if not quality.done():
                    quality.set_result({"status": "unavailable"})
            task.add_done_callback(finish_quality)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _completed(self, result: dict) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        future.set_result(result)
        if self.canonical_weak_audio:
            # A skipped request cannot certify that a provisional draft came
            # from normal speech. Do not silently bypass the weak-review gate.
            quality = asyncio.get_running_loop().create_future()
            quality.set_result({"status": "unavailable"})
            future.review_quality = quality
        return future

    def _record(self, turn: TurnRecording, status: str, **extra) -> dict:
        result = {"sequence": turn.sequence, "revision": turn.revision, "status": status, "model": self.model,
                  "audioMs": turn.total_bytes / 32, **extra}
        self.results.append(result)
        # Text stays in the bounded local result ledger, not application logs.
        logger.debug("ASR review sequence={} status={} audio_ms={} wall_ms={}",
                     turn.sequence, status, turn.total_bytes / 32, extra.get("wallMs"))
        return result

    def _prepare_request(self, turn: TurnRecording):
        """Opt-in routing/packaging, off the audio thread. Keep nonzero PCM.

        Peak is independent of a quiet tail's length. A transient can cause a
        weak turn to retain the original model, rather than routing normal
        speech to a model that regressed on a different recorded command.
        """
        model, url, api_format, peak_dbfs = self.model, self._url, "openai", None
        pcm = turn.pcm
        canonical = False
        if self.weak_model or self.canonical_weak_audio:
            peak_dbfs = pcm_peak_dbfs(pcm)
            # Pure silence is not weak speech and must not select the new route.
            if peak_dbfs is not None and peak_dbfs < self.weak_peak_dbfs:
                if self.weak_model:
                    model, url, api_format = self.weak_model, self._weak_url, "dashscope_flash"
                if self.canonical_weak_audio:
                    pcm = canonical_zero_padding(pcm)
                    canonical = True
        body = _request_body(pcm, model, self.language, api_format=api_format)
        return body, model, url, api_format, peak_dbfs, {"requestAudioMs": len(pcm) / 32,
            "requestPcmSha256": hashlib.sha256(pcm).hexdigest(), "canonicalWeakAudio": canonical}

    async def _run(self, turn: TurnRecording, started: float, quality: asyncio.Future | None = None) -> dict:
        model, api_format, peak_dbfs = self.model, "openai", None
        audio_details = {}
        try:
            async with asyncio.timeout(self.timeout):
                body, model, url, api_format, peak_dbfs, audio_details = await asyncio.to_thread(self._prepare_request, turn)
                if quality is not None and not quality.done():
                    quality.set_result(audio_details)
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self.timeout, trust_env=False)
                response = await self._client.post(url, content=body, headers={
                    "Authorization": "Bearer " + self._api_key, "Content-Type": "application/json",
                    "X-DashScope-SSE": "disable",
                })
                response.raise_for_status()
                value = response.json()
                text = value["output"]["text"] if api_format == "dashscope_flash" else value["choices"][0]["message"]["content"]
                if not isinstance(text, str):
                    raise ValueError("invalid review text")
                return self._record(turn, "ok", text=text.strip(), usage=value.get("usage"),
                             model=model, apiFormat=api_format, peakDbfs=peak_dbfs,
                             **audio_details,
                             wallMs=(time.perf_counter() - started) * 1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Error strings can contain URLs/credentials or echoed audio. Keep
            # only the class and status code in experiment diagnostics.
            code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            return self._record(turn, "timeout" if isinstance(exc, TimeoutError) else "error",
                         error=type(exc).__name__, httpStatus=code,
                         model=model, apiFormat=api_format, peakDbfs=peak_dbfs,
                         **audio_details,
                         wallMs=(time.perf_counter() - started) * 1000)
        finally:
            if quality is not None and not quality.done():
                quality.set_result({"status": "unavailable"})

    async def drain(self) -> None:
        """Explicitly await results in offline tests, never on the audio path."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        if self._client is not None and self._owns_client:
            await self._client.aclose()

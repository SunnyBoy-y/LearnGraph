// 云端 ASR 听写:麦克风持续采集(长会话,不因识别轮次中断),按「静音处
// 切段」把完整语段交给工作区已配置的转写 Provider,保留其原生标点推断。
// 分段在自然停顿处发生,因此不会把词切成两半;讲话不停顿时按最长时长兜底。

const VAD_POLL_MS = 100;
// 时域 RMS 高于该值视为有人声。配合浏览器的降噪(noiseSuppression)使用,
// 普通环境噪声低于该阈值。
const VAD_RMS_THRESHOLD = 0.012;
// 有声后静音持续该时长即在停顿处切段。
const SEGMENT_SILENCE_MS = 700;
// 段最短时长,避免把一次口误切成过碎的计费请求。
const SEGMENT_MIN_MS = 1000;
// 讲话不停顿时的切段兜底,约束单段转写延迟。
const SEGMENT_MAX_MS = 15_000;
// 段内累计有声时长低于该值视为纯噪声,不上传、不计费。
const SEGMENT_MIN_VOICED_MS = 240;
const MAX_CONSECUTIVE_FAILURES = 2;

export type ProviderDictationHandle = {
  /** 优雅收尾:封存当前段、等待全部转写返回。resolve 后文本已全部送达。 */
  stop: () => Promise<void>;
  /** 立即终止:丢弃未完成的段与在途请求结果。 */
  abort: () => void;
};

export type ProviderDictationOptions = {
  /** 上传一个完整语音段,返回转写文本(可为空字符串)。 */
  transcribe: (segment: Blob) => Promise<string>;
  /** 一段转写完成(按讲话顺序回调,永不乱序)。 */
  onSegmentText: (text: string) => void;
  /** 在途 + 排队的转写请求数变化(用于「正在转写」提示)。 */
  onPendingChange?: (pending: number) => void;
  /** 不可恢复的失败(连续转写失败、麦克风被拔出等),调用方负责收尾。 */
  onFatal: (message: string) => void;
};

function pickRecorderMimeType(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  for (const candidate of candidates) {
    if (
      typeof MediaRecorder !== "undefined" &&
      MediaRecorder.isTypeSupported(candidate)
    )
      return candidate;
  }
  return "";
}

export function providerDictationSupported(): boolean {
  return (
    typeof MediaRecorder !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

export async function startProviderDictation(
  options: ProviderDictationOptions,
): Promise<ProviderDictationHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const mimeType = pickRecorderMimeType();

  const audioContext = new AudioContext();
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 2048;
  audioContext.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);

  let recorder: MediaRecorder | null = null;
  let chunks: Blob[] = [];
  let segmentStartedAt = 0;
  let voicedMs = 0;
  let lastVoiceAt = 0;
  let stopped = false;
  let aborted = false;
  let consecutiveFailures = 0;
  let pending = 0;
  let uploadQueue: Promise<void> = Promise.resolve();
  let vadTimer: number | null = null;

  const teardownCapture = () => {
    if (vadTimer !== null) {
      window.clearInterval(vadTimer);
      vadTimer = null;
    }
    for (const track of stream.getTracks()) track.stop();
    void audioContext.close().catch(() => undefined);
  };

  const fatal = (message: string) => {
    if (stopped || aborted) return;
    stopped = true;
    aborted = true;
    try {
      recorder?.stop();
    } catch {
      // recorder 可能已随音轨失效。
    }
    recorder = null;
    teardownCapture();
    options.onFatal(message);
  };

  const setPending = (next: number) => {
    pending = next;
    options.onPendingChange?.(pending);
  };

  const enqueueSegment = (segment: Blob) => {
    setPending(pending + 1);
    uploadQueue = uploadQueue.then(async () => {
      if (aborted) {
        setPending(pending - 1);
        return;
      }
      try {
        const text = await options.transcribe(segment);
        consecutiveFailures = 0;
        if (!aborted && text.trim()) options.onSegmentText(text.trim());
      } catch (error) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
          fatal(
            error instanceof Error && error.message
              ? `语音转写连续失败：${error.message}`
              : "语音转写连续失败，已停止听写。",
          );
        }
      } finally {
        setPending(pending - 1);
      }
    });
  };

  /** 结束当前段;有声则入队上传,随后视状态开启下一段。 */
  const rotateSegment = () =>
    new Promise<void>((resolve) => {
      const active = recorder;
      if (!active || active.state === "inactive") {
        resolve();
        return;
      }
      const voiced = voicedMs >= SEGMENT_MIN_VOICED_MS;
      active.onstop = () => {
        const segment = new Blob(chunks, {
          type: mimeType || chunks[0]?.type || "audio/webm",
        });
        chunks = [];
        if (voiced && segment.size > 0 && !aborted) enqueueSegment(segment);
        if (!stopped && !aborted) startSegment();
        resolve();
      };
      active.stop();
    });

  function startSegment() {
    const next = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);
    next.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    // timeslice 让长段周期性落盘,避免一次性大内存块。
    next.start(1000);
    recorder = next;
    segmentStartedAt = performance.now();
    voicedMs = 0;
    lastVoiceAt = 0;
  }

  const pollVad = () => {
    if (stopped || aborted) return;
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (let index = 0; index < samples.length; index += 1) {
      const value = samples[index];
      sum += value * value;
    }
    const rms = Math.sqrt(sum / samples.length);
    const now = performance.now();
    if (rms >= VAD_RMS_THRESHOLD) {
      voicedMs += VAD_POLL_MS;
      lastVoiceAt = now;
    }
    const elapsed = now - segmentStartedAt;
    const pausedAfterSpeech =
      voicedMs >= SEGMENT_MIN_VOICED_MS &&
      lastVoiceAt > 0 &&
      now - lastVoiceAt >= SEGMENT_SILENCE_MS &&
      elapsed >= SEGMENT_MIN_MS;
    if (pausedAfterSpeech || elapsed >= SEGMENT_MAX_MS) {
      void rotateSegment();
    }
  };

  // 麦克风被系统或用户收回(拔设备、权限撤销)时主动收尾。
  for (const track of stream.getAudioTracks()) {
    track.onended = () => fatal("麦克风已断开，语音输入停止。");
  }

  startSegment();
  vadTimer = window.setInterval(pollVad, VAD_POLL_MS);

  return {
    stop: async () => {
      if (stopped) return;
      stopped = true;
      await rotateSegment();
      teardownCapture();
      recorder = null;
      await uploadQueue;
    },
    abort: () => {
      if (aborted) return;
      aborted = true;
      stopped = true;
      try {
        recorder?.stop();
      } catch {
        // recorder 可能已随音轨失效。
      }
      recorder = null;
      teardownCapture();
    },
  };
}

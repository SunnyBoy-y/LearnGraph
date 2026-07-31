// DashScope realtime 模型(qwen3-asr-flash-realtime / paraformer-realtime /
// gummy-realtime)只提供 WebSocket 接口。这里与后端 /sessions/dictation/realtime
// 建立一条真正的长连接:麦克风 PCM 持续上行,增量识别结果(带原生标点)
// 逐句下行,partial 逐字刷新、final 定稿追加,全程不中断。

import { apiClient } from "@/api/client";
import { authStore } from "@/api/auth-store";

const TARGET_SAMPLE_RATE = 16_000;
// 每帧约 128ms 音频;帧太小徒增开销,太大则字幕延迟明显。
const FRAME_SAMPLES = 2_048;
const STOP_DRAIN_TIMEOUT_MS = 20_000;

export type RealtimeDictationHandle = {
  /** 优雅收尾:结束上行并等待剩余识别结果送达。 */
  stop: () => Promise<void>;
  /** 立即终止:丢弃未送达的识别结果。 */
  abort: () => void;
};

export type RealtimeDictationOptions = {
  providerId: string;
  modelId: string;
  /** 未定稿的当前句(整句替换,不追加)。 */
  onPartial: (text: string) => void;
  /** 一句定稿(按顺序追加)。 */
  onFinal: (text: string) => void;
  /** 不可恢复失败(鉴权、上游任务失败、麦克风断开),调用方负责收尾。 */
  onFatal: (message: string, code?: string) => void;
};

export function realtimeDictationSupported(): boolean {
  return (
    typeof WebSocket !== "undefined" &&
    typeof AudioContext !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

function realtimeEndpointUrl(): string {
  const base = apiClient.baseUrl;
  const absolute = /^https?:\/\//i.test(base)
    ? base
    : `${window.location.origin}${base.startsWith("/") ? "" : "/"}${base}`;
  return `${absolute.replace(/^http/i, "ws")}/sessions/dictation/realtime`;
}

/** 线性重采样到 16kHz 并转 Int16 PCM。 */
function toPcm16(samples: Float32Array, sourceRate: number): ArrayBuffer {
  let mono = samples;
  if (sourceRate !== TARGET_SAMPLE_RATE) {
    const ratio = sourceRate / TARGET_SAMPLE_RATE;
    const length = Math.floor(samples.length / ratio);
    const resampled = new Float32Array(length);
    for (let index = 0; index < length; index += 1) {
      const position = index * ratio;
      const left = Math.floor(position);
      const right = Math.min(left + 1, samples.length - 1);
      const weight = position - left;
      resampled[index] =
        samples[left] * (1 - weight) + samples[right] * weight;
    }
    mono = resampled;
  }
  const pcm = new Int16Array(mono.length);
  for (let index = 0; index < mono.length; index += 1) {
    const value = Math.max(-1, Math.min(1, mono[index]));
    pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return pcm.buffer;
}

export async function startRealtimeDictation(
  options: RealtimeDictationOptions,
): Promise<RealtimeDictationHandle> {
  const token = authStore.getAccessToken();
  const workspaceId = authStore.getWorkspaceId();
  if (!token || !workspaceId) throw new Error("当前会话未登录");

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  let audioContext: AudioContext;
  try {
    audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  } catch {
    // 少数平台不接受 16kHz 上下文,退回默认采样率再软件重采样。
    audioContext = new AudioContext();
  }

  let socket: WebSocket | null = null;
  let processor: ScriptProcessorNode | null = null;
  let ready = false;
  let stopped = false;
  let aborted = false;
  let doneResolve: (() => void) | null = null;

  const teardown = () => {
    ready = false;
    if (processor) {
      processor.onaudioprocess = null;
      processor.disconnect();
      processor = null;
    }
    for (const track of stream.getTracks()) track.stop();
    void audioContext.close().catch(() => undefined);
  };

  const fatal = (message: string, code?: string) => {
    if (stopped || aborted) return;
    stopped = true;
    aborted = true;
    teardown();
    try {
      socket?.close();
    } catch {
      // 连接可能已经关闭。
    }
    options.onFatal(message, code);
  };

  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const ws = new WebSocket(realtimeEndpointUrl());
    ws.binaryType = "arraybuffer";
    socket = ws;
    ws.onopen = () => {
      // 无论上下文实际采样率是多少,上行帧都已重采样到 16kHz。
      ws.send(
        JSON.stringify({
          type: "start",
          token,
          workspace_id: workspaceId,
          provider_id: options.providerId,
          model_id: options.modelId,
          sample_rate: TARGET_SAMPLE_RATE,
        }),
      );
    };
    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let frame: Record<string, unknown>;
      try {
        frame = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return;
      }
      const type = frame.type;
      if (type === "ready") {
        ready = true;
        if (!settled) {
          settled = true;
          resolve();
        }
        return;
      }
      if (type === "partial" && typeof frame.text === "string") {
        if (!aborted) options.onPartial(frame.text);
        return;
      }
      if (type === "final" && typeof frame.text === "string") {
        if (!aborted) options.onFinal(frame.text);
        return;
      }
      if (type === "done") {
        doneResolve?.();
        return;
      }
      if (type === "error") {
        const message =
          typeof frame.message === "string" && frame.message
            ? frame.message
            : "实时语音转写失败";
        const code = typeof frame.code === "string" ? frame.code : undefined;
        if (!settled) {
          settled = true;
          teardown();
          reject(Object.assign(new Error(message), { code }));
          return;
        }
        fatal(`实时语音转写失败：${message}`, code);
      }
    };
    ws.onerror = () => {
      if (!settled) {
        settled = true;
        teardown();
        reject(new Error("无法连接实时语音转写服务"));
      }
    };
    ws.onclose = () => {
      doneResolve?.();
      if (!settled) {
        settled = true;
        teardown();
        reject(new Error("实时语音转写连接已断开"));
        return;
      }
      if (!stopped) fatal("实时语音转写连接已断开");
    };
  });

  // 连接就绪后再接音频管线:ScriptProcessor 输出保持静音,不会回放麦克风。
  const source = audioContext.createMediaStreamSource(stream);
  processor = audioContext.createScriptProcessor(FRAME_SAMPLES, 1, 1);
  processor.onaudioprocess = (event) => {
    if (!ready || stopped || aborted) return;
    const ws = socket;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    // 背压保护:缓冲积压过多时丢帧,避免断网时内存无限增长。
    if (ws.bufferedAmount > 1_000_000) return;
    ws.send(toPcm16(event.inputBuffer.getChannelData(0), audioContext.sampleRate));
  };
  source.connect(processor);
  processor.connect(audioContext.destination);

  for (const track of stream.getAudioTracks()) {
    track.onended = () => fatal("麦克风已断开，语音输入停止。");
  }

  return {
    stop: async () => {
      if (stopped) return;
      stopped = true;
      const ws = socket;
      teardown();
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      // 等服务端把剩余识别结果(final)推完并回 done,兜底超时。
      await new Promise<void>((resolve) => {
        let finished = false;
        const finish = () => {
          if (finished) return;
          finished = true;
          window.clearTimeout(timer);
          doneResolve = null;
          resolve();
        };
        const timer = window.setTimeout(finish, STOP_DRAIN_TIMEOUT_MS);
        doneResolve = finish;
        try {
          ws.send(JSON.stringify({ type: "stop" }));
        } catch {
          finish();
        }
      });
      try {
        ws.close();
      } catch {
        // 服务端可能已先关闭。
      }
    },
    abort: () => {
      if (aborted) return;
      aborted = true;
      stopped = true;
      teardown();
      try {
        socket?.close();
      } catch {
        // 连接可能已经关闭。
      }
    },
  };
}

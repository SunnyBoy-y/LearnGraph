// 双通道自动降级编排层（语音输入不中断）。
//
// realtime WS 长连接失败/超时时，自动切换到分段上传通道；已定稿文本由
// 调用方按 onFinal 语义保留（追加而非清空），仅在通道切换时通过 onDegrade
// 轻提示用户。每通道连续失败 ≥2 次才降级，避免瞬时网络抖动造成误切换；
// 降级发生后本会话不再回退 WS 通道。整条降级链走完仍失败才 onFatal。

import {
  realtimeDictationSupported,
  startRealtimeDictation,
} from "./realtime-dictation";
import {
  providerDictationSupported,
  startProviderDictation,
} from "./provider-dictation";

/** WS 启动即失败（start promise reject）时的可降级失败码。 */
const DEGRADABLE_START_CODES = new Set([
  "realtime_model_required", // 配置模型并非 realtime → 分段通道按 stored 模型重选
  "realtime_unsupported_provider", // base_url 非 DashScope → 同 Provider 的 HTTP 通道
  "upstream_connect_failed", // DashScope WS 不可达
  "asr_task_failed", // 任务被拒/中断
  "transcription_provider_unavailable", // 无可用 ASR Provider → 分段通道可能另有可用模型
]);

/** 运行中 fatal 的可降级失败码（协议/鉴权类不可降级）。 */
const DEGRADABLE_FATAL_CODES = new Set([
  "upstream_connect_failed",
  "asr_task_failed",
]);

// 无 code 的网络层错误（WS onerror / 未 ready 即断开）视为可降级瞬时错误。
const DEGRADABLE_CODELESS = true;

/** WS 通道最大连续失败次数；达到后降级到分段通道。 */
const MAX_REALTIME_FAILURES = 2;

export type DictationOrchestratorOptions = {
  /** realtime 通道配置；缺省时（或浏览器不支持 WS 实时）直接走分段通道。 */
  realtime?: {
    providerId: string;
    modelId: string;
    /** 显式语言（BCP-47，如 zh-CN / en-US）；"auto" 或缺省由模型自动检测。 */
    language?: string;
    /** 热词表文本列表，透传给支持热词的 ASR 模型。 */
    hotwords?: string[];
  };
  /** 分段通道本机 VAD 自适应基线开关（默认开启）。 */
  adaptiveVad?: boolean;
  /** 分段通道转写函数（由调用方绑定 stored provider）。 */
  transcribeSegment: (segment: Blob) => Promise<string>;
  /** 未定稿的当前句（仅 realtime 通道产生，逐字刷新）。 */
  onPartial: (text: string) => void;
  /** 一句/一段定稿（按顺序追加）。 */
  onFinal: (text: string) => void;
  /** 通道降级轻提示。 */
  onDegrade: (message: string) => void;
  /** 实时音量(0..1)，用于语音条波形。 */
  onLevel?: (level: number) => void;
  /** 在途 + 排队请求数变化（分段通道）。 */
  onPendingChange?: (pending: number) => void;
  /** 不可恢复失败（整条降级链走完，或协议/鉴权/麦克风权限类错误）。 */
  onFatal: (message: string, code?: string) => void;
};

export type DictationOrchestratorHandle = {
  /** 优雅收尾：等当前通道剩余结果送达。 */
  stop: () => Promise<void>;
  /** 立即终止：丢弃未送达结果。 */
  abort: () => void;
};

function isDegradableStartError(error: Error & { code?: string }): boolean {
  if (error.code) return DEGRADABLE_START_CODES.has(error.code);
  return DEGRADABLE_CODELESS;
}

function isDegradableFatal(code: string | undefined): boolean {
  if (code) return DEGRADABLE_FATAL_CODES.has(code);
  return DEGRADABLE_CODELESS;
}

export function startDictationOrchestrator(
  options: DictationOrchestratorOptions,
): Promise<DictationOrchestratorHandle> {
  let active: { stop: () => Promise<void>; abort: () => void } | null = null;
  let stopped = false;
  let aborted = false;

  const handle: DictationOrchestratorHandle = {
    stop: async () => {
      if (stopped) return;
      stopped = true;
      await active?.stop();
    },
    abort: () => {
      if (aborted) return;
      aborted = true;
      stopped = true;
      active?.abort();
    },
  };

  /** 启动分段上传通道；失败会 reject（麦克风权限/无可用 Provider），由顶层冒泡。 */
  const startSegmented = () =>
    startProviderDictation({
      transcribe: options.transcribeSegment,
      adaptiveVad: options.adaptiveVad,
      onLevel: options.onLevel,
      onSegmentText: (text) => {
        if (stopped || aborted) return;
        options.onFinal(text);
      },
      onPendingChange: options.onPendingChange,
      onFatal: (message) => options.onFatal(message),
    }).then((segmentedHandle) => {
      if (stopped || aborted) {
        segmentedHandle.abort();
        return;
      }
      active = segmentedHandle;
    });

  /** 尝试 realtime WS 通道；连续失败达到上限后降级到分段通道。 */
  const tryRealtime = (attempt: number): Promise<unknown> => {
    if (stopped || aborted) return Promise.resolve();
    const realtime = options.realtime;
    if (!realtime || !realtimeDictationSupported()) return startSegmented();
    return startRealtimeDictation({
      providerId: realtime.providerId,
      modelId: realtime.modelId,
      language: realtime.language,
      hotwords: realtime.hotwords,
      onLevel: options.onLevel,
      onPartial: (text) => {
        if (stopped || aborted) return;
        options.onPartial(text);
      },
      onFinal: (text) => {
        if (stopped || aborted) return;
        options.onFinal(text);
      },
      onFatal: (message, code) => {
        if (stopped || aborted) return;
        if (isDegradableFatal(code) && attempt < MAX_REALTIME_FAILURES) {
          // 运行中失败：先重试 WS（瞬时抖动自愈），连续失败再降级。
          void tryRealtime(attempt + 1);
          return;
        }
        if (isDegradableFatal(code)) {
          options.onDegrade("实时语音通道不可用，已切换分段模式");
          void startSegmented().catch((error: unknown) => {
            if (!aborted)
              options.onFatal(
                error instanceof Error && error.message
                  ? error.message
                  : "语音输入启动失败",
              );
          });
          return;
        }
        options.onFatal(message, code);
      },
    })
      .then((realtimeHandle) => {
        if (stopped || aborted) {
          realtimeHandle.abort();
          return;
        }
        active = realtimeHandle;
      })
      .catch((error: Error & { code?: string }) => {
        if (stopped || aborted) return;
        if (isDegradableStartError(error) && attempt < MAX_REALTIME_FAILURES) {
          // 启动即失败：重试一次 WS，仍失败则降级。
          return tryRealtime(attempt + 1);
        }
        if (isDegradableStartError(error)) {
          options.onDegrade("实时语音通道不可用，已切换分段模式");
          return startSegmented();
        }
        // 协议/鉴权/麦克风权限类错误不可降级，直接终止（顶层 reject）。
        options.onFatal(error.message || "无法启动实时语音转写", error.code);
        return undefined;
      });
  };

  return tryRealtime(1).then(() => handle);
}

/** 浏览器是否具备任一可用听写通道（供 UI 渲染语音按钮状态）。 */
export function dictationOrchestratorSupported(): boolean {
  return providerDictationSupported() || realtimeDictationSupported();
}

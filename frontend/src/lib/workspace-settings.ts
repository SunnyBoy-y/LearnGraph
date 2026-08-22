import type { WorkspaceSetting } from "@/types/settings";
import type { ResponseMode } from "@/lib/session-composer-prefs";

export const CHAT_SUGGESTED_PROMPTS_SETTING_KEY = "chat.suggested_prompts";
export const CHAT_AUTO_TITLE_MODEL_SETTING_KEY = "chat.auto_title_model";
export const CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY =
  "chat.suggested_prompts_model";
export const CHAT_DICTATION_CLEANUP_SETTING_KEY = "chat.dictation_cleanup";
export const CHAT_CONTEXT_USAGE_SETTING_KEY = "chat.context_usage";
export const CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY =
  "chat.dictation_cleanup_model";
/** ASR 显式语言选择（"auto" / zh-CN / en-US / 其他 BCP-47）。 */
export const CHAT_ASR_LANGUAGE_SETTING_KEY = "chat.asr_language";
/** ASR 热词表（术语/人名/代码标识符），paraformer-realtime-v2 原生支持。 */
export const CHAT_ASR_HOTWORDS_SETTING_KEY = "chat.asr_hotwords";
/** 本机/云端 VAD 分工：auto | local_only（实时模型也走分段，省计费）| cloud_only（调试）。 */
export const CHAT_ASR_VAD_MODE_SETTING_KEY = "chat.asr_vad_mode";
/** 本机 VAD 是否启用自适应基线（学习环境底噪动态调阈值）。 */
export const CHAT_ASR_VAD_ADAPTIVE_SETTING_KEY = "chat.asr_vad_adaptive";
export const CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY =
  "chat.default_response_mode";
export const CHAT_THINKING_CHAIN_DEFAULT_SETTING_KEY =
  "chat.thinking_chain_default";
export { CHAT_RESPONSE_STYLE_SETTING_KEY } from "@/lib/response-style";
export const TRAJECTORY_ENABLED_SETTING_KEY =
  "trajectory.enabled";

export type ChatFeatureModelSetting = {
  provider_id: string | null;
  model_id: string | null;
};

export type ChatDefaultResponseModeSetting = {
  response_mode: ResponseMode;
};

const DEFAULT_RESPONSE_MODE: ResponseMode = "agentic";

function isResponseMode(value: unknown): value is ResponseMode {
  return value === "fast" || value === "thinking" || value === "agentic";
}

export function areChatSuggestedPromptsEnabled(
  settings: WorkspaceSetting[] | undefined,
): boolean {
  const value = settings?.find(
    (setting) => setting.key === CHAT_SUGGESTED_PROMPTS_SETTING_KEY,
  )?.value;

  if (!value || typeof value !== "object" || !("enabled" in value)) return true;
  return value.enabled !== false;
}

export function isChatContextUsageEnabled(
  settings: WorkspaceSetting[] | undefined,
): boolean {
  const value = settings?.find(
    (setting) => setting.key === CHAT_CONTEXT_USAGE_SETTING_KEY,
  )?.value;

  if (!value || typeof value !== "object" || !("enabled" in value)) return true;
  return value.enabled !== false;
}

export function isChatDictationCleanupEnabled(
  settings: WorkspaceSetting[] | undefined,
): boolean {
  const value = settings?.find(
    (setting) => setting.key === CHAT_DICTATION_CLEANUP_SETTING_KEY,
  )?.value;

  // 每个语音片段都会产生一次计费调用,未配置时默认关闭。
  if (!value || typeof value !== "object" || !("enabled" in value)) return false;
  return value.enabled === true;
}


/** 轨迹追踪为可选特性，未配置时默认关闭。 */
export function isTrajectoryEnabled(
  settings: WorkspaceSetting[] | undefined,
): boolean {
  const value = settings?.find(
    (setting) => setting.key === TRAJECTORY_ENABLED_SETTING_KEY,
  )?.value;
  return value === true;
}

export function readChatDefaultResponseMode(
  settings: WorkspaceSetting[] | undefined,
): ResponseMode {
  const value = settings?.find(
    (setting) => setting.key === CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY,
  )?.value;
  if (!value || typeof value !== "object") return DEFAULT_RESPONSE_MODE;
  const record = value as Record<string, unknown>;
  return isResponseMode(record.response_mode)
    ? record.response_mode
    : DEFAULT_RESPONSE_MODE;
}

export type ThinkingChainDefaultState = "open" | "collapsed";

/**
 * Processing-phase default state of the thinking chain. Defaults to expanded
 * so the user watches reasoning / plan / tool steps unfold live; history
 * messages always load collapsed regardless of this preference.
 */
export function readChatThinkingChainDefault(
  settings: WorkspaceSetting[] | undefined,
): boolean {
  const value = settings?.find(
    (setting) => setting.key === CHAT_THINKING_CHAIN_DEFAULT_SETTING_KEY,
  )?.value;
  if (!value || typeof value !== "object") return true;
  const record = value as Record<string, unknown>;
  if (record.default_state === "collapsed") return false;
  return true;
}

export function readChatFeatureModelSetting(
  settings: WorkspaceSetting[] | undefined,
  key: string,
): ChatFeatureModelSetting {
  const value = settings?.find((setting) => setting.key === key)?.value;
  if (!value || typeof value !== "object") {
    return { provider_id: null, model_id: null };
  }
  const record = value as Record<string, unknown>;
  const providerId =
    typeof record.provider_id === "string" && record.provider_id.trim()
      ? record.provider_id.trim()
      : null;
  const modelId =
    typeof record.model_id === "string" && record.model_id.trim()
      ? record.model_id.trim()
      : null;
  if (!providerId || !modelId) {
    return { provider_id: null, model_id: null };
  }
  return { provider_id: providerId, model_id: modelId };
}

// ---- ASR 语音输入设置（语言 / 热词 / VAD 分工） ----

export type AsrVadMode = "auto" | "local_only" | "cloud_only";

export interface AsrHotword {
  text: string;
  /** 可选权重（1-10）；默认 5。 */
  weight?: number;
}

const DEFAULT_ASR_LANGUAGE = "auto";
const DEFAULT_ASR_VAD_MODE: AsrVadMode = "auto";

/** 显式 ASR 语言；未配置返回 "auto"（由模型自动检测）。 */
export function readAsrLanguage(
  settings: WorkspaceSetting[] | undefined,
): string {
  const value = settings?.find(
    (setting) => setting.key === CHAT_ASR_LANGUAGE_SETTING_KEY,
  )?.value;
  if (typeof value === "string" && value.trim()) return value.trim();
  return DEFAULT_ASR_LANGUAGE;
}

/** 工作区 ASR 热词表；未配置返回空数组。 */
export function readAsrHotwords(
  settings: WorkspaceSetting[] | undefined,
): AsrHotword[] {
  const value = settings?.find(
    (setting) => setting.key === CHAT_ASR_HOTWORDS_SETTING_KEY,
  )?.value;
  if (!Array.isArray(value)) return [];
  const items: AsrHotword[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim()) {
      items.push({ text: item.trim() });
      continue;
    }
    if (item && typeof item === "object") {
      const record = item as Record<string, unknown>;
      const text = typeof record.text === "string" ? record.text.trim() : "";
      if (!text) continue;
      const weight =
        typeof record.weight === "number" && record.weight > 0
          ? Math.min(10, Math.round(record.weight))
          : undefined;
      items.push({ text, weight });
    }
  }
  return items;
}

/** 本机/云端 VAD 分工模式；默认 auto（实时通道云端 VAD、分段通道本机 VAD）。 */
export function readAsrVadMode(
  settings: WorkspaceSetting[] | undefined,
): AsrVadMode {
  const value = settings?.find(
    (setting) => setting.key === CHAT_ASR_VAD_MODE_SETTING_KEY,
  )?.value;
  if (value === "local_only" || value === "cloud_only") return value;
  return DEFAULT_ASR_VAD_MODE;
}

/** 本机 VAD 自适应基线开关；默认开启。 */
export function isAsrVadAdaptive(
  settings: WorkspaceSetting[] | undefined,
): boolean {
  const value = settings?.find(
    (setting) => setting.key === CHAT_ASR_VAD_ADAPTIVE_SETTING_KEY,
  )?.value;
  return value !== false;
}

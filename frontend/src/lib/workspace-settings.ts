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

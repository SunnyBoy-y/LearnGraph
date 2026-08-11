/**
 * Shared model / thinking-mode picker helpers used by the chat composer and
 * the document learning panel. Single source of truth for how a Provider's
 * capabilities map to selectable models and thinking intensities.
 */

import type {
  Provider,
  ProviderModel,
  ThinkingMode,
} from "@/types/providers";
import { isDashscopeProvider } from "@/types/providers";

export const thinkingLabels: Record<ThinkingMode, string> = {
  off: "关闭",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "极高",
};

export function capabilityThinkingModes(value: unknown): ThinkingMode[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is ThinkingMode =>
      typeof item === "string" && item in thinkingLabels,
  );
}

export function providerCapabilityString(
  provider: Provider | undefined,
  key: string,
) {
  const value = provider?.capabilities[key];
  return typeof value === "string" ? value.trim() : "";
}

export function isRealtimeTranscriptionModel(modelId: string | null | undefined) {
  return Boolean(modelId?.toLocaleLowerCase().includes("realtime"));
}

/**
 * 解析 Provider 的转写模型 ID：优先读能力快照；DashScope 系 Provider
 * （qwen 等，无 transcription 角色）未显式配置时，与后端兜底默认一致。
 */
export function providerAsrModelId(
  provider: Provider | undefined,
  lane: "stored" | "realtime",
): string {
  if (!provider) return "";
  const capabilityKey =
    lane === "realtime"
      ? "default_realtime_transcription_model_id"
      : "default_transcription_model_id";
  const configured = providerCapabilityString(provider, capabilityKey);
  if (configured) return configured;
  if (!isDashscopeProvider(provider)) return "";
  return lane === "realtime" ? "qwen3-asr-flash-realtime" : "qwen3-asr-flash";
}

export function providerModelOptions(
  provider: Provider | undefined,
  discovered: ProviderModel[] | undefined,
  defaultModelCapability = "default_model",
) {
  if (!provider) return [];
  const persistedIds = Array.isArray(provider.capabilities.discovered_model_ids)
    ? provider.capabilities.discovered_model_ids.filter(
        (item): item is string => typeof item === "string" && Boolean(item.trim()),
      )
    : [];
  const persistedCapabilities =
    provider.capabilities.models &&
    typeof provider.capabilities.models === "object" &&
    !Array.isArray(provider.capabilities.models)
      ? (provider.capabilities.models as Record<string, ProviderModel["capabilities"]>)
      : {};
  const persistedModels: ProviderModel[] = persistedIds.map((id) => ({
    id,
    roles: ["llm"],
    streaming: true,
    remote: true,
    enabled: true,
    capabilities: persistedCapabilities[id],
  }));
  // Workspace-pinned manual models live as per-model capability snapshots even
  // when the vendor never reported them; keep them selectable in the composer.
  const manualModels: ProviderModel[] = Object.keys(persistedCapabilities)
    .filter((id) => !persistedIds.includes(id))
    .map((id) => ({
      id,
      roles: ["llm"],
      streaming: true,
      remote: true,
      enabled: true,
      capabilities: persistedCapabilities[id],
    }));
  const byId = new Map(
    [...(discovered ?? persistedModels), ...manualModels].map((model) => [
      model.id,
      model,
    ]),
  );
  const configured = providerCapabilityString(provider, defaultModelCapability);
  if (configured && !byId.has(configured))
    byId.set(configured, {
      id: configured,
      roles: ["llm"],
      streaming: true,
      remote: true,
      enabled: true,
    });
  const rawStates = provider.capabilities.model_states;
  const states =
    rawStates && typeof rawStates === "object" && !Array.isArray(rawStates)
      ? (rawStates as Record<string, unknown>)
      : {};
  return [...byId.values()].filter(
    (model) => model.enabled !== false && states[model.id] !== false,
  );
}

export function fuzzyModelMatch(value: string, query: string): boolean {
  const normalizedValue = value.toLocaleLowerCase();
  const normalizedQuery = query.trim().toLocaleLowerCase();
  if (!normalizedQuery) return true;
  if (normalizedValue.includes(normalizedQuery)) return true;
  let queryIndex = 0;
  for (const character of normalizedValue) {
    if (character === normalizedQuery[queryIndex]) queryIndex += 1;
    if (queryIndex === normalizedQuery.length) return true;
  }
  return false;
}

export function modelChoiceValue(providerId: string, modelId: string): string {
  return `${encodeURIComponent(providerId)}|${encodeURIComponent(modelId)}`;
}

export function parseModelChoiceValue(
  value: string,
): { providerId: string; modelId: string } | null {
  const separator = value.indexOf("|");
  if (separator < 1) return null;
  try {
    return {
      providerId: decodeURIComponent(value.slice(0, separator)),
      modelId: decodeURIComponent(value.slice(separator + 1)),
    };
  } catch {
    return null;
  }
}

export function modelProtocolLabel(providerType: string): string {
  if (providerType === "openai_responses") return "Responses";
  if (providerType === "anthropic_messages") return "Anthropic Messages";
  if (providerType === "ollama") return "Ollama";
  return "Compatible Chat";
}

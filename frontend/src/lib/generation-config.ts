/**
 * Shared generation configuration types, model-group builders, and 3-state
 * retry resolution helpers (historical snapshot, session config, retry draft).
 */

import {
  capabilityThinkingModes,
  modelSupportsAgent,
  providerModelOptions,
  thinkingLabels,
} from "@/lib/model-choices";
import type { ResponseMode, SearchRoute, ThinkingMode } from "@/lib/session-composer-prefs";
import type { UnknownRecord } from "@/types/common";
import {
  isDeepSeekProvider,
  type Provider,
  type ProviderModel,
} from "@/types/providers";

export interface ModelSelectorOption {
  id: string;
  remote?: boolean;
  capabilities?: ProviderModel["capabilities"];
}

export interface ModelSelectorGroup {
  id: string;
  label: string;
  models: ModelSelectorOption[];
  loading?: boolean;
  failed?: boolean;
}

export interface GenerationConfig {
  providerId: string;
  modelId: string;
  responseMode: ResponseMode;
  thinkingMode: ThinkingMode;
  webSearch: boolean;
  allowedDomains: string;
  searchRoute?: SearchRoute;
}

export interface HistoricalAnswerSummary {
  recordedProviderId: string;
  recordedModelId: string;
  searchLabel: string;
  searchEnabled: boolean | null;
  responseMode: ResponseMode | null;
  thinkingMode: ThinkingMode | null;
  allowedDomains: string;
  modelAvailable: boolean;
  recordedModelMissing: boolean;
}

export interface ResolvedRetryConfig {
  historicalConfig: GenerationConfig;
  draftInitialConfig: GenerationConfig;
  summary: HistoricalAnswerSummary;
}

const VALID_THINKING_MODES = new Set<ThinkingMode>([
  "off",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
]);

export function responseModeLabel(mode: ResponseMode): string {
  if (mode === "fast") return "极速";
  if (mode === "thinking") return "思考";
  return "智能体";
}

export const thinkingModeDescriptions: Record<ThinkingMode, string> = {
  off: "关闭深度思考，优先快速响应。",
  low: "适合日常问答、基础分析和常规任务。",
  medium: "平衡推理深度与响应速度，适合多数场景。",
  high: "适合复杂分析、多步推理与代码调试。",
  xhigh: "投入更深推理链，适合高难度综合问题。",
  max: "启用模型最高推理强度，推理更充分但耗时较长。",
};

export function thinkingModeDescription(mode: ThinkingMode): string {
  return thinkingModeDescriptions[mode] ?? thinkingModeDescriptions.low;
}

export function thinkingModesForModel(
  provider: Provider | undefined,
  model: ProviderModel | ModelSelectorOption | undefined,
): ThinkingMode[] {
  return capabilityThinkingModes(
    model?.capabilities?.reasoning_efforts ??
      provider?.capabilities.reasoning_efforts ??
      (provider && isDeepSeekProvider(provider)
        ? ["low", "high", "max"]
        : undefined),
  );
}

export function buildModelSelectorGroups(
  providers: readonly Provider[],
  modelQueries: readonly {
    data?: { models?: ProviderModel[] };
    isPending?: boolean;
    isError?: boolean;
  }[],
  options?: {
    defaultModelCapability?: string;
    filterModel?: (model: ProviderModel) => boolean;
  },
): ModelSelectorGroup[] {
  const defaultCap = options?.defaultModelCapability ?? "default_model";
  const filterModel = options?.filterModel;
  return providers.map((provider, index) => {
    const query = modelQueries[index];
    const models = providerModelOptions(
      provider,
      query?.data?.models,
      defaultCap,
    );
    return {
      id: provider.id,
      label: provider.display_name,
      models: filterModel ? models.filter(filterModel) : models,
      loading: query?.isPending,
      failed: query?.isError,
    };
  });
}

function parseThinkingMode(value: unknown): ThinkingMode | null {
  return typeof value === "string" && VALID_THINKING_MODES.has(value as ThinkingMode)
    ? (value as ThinkingMode)
    : null;
}

function parseTraceDomains(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      .map((item) => item.trim())
      .join(", ");
  }
  return typeof value === "string" ? value.trim() : "";
}

export function findAvailableModel(
  groups: readonly ModelSelectorGroup[],
  providerId: string,
  modelId: string,
): { group: ModelSelectorGroup; model: ModelSelectorOption } | null {
  if (!modelId) return null;
  if (providerId) {
    const exactGroup = groups.find((group) => group.id === providerId);
    const exactModel = exactGroup?.models.find(
      (model) => model.id === modelId && model.remote !== false,
    );
    if (exactGroup && exactModel) {
      return { group: exactGroup, model: exactModel };
    }
    return null;
  }
  const candidates = groups.flatMap((group) => {
    const model = group.models.find((item) => item.id === modelId && item.remote !== false);
    return model ? [{ group, model }] : [];
  });
  return candidates.length === 1 ? candidates[0] : null;
}

export function summarizeAnswerTrace(
  trace: UnknownRecord | undefined,
  groups: readonly ModelSelectorGroup[] = [],
): HistoricalAnswerSummary {
  const safeTrace = trace ?? {};
  const rawModel =
    typeof safeTrace.model_id === "string" ? safeTrace.model_id.trim() : "";
  const recordedModelId = rawModel && rawModel !== "unknown" ? rawModel : "";
  const recordedProviderId =
    typeof safeTrace.provider_id === "string" ? safeTrace.provider_id.trim() : "";

  const route = safeTrace.search_route;
  let searchEnabled: boolean | null = null;
  let searchLabel = "联网状态未知";
  if (typeof safeTrace.web_search === "boolean") {
    searchEnabled = safeTrace.web_search;
    searchLabel = safeTrace.web_search ? "联网已开启" : "联网已关闭";
  } else if (
    ["model_native", "external", "local"].includes(String(route)) ||
    (typeof safeTrace.search_provider_id === "string" &&
      Boolean(safeTrace.search_provider_id.trim()))
  ) {
    searchEnabled = true;
    searchLabel = "联网已开启";
  } else if (route === "disabled") {
    searchEnabled = false;
    searchLabel = "联网已关闭";
  }

  const thinkingMode = parseThinkingMode(safeTrace.thinking_mode);
  let responseMode: ResponseMode | null = null;
  if (safeTrace.agent_mode === true) {
    responseMode = "agentic";
  } else if (safeTrace.agent_mode === false) {
    responseMode = thinkingMode && thinkingMode !== "off" ? "thinking" : "fast";
  } else if (thinkingMode === "off") {
    responseMode = "fast";
  }

  const allowedDomains = parseTraceDomains(safeTrace.allowed_domains);
  const matched = findAvailableModel(groups, recordedProviderId, recordedModelId);
  const modelAvailable = Boolean(matched);
  const recordedModelMissing = Boolean(recordedModelId && groups.length > 0 && !matched);

  return {
    recordedProviderId,
    recordedModelId,
    searchLabel,
    searchEnabled,
    responseMode,
    thinkingMode,
    allowedDomains,
    modelAvailable,
    recordedModelMissing,
  };
}

export function formatHistoricalSummaryLine(summary: HistoricalAnswerSummary): string {
  const parts: string[] = [];
  parts.push(
    summary.recordedModelId
      ? `上次回答：${summary.recordedModelId}`
      : "上次回答：模型未知",
  );
  if (summary.responseMode) {
    parts.push(responseModeLabel(summary.responseMode));
  }
  if (summary.responseMode !== "fast" && summary.thinkingMode) {
    parts.push(`思考${thinkingLabels[summary.thinkingMode]}`);
  }
  parts.push(summary.searchLabel);
  return parts.join(" · ");
}

/**
 * Resolve the 3 independent retry states from a historical answer's trace:
 * - `historicalConfig`: parameters used by "直接重试 / 沿用上次回答配置"
 * - `draftInitialConfig`: initial state for the temporary retry draft (cloned
 *   from historicalConfig, never mutating sessionConfig)
 * - `summary`: display metadata about the recorded answer snapshot
 */
export function resolveHistoricalGenerationConfig({
  trace,
  groups,
  providers,
  sessionFallback,
}: {
  trace: UnknownRecord | undefined;
  groups: readonly ModelSelectorGroup[];
  providers: readonly Provider[];
  sessionFallback: GenerationConfig;
}): ResolvedRetryConfig {
  const summary = summarizeAnswerTrace(trace, groups);
  const matched = findAvailableModel(
    groups,
    summary.recordedProviderId,
    summary.recordedModelId,
  );
  const providerId = matched?.group.id ?? sessionFallback.providerId;
  const modelId = matched?.model.id ?? sessionFallback.modelId;

  const resolvedProvider = providers.find((provider) => provider.id === providerId);
  const fallbackModel = findAvailableModel(groups, providerId, modelId);
  const resolvedModel = fallbackModel?.model as ProviderModel | undefined;
  const supportsAgent = resolvedModel
    ? modelSupportsAgent(resolvedProvider, resolvedModel)
    : true;
  const supportedEfforts = thinkingModesForModel(resolvedProvider, resolvedModel);
  const thinkingRequired = resolvedModel?.capabilities?.thinking_required === true;

  let responseMode: ResponseMode =
    summary.responseMode ?? sessionFallback.responseMode;
  if (responseMode === "agentic" && !supportsAgent) {
    responseMode = supportedEfforts.length > 0 ? "thinking" : "fast";
  } else if (responseMode === "thinking" && supportsAgent) {
    responseMode = "agentic";
  }
  if (responseMode === "fast" && thinkingRequired) {
    responseMode = supportsAgent ? "agentic" : "thinking";
  }

  let thinkingMode: ThinkingMode =
    summary.thinkingMode ??
    (responseMode === "fast" ? "off" : sessionFallback.thinkingMode);
  if (responseMode === "fast" && !thinkingRequired) {
    thinkingMode = "off";
  } else if (!supportedEfforts.length) {
    thinkingMode = "off";
  } else if (
    !supportedEfforts.includes(thinkingMode) &&
    !(thinkingMode === "off" && !thinkingRequired)
  ) {
    thinkingMode = supportedEfforts.includes("medium")
      ? "medium"
      : supportedEfforts[0]!;
  }

  const webSearch = summary.searchEnabled ?? sessionFallback.webSearch;
  const allowedDomains = summary.allowedDomains;

  const route = trace?.search_route;
  const historicalConfig: GenerationConfig = {
    providerId: summary.recordedProviderId || matched?.group.id || providerId,
    modelId: summary.recordedModelId || modelId,
    responseMode: summary.responseMode ?? responseMode,
    thinkingMode: summary.thinkingMode ?? thinkingMode,
    webSearch,
    allowedDomains,
    ...((route === "model_native" || route === "external" || route === "local" || route === "auto") ? { searchRoute: route } : {}),
  };

  return {
    historicalConfig,
    draftInitialConfig: { providerId, modelId, responseMode, thinkingMode, webSearch, allowedDomains },
    summary,
  };
}

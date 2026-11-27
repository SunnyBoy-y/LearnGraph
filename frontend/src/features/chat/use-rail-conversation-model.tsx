import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ChevronDown } from "lucide-react";
import { discoverProviderModels, listProviders } from "@/api/providers";
import { GenerationConfigMenu } from "@/components/chat/generation-config";
import { buildModelSelectorGroups } from "@/lib/generation-config";
import {
  capabilityThinkingModes,
  modelSupportsAgent,
  providerModelOptions,
  thinkingLabels,
} from "@/lib/model-choices";
import { workspaceQueryKey } from "@/lib/query-keys";
import {
  getSessionComposerPrefs,
  prefsFromModelSnapshot,
  setSessionComposerPrefs,
  type ResponseMode,
  type ThinkingMode,
} from "@/lib/session-composer-prefs";
import { migrateThinkingPreference } from "@/lib/thinking-effort-migration";
import { isDeepSeekProvider, isModelProvider } from "@/types/providers";

/** One set of model / thinking controls for every conversation in the side rail. */
export function useRailConversationModel(
  workspaceId: string,
  prefsSessionId: string,
  sessionId?: string,
  snapshot?: Record<string, unknown>,
) {
  const navigate = useNavigate();
  const snapshotPrefs = prefsFromModelSnapshot(snapshot ?? {});
  const initialPrefs = getSessionComposerPrefs(prefsSessionId);
  const [selectedProviderId, setSelectedProviderId] = useState(
    () => initialPrefs.providerId ?? snapshotPrefs.providerId ?? "",
  );
  const [selectedModelId, setSelectedModelId] = useState(
    () => initialPrefs.modelId ?? snapshotPrefs.modelId ?? "",
  );
  const [responseMode, setResponseMode] = useState<ResponseMode>(
    () => initialPrefs.responseMode,
  );
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(
    () => initialPrefs.thinkingMode,
  );
  const [modelSearch, setModelSearch] = useState("");
  const [modeMenuOpen, setModeMenuOpen] = useState(false);

  const providers = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "providers"),
    queryFn: listProviders,
  });
  const modelProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          isModelProvider(provider),
      ),
    [providers.data],
  );
  const providerModelQueries = useQueries({
    queries: modelProviders.map((provider) => ({
      queryKey: workspaceQueryKey(workspaceId, "provider-models", provider.id),
      queryFn: () => discoverProviderModels(provider.id),
      retry: false,
    })),
  });
  const activeProvider =
    modelProviders.find((provider) => provider.id === selectedProviderId) ??
    modelProviders[0];
  const activeProviderIndex = modelProviders.findIndex(
    (provider) => provider.id === activeProvider?.id,
  );
  const discoveredModels =
    activeProviderIndex >= 0
      ? providerModelQueries[activeProviderIndex]
      : undefined;
  const modelOptions = useMemo(
    () => providerModelOptions(activeProvider, discoveredModels?.data?.models),
    [activeProvider, discoveredModels?.data?.models],
  );
  const selectedModel =
    modelOptions.find((model) => model.id === selectedModelId) ??
    modelOptions[0];
  const supportsAgentMode = modelSupportsAgent(activeProvider, selectedModel);
  const modelGroups = useMemo(
    () => buildModelSelectorGroups(modelProviders, providerModelQueries),
    [modelProviders, providerModelQueries],
  );
  const thinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        selectedModel?.capabilities?.reasoning_efforts ??
          activeProvider?.capabilities.reasoning_efforts ??
          (activeProvider && isDeepSeekProvider(activeProvider)
            ? ["low", "high", "max"]
            : undefined),
      ),
    [activeProvider, selectedModel?.capabilities?.reasoning_efforts],
  );
  const thinkingRequired =
    selectedModel?.capabilities?.thinking_required === true;
  const effectiveThinkingMode =
    responseMode === "fast" && !thinkingRequired
      ? "off"
      : thinkingModes.includes(thinkingMode)
        ? thinkingMode
        : (thinkingModes[0] ?? "off");

  useEffect(() => {
    const prefs = getSessionComposerPrefs(prefsSessionId);
    setSelectedProviderId(prefs.providerId ?? snapshotPrefs.providerId ?? "");
    setSelectedModelId(prefs.modelId ?? snapshotPrefs.modelId ?? "");
    setResponseMode(prefs.responseMode);
    setThinkingMode(prefs.thinkingMode);
  }, [prefsSessionId, snapshotPrefs.providerId, snapshotPrefs.modelId]);

  useEffect(() => {
    if (providers.isPending) return;
    setSelectedProviderId((current) =>
      modelProviders.some((provider) => provider.id === current)
        ? current
        : (modelProviders[0]?.id ?? ""),
    );
  }, [modelProviders, providers.isPending]);

  useEffect(() => {
    if (providers.isPending || discoveredModels?.isPending) return;
    setSelectedModelId((current) =>
      modelOptions.some((model) => model.id === current)
        ? current
        : (modelOptions[0]?.id ?? ""),
    );
  }, [modelOptions, providers.isPending, discoveredModels?.isPending]);

  useEffect(() => {
    if (providers.isPending || discoveredModels?.isPending) return;
    const migrated = migrateThinkingPreference(
      prefsSessionId,
      activeProvider?.id,
      selectedModel?.id,
      selectedModel?.capabilities,
      thinkingMode,
    );
    if (migrated !== thinkingMode) {
      setThinkingMode(migrated);
      return;
    }
    setThinkingMode((current) => {
      if (responseMode === "fast" && !thinkingRequired) return "off";
      if (!thinkingModes.length) return current === "off" ? "off" : current;
      if (current === "off" && thinkingRequired) {
        return thinkingModes.includes("medium") ? "medium" : thinkingModes[0]!;
      }
      return thinkingModes.includes(current)
        ? current
        : (thinkingModes[0] ?? "off");
    });
  }, [
    responseMode,
    thinkingModes,
    thinkingRequired,
    providers.isPending,
    discoveredModels?.isPending,
    prefsSessionId,
    activeProvider?.id,
    selectedModel?.id,
    selectedModel?.capabilities,
    thinkingMode,
  ]);

  const persistComposerPrefs = useCallback(
    (
      targetSessionId: string,
      overrides: {
        providerId?: string;
        modelId?: string;
        responseMode?: ResponseMode;
        thinkingMode?: ThinkingMode;
      } = {},
    ) => {
      if (!targetSessionId) return;
      setSessionComposerPrefs(targetSessionId, {
        providerId: overrides.providerId ?? activeProvider?.id,
        modelId: overrides.modelId ?? selectedModel?.id,
        responseMode: overrides.responseMode ?? responseMode,
        thinkingMode: overrides.thinkingMode ?? thinkingMode,
      });
    },
    [activeProvider?.id, selectedModel?.id, responseMode, thinkingMode],
  );

  function setMode(mode: ResponseMode) {
    setResponseMode(mode);
    if (mode === "fast") {
      setThinkingMode("off");
      if (sessionId) {
        persistComposerPrefs(sessionId, {
          responseMode: "fast",
          thinkingMode: "off",
        });
      }
      return;
    }
    const nextThinking =
      thinkingMode === "off" && thinkingModes.length
        ? thinkingModes.includes("medium")
          ? "medium"
          : thinkingModes[0]!
        : thinkingMode;
    setThinkingMode(nextThinking);
    if (sessionId) {
      persistComposerPrefs(sessionId, {
        responseMode: mode,
        thinkingMode: nextThinking,
      });
    }
  }

  function handleThinkingChange(nextThinking: ThinkingMode) {
    setThinkingMode(nextThinking);
    if (sessionId) {
      persistComposerPrefs(sessionId, { thinkingMode: nextThinking });
    }
  }

  const requestPreferences = useMemo(
    () => ({
      provider_id: activeProvider?.id,
      model_id: selectedModel?.id,
      thinking_mode: effectiveThinkingMode,
      agent_mode: responseMode === "agentic" && supportsAgentMode,
    }),
    [
      activeProvider?.id,
      selectedModel?.id,
      effectiveThinkingMode,
      responseMode,
      supportsAgentMode,
    ],
  );

  return {
    canSend: Boolean(activeProvider && selectedModel?.id),
    pending: providers.isPending,
    persistComposerPrefs,
    requestPreferences,
    control: (
      <GenerationConfigMenu
        open={modeMenuOpen}
        onOpenChange={setModeMenuOpen}
        align="start"
        side="top"
        layout="inline"
        groups={modelGroups}
        providerId={activeProvider?.id ?? ""}
        modelId={selectedModel?.id ?? ""}
        responseMode={responseMode}
        thinkingMode={effectiveThinkingMode}
        thinkingModes={thinkingModes}
        supportsAgent={supportsAgentMode}
        thinkingRequired={thinkingRequired}
        modelError={Boolean(discoveredModels?.isError)}
        onModelChange={(providerId, modelId) => {
          setSelectedProviderId(providerId);
          setSelectedModelId(modelId);
          if (sessionId) {
            persistComposerPrefs(sessionId, { providerId, modelId });
          }
        }}
        onModeChange={setMode}
        onThinkingChange={handleThinkingChange}
        onManageModels={() => {
          setModeMenuOpen(false);
          navigate(`/w/${workspaceId}/settings/providers`);
        }}
        onRefreshModels={() => void discoveredModels?.refetch()}
        modelSearch={modelSearch}
        onModelSearchChange={setModelSearch}
        trigger={
          <button
            aria-label="选择响应模式、思考力度和模型"
            className="chat-mode-chip"
            disabled={!modelProviders.length}
            title="生成配置"
            type="button"
          >
            <span>
              {modeMenuOpen
                ? "生成配置"
                : responseMode === "fast"
                  ? "极速"
                  : thinkingLabels[effectiveThinkingMode]}
            </span>
            <ChevronDown aria-hidden="true" className="size-3.5" />
          </button>
        }
      />
    ),
  };
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { ChevronDown, Search } from "lucide-react";
import { discoverProviderModels, listProviders } from "@/api/providers";
import { ThinkingSlider, THINKING_STOPS, thinkingStopIndex } from "@/components/chat/thinking-slider";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { workspaceQueryKey } from "@/lib/query-keys";
import { capabilityThinkingModes, fuzzyModelMatch, modelChoiceValue, modelProtocolLabel, parseModelChoiceValue, providerModelOptions, thinkingLabels } from "@/lib/model-choices";
import { getSessionComposerPrefs, prefsFromModelSnapshot, setSessionComposerPrefs, type ResponseMode, type ThinkingMode } from "@/lib/session-composer-prefs";
import { isDeepSeekProvider, isModelProviderType } from "@/types/providers";

/** One set of model / thinking controls for every conversation in the side rail. */
export function useRailConversationModel(workspaceId: string, prefsSessionId: string, sessionId?: string, snapshot?: Record<string, unknown>) {
  const snapshotPrefs = prefsFromModelSnapshot(snapshot ?? {});
  const initialPrefs = getSessionComposerPrefs(
    prefsSessionId,
  );
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
  /** 响应与模型菜单是否展开：展开时胶囊显示「思考强度」，收起时显示当前档位。 */
  const [modeMenuOpen, setModeMenuOpen] = useState(false);

  const providers = useQuery({ queryKey: workspaceQueryKey(workspaceId, "providers"), queryFn: listProviders });
  const modelProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          isModelProviderType(provider.provider_type),
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
    modelOptions.find((model) => model.id === selectedModelId) ?? modelOptions[0];
  const availableModelChoices = useMemo(
    () =>
      modelProviders.flatMap((provider, index) =>
        providerModelOptions(
          provider,
          providerModelQueries[index]?.data?.models,
        ).map((model) => ({ provider, model })),
      ),
    [modelProviders, providerModelQueries],
  );
  const filteredModelChoices = useMemo(
    () =>
      availableModelChoices.filter(({ provider, model }) =>
        fuzzyModelMatch(
          `${model.id} ${provider.display_name} ${modelProtocolLabel(provider.provider_type)}`,
          modelSearch,
        ),
      ),
    [availableModelChoices, modelSearch],
  );
  const thinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        selectedModel?.capabilities?.reasoning_efforts ??
          activeProvider?.capabilities.reasoning_efforts ??
          (activeProvider && isDeepSeekProvider(activeProvider)
            ? ["low", "medium", "high", "xhigh"]
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
    setSelectedProviderId((current) =>
      modelProviders.some((provider) => provider.id === current)
        ? current
        : (modelProviders[0]?.id ?? ""),
    );
  }, [modelProviders]);

  useEffect(() => {
    setSelectedModelId((current) =>
      modelOptions.some((model) => model.id === current)
        ? current
        : (modelOptions[0]?.id ?? ""),
    );
  }, [modelOptions]);

  useEffect(() => {
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
  }, [responseMode, thinkingModes, thinkingRequired]);

  const persistComposerPrefs = useCallback((
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
  }, [activeProvider?.id, selectedModel?.id, responseMode, thinkingMode]);
  /** 极速：清空思考力度；智能体：力度缺失时补一个可用档位。 */
  function setMode(mode: ResponseMode) {
    setResponseMode(mode);
    if (mode === "fast") {
      setThinkingMode("off");
      if (sessionId) {
        persistComposerPrefs(sessionId, { responseMode: "fast", thinkingMode: "off" });
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
        responseMode: "agentic",
        thinkingMode: nextThinking,
      });
    }
  }

  /** 「响应与思考力度」滑块：档位 0 = 极速，其后每档 = 智能体 + 一个思考力度。 */
  function handleThinkingSliderChange(index: number) {
    const stop = THINKING_STOPS[index];
    if (!stop) return;
    if (stop.responseMode === "fast") {
      setMode("fast");
      return;
    }
    const nextThinking = stop.thinking as ThinkingMode;
    setResponseMode("agentic");
    setThinkingMode(nextThinking);
    if (sessionId) {
      persistComposerPrefs(sessionId, {
        responseMode: "agentic",
        thinkingMode: nextThinking,
      });
    }
  }

  const requestPreferences = useMemo(() => ({
    provider_id: activeProvider?.id,
    model_id: selectedModel?.id,
    thinking_mode: effectiveThinkingMode,
    agent_mode: responseMode !== "fast",
  }), [activeProvider?.id, selectedModel?.id, effectiveThinkingMode, responseMode]);

  return {
    canSend: Boolean(activeProvider && selectedModel?.id),
    pending: providers.isPending,
    persistComposerPrefs,
    requestPreferences,
    control: (
        <DropdownMenu
          open={modeMenuOpen}
          onOpenChange={(open) => {
            setModeMenuOpen(open);
            if (!open) setModelSearch("");
          }}
        >
          <DropdownMenuTrigger asChild>
            <button
              aria-label="选择响应模式、思考力度和模型"
              className="chat-mode-chip"
              // 回复途中同样允许切换：本次回答沿用发送时锁定的模型，改动只对
              // 下一条消息生效。
              disabled={!modelProviders.length}
              title="响应模式与模型"
              type="button"
            >
              <span>
                {modeMenuOpen
                  ? "思考强度"
                  : responseMode === "fast"
                    ? "极速"
                    : thinkingLabels[thinkingMode]}
              </span>
              <ChevronDown aria-hidden="true" className="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64" collisionPadding={12}>
            <DropdownMenuLabel>响应与思考力度</DropdownMenuLabel>
            <div className="px-2 py-1">
              <ThinkingSlider
                onChange={handleThinkingSliderChange}
                value={thinkingStopIndex(responseMode, thinkingMode)}
              />
            </div>
            {!thinkingModes.length ? (
              <p className="px-2 pb-1 text-[10px] text-muted-foreground">
                当前模型未声明推理能力，按服务商默认执行。
              </p>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuLabel>模型</DropdownMenuLabel>
            <div className="relative px-1 pb-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label="搜索模型"
                className="h-8 pl-8 font-mono text-xs"
                onChange={(event) => setModelSearch(event.target.value)}
                onKeyDown={(event) => event.stopPropagation()}
                onPointerDown={(event) => event.stopPropagation()}
                placeholder="搜索模型名称或 Provider…"
                value={modelSearch}
              />
            </div>
            <div className="max-h-[min(50vh,20rem)] overflow-y-auto">
              {filteredModelChoices.length ? (
                <DropdownMenuRadioGroup
                  onValueChange={(value) => {
                    const choice = parseModelChoiceValue(value);
                    if (!choice) return;
                    setSelectedProviderId(choice.providerId);
                    setSelectedModelId(choice.modelId);
                    if (sessionId) {
                      persistComposerPrefs(sessionId, {
                        providerId: choice.providerId,
                        modelId: choice.modelId,
                      });
                    }
                  }}
                  value={
                    activeProvider && selectedModel
                      ? modelChoiceValue(activeProvider.id, selectedModel.id)
                      : ""
                  }
                >
                  {filteredModelChoices.map(({ provider, model }) => (
                    <DropdownMenuRadioItem
                      key={`${provider.id}:${model.id}`}
                      value={modelChoiceValue(provider.id, model.id)}
                    >
                      <span className="flex min-w-0 flex-col">
                        <span className="truncate font-mono">{model.id}</span>
                        <span className="text-[10px] text-muted-foreground">
                          {provider.display_name} ·{" "}
                          {modelProtocolLabel(provider.provider_type)}
                        </span>
                      </span>
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              ) : (
                <DropdownMenuItem disabled>
                  {discoveredModels?.isPending ? "正在载入模型…" : "暂无可用模型"}
                </DropdownMenuItem>
              )}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
    ),
  };
}

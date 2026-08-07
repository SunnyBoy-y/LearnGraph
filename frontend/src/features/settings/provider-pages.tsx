import {
  Children,
  isValidElement,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type FormEvent,
  type ReactNode,
  type SetStateAction,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Bot,
  LockKeyhole,
  Pencil,
  Plus,
  RefreshCcw,
  Search,
  Settings2,
  SlidersHorizontal,
  Trash2,
  WalletCards,
} from "lucide-react";
import { toast } from "sonner";
import { AnimatePresence, motion } from "motion/react";

import deepseekMark from "@/assets/deepseek.svg";
import deepseekBrandMark from "@/assets/brands/si-deepseek.svg";
import anthropicMark from "@/assets/brands/si-anthropic.svg";
import baiduMark from "@/assets/brands/si-baidu.svg";
import bytedanceMark from "@/assets/brands/si-bytedance.svg";
import githubMark from "@/assets/brands/si-github.svg";
import googleGeminiMark from "@/assets/brands/si-googlegemini.svg";
import longcatMark from "@/assets/brands/longcat.svg";
import minimaxMark from "@/assets/brands/si-minimax.svg";
import modelscopeMark from "@/assets/brands/si-modelscope.svg";
import moonshotMark from "@/assets/brands/si-moonshot.svg";
import ollamaMark from "@/assets/brands/si-ollama.svg";
import openrouterMark from "@/assets/brands/si-openrouter.svg";
import qwenMark from "@/assets/brands/si-qwen.svg";
import xiaomiMark from "@/assets/brands/si-xiaomi.svg";
import openAiMark from "@/assets/openai.svg";
import { brandIcon } from "@/lib/brand-icons";
import {
  createProvider,
  deleteProvider,
  deleteProviderModel,
  discoverProviderModels,
  getProviderBalance,
  getProviderModelCapabilities,
  getProviderModelDefaults,
  getSecretStoreStatus,
  listProviderCatalog,
  listProviders,
  pollCodexDeviceLogin,
  pollCopilotDeviceLogin,
  probeProvider,
  rotateProviderSecret,
  startCopilotDeviceLogin,
  startCodexDeviceLogin,
  syncProviderModelCatalogDefaults,
  updateProviderModelCapabilities,
  updateProviderModelGroupCapabilities,
  updateProviderModelStates,
  updateProvider,
} from "@/api";
import { ApiError } from "@/api";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronDown } from "lucide-react";
import {
  isAnthropicProvider,
  isOfficialDeepSeekProvider,
  providerBalanceQueryConfig,
  providerBalanceQueryLastResult,
  providerSupportsBalance,
  providerExtraHeaders,
} from "@/types/providers";
import {
  formatBalanceQuerySummary,
  persistCustomBalanceResult,
  relativeTimeLabel,
  runCustomBalanceQuery,
} from "@/lib/balance-query";
import {
  BalanceQueryConfigDialog,
  CustomBalanceDialog,
} from "./balance-query";
import type {
  CodexDeviceLoginStart,
  CopilotDeviceLoginStart,
  Provider,
  ProviderBalance,
  ProviderModelCapabilities,
  ProviderModelCapabilityView,
  ProviderModelsResponse,
  ProviderRole,
  ProviderTypeCatalogItem,
  ReasoningParameter,
  SearchRoute,
  ThinkingMode,
} from "@/types/providers";
import { Textarea } from "@/components/ui/textarea";
import { isRealtimeTranscriptionModel } from "@/lib/model-choices";

function providerDefaultModelId(provider: Provider): string {
  const capabilities = provider.capabilities;
  for (const key of [
    "default_image_generation_model_id",
    "default_transcription_model_id",
    "default_vision_model_id",
    "default_embedding_model_id",
    "deep_research_model",
    "default_model",
  ]) {
    const value = capabilities[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}
function persistedProviderModels(
  provider: Provider,
): ProviderModelsResponse | undefined {
  const discoveredIds = Array.isArray(provider.capabilities.discovered_model_ids)
    ? provider.capabilities.discovered_model_ids.filter(
        (item): item is string =>
          typeof item === "string" && Boolean(item.trim()),
      )
    : [];
  const rawStates =
    provider.capabilities.model_states &&
    typeof provider.capabilities.model_states === "object" &&
    !Array.isArray(provider.capabilities.model_states)
      ? (provider.capabilities.model_states as Record<string, unknown>)
      : {};
  const rawCapabilities =
    provider.capabilities.models &&
    typeof provider.capabilities.models === "object" &&
    !Array.isArray(provider.capabilities.models)
      ? (provider.capabilities.models as Record<
          string,
          ProviderModelCapabilities
        >)
      : {};
  // Models pinned manually through a catalog-defaults sync live as per-model
  // capability snapshots even when the vendor never reported them, so they
  // belong in the persisted list too.
  const ids = [
    ...discoveredIds,
    ...Object.keys(rawCapabilities).filter((id) => !discoveredIds.includes(id)),
  ];
  if (!ids.length) return undefined;
  return {
    provider_id: provider.id,
    status: "persisted_discovery",
    models: ids.map((id) => ({
      id,
      roles: ["llm"],
      streaming: true,
      remote: true,
      enabled: rawStates[id] !== false,
      capabilities: rawCapabilities[id],
    })),
  };
}

/**
 * Workspace-pinned manual models: per-model capability snapshots whose id the
 * vendor never listed (so it is absent from ``discovered_model_ids``).
 */
function manualProviderModels(provider: Provider): ProviderModelsResponse["models"] {
  const rawCapabilities = provider.capabilities.models;
  const snapshots =
    rawCapabilities &&
    typeof rawCapabilities === "object" &&
    !Array.isArray(rawCapabilities)
      ? (rawCapabilities as Record<string, ProviderModelCapabilities>)
      : {};
  const rawStates = provider.capabilities.model_states;
  const states =
    rawStates && typeof rawStates === "object" && !Array.isArray(rawStates)
      ? (rawStates as Record<string, unknown>)
      : {};
  const discoveredIds = new Set(
    Array.isArray(provider.capabilities.discovered_model_ids)
      ? provider.capabilities.discovered_model_ids.filter(
          (item): item is string =>
            typeof item === "string" && Boolean(item.trim()),
        )
      : [],
  );
  return Object.entries(snapshots)
    .filter(([id]) => !discoveredIds.has(id))
    .map(([id, capabilities]) => ({
      id,
      roles: ["llm"],
      streaming: true,
      remote: true,
      enabled: states[id] !== false,
      capabilities,
    }));
}

/**
 * Effective model list for the provider dialog: the fresh discovery response
 * plus workspace-pinned manual models.  Manual models are computed straight
 * from the persisted capability snapshots so a stale discovery in the
 * providers query can never resurrect removed vendor models.
 */
function mergeProviderModelLists(
  fresh: ProviderModelsResponse | undefined,
  provider: Provider,
): ProviderModelsResponse | undefined {
  const manual = manualProviderModels(provider);
  if (!fresh) {
    if (!manual.length) return undefined;
    return {
      provider_id: provider.id,
      status: "persisted_discovery",
      models: manual,
    };
  }
  const known = new Set(fresh.models.map((model) => model.id));
  const additions = manual.filter((model) => !known.has(model.id));
  if (!additions.length) return fresh;
  return { ...fresh, models: [...fresh.models, ...additions] };
}

function SearchableModelSelect({
  ariaLabel,
  children,
  onValueChange,
  value,
}: {
  ariaLabel?: string;
  children?: ReactNode;
  onValueChange: (value: string) => void;
  value: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const models = (() => {
    const values = new Map<string, boolean>();
    const visit = (nodes: ReactNode) => {
      Children.forEach(nodes, (node) => {
        if (!isValidElement(node)) return;
        const props = node.props as {
          children?: ReactNode;
          disabled?: boolean;
          value?: unknown;
        };
        const itemValue = props.value;
        if (typeof itemValue === "string") {
          values.set(
            itemValue,
            (values.get(itemValue) ?? false) || props.disabled === true,
          );
        }
        visit(props.children);
      });
    };
    visit(children);
    return [...values].map(([id, disabled]) => ({ disabled, id }));
  })();
  // 过滤与排序在本地完成；cmdk 内建排序会移动 DOM 节点，与 ScrollArea 的包裹结构冲突
  const query = search.trim().toLowerCase();
  const visibleModels = query
    ? models
        .map((model) => {
          const id = model.id.toLowerCase();
          const score = id.includes(query)
            ? 0
            : fuzzyMatchesModelId(id, query)
              ? 1
              : -1;
          return { model, score };
        })
        .filter((entry) => entry.score >= 0)
        .sort((left, right) => left.score - right.score)
        .map((entry) => entry.model)
    : models;
  const selectedLabel = value || "选择已发现的模型";

  return (
    <Popover
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        setSearch("");
      }}
      open={open}
    >
      <PopoverTrigger asChild>
        <button
          aria-expanded={open}
          aria-label={ariaLabel ?? "默认模型"}
          className="flex h-7 w-52 items-center justify-between gap-2 rounded-lg border border-input bg-transparent px-2.5 text-left font-mono text-xs whitespace-nowrap transition-colors outline-none hover:bg-muted/50 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          type="button"
        >
          <span className={value ? "truncate" : "truncate text-muted-foreground"}>
            {selectedLabel}
          </span>
          <Search className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0">
        <Command shouldFilter={false}>
          <CommandInput
            onValueChange={setSearch}
            placeholder="搜索模型名称…"
            value={search}
          />
          {/* CommandList 自带 no-scrollbar，改由 ScrollArea 承担滚动并常驻显示滚动条 */}
          <CommandList className="max-h-none">
            <ScrollArea
              className="[&>[data-slot=scroll-area-viewport]]:max-h-64"
              type="always"
            >
              <CommandEmpty>没有匹配的模型</CommandEmpty>
              {visibleModels.map(({ disabled, id: modelId }) => (
                <CommandItem
                  className="pr-4"
                  disabled={disabled}
                  key={modelId}
                  onSelect={() => {
                    if (disabled) return;
                    onValueChange(modelId);
                    setOpen(false);
                  }}
                  title={disabled ? "该模型已在供应商配置中停用" : undefined}
                  value={modelId}
                >
                  <span
                    className={`truncate font-mono text-xs ${
                      disabled ? "text-muted-foreground" : ""
                    }`}
                  >
                    {modelId}
                  </span>
                  {disabled ? (
                    <span className="ml-auto flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground">
                      <LockKeyhole className="size-3" />
                      已停用
                    </span>
                  ) : modelId === value ? (
                    <span className="ml-auto text-xs">✓</span>
                  ) : null}
                </CommandItem>
              ))}
            </ScrollArea>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/** 子序列式模糊匹配："glm5" 可命中 "glm-5.1"。 */
function fuzzyMatchesModelId(text: string, query: string): boolean {
  let index = 0;
  for (const char of text) {
    if (index < query.length && char === query[index]) index += 1;
  }
  return index === query.length;
}

export function ProvidersPage() {
  const queryClient = useQueryClient();
  const [roleFilter, setRoleFilter] = useState<ProviderRole | "all">("model");
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });
  const providerCatalog = useQuery({
    queryKey: ["provider-catalog"],
    queryFn: listProviderCatalog,
  });
  const secretStore = useQuery({
    queryKey: ["provider-secret-store"],
    queryFn: getSecretStoreStatus,
  });
  const [secretTarget, setSecretTarget] = useState<Provider | null>(null);
  const [secretValue, setSecretValue] = useState("");
  const [endpointTarget, setEndpointTarget] = useState<Provider | null>(null);
  const [endpointValue, setEndpointValue] = useState("");
  const [headersTarget, setHeadersTarget] = useState<Provider | null>(null);
  const [headersValue, setHeadersValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Provider | null>(null);
  const [balanceTarget, setBalanceTarget] = useState<Provider | null>(null);
  const [customBalanceTarget, setCustomBalanceTarget] =
    useState<Provider | null>(null);
  const [balanceQueryTarget, setBalanceQueryTarget] =
    useState<Provider | null>(null);
  const [models, setModels] = useState<Record<string, ProviderModelsResponse>>(
    {},
  );
  const [defaultModels, setDefaultModels] = useState<Record<string, string>>(
    {},
  );
  const [realtimeTranscriptionModels, setRealtimeTranscriptionModels] = useState<
    Record<string, string>
  >({});
  const [capabilityTarget, setCapabilityTarget] = useState<{
    provider: Provider;
    modelId: string;
  } | null>(null);
  const probe = useMutation({
    mutationFn: probeProvider,
    onSuccess: () => {
      toast.success("Provider 健康探测通过");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const balance = useMutation({
    mutationFn: getProviderBalance,
    onError: (error) => toast.error(error.message),
  });
  const customBalance = useMutation({
    mutationFn: async (provider: Provider) => {
      const config = providerBalanceQueryConfig(provider);
      if (!config?.enabled) throw new Error("尚未启用自定义余额查询");
      const outcome = await runCustomBalanceQuery(provider.id, config);
      try {
        await persistCustomBalanceResult(outcome);
        void queryClient.invalidateQueries({ queryKey: ["providers"] });
      } catch {
        // 缓存写入失败不影响本次查询结果展示。
      }
      return outcome;
    },
    onError: (error) => toast.error(error.message),
  });
  // cc-switch 风格的自动查询：仅在页面打开期间，按每个 Provider 配置的
  // 间隔静默刷新缓存余额（0 表示关闭）。
  const autoQueryInFlight = useRef<Set<string>>(new Set());
  useEffect(() => {
    const data = providers.data;
    if (!data) return;
    const tick = () => {
      for (const provider of data) {
        const config = providerBalanceQueryConfig(provider);
        if (!config?.enabled || config.auto_query_interval_minutes <= 0) {
          continue;
        }
        const last = providerBalanceQueryLastResult(provider);
        const staleMs = config.auto_query_interval_minutes * 60_000;
        if (
          last &&
          Date.now() - new Date(last.queried_at).getTime() < staleMs
        ) {
          continue;
        }
        if (autoQueryInFlight.current.has(provider.id)) continue;
        autoQueryInFlight.current.add(provider.id);
        runCustomBalanceQuery(provider.id, config)
          .then((outcome) => persistCustomBalanceResult(outcome))
          .then(() =>
            queryClient.invalidateQueries({ queryKey: ["providers"] }),
          )
          .catch(() => {
            // 静默轮询失败不打扰用户；手动查询会显示具体错误。
          })
          .finally(() => autoQueryInFlight.current.delete(provider.id));
      }
    };
    tick();
    const timer = window.setInterval(tick, 60_000);
    return () => window.clearInterval(timer);
  }, [providers.data, queryClient]);
  const discover = useMutation({
    mutationFn: discoverProviderModels,
    onSuccess: (result) => {
      setModels((current) => ({ ...current, [result.provider_id]: result }));
      if (result.warnings?.length) {
        toast.warning(
          `已发现 ${result.models.length} 个模型，其中 ${result.warnings.length} 个模型未提供上下文长度，已采用默认值，可在模型设置中调整`,
        );
      } else {
        toast.success(`已发现 ${result.models.length} 个模型`);
      }
      const configuredProvider = providers.data?.find(
        (provider) => provider.id === result.provider_id,
      );
      const configuredProviderRole = providerCatalog.data?.find(
        (item) => item.provider_type === configuredProvider?.provider_type,
      )?.role;
      const configuredModel =
        configuredProviderRole === "image_generation"
          ? configuredProvider?.capabilities.default_image_generation_model_id
          : configuredProviderRole === "transcription"
            ? configuredProvider?.capabilities.default_transcription_model_id
            : configuredProviderRole === "vision"
              ? configuredProvider?.capabilities.default_vision_model_id
                ?? configuredProvider?.capabilities.default_model
              : configuredProviderRole === "deep_research"
                ? configuredProvider?.capabilities.deep_research_model
                  ?? configuredProvider?.capabilities.default_model
                : configuredProviderRole === "embedding"
                  ? configuredProvider?.capabilities.default_embedding_model_id
                    ?? configuredProvider?.capabilities.default_model
                  : configuredProvider?.capabilities.default_model;
      const fallbackModel =
        configuredProviderRole === "transcription"
          ? result.models.find(
              (model) => !isRealtimeTranscriptionModel(model.id),
            )?.id
          : result.models[0]?.id;
      setDefaultModels((current) =>
        current[result.provider_id] ||
        (typeof configuredModel === "string" && configuredModel.trim()) ||
        !fallbackModel
          ? current
          : { ...current, [result.provider_id]: fallbackModel },
      );
      if (configuredProviderRole === "transcription") {
        const configuredRealtime =
          configuredProvider?.capabilities
            .default_realtime_transcription_model_id;
        setRealtimeTranscriptionModels((current) =>
          current[result.provider_id] ||
          (typeof configuredRealtime === "string" &&
            configuredRealtime.trim()) ||
          !result.models.find((model) => isRealtimeTranscriptionModel(model.id))
            ?.id
            ? current
            : {
                ...current,
                [result.provider_id]: result.models.find((model) =>
                  isRealtimeTranscriptionModel(model.id),
                )!.id,
              },
        );
      }
    },
    onError: (error) => toast.error(error.message),
  });
  const create = useMutation({
    mutationFn: createProvider,
    onSuccess: (provider) => {
      if (provider.enabled && provider.status === "healthy") {
        toast.success("Provider 探测健康，已自动启用");
      } else if (provider.status === "probe_failed") {
        toast.warning("Provider 已保存，但自动健康探测未通过");
      } else {
        toast.success("Provider 元数据已创建");
      }
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const update = useMutation({
    mutationFn: ({
      id,
      enabled,
      default_model,
      default_image_generation_model_id,
      default_transcription_model_id,
      default_realtime_transcription_model_id,
      default_vision_model_id,
      provider_priority,
    }: {
      id: string;
      enabled?: boolean;
      default_model?: string;
      default_image_generation_model_id?: string;
      default_transcription_model_id?: string;
      default_realtime_transcription_model_id?: string;
      default_vision_model_id?: string;
      provider_priority?: number;
    }) =>
      updateProvider(id, {
        enabled,
        default_model,
        default_image_generation_model_id,
        default_transcription_model_id,
        default_realtime_transcription_model_id,
        default_vision_model_id,
        provider_priority,
      }),
    onSuccess: (provider) => {
      toast.success(provider.enabled ? "Provider 已启用" : "Provider 已停用");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const updateEndpoint = useMutation({
    mutationFn: ({ id, baseUrl }: { id: string; baseUrl: string }) =>
      updateProvider(id, { base_url: baseUrl.trim() || null }),
    onSuccess: () => {
      setEndpointTarget(null);
      toast.success("Provider Base URL 已更新");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const updateHeaders = useMutation({
    mutationFn: ({
      id,
      extraHeaders,
    }: {
      id: string;
      extraHeaders: Record<string, string>;
    }) => updateProvider(id, { extra_headers: extraHeaders }),
    onSuccess: () => {
      setHeadersTarget(null);
      setHeadersValue("");
      toast.success("自定义请求头已更新");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const rotateSecret = useMutation({
    mutationFn: ({ id, apiKey }: { id: string; apiKey: string }) =>
      rotateProviderSecret(id, apiKey),
    onSuccess: () => {
      setSecretTarget(null);
      setSecretValue("");
      toast.success("Provider Secret 已轮换");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: deleteProvider,
    onSuccess: () => {
      setDeleteTarget(null);
      toast.success("Provider 实例已删除");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  if (providers.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (providers.isError)
    return (
      <PageFrame>
        <ErrorState message={providers.error.message} />
      </PageFrame>
    );
  const catalogByType = new Map(
    (providerCatalog.data ?? []).map((item) => [item.provider_type, item]),
  );
  // Prefer catalog roles so tabs stay stable even when a role has no instances yet.
  const catalogRoles = [
    ...new Set(
      (providerCatalog.data ?? [])
        .filter((item) => item.create_allowed || item.role)
        .map((item) => item.role),
    ),
  ] as ProviderRole[];
  const ROLE_ORDER: ProviderRole[] = [
    "model",
    "vision",
    "image_generation",
    "search",
    "fetch",
    "deep_research",
    "transcription",
    "embedding",
    "memory",
  ];
  const availableRoles = (
    catalogRoles.length
      ? catalogRoles
      : [
          ...new Set(
            providers.data.flatMap((provider) => {
              const role = catalogByType.get(provider.provider_type)?.role;
              return role ? [role] : [];
            }),
          ),
        ]
  ).sort(
    (a, b) =>
      (ROLE_ORDER.indexOf(a) === -1 ? 99 : ROLE_ORDER.indexOf(a)) -
      (ROLE_ORDER.indexOf(b) === -1 ? 99 : ROLE_ORDER.indexOf(b)),
  );
  const filteredProviders = providers.data.filter((provider) => {
    if (roleFilter === "all") return true;
    return catalogByType.get(provider.provider_type)?.role === roleFilter;
  });
  return (
    <PageFrame>
      <PageIntro
        actions={
          <div className="flex flex-wrap gap-2">
            <ProviderDialog
              busy={create.isPending}
              catalog={providerCatalog.data ?? []}
              catalogError={
                providerCatalog.isError ? providerCatalog.error.message : undefined
              }
              catalogPending={providerCatalog.isPending}
              initialRole={roleFilter === "all" ? undefined : roleFilter}
              onCreate={(payload) => create.mutate(payload)}
              secretStoreAvailable={
                !secretStore.isPending && Boolean(secretStore.data?.available)
              }
            />
          </div>
        }
        description="配置模型、搜索、抓取与记忆服务。"
        eyebrow="Provider gateway"
        title="Provider 管理"
      />
      <Surface className="overflow-hidden">
        <div className="provider-list-heading border-b p-5">
          <SectionHeading title="服务与模型" />
        </div>
        <div className="border-b px-5 py-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">服务能力</p>
          <div
            className="provider-role-switch"
            role="tablist"
            aria-label="服务能力"
          >
            {availableRoles.map((role) => (
              <button
                aria-selected={roleFilter === role}
                key={role}
                onClick={() => setRoleFilter(role)}
                role="tab"
                type="button"
              >
                {providerRoleLabel(role)}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead className="bg-muted/35 text-xs text-muted-foreground">
              <tr>
                <th className="px-5 py-3">实例</th>
                <th className="px-5 py-3">协议</th>
                <th className="px-5 py-3">状态</th>
                <th className="px-5 py-3">密钥</th>
                <th className="px-5 py-3">默认模型</th>
                <th className="px-5 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              <AnimatePresence initial={false}>
              {filteredProviders.map((provider) => {
                const providerSpec = catalogByType.get(provider.provider_type);
                // Identity display (brand mark / "DeepSeek" badge) belongs to
                // the official channel only — relay stations that merely host
                // DeepSeek models must not be labelled DeepSeek.
                const isDeepSeek = isOfficialDeepSeekProvider(provider);
                const isAnthropic = isAnthropicProvider(provider);
                const isOfficialOpenAi = isOfficialOpenAiProvider(provider);
                const isModelProvider = providerSpec?.role === "model";
                const isImageGenerationProvider =
                  providerSpec?.role === "image_generation";
                const isVisionProvider = providerSpec?.role === "vision";
                const isTranscriptionProvider =
                  providerSpec?.role === "transcription";
                const isDeepResearchProvider =
                  providerSpec?.role === "deep_research";
                const isEmbeddingProvider = providerSpec?.role === "embedding";
                const supportsManagedModels = providerSpec?.supports_model_discovery === true;
                const hasConfigurableDefaultModel =
                  isModelProvider ||
                  isImageGenerationProvider ||
                  isTranscriptionProvider ||
                  isVisionProvider ||
                  isDeepResearchProvider ||
                  isEmbeddingProvider;
                const supportsModelDiscovery =
                  providerSpec?.supports_model_discovery === true;
                const supportsProbe = providerSpec?.supports_probe === true;
                // Balance covers DeepSeek plus every origin with a verified
                // key-based balance endpoint and one-api style relay stations.
                const supportsBalance = providerSupportsBalance(provider);
                const customBalanceEnabled =
                  providerBalanceQueryConfig(provider)?.enabled ?? false;
                const customBalanceLast = customBalanceEnabled
                  ? providerBalanceQueryLastResult(provider)
                  : null;
                const hasProbeConfiguration =
                  (!providerSpec?.requires_base_url || Boolean(provider.base_url)) &&
                  (!providerSpec?.requires_secret || provider.secret_status === "active");
                const configurationNotice = providerSpec?.requires_secret
                  ? "请先配置 Base URL 和有效 Secret 后再执行此操作"
                  : "请先配置 Base URL 后再执行此操作";
                const configuredModelValue = isImageGenerationProvider
                  ? provider.capabilities.default_image_generation_model_id
                  : isTranscriptionProvider
                    ? provider.capabilities.default_transcription_model_id
                    : isVisionProvider
                      ? provider.capabilities.default_vision_model_id
                        ?? provider.capabilities.default_model
                      : isEmbeddingProvider
                        ? provider.capabilities.default_embedding_model_id
                          ?? provider.capabilities.default_model
                        : isDeepResearchProvider
                          ? provider.capabilities.deep_research_model
                            ?? provider.capabilities.default_model
                          : provider.capabilities.default_model;
                const configuredModel =
                  typeof configuredModelValue === "string"
                    ? configuredModelValue
                    : "";
                const modelValue =
                  defaultModels[provider.id] ?? configuredModel;
                const configuredRealtimeModelValue = isTranscriptionProvider
                  ? provider.capabilities.default_realtime_transcription_model_id
                  : undefined;
                const configuredRealtimeModel =
                  typeof configuredRealtimeModelValue === "string"
                    ? configuredRealtimeModelValue
                    : "";
                const realtimeModelValue =
                  realtimeTranscriptionModels[provider.id] ??
                  configuredRealtimeModel;
                const providerModels =
                  models[provider.id] ?? persistedProviderModels(provider);
                const storedTranscriptionModels = isTranscriptionProvider
                  ? (providerModels?.models ?? []).filter(
                      (model) => !isRealtimeTranscriptionModel(model.id),
                    )
                  : [];
                const realtimeTranscriptionOptions = isTranscriptionProvider
                  ? (providerModels?.models ?? []).filter((model) =>
                      isRealtimeTranscriptionModel(model.id),
                    )
                  : [];
                const capabilityModelValue =
                  modelValue.trim() || providerModels?.models[0]?.id || "";
                const priorityValue = (() => {
                  const value = provider.capabilities.provider_priority;
                  return typeof value === "number" && Number.isFinite(value)
                    ? Math.max(0, Math.round(value))
                    : 0;
                })();
                const customHeaderCount = Object.keys(
                  providerExtraHeaders(provider),
                ).length;
                return (
                  <motion.tr
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    initial={{ opacity: 0, y: 4 }}
                    key={provider.id}
                    layout
                    transition={{ duration: 0.16 }}
                  >
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-2">
                        <span className="grid size-8 place-items-center rounded-lg bg-white p-1 shadow-sm ring-1 ring-black/5">
                          {providerQuickBrand(provider) ? (
                            <img
                              alt=""
                              aria-hidden="true"
                              className="size-5 object-contain"
                              src={providerQuickBrand(provider)!.iconUrl}
                            />
                          ) : providerSpec?.brand_id === "openai" ||
                            providerSpec?.brand_id === "openai_compatible" ? (
                            <img
                              alt=""
                              aria-hidden="true"
                              className="size-4"
                              src={openAiMark}
                            />
                          ) : providerSpec &&
                            (brandIcon(providerSpec.brand_id) ??
                              providerSpec.brand_icon_url) ? (
                            <img
                              alt=""
                              aria-hidden="true"
                              className="size-4"
                              onError={(event) => {
                                event.currentTarget.style.display = "none";
                              }}
                              src={
                                brandIcon(providerSpec.brand_id) ??
                                providerSpec.brand_icon_url ??
                                undefined
                              }
                            />
                          ) : isOfficialOpenAi ? (
                            <img
                              alt=""
                              aria-hidden="true"
                              className="size-4"
                              src={openAiMark}
                            />
                          ) : isDeepSeek ? (
                            <img
                              alt=""
                              aria-hidden="true"
                              className="size-4"
                              src={deepseekMark}
                            />
                          ) : (
                            <Bot className="size-4" />
                          )}
                        </span>
                        <div>
                          <div className="flex flex-wrap items-center gap-1.5">
                            <p className="font-medium">{provider.display_name}</p>
                            {isOfficialOpenAi ? (
                              <span className="rounded-md border border-foreground/15 bg-foreground/[0.04] px-1.5 py-0.5 text-[10px] font-medium text-foreground">
                                OpenAI 官方
                              </span>
                            ) : isDeepSeek ? (
                              <span className="rounded-md border border-[#4d6bfe]/30 bg-[#4d6bfe]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#4d6bfe]">
                                DeepSeek
                              </span>
                            ) : isAnthropic ? (
                              <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                                Anthropic
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-4 text-xs">
                      <p>
                        {isOfficialOpenAi
                          ? providerSpec?.label ?? provider.provider_type
                          : isDeepSeek
                          ? "OpenAI-compatible Chat"
                          : isAnthropic
                            ? "Anthropic Messages"
                          : providerSpec?.label ?? provider.provider_type}
                      </p>
                      {customHeaderCount > 0 ? (
                        <p className="mt-1 text-[10px] text-muted-foreground">
                          {customHeaderCount} 个自定义请求头
                        </p>
                      ) : null}
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex flex-col items-start gap-1">
                        <StatePill
                          label={providerStatusLabel(
                            provider.status,
                            provider.enabled,
                          )}
                          status={
                            provider.enabled
                              ? provider.status
                              : provider.status === "healthy" ||
                                  provider.status === "healthy_local"
                                ? "degraded"
                                : provider.status
                          }
                        />
                        {!provider.enabled ? (
                          <p className="text-[10px] text-amber-700 dark:text-amber-300">
                            {provider.status === "probe_failed"
                              ? "自动健康探测未通过，接口保持停用"
                              : "接口当前停用"}
                          </p>
                        ) : null}
                        {customBalanceLast ? (
                          <p
                            className={`text-[10px] ${
                              customBalanceLast.is_valid === false
                                ? "text-destructive"
                                : "text-muted-foreground"
                            }`}
                            title="自定义余额查询的缓存结果"
                          >
                            {formatBalanceQuerySummary(customBalanceLast)} ·{" "}
                            {relativeTimeLabel(customBalanceLast.queried_at)}
                          </p>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-5 py-4 font-mono text-xs text-muted-foreground">
                      <p>{provider.api_key_masked ?? "未保存"}</p>
                    </td>
                    <td className="px-5 py-4">
                      {isTranscriptionProvider ? (
                        <div className="grid min-w-64 gap-3">
                          <div className="grid gap-1">
                            <Label className="text-[10px] text-muted-foreground">
                              文件转写模型（上传音频 / HTTP）
                            </Label>
                            {storedTranscriptionModels.length ? (
                              <SearchableModelSelect
                                onValueChange={(value) => {
                                  setDefaultModels((current) => ({
                                    ...current,
                                    [provider.id]: value,
                                  }));
                                  update.mutate({
                                    id: provider.id,
                                    enabled: provider.enabled,
                                    default_transcription_model_id: value,
                                  });
                                }}
                                value={modelValue}
                              >
                                <SelectTrigger className="h-7 w-64 font-mono text-xs">
                                  <SelectValue placeholder="选择非 realtime 模型" />
                                </SelectTrigger>
                                <SelectContent className="max-h-72 overflow-y-auto">
                                  {storedTranscriptionModels.map((model) => (
                                    <SelectItem key={model.id} value={model.id}>
                                      {model.id}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </SearchableModelSelect>
                            ) : (
                              <Input
                                aria-label={`${provider.display_name} 文件转写模型`}
                                className="h-7 w-64 font-mono text-xs"
                                onChange={(event) =>
                                  setDefaultModels((current) => ({
                                    ...current,
                                    [provider.id]: event.target.value,
                                  }))
                                }
                                onBlur={(event) => {
                                  const value = event.target.value.trim();
                                  if (!value || isRealtimeTranscriptionModel(value)) return;
                                  update.mutate({
                                    id: provider.id,
                                    enabled: provider.enabled,
                                    default_transcription_model_id: value,
                                  });
                                }}
                                placeholder="如 qwen3-asr-flash"
                                value={modelValue}
                              />
                            )}
                            {modelValue && isRealtimeTranscriptionModel(modelValue) ? (
                              <p className="text-[10px] text-destructive">
                                文件模型不能使用 realtime 型号。
                              </p>
                            ) : null}
                          </div>
                          <div className="grid gap-1">
                            <Label className="text-[10px] text-muted-foreground">
                              实时听写模型（麦克风 / WebSocket）
                            </Label>
                            {realtimeTranscriptionOptions.length ? (
                              <SearchableModelSelect
                                onValueChange={(value) => {
                                  setRealtimeTranscriptionModels((current) => ({
                                    ...current,
                                    [provider.id]: value,
                                  }));
                                  update.mutate({
                                    id: provider.id,
                                    enabled: provider.enabled,
                                    default_realtime_transcription_model_id: value,
                                  });
                                }}
                                value={realtimeModelValue}
                              >
                                <SelectTrigger className="h-7 w-64 font-mono text-xs">
                                  <SelectValue placeholder="选择 realtime 模型" />
                                </SelectTrigger>
                                <SelectContent className="max-h-72 overflow-y-auto">
                                  {realtimeTranscriptionOptions.map((model) => (
                                    <SelectItem key={model.id} value={model.id}>
                                      {model.id}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </SearchableModelSelect>
                            ) : (
                              <Input
                                aria-label={`${provider.display_name} 实时听写模型`}
                                className="h-7 w-64 font-mono text-xs"
                                onChange={(event) =>
                                  setRealtimeTranscriptionModels((current) => ({
                                    ...current,
                                    [provider.id]: event.target.value,
                                  }))
                                }
                                onBlur={(event) => {
                                  const value = event.target.value.trim();
                                  if (!value || !isRealtimeTranscriptionModel(value)) return;
                                  update.mutate({
                                    id: provider.id,
                                    enabled: provider.enabled,
                                    default_realtime_transcription_model_id: value,
                                  });
                                }}
                                placeholder="如 paraformer-realtime-v2"
                                value={realtimeModelValue}
                              />
                            )}
                            {realtimeModelValue &&
                            !isRealtimeTranscriptionModel(realtimeModelValue) ? (
                              <p className="text-[10px] text-destructive">
                                实时听写模型必须是 realtime 型号。
                              </p>
                            ) : null}
                          </div>
                        </div>
                      ) : hasConfigurableDefaultModel &&
                      (providerModels?.models.length ?? 0) > 0 ? (
                        <SearchableModelSelect
                          onValueChange={(value) => {
                            setDefaultModels((current) => ({ ...current, [provider.id]: value }));
                            update.mutate({
                              id: provider.id,
                              enabled: provider.enabled,
                              default_model:
                                isModelProvider ||
                                isDeepResearchProvider ||
                                isEmbeddingProvider
                                  ? value
                                  : undefined,
                              default_image_generation_model_id: isImageGenerationProvider ? value : undefined,
                              default_transcription_model_id: isTranscriptionProvider ? value : undefined,
                              default_vision_model_id: isVisionProvider ? value : undefined,
                            });
                          }}
                          value={modelValue}
                        >
                          <SelectTrigger
                            aria-label={`${provider.display_name} 默认模型`}
                            className="h-7 w-52 font-mono text-xs"
                          >
                            <SelectValue placeholder="选择已发现的模型" />
                          </SelectTrigger>
                          <SelectContent className="max-h-72 overflow-y-auto">
                            {(providerModels?.models ?? []).map((model) => (
                              <SelectItem
                                className="font-mono text-xs"
                                disabled={model.enabled === false}
                                key={model.id}
                                value={model.id}
                              >
                                {model.id}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </SearchableModelSelect>
                      ) : hasConfigurableDefaultModel ? (
                        <Input
                          aria-label={`${provider.display_name} 默认模型`}
                          className="h-7 w-52 font-mono text-xs"
                          onChange={(event) =>
                            setDefaultModels((current) => ({ ...current, [provider.id]: event.target.value }))
                          }
                          placeholder="先发现模型，或手动输入 ID"
                          value={modelValue}
                        />
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          {providerSpec
                            ? `${providerRoleLabel(providerSpec.role)}服务不设置默认模型`
                            : "不适用"}
                        </p>
                      )}
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex justify-end gap-2">
                        <div className="flex items-center gap-1.5 rounded-lg border px-2 py-1">
                          <span className="text-[10px] text-muted-foreground">优先级</span>
                          <Input
                            aria-label={`${provider.display_name} 供应商优先级`}
                            className="h-6 w-12 px-1 text-center text-xs"
                            defaultValue={priorityValue}
                            min={0}
                            max={10000}
                            onBlur={(event) => {
                              const next = Math.max(0, Math.min(10000, Math.round(Number(event.target.value) || 0)));
                              if (next !== priorityValue) {
                                update.mutate({
                                  id: provider.id,
                                  provider_priority: next,
                                });
                              }
                            }}
                            type="number"
                          />
                        </div>
                        {supportsModelDiscovery ? (
                          <Button
                            disabled={discover.isPending || !hasProbeConfiguration}
                            onClick={() => discover.mutate(provider.id)}
                            size="xs"
                            title={hasProbeConfiguration ? undefined : configurationNotice}
                            variant="outline"
                          >
                            <RefreshCcw className="size-3" />
                            发现模型
                          </Button>
                        ) : null}
                        {supportsProbe ? (
                          <Button
                            disabled={probe.isPending || !hasProbeConfiguration}
                            onClick={() => probe.mutate(provider.id)}
                            size="xs"
                            title={
                              hasProbeConfiguration
                                ? providerSpec?.probe_notice ?? undefined
                                : configurationNotice
                            }
                            variant="outline"
                          >
                            <Activity className="size-3" />
                            能力探测
                          </Button>
                        ) : null}
                        {supportsBalance ? (
                          <Button
                            disabled={
                              customBalanceEnabled
                                ? customBalance.isPending
                                : balance.isPending || !hasProbeConfiguration
                            }
                            onClick={() => {
                              if (customBalanceEnabled) {
                                customBalance.reset();
                                setCustomBalanceTarget(provider);
                                customBalance.mutate(provider);
                                return;
                              }
                              balance.reset();
                              setBalanceTarget(provider);
                              balance.mutate(provider.id);
                            }}
                            size="xs"
                            title={
                              customBalanceEnabled
                                ? "使用自定义脚本查询余额"
                                : hasProbeConfiguration
                                  ? "按需读取当前账户余额 / 用量"
                                  : configurationNotice
                            }
                            variant="outline"
                          >
                            <WalletCards className="size-3" />
                            {!customBalanceEnabled &&
                            provider.provider_type === "codex_chatgpt"
                              ? "查询用量"
                              : "查询余额"}
                          </Button>
                        ) : null}
                        {!isModelProvider && provider.provider_type !== "local_mock" ? (
                          <Button
                            onClick={() => setBalanceQueryTarget(provider)}
                            size="xs"
                            title="配置余额查询方式：官方内置或自定义脚本"
                            variant="outline"
                          >
                            <Settings2 className="size-3" />
                            余额配置
                          </Button>
                        ) : null}
                        {supportsManagedModels ? (
                          <Button
                            onClick={() =>
                              setCapabilityTarget({
                                provider,
                                modelId: capabilityModelValue,
                              })
                            }
                            size="xs"
                            title="全局模板、模型开关、连接与余额查询统一在这里配置"
                            variant="outline"
                          >
                            <SlidersHorizontal className="size-3" />
                            供应商配置
                          </Button>
                        ) : null}
                        {!isModelProvider && providerSpec?.requires_base_url ? (
                          <Button
                            disabled={updateEndpoint.isPending}
                            onClick={() => {
                              setEndpointTarget(provider);
                              setEndpointValue(provider.base_url ?? "");
                            }}
                            size="xs"
                            variant="outline"
                          >
                            <Pencil className="size-3" />
                            URL
                          </Button>
                        ) : null}
                        {!isModelProvider && provider.provider_type !== "local_mock" ? (
                          <Button
                            disabled={updateHeaders.isPending}
                            onClick={() => {
                              setHeadersTarget(provider);
                              setHeadersValue(
                                stringifyExtraHeaders(providerExtraHeaders(provider)),
                              );
                            }}
                            size="xs"
                            title="配置中转站/代理所需的自定义请求头"
                            variant="outline"
                          >
                            Headers
                          </Button>
                        ) : null}
                        <Button
                          disabled={
                            update.isPending ||
                            (hasConfigurableDefaultModel &&
                              !provider.enabled &&
                              !(isTranscriptionProvider
                                ? modelValue.trim() || realtimeModelValue.trim()
                                : modelValue.trim()))
                          }
                          onClick={() =>
                            update.mutate({
                              id: provider.id,
                              enabled: !provider.enabled,
                              default_model:
                                isModelProvider ||
                                isDeepResearchProvider ||
                                isEmbeddingProvider
                                  ? modelValue.trim() || undefined
                                  : undefined,
                              default_image_generation_model_id:
                                isImageGenerationProvider
                                  ? modelValue.trim() || undefined
                                  : undefined,
                              default_transcription_model_id:
                                isTranscriptionProvider && modelValue.trim()
                                  ? modelValue.trim()
                                  : undefined,
                              default_realtime_transcription_model_id:
                                isTranscriptionProvider && realtimeModelValue.trim()
                                  ? realtimeModelValue.trim()
                                  : undefined,
                              default_vision_model_id: isVisionProvider
                                ? modelValue.trim() || undefined
                                : undefined,
                            })
                          }
                          size="xs"
                          variant={provider.enabled ? "ghost" : "default"}
                        >
                          {provider.enabled ? "停用" : "启用"}
                        </Button>
                        {!isModelProvider && provider.provider_type !== "local_mock" ? (
                          <Button
                            disabled={rotateSecret.isPending}
                            onClick={() => {
                              setSecretValue("");
                              setSecretTarget(provider);
                            }}
                            size="xs"
                            variant="outline"
                          >
                            轮换 Secret
                          </Button>
                        ) : null}
                        <Button
                          disabled={remove.isPending}
                          onClick={() => setDeleteTarget(provider)}
                          size="xs"
                          variant="ghost"
                        >
                          <Trash2 className="size-3" />
                          删除
                        </Button>
                      </div>
                    </td>
                  </motion.tr>
                );
              })}
              {filteredProviders.length === 0 ? (
                <tr>
                  <td
                    className="px-5 py-10 text-center text-sm text-muted-foreground"
                    colSpan={6}
                  >
                    {roleFilter === "all"
                      ? "当前工作区还没有 Provider，点击右上角「新增 Provider」开始配置。"
                      : `当前「${providerRoleLabel(roleFilter)}」分类下还没有实例。`}
                  </td>
                </tr>
              ) : null}
              </AnimatePresence>
            </tbody>
          </table>
        </div>
      </Surface>
      <Dialog
        onOpenChange={(open) => {
          if (!open && !rotateSecret.isPending) {
            setSecretTarget(null);
            setSecretValue("");
          }
        }}
        open={Boolean(secretTarget)}
      >
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (secretTarget && secretValue) {
                rotateSecret.mutate({
                  id: secretTarget.id,
                  apiKey: secretValue,
                });
              }
            }}
          >
            <DialogHeader>
              <DialogTitle>
                轮换 {secretTarget?.display_name} 的 Secret
              </DialogTitle>
              <DialogDescription>
                新 Secret 只提交给后端一次；成功后旧密文立即被替换。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2 py-5">
              <Label htmlFor="provider-secret-rotation">新 API Key</Label>
              <Input
                autoComplete="off"
                id="provider-secret-rotation"
                onChange={(event) => setSecretValue(event.currentTarget.value)}
                type="password"
                value={secretValue}
              />
              {rotateSecret.isError ? (
                <p className="text-xs text-destructive" role="alert">
                  {rotateSecret.error.message}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                disabled={rotateSecret.isPending}
                onClick={() => setSecretTarget(null)}
                type="button"
                variant="outline"
              >
                取消
              </Button>
              <Button
                disabled={rotateSecret.isPending || !secretValue}
                type="submit"
              >
                {rotateSecret.isPending ? "轮换中…" : "确认轮换"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog
        onOpenChange={(open) => {
          if (!open && !updateEndpoint.isPending) {
            setEndpointTarget(null);
            setEndpointValue("");
          }
        }}
        open={Boolean(endpointTarget)}
      >
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!endpointTarget) return;
              const trimmed = endpointValue.trim();
              if (trimmed && !isValidHttpUrl(trimmed)) {
                toast.error(invalidBaseUrlMessage(trimmed));
                return;
              }
              updateEndpoint.mutate({
                id: endpointTarget.id,
                baseUrl: endpointValue,
              });
            }}
          >
            {(() => {
              const spec = endpointTarget
                ? catalogByType.get(endpointTarget.provider_type)
                : undefined;
              const trimmedEndpoint = endpointValue.trim();
              const endpointValid =
                !trimmedEndpoint || isValidHttpUrl(trimmedEndpoint);
              return (
                <>
                  <DialogHeader>
                    <DialogTitle>编辑 Provider Base URL</DialogTitle>
                    <DialogDescription>
                      {endpointTarget?.display_name} 的连接地址由后端保存并在下次调用时生效。可填写官方地址或自定义网关。
                    </DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4 py-5">
                    <div className="space-y-2">
                      <Label htmlFor="provider-endpoint-edit">Base URL</Label>
                      <Input
                        aria-invalid={Boolean(trimmedEndpoint) && !endpointValid}
                        id="provider-endpoint-edit"
                        onChange={(event) => setEndpointValue(event.currentTarget.value)}
                        placeholder={spec?.default_base_url ?? "https://provider.example/v1"}
                        value={endpointValue}
                      />
                      {trimmedEndpoint && !endpointValid ? (
                        <p className="text-xs text-destructive" role="alert">
                          {invalidBaseUrlMessage(trimmedEndpoint)}
                        </p>
                      ) : spec?.default_base_url ? (
                        <p className="text-xs text-muted-foreground">
                          官方默认地址：
                          <button
                            className="ml-1 font-mono text-primary underline-offset-2 hover:underline"
                            onClick={() => setEndpointValue(spec.default_base_url ?? "")}
                            type="button"
                          >
                            {spec.default_base_url}
                          </button>
                        </p>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          支持官方 API 或兼容网关地址。
                        </p>
                      )}
                    </div>
                    {spec?.documentation_url || spec?.key_management_url ? (
                      <div className="flex flex-wrap gap-3 text-xs">
                        {spec.documentation_url ? (
                          <a
                            className="text-primary underline-offset-4 hover:underline"
                            href={spec.documentation_url}
                            rel="noreferrer"
                            target="_blank"
                          >
                            官方文档
                          </a>
                        ) : null}
                        {spec.key_management_url ? (
                          <a
                            className="text-primary underline-offset-4 hover:underline"
                            href={spec.key_management_url}
                            rel="noreferrer"
                            target="_blank"
                          >
                            获取或管理 API Key
                          </a>
                        ) : null}
                      </div>
                    ) : null}
                    {updateEndpoint.isError ? (
                      <p className="text-sm text-destructive" role="alert">
                        {updateEndpoint.error.message}
                      </p>
                    ) : null}
                  </div>
                  <DialogFooter>
                    <Button
                      disabled={updateEndpoint.isPending}
                      onClick={() => setEndpointTarget(null)}
                      type="button"
                      variant="outline"
                    >
                      取消
                    </Button>
                    <Button
                      disabled={
                        updateEndpoint.isPending ||
                        !endpointValue.trim() ||
                        !endpointValid
                      }
                      type="submit"
                    >
                      {updateEndpoint.isPending ? "保存中…" : "保存地址"}
                    </Button>
                  </DialogFooter>
                </>
              );
            })()}
          </form>
        </DialogContent>
      </Dialog>
      <Dialog
        onOpenChange={(open) => {
          if (!open && !updateHeaders.isPending) {
            setHeadersTarget(null);
            setHeadersValue("");
          }
        }}
        open={Boolean(headersTarget)}
      >
        <DialogContent className="sm:max-w-lg">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!headersTarget) return;
              try {
                const parsed = parseExtraHeadersInput(headersValue);
                updateHeaders.mutate({
                  id: headersTarget.id,
                  extraHeaders: parsed,
                });
              } catch (error) {
                toast.error(
                  error instanceof Error ? error.message : "请求头格式不正确",
                );
              }
            }}
          >
            <DialogHeader>
              <DialogTitle>自定义请求头</DialogTitle>
              <DialogDescription>
                {headersTarget?.display_name}
                。用于对接中转站/代理站所需的专用请求头。不要在这里填写
                Authorization / API Key——密钥仍走 Secret Store。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3 py-5">
              <div className="space-y-2">
                <Label htmlFor="provider-extra-headers">请求头（JSON 对象）</Label>
                <Textarea
                  className="min-h-36 font-mono text-xs"
                  id="provider-extra-headers"
                  onChange={(event) => setHeadersValue(event.currentTarget.value)}
                  placeholder='{"X-Station-Token":"…","X-Custom":"value"}'
                  value={headersValue}
                />
                <p className="text-xs leading-5 text-muted-foreground">
                  留空并保存可清除全部自定义请求头。最多 32 项；
                  Authorization / x-api-key / Cookie 等会被后端忽略。
                </p>
              </div>
              {updateHeaders.isError ? (
                <p className="text-sm text-destructive" role="alert">
                  {updateHeaders.error.message}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                disabled={updateHeaders.isPending}
                onClick={() => {
                  setHeadersTarget(null);
                  setHeadersValue("");
                }}
                type="button"
                variant="outline"
              >
                取消
              </Button>
              <Button disabled={updateHeaders.isPending} type="submit">
                {updateHeaders.isPending ? "保存中…" : "保存请求头"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <ProviderBalanceDialog
        error={balance.error}
        isPending={balance.isPending}
        onClose={() => {
          setBalanceTarget(null);
          balance.reset();
        }}
        onRetry={() => {
          if (!balanceTarget) return;
          balance.reset();
          balance.mutate(balanceTarget.id);
        }}
        result={balance.data}
        target={balanceTarget}
      />
      <CustomBalanceDialog
        error={customBalance.error}
        isPending={customBalance.isPending}
        onClose={() => {
          setCustomBalanceTarget(null);
          customBalance.reset();
        }}
        onRetry={() => {
          if (!customBalanceTarget) return;
          const fresh =
            providers.data?.find(
              (item) => item.id === customBalanceTarget.id,
            ) ?? customBalanceTarget;
          customBalance.reset();
          customBalance.mutate(fresh);
        }}
        result={customBalance.data}
        target={customBalanceTarget}
      />
      <BalanceQueryConfigDialog
        onClose={() => setBalanceQueryTarget(null)}
        target={balanceQueryTarget}
      />
      <AlertDialog
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleteTarget(null);
        }}
        open={Boolean(deleteTarget)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogMedia className="bg-destructive/10 text-destructive">
              <Trash2 />
            </AlertDialogMedia>
            <AlertDialogTitle>
              删除 {deleteTarget?.display_name}？
            </AlertDialogTitle>
            <AlertDialogDescription>
              将删除 Provider 配置及其加密 Secret。历史用量记录会保留，但此实例之后不能再被调用。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {remove.isError ? (
            <p className="text-sm text-destructive" role="alert">
              {remove.error.message}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>取消，保留</AlertDialogCancel>
            <AlertDialogAction
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (deleteTarget) remove.mutate(deleteTarget.id);
              }}
              variant="destructive"
            >
              {remove.isPending ? "正在删除…" : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {capabilityTarget ? (
        <ModelCapabilitiesDialog
          key={`${capabilityTarget.provider.id}:${capabilityTarget.modelId}`}
          modelId={capabilityTarget.modelId}
          models={
            mergeProviderModelLists(
              models[capabilityTarget.provider.id],
              capabilityTarget.provider,
            ) ?? {
              provider_id: capabilityTarget.provider.id,
              status: "manual",
              models: capabilityTarget.modelId
                ? [
                    {
                      id: capabilityTarget.modelId,
                      roles: ["llm"],
                      streaming: true,
                      remote: true,
                      enabled: true,
                    },
                  ]
                : [],
            }
          }
          onClose={() => setCapabilityTarget(null)}
          onConfigureBalance={() =>
            setBalanceQueryTarget(capabilityTarget.provider)
          }
          onSaved={(snapshot) => {
            setModels((current) => {
              const discovered = current[snapshot.provider_id];
              if (!discovered) return current;
              const exists = discovered.models.some(
                (model) => model.id === snapshot.model_id,
              );
              return {
                ...current,
                [snapshot.provider_id]: {
                  ...discovered,
                  models: exists
                    ? discovered.models.map((model) =>
                        model.id === snapshot.model_id
                          ? { ...model, capabilities: snapshot.capabilities }
                          : model,
                      )
                    : [
                        ...discovered.models,
                        {
                          id: snapshot.model_id,
                          roles: ["llm"],
                          streaming: true,
                          remote: true,
                          enabled: true,
                          capabilities: snapshot.capabilities,
                        },
                      ],
                },
              };
            });
            void queryClient.invalidateQueries({ queryKey: ["providers"] });
          }}
                    onSetDefault={(nextModelId) => {
            const provider = capabilityTarget.provider;
            const spec = catalogByType.get(provider.provider_type);
            update.mutate({
              id: provider.id,
              enabled: provider.enabled,
              default_model:
                spec?.role === "model" || spec?.role === "deep_research" || spec?.role === "embedding"
                  ? nextModelId
                  : undefined,
              default_image_generation_model_id:
                spec?.role === "image_generation" ? nextModelId : undefined,
              default_transcription_model_id:
                spec?.role === "transcription" ? nextModelId : undefined,
              default_vision_model_id:
                spec?.role === "vision" ? nextModelId : undefined,
            });
          }}
          provider={capabilityTarget.provider}
        />
      ) : null}
    </PageFrame>
  );
}

function providerRoleLabel(role: ProviderRole) {
  switch (role) {
    case "model":
      return "模型";
    case "image_generation":
      return "图片生成";
    case "vision":
      return "识图 / 视觉";
    case "search":
      return "搜索";
    case "fetch":
      return "网页抓取";
    case "deep_research":
      return "Deep Research";
    case "memory":
      return "共同记忆";
    case "transcription":
      return "语音转写";
    case "embedding":
      return "Embedding";
  }
}

function ProviderBalanceDialog({
  error,
  isPending,
  onClose,
  onRetry,
  result,
  target,
}: {
  error: Error | null;
  isPending: boolean;
  onClose: () => void;
  onRetry: () => void;
  result: ProviderBalance | undefined;
  target: Provider | null;
}) {
  const visibleResult = result?.provider_id === target?.id ? result : undefined;
  const loading = isPending || (!error && !visibleResult);

  return (
    <Dialog onOpenChange={(open) => !open && onClose()} open={Boolean(target)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {visibleResult?.vendor_label
              ? `${visibleResult.vendor_label} 账户余额`
              : "账户余额"}
          </DialogTitle>
          <DialogDescription>
            {target?.display_name ?? "Provider"}
            。余额仅在你主动查询时从已配置的账户读取。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-1">
          {loading ? (
            <div className="py-3">
              <LoadingState label="正在读取账户余额…" />
            </div>
          ) : error ? (
            <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4" role="alert">
              <p className="font-medium text-destructive">余额查询未完成</p>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                {error.message}
              </p>
            </div>
          ) : visibleResult ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-muted/20 px-4 py-3">
                <StatePill
                  label={visibleResult.is_available ? "账户余额可用" : "账户余额不足"}
                  status={visibleResult.is_available ? "healthy" : "failed"}
                />
                <p className="text-xs text-muted-foreground">
                  查询于 {formatProviderBalanceTimestamp(visibleResult.queried_at)}
                </p>
              </div>
              {visibleResult.usage_windows?.length ? (
                <div className="overflow-hidden rounded-xl border">
                  {visibleResult.usage_windows.map((usageWindow) => (
                    <section
                      className="border-b p-4 last:border-b-0"
                      key={usageWindow.label}
                    >
                      <div className="flex items-baseline justify-between gap-3">
                        <p className="text-sm font-medium">{usageWindow.label}</p>
                        <p className="font-mono text-sm font-semibold">
                          已用 {usageWindow.used_percent.toFixed(0)}%
                        </p>
                      </div>
                      <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                        <div
                          className={`h-full rounded-full ${
                            usageWindow.used_percent >= 90
                              ? "bg-destructive"
                              : "bg-primary"
                          }`}
                          style={{
                            width: `${Math.min(100, Math.max(0, usageWindow.used_percent))}%`,
                          }}
                        />
                      </div>
                      {usageWindow.resets_at ? (
                        <p className="mt-2 text-xs text-muted-foreground">
                          将于 {formatProviderBalanceTimestamp(usageWindow.resets_at)}{" "}
                          重置
                        </p>
                      ) : null}
                    </section>
                  ))}
                </div>
              ) : null}
              {visibleResult.balance_infos.length ? (
                <div className="overflow-hidden rounded-xl border">
                  {visibleResult.balance_infos.map((balanceInfo) => (
                    <section
                      className="border-b p-4 last:border-b-0"
                      key={balanceInfo.currency}
                    >
                      <div className="flex items-baseline justify-between gap-3">
                        <p className="text-sm font-medium">{balanceInfo.currency}</p>
                        <p className="font-mono text-base font-semibold">
                          {formatProviderBalanceAmount(
                            balanceInfo.total_balance,
                            balanceInfo.currency,
                          )}
                        </p>
                      </div>
                      {balanceInfo.granted_balance !== null ||
                      balanceInfo.topped_up_balance !== null ? (
                        <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2 text-xs">
                          {balanceInfo.granted_balance !== null ? (
                            <div>
                              <dt className="text-muted-foreground">赠送余额</dt>
                              <dd className="mt-1 font-mono">
                                {formatProviderBalanceAmount(
                                  balanceInfo.granted_balance,
                                  balanceInfo.currency,
                                )}
                              </dd>
                            </div>
                          ) : null}
                          {balanceInfo.topped_up_balance !== null ? (
                            <div>
                              <dt className="text-muted-foreground">充值余额</dt>
                              <dd className="mt-1 font-mono">
                                {formatProviderBalanceAmount(
                                  balanceInfo.topped_up_balance,
                                  balanceInfo.currency,
                                )}
                              </dd>
                            </div>
                          ) : null}
                        </dl>
                      ) : null}
                    </section>
                  ))}
                </div>
              ) : !visibleResult.usage_windows?.length ? (
                <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
                  未返回可展示的币种余额。
                </p>
              ) : null}
              {visibleResult.notice ? (
                <p className="rounded-xl border bg-muted/35 p-3 text-xs leading-5 text-muted-foreground">
                  {visibleResult.notice}
                </p>
              ) : null}
              <p className="text-xs leading-5 text-muted-foreground">
                结果不会写入 Provider 配置；关闭后如需更新，请再次主动查询。
              </p>
            </>
          ) : null}
        </div>
        <DialogFooter>
          <Button onClick={onClose} type="button" variant="outline">
            关闭
          </Button>
          {!loading ? (
            <Button onClick={onRetry} type="button">
              <RefreshCcw className="size-4" />
              {error ? "重试查询" : "刷新余额"}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function formatProviderBalanceAmount(amount: string, currency: "CNY" | "USD") {
  return `${currency === "CNY" ? "¥" : "$"}${amount}`;
}

function formatProviderBalanceTimestamp(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function isOfficialOpenAiProvider(
  provider: Pick<Provider, "provider_type" | "base_url">,
) {
  if (
    !["openai_responses", "openai_images"].includes(provider.provider_type) ||
    !provider.base_url
  )
    return false;
  try {
    const url = new URL(provider.base_url);
    return (
      url.protocol === "https:" &&
      url.hostname.toLowerCase() === "api.openai.com" &&
      !url.port &&
      !url.username &&
      !url.password &&
      ["/", "/v1"].includes(url.pathname.replace(/\/+$/, "") || "/") &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

function stringifyExtraHeaders(headers: Record<string, string>): string {
  if (!Object.keys(headers).length) return "";
  return JSON.stringify(headers, null, 2);
}

function parseExtraHeadersInput(input: string): Record<string, string> {
  const trimmed = input.trim();
  if (!trimmed) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error("请求头必须是合法 JSON 对象，例如 {\"X-Foo\":\"bar\"}");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("请求头必须是 JSON 对象（不是数组或字符串）");
  }
  const headers: Record<string, string> = {};
  for (const [rawKey, rawValue] of Object.entries(
    parsed as Record<string, unknown>,
  )) {
    const key = String(rawKey).trim();
    const value =
      typeof rawValue === "string"
        ? rawValue.trim()
        : String(rawValue ?? "").trim();
    if (!key || !value) continue;
    headers[key] = value;
  }
  return headers;
}

type QuickProtocol = "openai" | "anthropic";

type QuickProvider = {
  id: string;
  name: string;
  description: string;
  baseUrl: string;
  brandId: string;
  iconUrl: string;
  protocol: QuickProtocol;
  keyUrl?: string;
  defaultModel?: string;
  endpoints?: Partial<Record<QuickProtocol, string>>;
  models?: Partial<Record<QuickProtocol, string>>;
  capabilityOverrides?: Record<string, unknown>;
};

const QUICK_PROVIDERS: QuickProvider[] = [
  { id: "openai", name: "OpenAI", description: "官方 Responses API", baseUrl: "https://api.openai.com/v1", brandId: "openai", iconUrl: openAiMark, protocol: "openai", keyUrl: "https://platform.openai.com/api-keys" },
  { id: "deepseek", name: "DeepSeek", description: "OpenAI 兼容接口", baseUrl: "https://api.deepseek.com", brandId: "deepseek", iconUrl: deepseekBrandMark, protocol: "openai", keyUrl: "https://platform.deepseek.com/api_keys" },
  { id: "qwen", name: "通义千问", description: "阿里云 Model Studio", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", brandId: "qwen", iconUrl: qwenMark, protocol: "openai", keyUrl: "https://bailian.console.aliyun.com/?tab=model#/api-key" },
  { id: "gemini", name: "Google Gemini", description: "OpenAI 兼容接口", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/", brandId: "gemini", iconUrl: googleGeminiMark, protocol: "openai", keyUrl: "https://aistudio.google.com/apikey" },
  { id: "mimo", name: "Xiaomi MiMo", description: "OpenAI / Anthropic 兼容", baseUrl: "https://api.xiaomimimo.com/v1", brandId: "mimo", iconUrl: xiaomiMark, protocol: "openai", keyUrl: "https://platform.xiaomimimo.com/#/console/api-keys", defaultModel: "mimo-v2.5-pro", endpoints: { openai: "https://api.xiaomimimo.com/v1", anthropic: "https://api.xiaomimimo.com/anthropic" }, models: { openai: "mimo-v2.5-pro", anthropic: "mimo-v2.5-pro" } },
  { id: "anthropic", name: "Anthropic", description: "Claude Messages API", baseUrl: "https://api.anthropic.com", brandId: "anthropic", iconUrl: anthropicMark, protocol: "anthropic", keyUrl: "https://platform.claude.com/settings/keys" },
  { id: "github_copilot", name: "GitHub Copilot", description: "GitHub 账号设备授权", baseUrl: "https://api.githubcopilot.com", brandId: "github", iconUrl: githubMark, protocol: "openai", defaultModel: "claude-sonnet-5", models: { openai: "claude-sonnet-5" } },
  { id: "qianfan", name: "Baidu Qianfan Coding Plan", description: "Anthropic 兼容接口", baseUrl: "https://qianfan.baidubce.com/anthropic/coding", brandId: "qianfan", iconUrl: baiduMark, protocol: "anthropic", keyUrl: "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application", defaultModel: "qianfan-code-latest", models: { anthropic: "qianfan-code-latest" } },
  { id: "volc_agentplan", name: "火山 Agentplan", description: "Anthropic 兼容接口", baseUrl: "https://ark.cn-beijing.volces.com/api/coding", brandId: "volc_agentplan", iconUrl: bytedanceMark, protocol: "anthropic", keyUrl: "https://www.volcengine.com/activity/codingplan", defaultModel: "ark-code-latest", models: { anthropic: "ark-code-latest" } },
  { id: "openrouter", name: "OpenRouter", description: "Anthropic 兼容接口", baseUrl: "https://openrouter.ai/api", brandId: "openrouter", iconUrl: openrouterMark, protocol: "anthropic", keyUrl: "https://openrouter.ai/keys", defaultModel: "anthropic/claude-sonnet-5", models: { anthropic: "anthropic/claude-sonnet-5" } },
  { id: "longcat", name: "Longcat", description: "Anthropic 兼容接口", baseUrl: "https://api.longcat.chat/anthropic", brandId: "longcat", iconUrl: longcatMark, protocol: "anthropic", keyUrl: "https://longcat.chat/platform/api_keys", defaultModel: "LongCat-2.0", models: { anthropic: "LongCat-2.0" }, capabilityOverrides: { max_output_tokens: 131072 } },
  { id: "kimi", name: "Kimi", description: "Anthropic 兼容接口", baseUrl: "https://api.moonshot.cn/anthropic", brandId: "kimi", iconUrl: moonshotMark, protocol: "anthropic", keyUrl: "https://platform.kimi.com/console/api-keys", defaultModel: "kimi-k2.7-code", models: { anthropic: "kimi-k2.7-code" } },
  { id: "kimi_coding", name: "Kimi For Coding", description: "Anthropic 兼容 Coding Plan", baseUrl: "https://api.kimi.com/coding/", brandId: "kimi_coding", iconUrl: moonshotMark, protocol: "anthropic", keyUrl: "https://www.kimi.com/code/", defaultModel: "kimi-for-coding", models: { anthropic: "kimi-for-coding" }, capabilityOverrides: { context_window_tokens: 262144, context_limit_tokens: 262144 } },
  { id: "modelscope", name: "ModelScope", description: "Anthropic 兼容接口", baseUrl: "https://api-inference.modelscope.cn", brandId: "modelscope", iconUrl: modelscopeMark, protocol: "anthropic", keyUrl: "https://modelscope.cn/my/myaccesstoken", defaultModel: "ZhipuAI/GLM-5.1", models: { anthropic: "ZhipuAI/GLM-5.1" } },
  { id: "minimax", name: "MiniMax", description: "OpenAI 兼容接口", baseUrl: "https://api.minimaxi.com/v1", brandId: "minimax", iconUrl: minimaxMark, protocol: "openai", keyUrl: "https://platform.minimaxi.com/user-center/basic-information/interface-key" },
  { id: "ollama", name: "Ollama", description: "本地模型（无需 API Key）", baseUrl: "http://127.0.0.1:11434/v1", brandId: "ollama", iconUrl: ollamaMark, protocol: "openai" },
];

type RoleQuickProvider = {
  id: string;
  name: string;
  description: string;
  baseUrl: string;
  brandId?: string;
  iconUrl?: string;
  keyUrl?: string;
  providerType: string;
  protocol?: QuickProtocol;
  defaultModel?: string;
  endpoints?: QuickProvider["endpoints"];
  models?: QuickProvider["models"];
  capabilityOverrides?: Record<string, unknown>;
  isCustom?: boolean;
};

function roleQuickProviders(
  role: ProviderRole,
  catalog: ProviderTypeCatalogItem[],
): RoleQuickProvider[] {
  const findType = (providerType: string) =>
    catalog.find(
      (item) => item.create_allowed && item.provider_type === providerType,
    );
  const compatibleChat = findType("openai_compatible_chat");
  const qwenChat = findType("qwen");
  const ollamaChat = findType("ollama");
  const copilotChat = findType("github_copilot");
  const openAi = findType("openai_responses");
  const openAiVision = findType("openai_responses_vision");
  const compatibleVision = findType("openai_compatible_vision");
  const openAiImages = findType("openai_images");
  const compatibleTranscription = findType("openai_compatible_transcription");
  const compatibleEmbedding = findType("openai_compatible_embedding");
  const ollamaEmbedding = findType("ollama_embedding");
  const qwenDeepResearch = findType("qwen_deep_research");

  if (role === "model") {
    return QUICK_PROVIDERS.flatMap((preset) => {
      const providerType =
        preset.id === "openai"
          ? openAi?.provider_type
          : preset.id === "qwen"
            ? (qwenChat ?? compatibleChat)?.provider_type
            : preset.id === "ollama"
              ? ollamaChat?.provider_type
              : preset.id === "github_copilot"
                ? copilotChat?.provider_type
                : preset.protocol === "anthropic"
                ? findType("anthropic_messages")?.provider_type
                : compatibleChat?.provider_type;
      return providerType ? [{ ...preset, providerType }] : [];
    });
  }

  if (role === "vision") {
    return QUICK_PROVIDERS.flatMap((preset) => {
      // DeepSeek and GitHub Copilot do not currently expose supported vision presets.
      if (
        preset.id === "deepseek" ||
        preset.id === "github_copilot" ||
        preset.protocol === "anthropic"
      )
        return [];
      const providerType =
        preset.id === "openai"
          ? openAiVision?.provider_type
          : compatibleVision?.provider_type;
      return providerType ? [{ ...preset, providerType }] : [];
    });
  }

  if (role === "image_generation" && openAiImages) {
    const imageBrands = new Set(["openai", "gemini", "qwen"]);
    return QUICK_PROVIDERS.filter((preset) => imageBrands.has(preset.id)).map(
      (preset) => ({ ...preset, providerType: openAiImages.provider_type }),
    );
  }

  // Embedding / transcription: expose 通义千问 as a first-class quick brand
  // alongside OpenAI, both on the OpenAI-compatible wire protocol.
  if (role === "transcription" && compatibleTranscription) {
    return [
      {
        id: "qwen",
        name: "通义千问",
        description: "DashScope ASR（qwen3-asr-flash）",
        baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        brandId: "qwen",
        iconUrl: qwenMark,
        keyUrl:
          "https://bailian.console.aliyun.com/?tab=model#/api-key",
        providerType: compatibleTranscription.provider_type,
      },
      {
        id: "openai",
        name: "OpenAI",
        description: "官方 Audio Transcriptions",
        baseUrl: "https://api.openai.com/v1",
        brandId: "openai",
        iconUrl: openAiMark,
        keyUrl: "https://platform.openai.com/api-keys",
        providerType: compatibleTranscription.provider_type,
      },
    ];
  }

  if (role === "embedding" && compatibleEmbedding) {
    return [
      {
        id: "qwen",
        name: "通义千问",
        description: "text-embedding-v4（DashScope）",
        baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        brandId: "qwen",
        iconUrl: qwenMark,
        keyUrl:
          "https://bailian.console.aliyun.com/?tab=model#/api-key",
        providerType: compatibleEmbedding.provider_type,
      },
      {
        id: "openai",
        name: "OpenAI",
        description: "官方 Embeddings",
        baseUrl: "https://api.openai.com/v1",
        brandId: "openai",
        iconUrl: openAiMark,
        keyUrl: "https://platform.openai.com/api-keys",
        providerType: compatibleEmbedding.provider_type,
      },
      ...(ollamaEmbedding
        ? [
            {
              id: "ollama",
              name: "Ollama",
              description: "本地 Embeddings（nomic-embed-text 等）",
              baseUrl: "http://127.0.0.1:11434/v1",
              brandId: "ollama",
              iconUrl: ollamaMark,
              providerType: ollamaEmbedding.provider_type,
            },
          ]
        : []),
    ];
  }

  // Deep Research: promote 通义千问 to the front of the quick list.
  if (role === "deep_research") {
    const items = catalog
      .filter((item) => item.create_allowed && item.role === role)
      .map((item) => ({
        id: item.provider_type,
        name: item.label,
        description: item.description,
        baseUrl: item.default_base_url ?? "",
        brandId: item.brand_id ?? undefined,
        iconUrl:
          item.brand_id === "openai" || item.brand_id === "openai_compatible"
            ? openAiMark
            : (brandIcon(item.brand_id) ?? item.brand_icon_url ?? undefined),
        keyUrl: item.key_management_url ?? undefined,
        providerType: item.provider_type,
      }));
    if (qwenDeepResearch) {
      items.sort((a, b) => {
        if (a.providerType === "qwen_deep_research") return -1;
        if (b.providerType === "qwen_deep_research") return 1;
        return 0;
      });
    }
    return items;
  }

  return catalog
    .filter((item) => item.create_allowed && item.role === role)
    .map((item) => ({
      id: item.provider_type,
      name: item.label,
      description: item.description,
      baseUrl: item.default_base_url ?? "",
      brandId: item.brand_id ?? undefined,
      iconUrl:
        item.brand_id === "openai" || item.brand_id === "openai_compatible"
          ? openAiMark
          : (brandIcon(item.brand_id) ?? item.brand_icon_url ?? undefined),
      keyUrl: item.key_management_url ?? undefined,
      providerType: item.provider_type,
    }));
}

function CodexDeviceLoginPanel({
  hasCredential,
  onAuthorized,
}: {
  hasCredential: boolean;
  onAuthorized: (secret: string, planType: string | null) => void;
}) {
  const [login, setLogin] = useState<CodexDeviceLoginStart | null>(null);
  const [error, setError] = useState<string>();
  const [waiting, setWaiting] = useState(false);

  const start = useMutation({
    mutationFn: startCodexDeviceLogin,
    onSuccess: (data) => {
      setError(undefined);
      setLogin(data);
      setWaiting(true);
      window.open(data.verification_url, "_blank", "noopener,noreferrer");
    },
    onError: (mutationError: Error) => setError(mutationError.message),
  });

  useEffect(() => {
    if (!login || !waiting) return;
    let cancelled = false;
    let polling = false;
    let consecutiveFailures = 0;
    // The device code expires after 15 minutes upstream; stop polling then so
    // a forgotten dialog cannot keep calling the login endpoint forever.
    const deadline = Date.now() + 15 * 60 * 1000;
    const timer = window.setInterval(async () => {
      if (cancelled || polling) return;
      if (Date.now() > deadline) {
        setWaiting(false);
        setError("设备码已过期，请重新发起直登。");
        return;
      }
      polling = true;
      try {
        const result = await pollCodexDeviceLogin({
          device_auth_id: login.device_auth_id,
          user_code: login.user_code,
        });
        if (cancelled) return;
        consecutiveFailures = 0;
        setError(undefined);
        if (result.status !== "authorized" || !result.api_key) return;
        setWaiting(false);
        onAuthorized(result.api_key, result.plan_type);
        toast.success("Codex 直登成功，凭据已填入");
      } catch (pollError) {
        if (cancelled) return;
        consecutiveFailures += 1;
        // A single proxy/network hiccup must not abort the device-code login:
        // keep polling and only give up after repeated consecutive failures.
        if (consecutiveFailures >= 3) {
          setWaiting(false);
          setError(
            pollError instanceof Error
              ? pollError.message
              : "Codex 直登轮询失败",
          );
        } else {
          setError(
            pollError instanceof Error
              ? `直登轮询暂时失败（${pollError.message}），正在重试…`
              : "直登轮询暂时失败，正在重试…",
          );
        }
      } finally {
        polling = false;
      }
    }, Math.max(1, login.interval_seconds) * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [login, waiting, onAuthorized]);

  return (
    <div className="space-y-2 rounded-xl border bg-muted/25 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium">
          使用 ChatGPT 账号直登（设备码授权）
        </p>
        <Button
          disabled={start.isPending || waiting}
          onClick={() => start.mutate()}
          size="xs"
          type="button"
          variant="outline"
        >
          <LockKeyhole className="size-3" />
          {waiting ? "等待授权…" : hasCredential ? "重新直登" : "开始直登"}
        </Button>
      </div>
      {login ? (
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">
            在打开的页面输入配对码后完成授权；本页会自动收取凭据。
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="rounded-md border bg-background px-2 py-1 font-mono text-sm tracking-widest">
              {login.user_code}
            </code>
            <a
              className="text-xs text-primary underline-offset-4 hover:underline"
              href={login.verification_url}
              rel="noreferrer"
              target="_blank"
            >
              重新打开授权页 ↗
            </a>
          </div>
        </div>
      ) : null}
      {error ? (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function CopilotDeviceLoginPanel({
  hasCredential,
  onAuthorized,
}: {
  hasCredential: boolean;
  onAuthorized: (secret: string) => void;
}) {
  const [login, setLogin] = useState<CopilotDeviceLoginStart | null>(null);
  const [error, setError] = useState<string>();
  const [waiting, setWaiting] = useState(false);

  const start = useMutation({
    mutationFn: startCopilotDeviceLogin,
    onSuccess: (data) => {
      setError(undefined);
      setLogin(data);
      setWaiting(true);
      window.open(data.verification_url, "_blank", "noopener,noreferrer");
    },
    onError: (mutationError: Error) => setError(mutationError.message),
  });

  useEffect(() => {
    if (!login || !waiting) return;
    let cancelled = false;
    let polling = false;
    let consecutiveFailures = 0;
    const deadline = Date.now() + 15 * 60 * 1000;
    const timer = window.setInterval(async () => {
      if (cancelled || polling) return;
      if (Date.now() > deadline) {
        setWaiting(false);
        setError("GitHub 设备码已过期，请重新授权。");
        return;
      }
      polling = true;
      try {
        const result = await pollCopilotDeviceLogin({
          device_auth_id: login.device_auth_id,
          user_code: login.user_code,
        });
        if (cancelled) return;
        consecutiveFailures = 0;
        setError(undefined);
        if (result.status !== "authorized" || !result.api_key) return;
        setWaiting(false);
        onAuthorized(result.api_key);
        toast.success("GitHub Copilot 授权成功，凭据已填入");
      } catch (pollError) {
        if (cancelled) return;
        consecutiveFailures += 1;
        // A single proxy/network hiccup must not abort the device-code login:
        // keep polling and only give up after repeated consecutive failures.
        if (consecutiveFailures >= 3) {
          setWaiting(false);
          setError(
            pollError instanceof Error
              ? pollError.message
              : "GitHub Copilot 授权轮询失败",
          );
        } else {
          setError(
            pollError instanceof Error
              ? `授权轮询暂时失败（${pollError.message}），正在重试…`
              : "授权轮询暂时失败，正在重试…",
          );
        }
      } finally {
        polling = false;
      }
    }, Math.max(1, login.interval_seconds) * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [login, waiting, onAuthorized]);

  return (
    <div className="space-y-2 rounded-xl border bg-muted/25 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium">使用 GitHub 账号授权 Copilot</p>
        <Button
          disabled={start.isPending || waiting}
          onClick={() => start.mutate()}
          size="xs"
          type="button"
          variant="outline"
        >
          <LockKeyhole className="size-3" />
          {waiting ? "等待授权…" : hasCredential ? "重新授权" : "开始授权"}
        </Button>
      </div>
      {login ? (
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">
            在 GitHub 页面输入配对码；完成后本页会自动收取凭据。
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="rounded-md border bg-background px-2 py-1 font-mono text-sm tracking-widest">
              {login.user_code}
            </code>
            <a
              className="text-xs text-primary underline-offset-4 hover:underline"
              href={login.verification_url}
              rel="noreferrer"
              target="_blank"
            >
              重新打开授权页 ↗
            </a>
          </div>
        </div>
      ) : null}
      {error ? <p className="text-xs text-destructive" role="alert">{error}</p> : null}
    </div>
  );
}

function QuickBrandIcon({ iconUrl, name }: { iconUrl?: string; name: string }) {
  const [failedUrl, setFailedUrl] = useState<string>();
  if (!iconUrl || failedUrl === iconUrl) {
    return <Bot className="size-4 text-foreground" />;
  }
  return (
    <img
      alt={`${name} 图标`}
      className="size-6 object-contain"
      onError={() => setFailedUrl(iconUrl)}
      src={iconUrl}
    />
  );
}

function normalizedQuickEndpoint(value: string) {
  return value.trim().replace(/\/+$/, "").toLowerCase();
}

/** Client-side interception of Base URLs the backend could never issue. */
function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim());
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      Boolean(parsed.hostname)
    );
  } catch {
    return false;
  }
}

function invalidBaseUrlMessage(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "Base URL 不能为空";
  if (!/^[a-z][a-z0-9+.-]*:/i.test(trimmed) && !trimmed.startsWith("//")) {
    return `Base URL 缺少协议前缀，请填写以 http:// 或 https:// 开头的完整地址（如 https://${trimmed.split(/\s+/)[0]}）`;
  }
  return "Base URL 必须以 http:// 或 https:// 开头，且包含有效的主机名";
}

function isKnownQuickEndpoint(preset: RoleQuickProvider, value: string) {
  const normalized = normalizedQuickEndpoint(value);
  return Boolean(normalized) && Object.values(
    preset.endpoints ?? { [preset.protocol ?? "openai"]: preset.baseUrl },
  ).some(
    (endpoint) =>
      Boolean(endpoint) && normalizedQuickEndpoint(endpoint ?? "") === normalized,
  );
}

function providerQuickBrand(provider: Provider): QuickProvider | undefined {
  // The DeepSeek quick preset is the official-channel identity. Relay
  // stations whose base URL or display name merely mentions DeepSeek must
  // fall back to the generic icon instead of the DeepSeek brand mark.
  const preset = QUICK_PROVIDERS.find((item) => {
    const brandId = String(provider.capabilities.brand_id ?? "").toLowerCase();
    const baseUrl = normalizedQuickEndpoint(provider.base_url ?? "");
    const name = provider.display_name.toLowerCase();
    return (
      item.brandId === brandId ||
      Object.values(item.endpoints ?? { [item.protocol]: item.baseUrl }).some(
        (endpoint) => normalizedQuickEndpoint(endpoint ?? "") === baseUrl,
      ) ||
      name.includes(item.id) ||
      name.includes(item.name.toLowerCase())
    );
  });
  if (!preset) return undefined;
  if (preset.id === "deepseek" && !isOfficialDeepSeekProvider(provider)) {
    return undefined;
  }
  return preset;
}

function ProviderDialog({
  busy,
  catalog,
  catalogError,
  catalogPending,
  initialRole,
  onCreate,
  secretStoreAvailable,
}: {
  busy: boolean;
  catalog: ProviderTypeCatalogItem[];
  catalogError?: string;
  catalogPending: boolean;
  initialRole?: ProviderRole;
  onCreate: (payload: {
    display_name: string;
    provider_type: string;
    base_url?: string;
    api_key?: string;
    capabilities?: Record<string, unknown>;
  }) => void;
  secretStoreAvailable: boolean;
}) {
  const creatable = catalog.filter((item) => item.create_allowed);
  const ROLE_ORDER: ProviderRole[] = [
    "model",
    "vision",
    "image_generation",
    "search",
    "fetch",
    "deep_research",
    "transcription",
    "embedding",
    "memory",
  ];
  const roles = (
    [...new Set(creatable.map((item) => item.role))] as ProviderRole[]
  ).sort(
    (a, b) =>
      (ROLE_ORDER.indexOf(a) === -1 ? 99 : ROLE_ORDER.indexOf(a)) -
      (ROLE_ORDER.indexOf(b) === -1 ? 99 : ROLE_ORDER.indexOf(b)),
  );
  const compatiblePreset = catalog.find(
    (item) =>
      item.create_allowed && item.provider_type === "openai_compatible_chat",
  );
  const openAiPreset = catalog.find(
    (item) => item.create_allowed && item.provider_type === "openai_responses",
  );
  const [name, setName] = useState("DeepSeek");
  const [role, setRole] = useState<ProviderRole>("model");
  const [type, setType] = useState("openai_compatible_chat");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com");
  const [key, setKey] = useState("");
  const [headersText, setHeadersText] = useState("");
  // DeepSeek is no longer a separate protocol — it is an OpenAI-compatible preset.
  const [deepSeekPresetActive, setDeepSeekPresetActive] = useState(true);
  const [quickPreset, setQuickPreset] = useState<string>("deepseek");

  const selected = catalog.find((item) => item.provider_type === type);
  const roleTypes = creatable.filter((item) => item.role === role);
  const quickProviders = roleQuickProviders(role, catalog);

  useEffect(() => {
    if (!catalog.length) return;
    if (!catalog.some((item) => item.provider_type === type && item.create_allowed)) {
      const preferred =
        compatiblePreset ??
        openAiPreset ??
        catalog.find((item) => item.create_allowed);
      if (preferred) {
        setType(preferred.provider_type);
        setRole(preferred.role);
        if (preferred.provider_type === "openai_compatible_chat") {
          setBaseUrl("https://api.deepseek.com");
          setName("DeepSeek");
          setDeepSeekPresetActive(true);
        } else {
          setBaseUrl(preferred.default_base_url ?? "");
          setName(preferred.label);
          setDeepSeekPresetActive(false);
        }
      }
    }
  }, [catalog, type, compatiblePreset, openAiPreset]);

  function applyCatalogItem(next: ProviderTypeCatalogItem) {
    setType(next.provider_type);
    setRole(next.role);
    setDeepSeekPresetActive(false);
    // A stale quick preset would keep stamping its brand_id and key link onto
    // a protocol the user picked manually. Non-model roles list the same
    // provider types as quick cards, so keep those in sync instead.
    setQuickPreset(
      roleQuickProviders(next.role, catalog).some(
        (item) => item.id === next.provider_type,
      )
        ? next.provider_type
        : "",
    );
    setBaseUrl(next.default_base_url ?? "");
    if (next.provider_type === "openai_responses") {
      setName("OpenAI");
      return;
    }
    if (next.provider_type === "openai_images") {
      setName("OpenAI Images");
      return;
    }
    if (next.provider_type === "anthropic_messages") {
      setName("Anthropic");
      return;
    }
    if (next.provider_type === "openai_compatible_chat") {
      setName("自定义兼容服务");
      return;
    }
    setName(next.label);
  }

  function selectRole(nextRole: ProviderRole) {
    setRole(nextRole);
    const first = creatable.find((item) => item.role === nextRole);
    if (first) {
      applyCatalogItem(first);
    }
  }

  function selectType(nextType: string) {
    const next = catalog.find((item) => item.provider_type === nextType);
    if (!next) return;
    if (role === "model" && next.role === "model") {
      const nextProtocol: QuickProtocol =
        next.provider_type === "anthropic_messages" ? "anthropic" : "openai";
      if (
        activeQuickProvider &&
        activeQuickProvider.id !== "github_copilot" &&
        isKnownQuickEndpoint(activeQuickProvider, baseUrl)
      ) {
        const nextEndpoint = activeQuickProvider.endpoints?.[nextProtocol];
        if (nextEndpoint) setBaseUrl(nextEndpoint);
      }
      setType(next.provider_type);
      return;
    }
    applyCatalogItem(next);
  }

  function selectQuickProvider(kind: string) {
    if (kind === "compatible") {
      const compatible =
        role === "vision"
          ? catalog.find(
              (item) =>
                item.create_allowed &&
                item.provider_type === "openai_compatible_vision",
            )
          : role === "model"
            ? compatiblePreset
            : roleTypes[0];
      if (!compatible) return;
      applyCatalogItem(compatible);
      setQuickPreset(kind);
      return;
    }
    const preset = quickProviders.find((item) => item.id === kind);
    if (!preset) return;
    const next = catalog.find(
      (item) =>
        item.create_allowed && item.provider_type === preset.providerType,
    );
    if (!next) return;
    if (role === "model" && preset.id === "deepseek") {
      setQuickPreset(preset.id);
      setType(next.provider_type);
      setRole(next.role);
      setBaseUrl(preset.baseUrl);
      setName(preset.name);
      setDeepSeekPresetActive(true);
      return;
    }
    setType(next.provider_type);
    setRole(next.role);
    setDeepSeekPresetActive(false);
    setBaseUrl(preset.baseUrl);
    setName(preset.name);
    setQuickPreset(preset.id);
  }

  const activeQuickProvider = quickProviders.find(
    (item) => item.id === quickPreset,
  );
  // For vendor-neutral "compatible" types the key console only matches while
  // the Base URL still points at the prefilled official endpoint.
  const normalizedBaseUrl = baseUrl.trim().replace(/\/+$/, "");
  const quickKeyUrl =
    activeQuickProvider?.keyUrl &&
    (activeQuickProvider.brandId !== "openai_compatible" ||
      normalizedBaseUrl === activeQuickProvider.baseUrl.replace(/\/+$/, ""))
      ? activeQuickProvider.keyUrl
      : undefined;
  const specKeyUrl =
    selected?.key_management_url &&
    (selected.brand_id !== "openai_compatible" ||
      normalizedBaseUrl ===
        (selected.default_base_url ?? "").replace(/\/+$/, ""))
      ? selected.key_management_url
      : undefined;
  const apiKeyUrl = quickKeyUrl ?? specKeyUrl;
  const isCodex = type === "codex_chatgpt";
  const isCopilot = type === "github_copilot";
  const activeProtocol: QuickProtocol =
    type === "anthropic_messages" ? "anthropic" : "openai";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    // Intercept unusable endpoints before the request reaches the backend
    // probe: a URL without an http(s) protocol can never be issued.
    if (selected.requires_base_url && !trimmedBaseUrl) {
      toast.error("该 Provider 需要填写 Base URL 才能创建");
      return;
    }
    if (trimmedBaseUrl && !baseUrlValid) {
      toast.error(invalidBaseUrlMessage(trimmedBaseUrl));
      return;
    }
    let extraHeaders: Record<string, string> = {};
    try {
      extraHeaders = parseExtraHeadersInput(headersText);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "请求头格式不正确");
      return;
    }
    const capabilities: Record<string, unknown> = {};
    if (Object.keys(extraHeaders).length) {
      capabilities.extra_headers = extraHeaders;
    }
    if (deepSeekPresetActive) {
      capabilities.model_family = "deepseek";
    }
    if (activeQuickProvider?.brandId) {
      capabilities.brand_id = activeQuickProvider.brandId;
    }
    if (role === "model" && activeQuickProvider?.defaultModel) {
      const defaultModel =
        activeQuickProvider.models?.[activeProtocol] ??
        activeQuickProvider.defaultModel;
      capabilities.default_model = defaultModel;
      capabilities.discovered_model_ids = [defaultModel];
      Object.assign(capabilities, activeQuickProvider.capabilityOverrides);
    }
    // Seed recommended default model IDs for 通义千问 embedding / ASR presets
    // so the row can be enabled without a second manual step.
    if (role === "transcription" && activeQuickProvider?.brandId === "qwen") {
      capabilities.default_transcription_model_id = "qwen3-asr-flash";
      capabilities.default_realtime_transcription_model_id =
        "paraformer-realtime-v2";
    }
    if (role === "embedding" && activeQuickProvider?.brandId === "qwen") {
      capabilities.default_model = "text-embedding-v4";
      capabilities.default_embedding_model_id = "text-embedding-v4";
    }
    if (role === "transcription" && activeQuickProvider?.brandId === "openai") {
      capabilities.default_transcription_model_id = "whisper-1";
    }
    if (role === "embedding" && activeQuickProvider?.brandId === "openai") {
      capabilities.default_model = "text-embedding-3-small";
      capabilities.default_embedding_model_id = "text-embedding-3-small";
    }
    if (role === "embedding" && activeQuickProvider?.brandId === "ollama") {
      capabilities.default_model = "nomic-embed-text";
      capabilities.default_embedding_model_id = "nomic-embed-text";
    }
    if (selected.provider_type === "qwen_deep_research") {
      capabilities.default_model = "qwen-deep-research";
      capabilities.deep_research_model = "qwen-deep-research";
    }
    onCreate({
      display_name: name.trim(),
      provider_type: selected.provider_type,
      base_url: trimmedBaseUrl || undefined,
      api_key: key || undefined,
      capabilities: Object.keys(capabilities).length ? capabilities : undefined,
    });
  }

  const isDeepSeekQuick = deepSeekPresetActive && quickPreset === "deepseek";
  const trimmedBaseUrl = baseUrl.trim();
  // The backend probe can never issue an endpoint without an http(s) protocol;
  // intercept such URLs before submission instead of surfacing a 500.
  const baseUrlValid =
    !trimmedBaseUrl || isValidHttpUrl(trimmedBaseUrl);

  return (
    <Dialog
      onOpenChange={(open) => {
        if (
          open &&
          initialRole &&
          creatable.some((item) => item.role === initialRole)
        ) {
          selectRole(initialRole);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button disabled={catalogPending || Boolean(catalogError)} size="sm">
          <Plus className="size-4" />
          新增 Provider
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-hidden p-0 sm:max-w-2xl">
        <form
          className="flex min-h-0 max-h-[calc(100dvh-2rem)] flex-col"
          onSubmit={submit}
        >
          <DialogHeader className="shrink-0 px-5 pt-5 pr-12">
            <DialogTitle>新增 Provider</DialogTitle>
            <DialogDescription>
              快捷项仅预填厂商名称、地址和协议；填入 API Key 后仍需执行真实能力探测。
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
            {catalogError ? (
              <p className="text-sm text-destructive" role="alert">
                {catalogError}
              </p>
            ) : null}
            <div className="space-y-2">
              <Label>服务能力</Label>
              <div
                className="flex flex-wrap gap-2"
                role="tablist"
                aria-label="Provider 服务能力"
              >
                {roles.map((item) => (
                  <button
                    aria-selected={role === item}
                    className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                      role === item
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground"
                    }`}
                    key={item}
                    onClick={() => selectRole(item)}
                    role="tab"
                    type="button"
                  >
                    {providerRoleLabel(item)}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-2">
              <Label>快捷接入</Label>
              <div className="grid max-h-56 grid-cols-2 gap-2 overflow-y-auto pr-1 sm:grid-cols-3">
                {quickProviders.map((preset) => (
                  <button
                    aria-pressed={quickPreset === preset.id}
                    className={`flex min-h-16 items-center gap-2 rounded-lg border p-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${quickPreset === preset.id ? "border-primary bg-primary/5" : "border-border bg-background hover:border-primary/45 hover:bg-muted/35"}`}
                    disabled={catalogPending}
                    key={preset.id}
                    onClick={() => selectQuickProvider(preset.id)}
                    type="button"
                  >
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-white p-1 shadow-sm ring-1 ring-black/5">
                      <QuickBrandIcon iconUrl={preset.iconUrl} name={preset.name} />
                    </span>
                    <span className="min-w-0"><span className="block truncate text-xs font-medium">{preset.name}</span><span className="mt-0.5 block truncate text-[10px] text-muted-foreground">{preset.description}</span></span>
                  </button>
                ))}
                <button
                  aria-pressed={
                    quickPreset === "compatible"
                  }
                  className={`flex min-h-20 items-center gap-3 rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    quickPreset === "compatible"
                      ? "border-primary bg-primary/5"
                      : "border-border bg-background hover:border-primary/45 hover:bg-muted/35"
                  }`}
                  disabled={catalogPending || !compatiblePreset}
                  onClick={() => selectQuickProvider("compatible")}
                  type="button"
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
                    <Bot className="size-4" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">自定义兼容</span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">
                      自定义地址与鉴权
                    </span>
                  </span>
                </button>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="provider-name">显示名称</Label>
              <Input
                id="provider-name"
                onChange={(event) => setName(event.target.value)}
                value={name}
              />
            </div>
            <div className="space-y-2">
              <Label>协议类型</Label>
              <div className="grid gap-2 sm:grid-cols-2">
                {roleTypes.map((item) => {
                  const active = item.provider_type === type;
                  return (
                    <button
                      aria-pressed={active}
                      className={`rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        active
                          ? "border-primary bg-primary/5"
                          : "border-border bg-background hover:border-primary/40 hover:bg-muted/30"
                      }`}
                      key={item.provider_type}
                      onClick={() => selectType(item.provider_type)}
                      type="button"
                    >
                      <span className="block text-sm font-medium">
                        {item.label}
                      </span>
                      <span className="mt-1 line-clamp-2 block text-xs leading-5 text-muted-foreground">
                        {item.description}
                      </span>
                    </button>
                  );
                })}
                {!roleTypes.length ? (
                  <p className="col-span-full rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
                    当前能力下没有可创建的协议类型
                  </p>
                ) : null}
              </div>
              {selected ? (
                <div className="space-y-1.5">
                  {selected.documentation_url || isDeepSeekQuick ? (
                    <div className="flex flex-wrap gap-3 text-xs">
                      {selected.documentation_url ? (
                        <a
                          className="text-primary underline-offset-4 hover:underline"
                          href={selected.documentation_url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          官方文档
                        </a>
                      ) : null}
                      {isDeepSeekQuick ? (
                        <a
                          className="text-primary underline-offset-4 hover:underline"
                          href="https://api-docs.deepseek.com/"
                          rel="noreferrer"
                          target="_blank"
                        >
                          DeepSeek 文档
                        </a>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className="space-y-2">
              <Label htmlFor="provider-url">
                Base URL{selected?.requires_base_url ? "（启用必填）" : "（可选）"}
              </Label>
              <Input
                id="provider-url"
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder={
                  isDeepSeekQuick
                    ? "https://api.deepseek.com"
                    : (selected?.default_base_url ?? "https://provider.example/v1")
                }
                value={baseUrl}
              />
              <p className="text-xs text-muted-foreground">
                {isDeepSeekQuick
                  ? `厂商预设为 DeepSeek，当前使用 ${selected?.label ?? "所选"} 协议。请确认该端点真实支持此协议；模型名称不会替你改写协议。`
                  : selected?.default_base_url
                    ? `官方默认：${selected.default_base_url}。也可填写兼容网关或代理地址。`
                    : "支持官方 API 或兼容网关地址。"}
              </p>
              {trimmedBaseUrl && !baseUrlValid ? (
                <p className="text-xs text-destructive" role="alert">
                  {invalidBaseUrlMessage(trimmedBaseUrl)}
                </p>
              ) : null}
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="provider-key">
                  {isCodex
                    ? "Codex 凭据"
                    : isCopilot
                      ? "GitHub OAuth 凭据"
                      : "API Key"}
                  {selected?.requires_secret ? "（启用必填）" : "（可选）"}
                </Label>
                {apiKeyUrl && !isCodex && !isCopilot ? (
                  <a
                    className="text-xs text-primary underline-offset-4 hover:underline"
                    href={apiKeyUrl}
                    rel="noreferrer"
                    target="_blank"
                  >
                    获取 API Key ↗
                  </a>
                ) : null}
              </div>
              {isCodex ? (
                <CodexDeviceLoginPanel
                  hasCredential={Boolean(key)}
                  onAuthorized={(secret, planType) => {
                    setKey(secret);
                    setName(
                      planType ? `Codex（${planType}）` : "Codex 官方直登",
                    );
                  }}
                />
              ) : null}
              {isCopilot ? (
                <CopilotDeviceLoginPanel
                  hasCredential={Boolean(key)}
                  onAuthorized={(secret) => {
                    setKey(secret);
                    setName("GitHub Copilot");
                  }}
                />
              ) : null}
              <Input
                autoComplete="off"
                id="provider-key"
                onChange={(event) => setKey(event.target.value)}
                placeholder={
                  isCodex
                    ? "直登后自动填入，也可粘贴 ~/.codex/auth.json 内容"
                    : isCopilot
                      ? "授权后自动填入 GitHub OAuth token"
                      : "仅提交一次"
                }
                type="password"
                value={key}
              />
              {isCodex ? (
                <p className="text-xs leading-5 text-muted-foreground">
                  凭据为 ChatGPT OAuth 令牌，按订阅计划计费而非 API 额度。令牌会自动续期，
                  续期后的新令牌将加密保存。
                </p>
              ) : null}
              {isCopilot ? (
                <p className="text-xs leading-5 text-muted-foreground">
                  长期 GitHub OAuth token 会进入 Secret Store；调用时后端仅在内存中换取短期 Copilot token。
                </p>
              ) : null}
            </div>
            <div className="space-y-2">
              <Label htmlFor="provider-create-headers">
                自定义请求头（可选，中转站用）
              </Label>
              <Textarea
                className="min-h-24 font-mono text-xs"
                id="provider-create-headers"
                onChange={(event) => setHeadersText(event.target.value)}
                placeholder='{"X-Station-Token":"…"}'
                value={headersText}
              />
              <p className="text-xs text-muted-foreground">
                JSON 对象。Authorization / API Key 请勿写在这里，统一走 Secret。
              </p>
            </div>
            {selected?.probe_notice ? (
              <p className="rounded-xl border bg-muted/35 p-3 text-xs leading-5 text-muted-foreground">
                探测说明：{selected.probe_notice}
              </p>
            ) : null}
            {!secretStoreAvailable ? (
              <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                当前系统安全凭据库不可用。可先不填 API Key 保存配置，恢复安全凭据库后再录入。
              </div>
            ) : null}
          </div>
          <DialogFooter className="mx-0 mb-0 shrink-0 rounded-none">
            <Button
              disabled={
                busy || catalogPending || Boolean(catalogError) || !name.trim() || !selected || !baseUrlValid
              }
              type="submit"
            >
              {busy ? "创建中…" : "创建 Provider"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const reasoningModes: Exclude<ThinkingMode, "off">[] = [
  "low",
  "medium",
  "high",
  "xhigh",
];
const searchRoutes: SearchRoute[] = [
  "auto",
  "model_native",
  "external",
];

function normalizeDefaultSearchRoute(route: SearchRoute | undefined): SearchRoute {
  if (route === "model_native" || route === "external" || route === "auto") {
    return route;
  }
  // Legacy "disabled" and "local" values are no longer exposed by this
  // default-routing control.
  return route === "local" ? "external" : "auto";
}

function normalizeReasoningParameter(value: unknown): ReasoningParameter {
  if (
    value === "reasoning_effort" ||
    value === "reasoning.effort" ||
    value === "enable_thinking" ||
    value === "thinking_budget" ||
    value === "thinking"
  ) {
    return value;
  }
  return "reasoning_effort";
}

function normalizeLoadedCapabilities(
  capabilities: ProviderModelCapabilities,
): ProviderModelCapabilities {
  const chatRatio = Number(capabilities.chat_compaction_ratio);
  const agentRatio = Number(capabilities.agent_compaction_ratio);
  const rawWindow = Number(capabilities.context_window_tokens);
  const contextWindow =
    Number.isFinite(rawWindow) && rawWindow >= 8_000 && rawWindow <= 10_000_000
      ? rawWindow
      : 256_000;
  const rawLimit = Number(capabilities.context_limit_tokens);
  const contextLimit =
    Number.isFinite(rawLimit) && rawLimit >= 8_000
      ? Math.min(rawLimit, contextWindow)
      : 204_000;
  return {
    ...capabilities,
    context_window_tokens: contextWindow,
    context_limit_tokens: contextLimit,
    context_window_source:
      contextWindow === rawWindow
        ? capabilities.context_window_source
        : "conservative_default",
    context_window_confidence:
      contextWindow === rawWindow
        ? capabilities.context_window_confidence
        : "unknown",
    reasoning_parameter: normalizeReasoningParameter(capabilities.reasoning_parameter),
    default_search_route: normalizeDefaultSearchRoute(capabilities.default_search_route),
    chat_compaction_ratio:
      Number.isFinite(chatRatio) && chatRatio >= 0.1 && chatRatio <= 1
        ? chatRatio
        : 0.8,
    agent_compaction_ratio:
      Number.isFinite(agentRatio) && agentRatio >= 0.1 && agentRatio <= 1
        ? agentRatio
        : 1 / 3,
  };
}

function emptyModelCapabilities(): ProviderModelCapabilities {
  return {
    // New LLM connections default to reasoning on; unsupported models can be
    // narrowed in their individual override.
    reasoning_efforts: ["low", "medium", "high", "xhigh"],
    thinking_mapping: { low: "low", medium: "medium", high: "high", xhigh: "xhigh" },
    default_thinking_mode: "medium",
    reasoning_parameter: "reasoning_effort",
    thinking_required: false,
    hosted_web_search: false,
    hosted_web_fetch: false,
    hosted_image_search: false,
    supports_image_input: false,
    supports_video_input: false,
    supports_structured_output: false,
    supports_agent_tools: true,
    image_input_mode: "auto",
    default_search_route: "auto",
    capability_source: "user_declared",
    context_window_tokens: 256_000,
    context_limit_tokens: 204_000,
    context_window_source: "conservative_default",
    context_window_confidence: "unknown",
    max_output_tokens: 4_096,
    chat_compaction_ratio: 0.8,
    agent_compaction_ratio: 1 / 3,
  };
}

function parseThinkingMappingValue(
  value: string,
  parameter: ReasoningParameter,
): string | number | boolean | null {
  const normalized = value.trim();
  if (!normalized) return null;
  if (parameter === "enable_thinking") {
    return normalized.toLowerCase() === "true";
  }
  if (parameter === "thinking_budget") {
    const parsed = Number.parseInt(normalized, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }
  return normalized;
}

function searchRouteLabel(route: SearchRoute) {
  switch (route) {
    case "model_native":
      return "仅原生";
    case "external":
      return "仅外挂";
    case "auto":
      return "自动（有原生则原生，否则外挂搜索）";
    // Retain labels for legacy values that can still arrive from saved data.
    case "disabled":
      return "自动（有原生则原生，否则外挂搜索）";
    case "local":
      return "仅外挂";
  }
}

/** Probe health is independent of workspace enablement. */
function providerStatusLabel(status: string, enabled: boolean): string {
  if (!enabled) {
    if (status === "healthy" || status === "healthy_local") {
      return "探测通过 · 未启用";
    }
    if (status === "configured_disabled") return "已配置 · 未启用";
    if (status === "unconfigured") return "未配置";
    if (status === "disabled") return "已停用";
    return `未启用 · ${status}`;
  }
  if (status === "enabled_unverified") return "已启用 · 未探测";
  if (status === "healthy") return "健康 · 已启用";
  if (status === "healthy_local") return "本地健康 · 已启用";
  return status;
}

function ModelCapabilitiesDialog({
  models,
  onClose,
  onConfigureBalance,
  onSaved,
  onSetDefault,
  provider,
}: {
  modelId: string;
  models: ProviderModelsResponse;
  onClose: () => void;
  onConfigureBalance: () => void;
  onSaved: (snapshot: ProviderModelCapabilityView) => void;
  onSetDefault: (modelId: string) => void;
  provider: Provider;
}) {
  const queryClient = useQueryClient();
  const protocolCatalog = useQuery({
    queryKey: ["provider-catalog"],
    queryFn: listProviderCatalog,
  });
  const protocolOptions = (protocolCatalog.data ?? []).filter((item) => {
    const current = (protocolCatalog.data ?? []).find(
      (candidate) => candidate.provider_type === provider.provider_type,
    );
    return item.create_allowed && current && item.role === current.role;
  });
  // "none" keeps the dialog lightweight: the template form only appears after
  // an explicit edit action. Per-model parameters open in a nested dialog.
  const [editScope, setEditScope] = useState<"none" | "group">("none");
  const [editModelId, setEditModelId] = useState<string | null>(null);
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? "");
  const [protocolType, setProtocolType] = useState(provider.provider_type);
  const [headers, setHeaders] = useState(() => stringifyExtraHeaders(providerExtraHeaders(provider)));
  const [secret, setSecret] = useState("");
  const [modelSearch, setModelSearch] = useState("");
  // The dialog can grow the list by pinning models manually, so it keeps its
  // own copy instead of reading the discovery-derived prop directly.
  const [modelsList, setModelsList] = useState(
    () => mergeProviderModelLists(models, provider) ?? models,
  );
  const [manualModelId, setManualModelId] = useState("");
  const [modelStates, setModelStates] = useState<Record<string, boolean>>(
    Object.fromEntries(modelsList.models.map((model) => [model.id, model.enabled !== false])),
  );
  const [defaultModelId, setDefaultModelId] = useState(() =>
    providerDefaultModelId(provider),
  );
  const [capabilities, setCapabilities] =
    useState<ProviderModelCapabilities>(emptyModelCapabilities);

  const latestModels = useQuery({
    queryKey: ["provider-models", "capability-dialog", provider.id],
    queryFn: () => discoverProviderModels(provider.id),
    retry: false,
  });
  useEffect(() => {
    const discovered = latestModels.data;
    if (!discovered) return;
    const merged = mergeProviderModelLists(discovered, provider) ?? discovered;
    setModelsList((current) => {
      const byId = new Map(current.models.map((model) => [model.id, model]));
      for (const model of merged.models) byId.set(model.id, model);
      return { ...merged, models: [...byId.values()] };
    });
    setModelStates((current) => {
      const next = { ...current };
      for (const model of merged.models) {
        if (!(model.id in next)) next[model.id] = model.enabled !== false;
      }
      return next;
    });
  }, [latestModels.data, provider]);
  const templateRaw = provider.capabilities.model_defaults;
  const templateConfigured = Boolean(
    templateRaw &&
      typeof templateRaw === "object" &&
      !Array.isArray(templateRaw) &&
      Object.keys(templateRaw as Record<string, unknown>).length > 0,
  );
  // Absent flag = on for providers that already carry a template (legacy data).
  const [templateOn, setTemplateOn] = useState(() =>
    typeof provider.capabilities.model_defaults_enabled === "boolean"
      ? provider.capabilities.model_defaults_enabled
      : templateConfigured,
  );

  useEffect(() => {
    if (editScope !== "group") return;
    const defaults = provider.capabilities.model_defaults;
    const merged = {
      ...emptyModelCapabilities(),
      ...(defaults && typeof defaults === "object" && !Array.isArray(defaults)
        ? defaults
        : {}),
    } as ProviderModelCapabilities;
    setCapabilities(normalizeLoadedCapabilities(merged));
  }, [editScope, provider.capabilities.model_defaults]);

  const save = useMutation({
    mutationFn: (payload: ProviderModelCapabilities) =>
      updateProviderModelGroupCapabilities(provider.id, capabilitiesForSave(payload)),
    onSuccess: (snapshot) => {
      onSaved(snapshot);
      toast.success("全局模板已保存");
      onClose();
    },
    onError: (error) => toast.error(error.message),
  });
  const toggleTemplate = useMutation({
    mutationFn: (enabled: boolean) =>
      updateProvider(provider.id, { model_defaults_enabled: enabled }),
    onSuccess: (_, enabled) => {
      toast.success(
        enabled
          ? "全局覆盖已开启，全部模型将遵从全局模板"
          : "全局覆盖已关闭，各模型使用自身默认配置",
      );
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error, enabled) => {
      setTemplateOn(!enabled);
      toast.error(error.message);
    },
  });
  const syncCatalogDefaults = useMutation({
    mutationFn: () =>
      syncProviderModelCatalogDefaults(
        provider.id,
        modelsList.models.map((model) => model.id),
      ),
    onSuccess: (result) => {
      for (const snapshot of result.models) {
        queryClient.setQueryData(
          ["provider-model-capabilities", provider.id, snapshot.model_id],
          snapshot,
        );
        onSaved(snapshot);
      }
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      toast.success(`已为 ${result.models.length} 个模型同步官方默认参数`);
    },
    onError: (error) => toast.error(error.message),
  });
  const addManualModel = useMutation({
    mutationFn: (modelId: string) =>
      syncProviderModelCatalogDefaults(provider.id, [modelId]),
    onSuccess: (result) => {
      const snapshot = result.models[0];
      if (!snapshot) return;
      setModelsList((current) => {
        if (current.models.some((model) => model.id === snapshot.model_id)) {
          return current;
        }
        return {
          ...current,
          models: [
            ...current.models,
            {
              id: snapshot.model_id,
              roles: ["llm"],
              streaming: true,
              remote: true,
              enabled: true,
              capabilities: snapshot.capabilities,
            },
          ],
        };
      });
      setModelStates((current) => ({ ...current, [snapshot.model_id]: true }));
      setManualModelId("");
      onSaved(snapshot);
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      toast.success(`已添加模型 ${snapshot.model_id}，可继续设置上下文窗口`);
      // Open the per-model editor immediately so the context window can be
      // configured right away instead of hunting for the new row.
      setEditModelId(snapshot.model_id);
    },
    onError: (error) => toast.error(error.message),
  });
  const removeModel = useMutation({
    mutationFn: (modelId: string) => deleteProviderModel(provider.id, modelId),
    onSuccess: (result) => {
      setModelsList((current) => ({
        ...current,
        models: current.models.filter((model) => model.id !== result.model_id),
      }));
      setModelStates((current) => {
        const next = { ...current };
        delete next[result.model_id];
        return next;
      });
      setDefaultModelId(result.default_model ?? "");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      toast.success(`已删除模型 ${result.model_id}`);
    },
    onError: (error) => toast.error(error.message),
  });
  const updateConnection = useMutation({
    mutationFn: () => updateProvider(provider.id, {
      provider_type: protocolType === provider.provider_type ? undefined : protocolType,
      base_url: baseUrl.trim() || null,
      extra_headers: parseExtraHeadersInput(headers),
    }),
    onSuccess: (updatedProvider) => {
      queryClient.setQueryData<Provider[]>(["providers"], (current) =>
        current?.map((item) =>
          item.id === updatedProvider.id ? updatedProvider : item,
        ),
      );
      toast.success("连接配置已保存");
      onClose();
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const rotateSecretHere = useMutation({
    mutationFn: () => rotateProviderSecret(provider.id, secret),
    onSuccess: () => {
      setSecret("");
      toast.success("Secret 已更新");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (editScope === "none") {
      // Nothing is being edited — the footer action only commits the model
      // on/off switches.
      updateProviderModelStates(provider.id, modelStates)
        .then(() => {
          toast.success("模型开关已保存");
          void queryClient.invalidateQueries({ queryKey: ["providers"] });
          onClose();
        })
        .catch((error: Error) => toast.error(error.message));
      return;
    }
    if (
      capabilities.default_thinking_mode !== "off" &&
      !capabilities.reasoning_efforts.includes(capabilities.default_thinking_mode)
    ) {
      toast.error("默认思考模式必须已列入支持的推理强度");
      return;
    }
    if (
      capabilities.default_search_route === "model_native" &&
      !capabilities.hosted_web_search
    ) {
      toast.error("模型原生联网需要先确认托管网页搜索能力");
      return;
    }
    const contextError = capabilityContextError(capabilities);
    if (contextError) {
      toast.error(contextError);
      return;
    }
    // Model switches are part of this supplier configuration and commit with
    // the footer action, rather than requiring a second "apply" step.
    updateProviderModelStates(provider.id, modelStates)
      .then(() => save.mutate(capabilities))
      .catch((error: Error) => toast.error(error.message));
  }

  function addManualModelHandler() {
    const name = manualModelId.trim();
    if (!name) {
      toast.error("请输入模型名称");
      return;
    }
    if (name.length > 160) {
      toast.error("模型名称不能超过 160 个字符");
      return;
    }
    if (modelsList.models.some((model) => model.id === name)) {
      toast.error(`模型 ${name} 已在列表中`);
      return;
    }
    addManualModel.mutate(name);
  }

  const overridesRaw = provider.capabilities.models;
  const overrideModelIds =
    overridesRaw &&
    typeof overridesRaw === "object" &&
    !Array.isArray(overridesRaw)
      ? Object.keys(overridesRaw as Record<string, unknown>)
      : [];
  const visibleModels = (() => {
    const query = modelSearch.trim().toLowerCase();
    if (!query) return modelsList.models;
    return modelsList.models
      .map((model) => ({
        model,
        score: model.id.toLowerCase().includes(query)
          ? 0
          : fuzzyMatchesModelId(model.id.toLowerCase(), query)
            ? 1
            : -1,
      }))
      .filter((entry) => entry.score >= 0)
      .sort((left, right) => left.score - right.score)
      .map((entry) => entry.model);
  })();
  const balanceConfig = providerBalanceQueryConfig(provider);
  const balanceLast = balanceConfig?.enabled
    ? providerBalanceQueryLastResult(provider)
    : null;
  return (
    <Dialog onOpenChange={(open) => !open && !save.isPending && onClose()} open>
      <DialogContent className="h-[min(88dvh,860px)] overflow-hidden p-0 sm:max-w-3xl">
        <form className="flex min-h-0 flex-1 flex-col" onSubmit={submit}>
          <DialogHeader className="shrink-0 border-b px-5 py-5 pr-14">
            <DialogTitle>供应商配置</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5">
            <div className="space-y-5 py-5">
              <section className="space-y-3 rounded-xl border p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">全局模板</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      开启后模板覆盖该供应商全部模型；关闭后各模型使用自身默认配置。
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      disabled={
                        modelsList.models.length === 0 ||
                        syncCatalogDefaults.isPending
                      }
                      onClick={() => syncCatalogDefaults.mutate()}
                      size="xs"
                      title={
                        modelsList.models.length > 0
                          ? "为模型列表中的全部模型写入官方目录默认参数"
                          : "请先发现模型，或手动添加模型"
                      }
                      type="button"
                      variant="outline"
                    >
                      {syncCatalogDefaults.isPending
                        ? "同步中…"
                        : "一键同步官方默认参数"}
                    </Button>
                    <Button
                      onClick={() =>
                        setEditScope(editScope === "group" ? "none" : "group")
                      }
                      size="xs"
                      type="button"
                      variant={editScope === "group" ? "default" : "outline"}
                    >
                      {editScope === "group" ? "收起编辑" : "编辑全局模板"}
                    </Button>
                    <label className="flex items-center gap-2 rounded-lg border px-2.5 py-1 text-xs font-medium">
                      全局覆盖
                      <Switch
                        checked={templateOn}
                        disabled={toggleTemplate.isPending}
                        onCheckedChange={(checked) => {
                          setTemplateOn(checked);
                          toggleTemplate.mutate(checked);
                        }}
                      />
                    </label>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5 text-[11px]">
                  <span
                    className={`rounded-md border px-1.5 py-0.5 font-medium ${
                      templateConfigured && templateOn
                        ? "border-primary/30 bg-primary/10 text-primary"
                        : "bg-muted/50 text-muted-foreground"
                    }`}
                  >
                    {templateConfigured
                      ? templateOn
                        ? "全局覆盖已开启 · 模板对全部模型生效"
                        : "模板已保存 · 全局覆盖已关闭"
                      : "未配置模板参数"}
                  </span>
                  <span className="rounded-md border bg-muted/50 px-1.5 py-0.5 text-muted-foreground">
                    {overrideModelIds.length > 0
                      ? `${overrideModelIds.length} 个模型有单独配置`
                      : "无单模型配置"}
                  </span>
                </div>
                {templateOn && !templateConfigured ? (
                  <p className="rounded-lg bg-muted px-3 py-2 text-xs">
                    全局覆盖已开启，但尚未保存模板参数；请点击「编辑全局模板」完成配置。
                  </p>
                ) : editScope === "none" ? (
                  <p className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
                    模板参数表单在点击「编辑全局模板」后展开；单个模型的参数请在模型行「编辑」弹出的窗口中调整。
                  </p>
                ) : null}
              </section>
              <section className="space-y-3 rounded-xl border p-4">
                <div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold">模型列表</p><p className="mt-1 text-xs text-muted-foreground">开关将在底部保存时统一提交。</p></div><div className="flex gap-2"><Button onClick={() => setModelStates(Object.fromEntries(modelsList.models.map((model) => [model.id, true])))} size="xs" type="button" variant="outline">全部启用</Button><Button onClick={() => setModelStates(Object.fromEntries(modelsList.models.map((model) => [model.id, false])))} size="xs" type="button" variant="outline">全部停用</Button></div></div>
                <div className="flex items-center gap-2">
                  <Input
                    aria-label="手动添加模型"
                    className="h-8 flex-1 font-mono text-xs"
                    disabled={addManualModel.isPending}
                    onChange={(event) => setManualModelId(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        addManualModelHandler();
                      }
                    }}
                    placeholder="手动输入模型名称，例如 my-private-model-v1"
                    value={manualModelId}
                  />
                  <Button
                    disabled={addManualModel.isPending || !manualModelId.trim()}
                    onClick={addManualModelHandler}
                    size="xs"
                    type="button"
                    variant="outline"
                  >
                    <Plus className="size-3" />
                    {addManualModel.isPending ? "添加中…" : "手动添加"}
                  </Button>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  厂商未在列表暴露的模型（新发布型号 / 私有模型 / 中继别名）可手动添加，随后点击「编辑」设置上下文窗口。
                </p>
                <Input aria-label="搜索模型" onChange={(event) => setModelSearch(event.target.value)} placeholder="模糊搜索模型名称…" value={modelSearch} />
                <ScrollArea className="rounded-lg border [&>[data-slot=scroll-area-viewport]]:max-h-48" type="always">
                  <div className="divide-y">
                    {modelsList.models.length === 0 ? (
                      <p className="px-3 py-4 text-xs text-muted-foreground">尚未发现模型。可先在上方手动添加模型名称，或返回列表「发现模型」。</p>
                    ) : visibleModels.length === 0 ? (
                      <p className="px-3 py-4 text-xs text-muted-foreground">没有匹配的模型。</p>
                    ) : null}
                    {visibleModels.map((model) => (
                    <div
                      className="flex items-center gap-3 px-3 py-2 text-xs"
                      key={model.id}
                    >
                      <span className="min-w-0 flex-1 truncate font-mono">{model.id}</span>
                      {overrideModelIds.includes(model.id) ? (
                        <span
                          className="rounded border px-1 py-0.5 text-[10px] text-muted-foreground"
                          title="该模型有单独配置；全局覆盖开启时以全局模板为准"
                        >
                          单独配置
                        </span>
                      ) : null}
                      {defaultModelId === model.id ? (
                        <Button disabled size="xs" type="button" variant="ghost">默认</Button>
                      ) : (
                        <Button
                          disabled={removeModel.isPending}
                          onClick={() => {
                            const previous = defaultModelId;
                            setDefaultModelId(model.id);
                            onSetDefault(model.id);
                            void queryClient
                              .invalidateQueries({ queryKey: ["providers"] })
                              .catch(() => setDefaultModelId(previous));
                          }}
                          size="xs"
                          type="button"
                          variant="ghost"
                        >
                          设为默认
                        </Button>
                      )}
                      <Button onClick={() => setEditModelId(model.id)} size="xs" type="button" variant="ghost"><Pencil className="size-3" />编辑</Button>
                      <Button
                        disabled={removeModel.isPending}
                        onClick={() => removeModel.mutate(model.id)}
                        size="xs"
                        title="从此供应商的模型列表移除"
                        type="button"
                        variant="ghost"
                      >
                        <Trash2 className="size-3" />删除
                      </Button>
                      <Switch
                        checked={modelStates[model.id] === true}
                        onCheckedChange={(checked) =>
                          setModelStates((current) => ({
                            ...current,
                            [model.id]: checked,
                          }))
                        }
                      />
                    </div>
                  ))}
                  </div>
                </ScrollArea>
              </section>
              {editScope === "group" ? (
                <CapabilityFormFields
                  capabilities={capabilities}
                  idPrefix={`group-${provider.id}`}
                  providerType={provider.provider_type}
                  setCapabilities={setCapabilities}
                />
              ) : null}
              <section className="space-y-3 rounded-xl border p-4">
                <div>
                  <p className="text-sm font-semibold">连接配置</p>
                  <p className="mt-1 text-xs text-muted-foreground">URL、请求头和 Secret 统一在这里维护。</p>
                </div>
                <Label>
                  协议类型
                  <Select onValueChange={setProtocolType} value={protocolType}>
                    <SelectTrigger className="mt-2"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {protocolOptions.map((item) => (
                        <SelectItem key={item.provider_type} value={item.provider_type}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Label>
                <Label>
                  Base URL
                  <Input className="mt-2" onChange={(event) => setBaseUrl(event.target.value)} value={baseUrl} />
                  {(() => {
                    const trimmedConnectionUrl = baseUrl.trim();
                    if (trimmedConnectionUrl && !isValidHttpUrl(trimmedConnectionUrl)) {
                      return (
                        <span className="mt-1 block text-xs text-destructive" role="alert">
                          {invalidBaseUrlMessage(trimmedConnectionUrl)}
                        </span>
                      );
                    }
                    return null;
                  })()}
                </Label>
                <Label>请求头（JSON 对象）<Textarea className="mt-2 min-h-20 font-mono text-xs" onChange={(event) => setHeaders(event.target.value)} value={headers} /></Label>
                <div className="flex flex-wrap items-end gap-2">
                  <Label className="min-w-52 flex-1">替换 Secret<Input className="mt-2" onChange={(event) => setSecret(event.target.value)} placeholder="输入新 Secret" type="password" value={secret} /></Label>
                  <Button
                    disabled={updateConnection.isPending}
                    onClick={() => {
                      const trimmedConnectionUrl = baseUrl.trim();
                      if (trimmedConnectionUrl && !isValidHttpUrl(trimmedConnectionUrl)) {
                        toast.error(invalidBaseUrlMessage(trimmedConnectionUrl));
                        return;
                      }
                      updateConnection.mutate();
                    }}
                    type="button"
                    variant="outline"
                  >
                    保存连接
                  </Button>
                  <Button disabled={!secret.trim() || rotateSecretHere.isPending} onClick={() => rotateSecretHere.mutate()} type="button" variant="outline">更新 Secret</Button>
                </div>
              </section>
              <section className="space-y-3 rounded-xl border p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">余额查询</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {balanceConfig?.enabled
                        ? `自定义脚本已启用${
                            balanceConfig.auto_query_interval_minutes > 0
                              ? ` · 每 ${balanceConfig.auto_query_interval_minutes} 分钟自动查询`
                              : ""
                          }`
                        : "当前使用官方内置方式；可切换为自定义脚本（兼容 cc-switch）。"}
                    </p>
                    {balanceLast ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatBalanceQuerySummary(balanceLast)} ·{" "}
                        {relativeTimeLabel(balanceLast.queried_at)}
                      </p>
                    ) : null}
                  </div>
                  <Button
                    onClick={onConfigureBalance}
                    size="xs"
                    type="button"
                    variant="outline"
                  >
                    <Settings2 className="size-3" />
                    配置余额查询
                  </Button>
                </div>
              </section>
              {save.isError ? (
                <p className="text-sm text-destructive" role="alert">
                  {save.error.message}
                </p>
              ) : null}
            </div>
          </div>
          <DialogFooter className="mx-0 mb-0 shrink-0 rounded-none rounded-b-2xl px-5 py-4">
            <Button
              disabled={save.isPending}
              onClick={onClose}
              type="button"
              variant="outline"
            >
              取消
            </Button>
            <Button disabled={save.isPending} type="submit">
              {save.isPending
                ? "保存中…"
                : editScope === "none"
                  ? "保存模型开关"
                  : "保存全局模板"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
      {editModelId ? (
        <ModelOverrideDialog
          globalOverrideOn={templateOn && templateConfigured}
          modelId={editModelId}
          onClose={() => setEditModelId(null)}
          onSaved={onSaved}
          provider={provider}
        />
      ) : null}
    </Dialog>
  );
}

function clampCompactionPercent(value: number): number {
  if (!Number.isFinite(value)) return 10;
  return Math.min(100, Math.max(10, Math.round(value)));
}

function CompactionRatioField({
  description,
  label,
  onChange,
  ratio,
  tokenLimit,
}: {
  description: string;
  label: string;
  onChange: (ratio: number) => void;
  ratio: number;
  tokenLimit: number;
}) {
  const percent = clampCompactionPercent(ratio * 100);
  const updatePercent = (value: number) => onChange(clampCompactionPercent(value) / 100);

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <Label>{label}</Label>
          <p className="mt-1 text-xs text-muted-foreground">{description}</p>
        </div>
        <span className="text-xs tabular-nums text-muted-foreground">
          约 {Math.round(tokenLimit * (percent / 100)).toLocaleString()} tokens
        </span>
      </div>
      <Slider
        aria-label={`${label}百分比`}
        max={100}
        min={10}
        onValueChange={([value]) => updatePercent(value ?? percent)}
        step={1}
        value={[percent]}
      />
      <div className="flex flex-wrap items-center gap-2">
        {[30, 80].map((quickValue) => (
          <Button
            className="h-8 px-3 text-xs"
            key={quickValue}
            onClick={() => updatePercent(quickValue)}
            type="button"
            variant={percent === quickValue ? "default" : "outline"}
          >
            {quickValue}%
          </Button>
        ))}
        <div className="relative ml-auto w-24">
          <Input
            aria-label={`${label}比例`}
            className="h-8 pr-7 text-right tabular-nums"
            max={100}
            min={10}
            onChange={(event) => updatePercent(Number(event.target.value))}
            step={1}
            type="number"
            value={percent}
          />
          <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-xs text-muted-foreground">
            %
          </span>
        </div>
      </div>
    </div>
  );
}

function capabilityContextError(capabilities: ProviderModelCapabilities): string | null {
  if (
    !Number.isFinite(capabilities.context_window_tokens) ||
    capabilities.context_window_tokens < 8_000 ||
    capabilities.context_window_tokens > 10_000_000
  ) {
    return "最大输入上下文必须在 8,000 到 10,000,000 tokens 之间";
  }
  if (
    !Number.isFinite(capabilities.max_output_tokens) ||
    capabilities.max_output_tokens < 1 ||
    capabilities.max_output_tokens > 1_000_000
  ) {
    return "最大输出上下文必须在 1 到 1,000,000 tokens 之间";
  }
  if (
    capabilities.chat_compaction_ratio < 0.1 ||
    capabilities.chat_compaction_ratio > 1 ||
    capabilities.agent_compaction_ratio < 0.1 ||
    capabilities.agent_compaction_ratio > 1
  ) {
    return "压缩门槛必须在 10% 到 100% 之间";
  }
  return null;
}

function capabilitiesForSave(
  capabilities: ProviderModelCapabilities,
): ProviderModelCapabilities {
  return capabilities;
}

function CapabilityFormFields({
  capabilities,
  idPrefix,
  providerType,
  setCapabilities,
}: {
  capabilities: ProviderModelCapabilities;
  idPrefix: string;
  providerType: string;
  setCapabilities: Dispatch<SetStateAction<ProviderModelCapabilities>>;
}) {
  const [showThinkingAdvanced, setShowThinkingAdvanced] = useState(false);

  function toggleReasoningEffort(
    effort: Exclude<ThinkingMode, "off">,
    checked: boolean,
  ) {
    setCapabilities((current) => {
      const reasoning_efforts = checked
        ? [...new Set([...current.reasoning_efforts, effort])]
        : current.reasoning_efforts.filter((item) => item !== effort);
      return {
        ...current,
        reasoning_efforts,
        default_thinking_mode:
          !checked && current.default_thinking_mode === effort
            ? "off"
            : current.default_thinking_mode,
      };
    });
  }

  return (
    <>
      <section className="space-y-4 rounded-xl border p-4">
        <div>
          <p className="text-sm font-semibold">上下文限制</p>
          <p className="mt-1 text-xs text-muted-foreground">
            分别设置模型可接收的最大输入和最大输出，并按模式控制历史消息压缩门槛。
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            当前输入上限来源：{capabilities.context_window_source === "provider"
              ? "供应商声明"
              : capabilities.context_window_source === "official_catalog"
                ? "模型目录"
                : capabilities.context_window_source === "user_declared"
                  ? "手动设置"
                  : "默认值（供应商未提供，可手动调整）"}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Label>
            最大输入上下文
            <Input
              className="mt-2"
              max={10_000_000}
              min={8_000}
              onChange={(event) => {
                const value = Number(event.target.value);
                setCapabilities((current) => ({
                  ...current,
                  context_window_tokens: value,
                  context_limit_tokens: Math.min(current.context_limit_tokens, value),
                  context_window_source: "user_declared",
                  context_window_confidence: "confirmed",
                }));
              }}
              type="number"
              value={capabilities.context_window_tokens}
            />
          </Label>
          <Label>
            最大输出上下文
            <Input
              className="mt-2"
              max={1_000_000}
              min={1}
              onChange={(event) =>
                setCapabilities((current) => ({
                  ...current,
                  max_output_tokens: Number(event.target.value),
                }))
              }
              type="number"
              value={capabilities.max_output_tokens}
            />
          </Label>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <CompactionRatioField
            description="极速与思考模式共用此门槛。"
            label="对话模式压缩门槛"
            onChange={(ratio) =>
              setCapabilities((current) => ({
                ...current,
                chat_compaction_ratio: ratio,
              }))
            }
            ratio={capabilities.chat_compaction_ratio}
            tokenLimit={capabilities.context_window_tokens}
          />
          <CompactionRatioField
            description="为后续工具调用和结果预留空间。"
            label="智能体模式压缩门槛"
            onChange={(ratio) =>
              setCapabilities((current) => ({
                ...current,
                agent_compaction_ratio: ratio,
              }))
            }
            ratio={capabilities.agent_compaction_ratio}
            tokenLimit={capabilities.context_window_tokens}
          />
        </div>
      </section>
      <section className="space-y-3 rounded-xl border p-4">
        <Collapsible
          open={showThinkingAdvanced}
          onOpenChange={setShowThinkingAdvanced}
        >
          <CollapsibleTrigger asChild>
            <button className="group flex w-full items-center justify-between gap-3 rounded-lg text-left">
              <span>
                <p className="text-sm font-semibold">推理强度与映射（高级）</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  已按官方文档自动适配，一般无需修改；展开可覆盖该模型实际接受的参数形状与档位映射。
                </p>
              </span>
              <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-3 pt-3">
        <div>
          <p className="text-sm font-semibold">推理强度与映射</p>
          <p className="mt-1 text-xs text-muted-foreground">
            选择 LearnGraph 可以请求的级别，并映射为该 Provider 实际接受的参数值。
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {reasoningModes.map((effort) => {
            const inputId = `${idPrefix}-${effort}`;
            const enabled = capabilities.reasoning_efforts.includes(effort);
            return (
              <div className="rounded-lg border p-3" key={effort}>
                <div className="flex items-center gap-2">
                  <Checkbox
                    checked={enabled}
                    id={inputId}
                    onCheckedChange={(checked) =>
                      toggleReasoningEffort(effort, checked === true)
                    }
                  />
                  <Label htmlFor={inputId}>支持 {effort}</Label>
                </div>
                <Input
                  className="mt-3 h-8 font-mono text-xs"
                  disabled={!enabled}
                  onChange={(event) =>
                    setCapabilities((current) => ({
                      ...current,
                      thinking_mapping: {
                        ...current.thinking_mapping,
                        [effort]: parseThinkingMappingValue(
                          event.target.value,
                          current.reasoning_parameter,
                        ),
                      },
                    }))
                  }
                  placeholder={`实际参数值，默认 ${effort}`}
                  value={String(capabilities.thinking_mapping[effort] ?? "")}
                />
              </div>
            );
          })}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>实际参数形状</Label>
            <Select
              onValueChange={(value) =>
                setCapabilities((current) => ({
                  ...current,
                  reasoning_parameter: value as ReasoningParameter,
                }))
              }
              value={capabilities.reasoning_parameter}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="reasoning_effort">
                  reasoning_effort
                </SelectItem>
                <SelectItem value="reasoning.effort">
                  reasoning.effort
                </SelectItem>
                <SelectItem value="enable_thinking">
                  enable_thinking（布尔开关）
                </SelectItem>
                <SelectItem value="thinking_budget">
                  thinking_budget（Token 预算）
                </SelectItem>
                <SelectItem value="thinking">
                  thinking（adaptive / disabled）
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
            <Label htmlFor={`${idPrefix}-thinking-required`}>仅支持思考模式</Label>
            <Switch
              checked={capabilities.thinking_required === true}
              id={`${idPrefix}-thinking-required`}
              onCheckedChange={(checked) =>
                setCapabilities((current) => ({
                  ...current,
                  thinking_required: checked,
                  default_thinking_mode:
                    checked && current.default_thinking_mode === "off"
                      ? current.reasoning_efforts.includes("medium")
                        ? "medium"
                        : current.reasoning_efforts[0] ?? "medium"
                      : current.default_thinking_mode,
                }))
              }
            />
          </div>
        </div>
          </CollapsibleContent>
        </Collapsible>
      </section>
      <section className="space-y-4 rounded-xl border p-4">
        <div>
          <p className="text-sm font-semibold">联网能力</p>
          <p className="mt-1 text-xs text-muted-foreground">
            设置模型是否可使用原生网页搜索，以及默认的联网方式。
          </p>
        </div>
        <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
          <Label htmlFor={`${idPrefix}-hosted-web-search`}>支持托管网页搜索</Label>
          <Switch
            checked={capabilities.hosted_web_search}
            id={`${idPrefix}-hosted-web-search`}
            onCheckedChange={(checked) =>
              setCapabilities((current) => ({
                ...current,
                hosted_web_search: checked,
                default_search_route:
                  !checked && current.default_search_route === "model_native"
                    ? "auto"
                    : current.default_search_route,
              }))
            }
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
            <Label htmlFor={`${idPrefix}-hosted-web-fetch`}>原生网页抓取</Label>
            <Switch
              checked={capabilities.hosted_web_fetch === true}
              id={`${idPrefix}-hosted-web-fetch`}
              onCheckedChange={(checked) =>
                setCapabilities((current) => ({
                  ...current,
                  hosted_web_fetch: checked,
                }))
              }
            />
          </div>
          <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
            <Label htmlFor={`${idPrefix}-hosted-image-search`}>原生图片搜索</Label>
            <Switch
              checked={capabilities.hosted_image_search === true}
              id={`${idPrefix}-hosted-image-search`}
              onCheckedChange={(checked) =>
                setCapabilities((current) => ({
                  ...current,
                  hosted_image_search: checked,
                }))
              }
            />
          </div>
        </div>
        {providerType === "qwen" &&
        (capabilities.hosted_web_search ||
          capabilities.hosted_web_fetch ||
          capabilities.hosted_image_search) ? (
          <p className="rounded-lg border border-primary/25 bg-primary/5 px-3 py-2 text-xs leading-5">
            组合调度：该 Qwen 模型可作为其他主模型的联网搜索、网页抓取或图片搜索工具。
            图片搜索与精细抓取使用 Responses 内置工具，模型 Token 与工具调用会分别计费。
            当前目录参考价：Agent 搜索 ¥4/千次、文字搜图 ¥24/千次、反向搜图
            ¥48/千次；最终以千问控制台账单为准。
          </p>
        ) : null}
        <div className="space-y-2">
          <Label>默认联网路由</Label>
          <Select
            onValueChange={(value) =>
              setCapabilities((current) => ({
                ...current,
                default_search_route: value as SearchRoute,
              }))
            }
            value={capabilities.default_search_route}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {searchRoutes.map((route) => (
                <SelectItem
                  disabled={
                    route === "model_native" && !capabilities.hosted_web_search
                  }
                  key={route}
                  value={route}
                >
                  {searchRouteLabel(route)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </section>
      <section className="space-y-3 rounded-xl border p-4">
        <div>
          <p className="text-sm font-semibold">多模态输入</p>
          <p className="mt-1 text-xs text-muted-foreground">
            开启「支持图片输入」后，图片会从受授权对象存储直接传给本次模型调用。
            未开启时默认走工作区「识图 / 视觉」Provider（外挂描述后再由本模型回答）。
            图片不会写入消息正文。
          </p>
        </div>
        <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
          <Label htmlFor={`${idPrefix}-supports-image-input`}>支持图片输入</Label>
          <Switch
            checked={capabilities.supports_image_input}
            id={`${idPrefix}-supports-image-input`}
            onCheckedChange={(checked) =>
              setCapabilities((current) => ({
                ...current,
                supports_image_input: checked,
                // Native mode is only valid when image input is confirmed.
                image_input_mode:
                  checked
                    ? current.image_input_mode === "external_vision"
                      ? "external_vision"
                      : current.image_input_mode ?? "auto"
                    : current.image_input_mode === "native"
                      ? "auto"
                      : current.image_input_mode ?? "auto",
              }))
            }
          />
        </div>
        <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
          <Label htmlFor={`${idPrefix}-supports-video-input`}>支持视频理解</Label>
          <Switch
            checked={capabilities.supports_video_input === true}
            id={`${idPrefix}-supports-video-input`}
            onCheckedChange={(checked) =>
              setCapabilities((current) => ({
                ...current,
                supports_video_input: checked,
              }))
            }
          />
        </div>
        {providerType === "qwen" &&
        (capabilities.supports_image_input ||
          capabilities.supports_video_input) ? (
          <p className="rounded-lg border border-primary/25 bg-primary/5 px-3 py-2 text-xs leading-5">
            可将此模型作为其他文本模型的视觉伴随模型。视频理解会按采样帧计入输入 Token；
            使用前请确认文件大小、时长与当前快照限制。
          </p>
        ) : null}
        <div className="space-y-2">
          <Label>图片输入路径</Label>
          <Select
            onValueChange={(value) =>
              setCapabilities((current) => ({
                ...current,
                image_input_mode: value as
                  | "native"
                  | "external_vision"
                  | "auto",
                supports_image_input:
                  value === "native"
                    ? true
                    : current.supports_image_input,
              }))
            }
            value={capabilities.image_input_mode ?? "auto"}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">
                自动（有原生用原生，否则外挂视觉）
              </SelectItem>
              <SelectItem
                disabled={!capabilities.supports_image_input}
                value="native"
              >
                原生多模态
              </SelectItem>
              <SelectItem value="external_vision">
                外挂识图 Provider
              </SelectItem>
            </SelectContent>
          </Select>
        </div>
      </section>
    </>
  );
}

function ModelOverrideDialog({
  globalOverrideOn,
  modelId,
  onClose,
  onSaved,
  provider,
}: {
  globalOverrideOn: boolean;
  modelId: string;
  onClose: () => void;
  onSaved: (snapshot: ProviderModelCapabilityView) => void;
  provider: Provider;
}) {
  const queryClient = useQueryClient();
  const capabilitiesQuery = useQuery({
    queryKey: ["provider-model-capabilities", provider.id, modelId],
    queryFn: () => getProviderModelCapabilities(provider.id, modelId),
    retry: false,
  });
  const snapshotMissing =
    capabilitiesQuery.error instanceof ApiError &&
    capabilitiesQuery.error.code === "model_capabilities_not_found";
  const [capabilities, setCapabilities] =
    useState<ProviderModelCapabilities>(emptyModelCapabilities);

  useEffect(() => {
    if (capabilitiesQuery.data) {
      const next = capabilitiesQuery.data.capabilities;
      setCapabilities(
        normalizeLoadedCapabilities({
          ...emptyModelCapabilities(),
          ...next,
          image_input_mode: next.image_input_mode ?? "auto",
        }),
      );
    } else if (snapshotMissing) {
      setCapabilities(emptyModelCapabilities());
    }
  }, [capabilitiesQuery.data, snapshotMissing]);

  const loadCatalogDefaults = useMutation({
    mutationFn: () => getProviderModelDefaults(modelId, provider.provider_type),
    onSuccess: (view) => {
      const merged = {
        ...emptyModelCapabilities(),
        ...view.capabilities,
      } as ProviderModelCapabilities;
      setCapabilities(
        normalizeLoadedCapabilities({
          ...merged,
          // The catalog may report internal source labels the save schema
          // rejects; a user-triggered fill is always an official-catalog value.
          capability_source: "official_catalog",
        }),
      );
      toast.success("已填入官方默认参数，确认后请保存");
    },
    onError: (error) => toast.error(error.message),
  });
  const save = useMutation({
    mutationFn: () =>
      updateProviderModelCapabilities(
        provider.id,
        modelId,
        capabilitiesForSave(capabilities),
      ),
    onSuccess: (snapshot) => {
      queryClient.setQueryData(
        ["provider-model-capabilities", provider.id, modelId],
        snapshot,
      );
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      onSaved(snapshot);
      toast.success("模型配置已保存");
      onClose();
    },
    onError: (error) => toast.error(error.message),
  });

  const terminalError = capabilitiesQuery.isError && !snapshotMissing;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (
      capabilities.default_thinking_mode !== "off" &&
      !capabilities.reasoning_efforts.includes(capabilities.default_thinking_mode)
    ) {
      toast.error("默认思考模式必须已列入支持的推理强度");
      return;
    }
    if (
      capabilities.default_search_route === "model_native" &&
      !capabilities.hosted_web_search
    ) {
      toast.error("模型原生联网需要先确认托管网页搜索能力");
      return;
    }
    const contextError = capabilityContextError(capabilities);
    if (contextError) {
      toast.error(contextError);
      return;
    }
    save.mutate();
  }

  return (
    <Dialog onOpenChange={(open) => !open && !save.isPending && onClose()} open>
      <DialogContent className="h-[min(84dvh,780px)] overflow-hidden p-0 sm:max-w-2xl">
        <form className="flex min-h-0 flex-1 flex-col" onSubmit={submit}>
          <DialogHeader className="shrink-0 border-b px-5 py-5 pr-14">
            <DialogTitle className="font-mono text-base">{modelId}</DialogTitle>
            <DialogDescription>
              单模型参数配置，保存后仅对该模型生效。
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5">
            {capabilitiesQuery.isPending ? (
              <div className="py-10">
                <LoadingState label="正在读取已保存的能力快照…" />
              </div>
            ) : terminalError ? (
              <div className="py-5">
                <ErrorState message={capabilitiesQuery.error.message} />
              </div>
            ) : (
              <div className="space-y-5 py-5">
                {globalOverrideOn ? (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200">
                    全局覆盖开启中：此单模型配置会保存，但需在供应商配置中关闭「全局覆盖」后才生效。
                  </p>
                ) : null}
                {snapshotMissing ? (
                  <p className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
                    尚未保存过该模型的单独配置，以下为当前生效的默认参数。
                  </p>
                ) : null}
                <CapabilityFormFields
                  capabilities={capabilities}
                  idPrefix={`model-${provider.id}-${modelId}`}
                  providerType={provider.provider_type}
                  setCapabilities={setCapabilities}
                />
                {save.isError ? (
                  <p className="text-sm text-destructive" role="alert">
                    {save.error.message}
                  </p>
                ) : null}
              </div>
            )}
          </div>
          <DialogFooter className="mx-0 mb-0 shrink-0 rounded-none rounded-b-2xl px-5 py-4">
            <Button
              className="mr-auto"
              disabled={
                loadCatalogDefaults.isPending || capabilitiesQuery.isPending
              }
              onClick={() => loadCatalogDefaults.mutate()}
              type="button"
              variant="outline"
            >
              {loadCatalogDefaults.isPending ? "读取中…" : "填入官方默认参数"}
            </Button>
            <Button
              disabled={save.isPending}
              onClick={onClose}
              type="button"
              variant="outline"
            >
              取消
            </Button>
            <Button
              disabled={
                capabilitiesQuery.isPending || terminalError || save.isPending
              }
              type="submit"
            >
              {save.isPending ? "保存中…" : "保存该模型配置"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

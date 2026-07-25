import {
  Children,
  isValidElement,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  AlertTriangle,
  Bot,
  CircleDollarSign,
  Download,
  Pencil,
  Plus,
  RefreshCcw,
  Search,
  SlidersHorizontal,
  Trash2,
  WalletCards,
} from "lucide-react";
import { toast } from "sonner";
import { AnimatePresence, motion } from "motion/react";

import deepseekMark from "@/assets/deepseek.svg";
import openAiMark from "@/assets/openai.svg";
import {
  acknowledgeBudgetAlert,
  clearUsageEvents,
  createBudgetPolicy,
  createExchangeRate,
  createPriceVersion,
  createProvider,
  deleteBudgetPolicy,
  deleteProvider,
  discoverProviderModels,
  getProviderBalance,
  getProviderModelCapabilities,
  getSecretStoreStatus,
  getUsageSummary,
  listBudgetAlerts,
  listBudgetPolicies,
  listBudgetStatuses,
  listExchangeRates,
  listPriceVersions,
  listPriceCatalog,
  listProviderCatalog,
  listProviders,
  listSettings,
  listUsageEvents,
  probeProvider,
  retireExchangeRate,
  retirePriceVersion,
  rotateProviderSecret,
  updateBudgetPolicy,
  updateProviderModelCapabilities,
  updateProviderModelGroupCapabilities,
  updateProviderModelStates,
  updateProvider,
  updateSetting,
} from "@/api";
import { ApiError } from "@/api";
import {
  ErrorState,
  LoadingState,
  MetricStrip,
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
  AlertDialogTrigger,
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
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  isAnthropicProvider,
  isDeepSeekProvider,
  providerExtraHeaders,
} from "@/types/providers";
import type {
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
import type {
  BudgetPolicy,
  BudgetPolicyCreate,
  BudgetPolicyUpdate,
  PriceVersionCreate,
} from "@/types/usage";

function persistedProviderModels(
  provider: Provider,
): ProviderModelsResponse | undefined {
  const ids = Array.isArray(provider.capabilities.discovered_model_ids)
    ? provider.capabilities.discovered_model_ids.filter(
        (item): item is string =>
          typeof item === "string" && Boolean(item.trim()),
      )
    : [];
  if (!ids.length) return undefined;
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
  const models = (() => {
    const values: string[] = [];
    const visit = (nodes: ReactNode) => {
      Children.forEach(nodes, (node) => {
        if (!isValidElement(node)) return;
        const props = node.props as { children?: ReactNode; value?: unknown };
        const itemValue = props.value;
        if (typeof itemValue === "string") values.push(itemValue);
        visit(props.children);
      });
    };
    visit(children);
    return [...new Set(values)];
  })();
  const selectedLabel = value || "选择已发现的模型";

  return (
    <Popover
      onOpenChange={(nextOpen) => setOpen(nextOpen)}
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
        <Command>
          <CommandInput placeholder="搜索模型名称…" />
          <CommandList className="max-h-64">
            <CommandEmpty>没有匹配的模型</CommandEmpty>
            {models.map((modelId) => (
              <CommandItem
                key={modelId}
                onSelect={() => {
                  onValueChange(modelId);
                  setOpen(false);
                }}
                value={modelId}
              >
                <span className="truncate font-mono text-xs">{modelId}</span>
                {modelId === value ? <span className="ml-auto text-xs">✓</span> : null}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
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
  const [models, setModels] = useState<Record<string, ProviderModelsResponse>>(
    {},
  );
  const [defaultModels, setDefaultModels] = useState<Record<string, string>>(
    {},
  );
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
  const discover = useMutation({
    mutationFn: discoverProviderModels,
    onSuccess: (result) => {
      setModels((current) => ({ ...current, [result.provider_id]: result }));
      const configuredProvider = providers.data?.find(
        (provider) => provider.id === result.provider_id,
      );
      const configuredProviderRole = providerCatalog.data?.find(
        (item) => item.provider_type === configuredProvider?.provider_type,
      )?.role;
      const configuredModel =
        configuredProviderRole === "image_generation"
          ? configuredProvider?.capabilities.default_image_generation_model_id
          : configuredProviderRole === "vision"
            ? configuredProvider?.capabilities.default_vision_model_id
              ?? configuredProvider?.capabilities.default_model
            : configuredProvider?.capabilities.default_model;
      setDefaultModels((current) =>
        current[result.provider_id] ||
        (typeof configuredModel === "string" && configuredModel.trim()) ||
        !result.models[0]?.id
          ? current
          : { ...current, [result.provider_id]: result.models[0].id },
      );
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
      default_vision_model_id,
    }: {
      id: string;
      enabled: boolean;
      default_model?: string;
      default_image_generation_model_id?: string;
      default_transcription_model_id?: string;
      default_vision_model_id?: string;
    }) =>
      updateProvider(id, {
        enabled,
        default_model,
        default_image_generation_model_id,
        default_transcription_model_id,
        default_vision_model_id,
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
                const isDeepSeek = isDeepSeekProvider(provider);
                const isAnthropic = isAnthropicProvider(provider);
                const isOfficialOpenAi = isOfficialOpenAiProvider(provider);
                const isModelProvider = providerSpec?.role === "model";
                const isImageGenerationProvider =
                  providerSpec?.role === "image_generation";
                const isVisionProvider = providerSpec?.role === "vision";
                const isTranscriptionProvider =
                  providerSpec?.role === "transcription";
                const hasConfigurableDefaultModel =
                  isModelProvider ||
                  isImageGenerationProvider ||
                  isTranscriptionProvider ||
                  isVisionProvider;
                const supportsModelDiscovery =
                  providerSpec?.supports_model_discovery === true;
                const supportsProbe = providerSpec?.supports_probe === true;
                // Balance is available for DeepSeek family (including openai-compatible
                // rows that declare model_family/brand_id deepseek or point at official host).
                const supportsBalance = isDeepSeek;
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
                      : provider.capabilities.default_model;
                const configuredModel =
                  typeof configuredModelValue === "string"
                    ? configuredModelValue
                    : "";
                const modelValue =
                  defaultModels[provider.id] ?? configuredModel;
                const providerModels =
                  models[provider.id] ?? persistedProviderModels(provider);
                const capabilityModelValue =
                  modelValue.trim() || providerModels?.models[0]?.id || "";
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
                          ) : providerSpec?.brand_id === "openai" ? (
                            <img
                              alt=""
                              aria-hidden="true"
                              className="size-4"
                              src={openAiMark}
                            />
                          ) : providerSpec?.brand_icon_url ? (
                            <img
                              alt=""
                              aria-hidden="true"
                              className="size-4"
                              onError={(event) => {
                                event.currentTarget.style.display = "none";
                              }}
                              src={providerSpec.brand_icon_url}
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
                          <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                            {provider.id}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-4 text-xs">
                      <p>
                        {isOfficialOpenAi
                          ? providerSpec?.label ?? provider.provider_type
                          : isDeepSeek
                          ? "OpenAI-compatible · DeepSeek"
                          : isAnthropic
                            ? "Anthropic Messages"
                          : providerSpec?.label ?? provider.provider_type}
                      </p>
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        {providerSpec
                          ? providerRoleLabel(providerSpec.role)
                          : provider.provider_type === "local_mock"
                            ? "仅开发演示"
                            : "后端未声明的类型"}
                        {customHeaderCount > 0
                          ? ` · ${customHeaderCount} 个自定义请求头`
                          : ""}
                      </p>
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
                      </div>
                    </td>
                    <td className="px-5 py-4 font-mono text-xs text-muted-foreground">
                      <p>{provider.api_key_masked ?? "未保存"}</p>
                      <p className="mt-1 text-[10px]">
                        {provider.secret_status === "active"
                          ? `Secret v${provider.secret_version} · ${provider.secret_key_provider ?? "key"} v${provider.secret_key_version}`
                          : provider.secret_status === "revoked"
                            ? `已吊销 · Secret v${provider.secret_version}`
                            : "无 Secret"}
                      </p>
                    </td>
                    <td className="px-5 py-4">
                      {hasConfigurableDefaultModel &&
                      (providerModels?.models.length ?? 0) > 0 ? (
                        <SearchableModelSelect
                          onValueChange={(value) =>
                            setDefaultModels((current) => ({ ...current, [provider.id]: value }))
                          }
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
                              <SelectItem className="font-mono text-xs" key={model.id} value={model.id}>
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
                            disabled={balance.isPending || !hasProbeConfiguration}
                            onClick={() => {
                              balance.reset();
                              setBalanceTarget(provider);
                              balance.mutate(provider.id);
                            }}
                            size="xs"
                            title={
                              hasProbeConfiguration
                                ? "按需读取 DeepSeek 当前账户余额"
                                : configurationNotice
                            }
                            variant="outline"
                          >
                            <WalletCards className="size-3" />
                            查询余额
                          </Button>
                        ) : null}
                        {isModelProvider ? (
                          <Button
                            disabled={!capabilityModelValue}
                            onClick={() =>
                              setCapabilityTarget({
                                provider,
                                modelId: capabilityModelValue,
                              })
                            }
                            size="xs"
                            title={
                              capabilityModelValue
                                ? "读取或编辑此模型的能力快照"
                                : "请先发现或填写模型 ID"
                            }
                            variant="outline"
                          >
                            <SlidersHorizontal className="size-3" />
                            能力快照
                          </Button>
                        ) : null}
                        {providerSpec?.requires_base_url ? (
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
                        {provider.provider_type !== "local_mock" ? (
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
                              !modelValue.trim())
                          }
                          onClick={() =>
                            update.mutate({
                              id: provider.id,
                              enabled: !provider.enabled,
                              default_model: isModelProvider
                                ? modelValue.trim() || undefined
                                : undefined,
                              default_image_generation_model_id:
                                isImageGenerationProvider
                                  ? modelValue.trim() || undefined
                                  : undefined,
                              default_transcription_model_id:
                                isTranscriptionProvider
                                  ? modelValue.trim() || undefined
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
                        {provider.provider_type !== "local_mock" ? (
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
              if (endpointTarget) {
                updateEndpoint.mutate({
                  id: endpointTarget.id,
                  baseUrl: endpointValue,
                });
              }
            }}
          >
            {(() => {
              const spec = endpointTarget
                ? catalogByType.get(endpointTarget.provider_type)
                : undefined;
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
                        id="provider-endpoint-edit"
                        onChange={(event) => setEndpointValue(event.currentTarget.value)}
                        placeholder={spec?.default_base_url ?? "https://provider.example/v1"}
                        value={endpointValue}
                      />
                      {spec?.default_base_url ? (
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
                        !endpointValue.trim()
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
            models[capabilityTarget.provider.id] ??
            persistedProviderModels(capabilityTarget.provider) ?? {
              provider_id: capabilityTarget.provider.id,
              status: "manual",
              models: [
                {
                  id: capabilityTarget.modelId,
                  roles: ["llm"],
                  streaming: true,
                  remote: true,
                  enabled: true,
                },
              ],
            }
          }
          onClose={() => setCapabilityTarget(null)}
          onSaved={(snapshot) => {
            setModels((current) => {
              const discovered = current[snapshot.provider_id];
              if (!discovered) return current;
              return {
                ...current,
                [snapshot.provider_id]: {
                  ...discovered,
                  models: discovered.models.map((model) =>
                    model.id === snapshot.model_id
                      ? { ...model, capabilities: snapshot.capabilities }
                      : model,
                  ),
                },
              };
            });
            void queryClient.invalidateQueries({ queryKey: ["providers"] });
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
          <DialogTitle>DeepSeek 账户余额</DialogTitle>
          <DialogDescription>
            {target?.display_name ?? "DeepSeek"}。余额仅在你主动查询时从已配置的
            DeepSeek 账户读取。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-1">
          {loading ? (
            <div className="py-3">
              <LoadingState label="正在读取 DeepSeek 账户余额…" />
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
                      <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2 text-xs">
                        <div>
                          <dt className="text-muted-foreground">赠送余额</dt>
                          <dd className="mt-1 font-mono">
                            {formatProviderBalanceAmount(
                              balanceInfo.granted_balance,
                              balanceInfo.currency,
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-muted-foreground">充值余额</dt>
                          <dd className="mt-1 font-mono">
                            {formatProviderBalanceAmount(
                              balanceInfo.topped_up_balance,
                              balanceInfo.currency,
                            )}
                          </dd>
                        </div>
                      </dl>
                    </section>
                  ))}
                </div>
              ) : (
                <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
                  DeepSeek 未返回可展示的币种余额。
                </p>
              )}
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

type QuickProvider = {
  id: string;
  name: string;
  description: string;
  baseUrl: string;
  brandId: string;
  iconUrl: string;
  protocol: "openai" | "anthropic";
};

const QUICK_PROVIDERS: QuickProvider[] = [
  { id: "openai", name: "OpenAI", description: "官方 Responses API", baseUrl: "https://api.openai.com/v1", brandId: "openai", iconUrl: openAiMark, protocol: "openai" },
  { id: "deepseek", name: "DeepSeek", description: "OpenAI 兼容接口", baseUrl: "https://api.deepseek.com", brandId: "deepseek", iconUrl: "https://cdn.simpleicons.org/deepseek/4D6BFE", protocol: "openai" },
  { id: "qwen", name: "通义千问", description: "阿里云 Model Studio", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", brandId: "qwen", iconUrl: "https://cdn.simpleicons.org/qwen", protocol: "openai" },
  { id: "gemini", name: "Google Gemini", description: "OpenAI 兼容接口", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/", brandId: "gemini", iconUrl: "https://cdn.simpleicons.org/googlegemini", protocol: "openai" },
  { id: "mimo", name: "Xiaomi MiMo", description: "OpenAI 兼容接口", baseUrl: "https://api.xiaomimimo.com/v1", brandId: "mimo", iconUrl: "https://cdn.simpleicons.org/xiaomi", protocol: "openai" },
  { id: "anthropic", name: "Anthropic", description: "Claude Messages API", baseUrl: "https://api.anthropic.com", brandId: "anthropic", iconUrl: "https://cdn.simpleicons.org/anthropic", protocol: "anthropic" },
  { id: "minimax", name: "MiniMax", description: "OpenAI 兼容接口", baseUrl: "https://api.minimaxi.com/v1", brandId: "minimax", iconUrl: "https://cdn.simpleicons.org/minimax", protocol: "openai" },
];

function providerQuickBrand(provider: Provider): QuickProvider | undefined {
  const brandId = String(provider.capabilities.brand_id ?? "").toLowerCase();
  const baseUrl = provider.base_url?.toLowerCase() ?? "";
  const name = provider.display_name.toLowerCase();
  return QUICK_PROVIDERS.find(
    (item) =>
      item.brandId === brandId ||
      (item.id !== "openai" &&
        (baseUrl.includes(item.id === "mimo" ? "xiaomimimo" : item.id) ||
          name.includes(item.id) ||
          name.includes(item.name.toLowerCase()))),
  );
}

function ProviderDialog({
  busy,
  catalog,
  catalogError,
  catalogPending,
  onCreate,
  secretStoreAvailable,
}: {
  busy: boolean;
  catalog: ProviderTypeCatalogItem[];
  catalogError?: string;
  catalogPending: boolean;
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
  const anthropicPreset = catalog.find(
    (item) => item.create_allowed && item.provider_type === "anthropic_messages",
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
    if (first) applyCatalogItem(first);
  }

  function selectType(nextType: string) {
    const next = catalog.find((item) => item.provider_type === nextType);
    if (!next) return;
    if (role === "model" && next.role === "model") {
      // Transport is an endpoint-level choice. Keep the selected vendor/model
      // preset, URL and display name when switching wire protocols.
      setType(next.provider_type);
      return;
    }
    applyCatalogItem(next);
  }

  function selectQuickProvider(kind: string) {
    if (kind === "compatible") {
      if (!compatiblePreset) return;
      applyCatalogItem(compatiblePreset);
      setQuickPreset(kind);
      return;
    }
    const preset = QUICK_PROVIDERS.find((item) => item.id === kind);
    if (!preset) return;
    if (preset.id === "deepseek") {
      if (!compatiblePreset) return;
      setQuickPreset(preset.id);
      setType(compatiblePreset.provider_type);
      setRole(compatiblePreset.role);
      setBaseUrl(preset.baseUrl);
      setName(preset.name);
      setDeepSeekPresetActive(true);
      return;
    }
    const next = preset.protocol === "anthropic" ? anthropicPreset : preset.id === "openai" ? openAiPreset : compatiblePreset;
    if (!next) return;
    setType(next.provider_type);
    setRole(next.role);
    setDeepSeekPresetActive(false);
    setBaseUrl(preset.baseUrl);
    setName(preset.name);
    setQuickPreset(preset.id);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
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
    const selectedQuickProvider = QUICK_PROVIDERS.find(
      (item) => item.id === quickPreset,
    );
    if (selectedQuickProvider) {
      capabilities.brand_id = selectedQuickProvider.brandId;
    }
    onCreate({
      display_name: name.trim(),
      provider_type: selected.provider_type,
      base_url: baseUrl.trim() || undefined,
      api_key: key || undefined,
      capabilities: Object.keys(capabilities).length ? capabilities : undefined,
    });
  }

  const isDeepSeekQuick = deepSeekPresetActive && quickPreset === "deepseek";

  return (
    <Dialog>
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
              <Label>快捷接入</Label>
              <div className="grid max-h-56 grid-cols-2 gap-2 overflow-y-auto pr-1 sm:grid-cols-3">
                {QUICK_PROVIDERS.map((preset) => (
                  <button
                    aria-pressed={quickPreset === preset.id}
                    className={`flex min-h-16 items-center gap-2 rounded-lg border p-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${quickPreset === preset.id ? "border-primary bg-primary/5" : "border-border bg-background hover:border-primary/45 hover:bg-muted/35"}`}
                    disabled={catalogPending || (preset.protocol === "anthropic" ? !anthropicPreset : preset.id === "openai" ? !openAiPreset : !compatiblePreset)}
                    key={preset.id}
                    onClick={() => selectQuickProvider(preset.id)}
                    type="button"
                  >
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-white p-1 shadow-sm ring-1 ring-black/5">
                      <img alt={`${preset.name} 图标`} className="size-6 object-contain" src={preset.iconUrl} />
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
                      OpenAI-compatible / 中转站
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
                  {selected.documentation_url || selected.key_management_url ? (
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
                      {selected.key_management_url ? (
                        <a
                          className="text-primary underline-offset-4 hover:underline"
                          href={selected.key_management_url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          获取 API Key
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
            </div>
            <div className="space-y-2">
              <Label htmlFor="provider-key">
                API Key{selected?.requires_secret ? "（启用必填）" : "（可选）"}
              </Label>
              <Input
                autoComplete="off"
                id="provider-key"
                onChange={(event) => setKey(event.target.value)}
                placeholder="仅提交一次"
                type="password"
                value={key}
              />
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
                busy || catalogPending || Boolean(catalogError) || !name.trim() || !selected
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
  "disabled",
  "model_native",
  "external",
  "local",
  "auto",
];

function emptyModelCapabilities(): ProviderModelCapabilities {
  return {
    reasoning_efforts: [],
    thinking_mapping: {},
    default_thinking_mode: "off",
    reasoning_parameter: "reasoning_effort",
    hosted_web_search: false,
    supports_image_input: false,
    image_input_mode: "auto",
    default_search_route: "disabled",
    capability_source: "user_declared",
    context_window_tokens: 256_000,
    context_limit_tokens: 256_000,
    max_output_tokens: 4_096,
  };
}

function searchRouteLabel(route: SearchRoute) {
  switch (route) {
    case "disabled":
      return "禁用联网";
    case "model_native":
      return "模型原生联网";
    case "external":
      return "外部 Search Provider";
    case "local":
      return "本地 Search Provider";
    case "auto":
      return "按已授权链路自动选择";
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
  modelId,
  models,
  onClose,
  onSaved,
  provider,
}: {
  modelId: string;
  models: ProviderModelsResponse;
  onClose: () => void;
  onSaved: (snapshot: ProviderModelCapabilityView) => void;
  provider: Provider;
}) {
  const queryClient = useQueryClient();
  const [selectedModelId, setSelectedModelId] = useState(modelId);
  const [editScope, setEditScope] = useState<"group" | "model">("group");
  const [modelStates, setModelStates] = useState<Record<string, boolean>>(
    Object.fromEntries(models.models.map((model) => [model.id, model.enabled !== false])),
  );
  const capabilitiesQuery = useQuery({
    queryKey: ["provider-model-capabilities", provider.id, selectedModelId],
    queryFn: () => getProviderModelCapabilities(provider.id, selectedModelId),
    retry: false,
    enabled: editScope === "model",
  });
  const snapshotMissing =
    capabilitiesQuery.error instanceof ApiError &&
    capabilitiesQuery.error.code === "model_capabilities_not_found";
  const [capabilities, setCapabilities] =
    useState<ProviderModelCapabilities>(emptyModelCapabilities);

  useEffect(() => {
    if (editScope === "group") {
      const defaults = provider.capabilities.model_defaults;
      setCapabilities({
        ...emptyModelCapabilities(),
        ...(defaults && typeof defaults === "object" && !Array.isArray(defaults)
          ? defaults
          : {}),
      } as ProviderModelCapabilities);
    } else if (capabilitiesQuery.data) {
      const next = capabilitiesQuery.data.capabilities;
      setCapabilities({
        ...emptyModelCapabilities(),
        ...next,
        image_input_mode: next.image_input_mode ?? "auto",
      });
    } else if (snapshotMissing) {
      setCapabilities(emptyModelCapabilities());
    }
  }, [capabilitiesQuery.data, editScope, provider.capabilities.model_defaults, snapshotMissing]);

  const save = useMutation({
    mutationFn: (payload: ProviderModelCapabilities) =>
      editScope === "group"
        ? updateProviderModelGroupCapabilities(provider.id, payload)
        : updateProviderModelCapabilities(provider.id, selectedModelId, payload),
    onSuccess: (snapshot) => {
      queryClient.setQueryData(
        ["provider-model-capabilities", provider.id, selectedModelId],
        snapshot,
      );
      onSaved(snapshot);
      toast.success("模型能力快照已保存");
      onClose();
    },
    onError: (error) => toast.error(error.message),
  });
  const updateModelState = useMutation({
    mutationFn: () => updateProviderModelStates(provider.id, modelStates),
    onSuccess: (result) => {
      setModelStates(result.states);
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      toast.success("模型列表已更新");
    },
    onError: (error) => toast.error(error.message),
  });

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
    save.mutate(capabilities);
  }

  const terminalError =
    editScope === "model" && capabilitiesQuery.isError && !snapshotMissing;
  return (
    <Dialog onOpenChange={(open) => !open && !save.isPending && onClose()} open>
      <DialogContent className="max-h-[min(88vh,860px)] overflow-y-auto sm:max-w-3xl">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>模型能力快照</DialogTitle>
          </DialogHeader>
          {editScope === "model" && capabilitiesQuery.isPending ? (
            <div className="py-10">
              <LoadingState label="正在读取已保存的能力快照…" />
            </div>
          ) : terminalError ? (
            <div className="py-5">
              <ErrorState message={capabilitiesQuery.error.message} />
            </div>
          ) : (
            <div className="space-y-5 py-5">
              <section className="space-y-3 rounded-xl border p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold">模型列表开关</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      仅启用选中的模型；可以一键全选或全部禁用。
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      onClick={() =>
                        setModelStates(
                          Object.fromEntries(models.models.map((model) => [model.id, true])),
                        )
                      }
                      size="xs"
                      type="button"
                      variant="outline"
                    >
                      全选
                    </Button>
                    <Button
                      onClick={() =>
                        setModelStates(
                          Object.fromEntries(models.models.map((model) => [model.id, false])),
                        )
                      }
                      size="xs"
                      type="button"
                      variant="outline"
                    >
                      全部禁用
                    </Button>
                    <Button
                      disabled={updateModelState.isPending}
                      onClick={() => updateModelState.mutate()}
                      size="xs"
                      type="button"
                    >
                      应用开关
                    </Button>
                  </div>
                </div>
                <div className="max-h-48 divide-y overflow-y-auto rounded-lg border">
                  {models.models.map((model) => (
                    <label
                      className="flex cursor-pointer items-center gap-3 px-3 py-2 text-xs"
                      key={model.id}
                    >
                      <Checkbox
                        checked={modelStates[model.id] === true}
                        onCheckedChange={(checked) =>
                          setModelStates((current) => ({
                            ...current,
                            [model.id]: checked === true,
                          }))
                        }
                      />
                      <span className="min-w-0 flex-1 truncate font-mono">{model.id}</span>
                      {provider.capabilities.default_model === model.id ? (
                        <span className="text-muted-foreground">默认</span>
                      ) : null}
                    </label>
                  ))}
                </div>
                {updateModelState.isError ? (
                  <p className="text-sm text-destructive" role="alert">
                    {updateModelState.error.message}
                  </p>
                ) : null}
              </section>
              <section className="space-y-3 rounded-xl border p-4">
                <div>
                  <p className="text-sm font-semibold">配置作用范围</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    群体配置作用于该 Provider 的全部模型；单模型配置覆盖群体配置。
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Select
                    onValueChange={(value) => setEditScope(value as "group" | "model")}
                    value={editScope}
                  >
                    <SelectTrigger aria-label="配置作用范围">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="group">全部模型（群体配置）</SelectItem>
                      <SelectItem value="model">单个模型（覆盖群体）</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select
                    disabled={editScope !== "model"}
                    onValueChange={setSelectedModelId}
                    value={selectedModelId}
                  >
                    <SelectTrigger aria-label="选择单独配置的模型">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="max-h-72">
                      {models.models.map((model) => (
                        <SelectItem key={model.id} value={model.id}>
                          {model.id}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </section>
              <section className="space-y-3 rounded-xl border p-4">
                <div>
                  <p className="text-sm font-semibold">上下文上限</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    未知模型默认 256K。智能体达到有效上限的 1/3 时压缩；极速/思考达到 80% 时压缩。
                  </p>
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                  <Label>
                    模型总上下文
                    <Input
                      className="mt-2"
                      min={8000}
                      onChange={(event) =>
                        setCapabilities((current) => ({
                          ...current,
                          context_window_tokens: Number(event.target.value),
                          context_limit_tokens: Math.min(
                            current.context_limit_tokens,
                            Number(event.target.value),
                          ),
                        }))
                      }
                      type="number"
                      value={capabilities.context_window_tokens}
                    />
                  </Label>
                  <Label>
                    使用上限
                    <Input
                      className="mt-2"
                      max={capabilities.context_window_tokens}
                      min={8000}
                      onChange={(event) =>
                        setCapabilities((current) => ({
                          ...current,
                          context_limit_tokens: Number(event.target.value),
                        }))
                      }
                      type="number"
                      value={capabilities.context_limit_tokens}
                    />
                  </Label>
                  <Label>
                    最大输出
                    <Input
                      className="mt-2"
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
              </section>
              <section className="space-y-3 rounded-xl border p-4">
                <div>
                  <p className="text-sm font-semibold">推理强度与映射</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    选择 LearnGraph 可以请求的级别，并映射为该 Provider 实际接受的参数值。
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  {reasoningModes.map((effort) => {
                    const inputId = `capability-${provider.id}-${selectedModelId}-${effort}`;
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
                                [effort]: event.target.value || null,
                              },
                            }))
                          }
                          placeholder={`实际参数值，默认 ${effort}`}
                          value={capabilities.thinking_mapping[effort] ?? ""}
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
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </section>
              <section className="space-y-4 rounded-xl border p-4">
                <div>
                  <p className="text-sm font-semibold">联网能力</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    设置模型是否可使用原生网页搜索，以及默认的联网方式。
                  </p>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
                  <Label htmlFor="hosted-web-search">支持托管网页搜索</Label>
                  <Switch
                    checked={capabilities.hosted_web_search}
                    id="hosted-web-search"
                    onCheckedChange={(checked) =>
                      setCapabilities((current) => ({
                        ...current,
                        hosted_web_search: checked,
                        default_search_route:
                          !checked && current.default_search_route === "model_native"
                            ? "disabled"
                            : current.default_search_route,
                      }))
                    }
                  />
                </div>
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
                  <Label htmlFor="supports-image-input">支持图片输入</Label>
                  <Switch
                    checked={capabilities.supports_image_input}
                    id="supports-image-input"
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
              {save.isError ? (
                <p className="text-sm text-destructive" role="alert">
                  {save.error.message}
                </p>
              ) : null}
            </div>
          )}
          <DialogFooter>
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
                (editScope === "model" && capabilitiesQuery.isPending) ||
                terminalError ||
                save.isPending
              }
              type="submit"
            >
              {save.isPending
                ? "保存中…"
                : editScope === "group"
                  ? "保存群体配置"
                  : "保存单模型覆盖"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function BillingConfigDialog({
  busy,
  onBudget,
  onExchangeRate,
  onPrice,
}: {
  busy: boolean;
  onBudget: (payload: BudgetPolicyCreate) => void;
  onExchangeRate: (rate: number) => void;
  onPrice: (payload: PriceVersionCreate) => void;
}) {
  const [providerId, setProviderId] = useState("*");
  const [modelId, setModelId] = useState("*");
  const [feature, setFeature] = useState("*");
  const [priceCurrency, setPriceCurrency] = useState<"USD" | "CNY">("USD");
  const [inputPrice, setInputPrice] = useState("0");
  const [cachedInputPrice, setCachedInputPrice] = useState("");
  const [outputPrice, setOutputPrice] = useState("0");
  const [exchangeRate, setExchangeRate] = useState("6.7704");
  const [budgetName, setBudgetName] = useState("工作区月度预算");
  const [softLimit, setSoftLimit] = useState("");
  const [hardLimit, setHardLimit] = useState("");
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm">
          <CircleDollarSign className="size-4" />
          配置计费
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>价格、汇率与预算</DialogTitle>
          <DialogDescription>
            每次保存都会写入服务端版本或策略；历史 UsageEvent 仍保留原快照。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-5 py-3 md:grid-cols-2">
          <section className="space-y-3 rounded-xl border p-4">
            <p className="text-sm font-semibold">模型价格版本</p>
            <Label htmlFor="billing-provider">Provider ID</Label>
            <Input
              id="billing-provider"
              onChange={(event) => setProviderId(event.currentTarget.value)}
              value={providerId}
            />
            <Label htmlFor="billing-model">Model ID</Label>
            <Input
              id="billing-model"
              onChange={(event) => setModelId(event.currentTarget.value)}
              value={modelId}
            />
            <Label htmlFor="billing-feature">Feature</Label>
            <Input
              id="billing-feature"
              onChange={(event) => setFeature(event.currentTarget.value)}
              value={feature}
            />
            <div className="space-y-2">
              <Label>定价币种</Label>
              <Select
                onValueChange={(value) => setPriceCurrency(value as "USD" | "CNY")}
                value={priceCurrency}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="USD">USD（美元原生定价）</SelectItem>
                  <SelectItem value="CNY">CNY（人民币原生定价）</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <Label htmlFor="billing-input">输入 {priceCurrency}/1M</Label>
                <Input
                  id="billing-input"
                  min="0"
                  onChange={(event) => setInputPrice(event.currentTarget.value)}
                  type="number"
                  value={inputPrice}
                />
              </div>
              <div>
                <Label htmlFor="billing-cached-input">缓存输入 {priceCurrency}/1M</Label>
                <Input
                  id="billing-cached-input"
                  min="0"
                  onChange={(event) => setCachedInputPrice(event.currentTarget.value)}
                  placeholder="同普通输入"
                  type="number"
                  value={cachedInputPrice}
                />
              </div>
              <div>
                <Label htmlFor="billing-output">输出 {priceCurrency}/1M</Label>
                <Input
                  id="billing-output"
                  min="0"
                  onChange={(event) =>
                    setOutputPrice(event.currentTarget.value)
                  }
                  type="number"
                  value={outputPrice}
                />
              </div>
            </div>
            <Button
              disabled={
                busy || !providerId.trim() || !modelId.trim() || !feature.trim()
              }
              onClick={() =>
                onPrice({
                  provider_id: providerId.trim(),
                  model_id: modelId.trim(),
                  feature: feature.trim(),
                  currency: priceCurrency,
                  ...(priceCurrency === "CNY"
                    ? {
                        input_cny_per_million: Number(inputPrice) || 0,
                        cached_input_cny_per_million:
                          cachedInputPrice === "" ? null : Number(cachedInputPrice),
                        output_cny_per_million: Number(outputPrice) || 0,
                      }
                    : {
                        input_usd_per_million: Number(inputPrice) || 0,
                        cached_input_usd_per_million:
                          cachedInputPrice === "" ? null : Number(cachedInputPrice),
                        output_usd_per_million: Number(outputPrice) || 0,
                      }),
                  source: "workspace_manual",
                })
              }
              size="sm"
              variant="outline"
            >
              保存价格版本
            </Button>
          </section>
          <section className="space-y-3 rounded-xl border p-4">
            <p className="text-sm font-semibold">USD/CNY 汇率版本</p>
            <Label htmlFor="billing-rate">1 USD = CNY</Label>
            <Input
              id="billing-rate"
              min="0.0001"
              onChange={(event) => setExchangeRate(event.currentTarget.value)}
              step="0.0001"
              type="number"
              value={exchangeRate}
            />
            <Button
              disabled={busy || Number(exchangeRate) <= 0}
              onClick={() => onExchangeRate(Number(exchangeRate))}
              size="sm"
              variant="outline"
            >
              保存汇率版本
            </Button>
            <div className="border-t pt-3">
              <p className="text-sm font-semibold">预算策略</p>
            </div>
            <Label htmlFor="budget-name">名称</Label>
            <Input
              id="budget-name"
              onChange={(event) => setBudgetName(event.currentTarget.value)}
              value={budgetName}
            />
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label htmlFor="budget-soft">软告警 CNY</Label>
                <Input
                  id="budget-soft"
                  min="0"
                  onChange={(event) => setSoftLimit(event.currentTarget.value)}
                  type="number"
                  value={softLimit}
                />
              </div>
              <div>
                <Label htmlFor="budget-hard">硬阻断 CNY</Label>
                <Input
                  id="budget-hard"
                  min="0"
                  onChange={(event) => setHardLimit(event.currentTarget.value)}
                  type="number"
                  value={hardLimit}
                />
              </div>
            </div>
            <Button
              disabled={
                busy ||
                !budgetName.trim() ||
                (!softLimit && !hardLimit) ||
                (Boolean(softLimit) &&
                  Boolean(hardLimit) &&
                  Number(softLimit) > Number(hardLimit))
              }
              onClick={() =>
                onBudget({
                  name: budgetName.trim(),
                  soft_limit_cny: softLimit ? Number(softLimit) : null,
                  hard_limit_cny: hardLimit ? Number(hardLimit) : null,
                  period: "calendar_month_utc",
                })
              }
              size="sm"
            >
              保存预算策略
            </Button>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RetireVersionButton({
  actionLabel,
  busy,
  description,
  onRetire,
}: {
  actionLabel: string;
  busy: boolean;
  description: string;
  onRetire: () => void;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button disabled={busy} size="xs" variant="outline">
          {actionLabel}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogMedia className="bg-amber-500/10 text-amber-700">
            <AlertTriangle />
          </AlertDialogMedia>
          <AlertDialogTitle>{actionLabel}？</AlertDialogTitle>
          <AlertDialogDescription>
            {description} 历史 UsageEvent 会继续引用当前版本的价格或汇率快照；此操作不会删除历史账本。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction disabled={busy} onClick={onRetire}>
            {busy ? "提交中…" : "确认退役"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function parseOptionalLimit(value: string): number | null | undefined {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

function EditBudgetPolicyDialog({
  busy,
  onUpdate,
  policy,
}: {
  busy: boolean;
  onUpdate: (payload: BudgetPolicyUpdate) => Promise<void>;
  policy: BudgetPolicy;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(policy.name);
  const [softLimit, setSoftLimit] = useState(
    policy.soft_limit_cny === null ? "" : String(policy.soft_limit_cny),
  );
  const [hardLimit, setHardLimit] = useState(
    policy.hard_limit_cny === null ? "" : String(policy.hard_limit_cny),
  );
  const [enabled, setEnabled] = useState(policy.enabled);
  const soft = parseOptionalLimit(softLimit);
  const hard = parseOptionalLimit(hardLimit);
  const limitsValid =
    soft !== undefined &&
    hard !== undefined &&
    (soft !== null || hard !== null) &&
    !(soft !== null && hard !== null && soft > hard);

  function resetFromPolicy() {
    setName(policy.name);
    setSoftLimit(policy.soft_limit_cny === null ? "" : String(policy.soft_limit_cny));
    setHardLimit(policy.hard_limit_cny === null ? "" : String(policy.hard_limit_cny));
    setEnabled(policy.enabled);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!limitsValid || soft === undefined || hard === undefined) return;
    try {
      await onUpdate({
        name: name.trim(),
        soft_limit_cny: soft,
        hard_limit_cny: hard,
        enabled,
      });
      setOpen(false);
    } catch {
      // The page-level mutation presents the server error without closing the form.
    }
  }

  return (
    <Dialog
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen) resetFromPolicy();
      }}
      open={open}
    >
      <DialogTrigger asChild>
        <Button disabled={busy} size="xs" variant="outline">
          <Pencil className="size-3" />编辑
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>编辑预算策略</DialogTitle>
            <DialogDescription>
              范围和周期保持不可变；若要改变匹配范围，请删除后显式创建新策略。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
            <div className="rounded-xl border bg-muted/25 p-3 text-xs text-muted-foreground">
              {policy.provider_id} / {policy.model_id} / {policy.feature} · {policy.period}
            </div>
            <div className="space-y-2">
              <Label htmlFor={`budget-policy-name-${policy.id}`}>名称</Label>
              <Input
                id={`budget-policy-name-${policy.id}`}
                maxLength={160}
                onChange={(event) => setName(event.currentTarget.value)}
                value={name}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor={`budget-policy-soft-${policy.id}`}>软告警 CNY</Label>
                <Input
                  id={`budget-policy-soft-${policy.id}`}
                  min="0"
                  onChange={(event) => setSoftLimit(event.currentTarget.value)}
                  type="number"
                  value={softLimit}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`budget-policy-hard-${policy.id}`}>硬阻断 CNY</Label>
                <Input
                  id={`budget-policy-hard-${policy.id}`}
                  min="0"
                  onChange={(event) => setHardLimit(event.currentTarget.value)}
                  type="number"
                  value={hardLimit}
                />
              </div>
            </div>
            <div className="flex items-center justify-between rounded-xl border p-3">
              <div>
                <Label htmlFor={`budget-policy-enabled-${policy.id}`}>启用策略</Label>
                <p className="mt-1 text-xs text-muted-foreground">关闭后保留历史策略与告警，但不再参与后续预算匹配。</p>
              </div>
              <Switch
                checked={enabled}
                id={`budget-policy-enabled-${policy.id}`}
                onCheckedChange={setEnabled}
              />
            </div>
            {!limitsValid ? <p className="text-xs text-destructive">至少填写一个非负门槛，且软告警不能高于硬阻断。</p> : null}
          </div>
          <DialogFooter>
            <Button disabled={busy || !name.trim() || !limitsValid} type="submit">
              {busy ? "保存中…" : "保存策略"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteBudgetPolicyButton({
  busy,
  onDelete,
  policy,
}: {
  busy: boolean;
  onDelete: () => void;
  policy: BudgetPolicy;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button aria-label={`删除预算策略 ${policy.name}`} disabled={busy} size="icon-xs" variant="ghost">
          <Trash2 className="size-3.5 text-destructive" />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogMedia className="bg-destructive/10 text-destructive"><Trash2 /></AlertDialogMedia>
          <AlertDialogTitle>删除“{policy.name}”？</AlertDialogTitle>
          <AlertDialogDescription>
            该策略及其预算告警将被删除，之后新的调用不会再匹配这组范围。UsageEvent、价格版本和汇率版本不会被删除。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction disabled={busy} onClick={onDelete} variant="destructive">确认删除策略</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function UsagePage() {
  const queryClient = useQueryClient();
  const usageDisplaySetting = useQuery({
    queryKey: ["settings"],
    queryFn: listSettings,
  });
  const summary = useQuery({
    queryKey: ["usage-summary"],
    queryFn: getUsageSummary,
  });
  const events = useQuery({
    queryKey: ["usage-events"],
    queryFn: listUsageEvents,
  });
  const prices = useQuery({
    queryKey: ["usage-prices"],
    queryFn: listPriceVersions,
  });
  const priceCatalog = useQuery({
    queryKey: ["usage-price-catalog"],
    queryFn: listPriceCatalog,
  });
  const exchangeRates = useQuery({
    queryKey: ["usage-exchange-rates"],
    queryFn: listExchangeRates,
  });
  const policies = useQuery({
    queryKey: ["usage-budget-policies"],
    queryFn: listBudgetPolicies,
  });
  const budgetStatuses = useQuery({
    queryKey: ["usage-budget-status"],
    queryFn: listBudgetStatuses,
  });
  const alerts = useQuery({
    queryKey: ["usage-budget-alerts"],
    queryFn: listBudgetAlerts,
  });
  const refreshBilling = () => {
    void queryClient.invalidateQueries({ queryKey: ["usage-prices"] });
    void queryClient.invalidateQueries({ queryKey: ["usage-exchange-rates"] });
    void queryClient.invalidateQueries({ queryKey: ["usage-budget-policies"] });
    void queryClient.invalidateQueries({ queryKey: ["usage-budget-status"] });
    void queryClient.invalidateQueries({ queryKey: ["usage-budget-alerts"] });
  };
  const createPrice = useMutation({
    mutationFn: createPriceVersion,
    onSuccess: () => {
      toast.success("价格版本已保存");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const createRate = useMutation({
    mutationFn: createExchangeRate,
    onSuccess: () => {
      toast.success("汇率版本已保存");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const createBudget = useMutation({
    mutationFn: createBudgetPolicy,
    onSuccess: () => {
      toast.success("预算策略已保存");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const retirePrice = useMutation({
    mutationFn: retirePriceVersion,
    onSuccess: () => {
      toast.success("价格版本已退役；历史用量快照保持不变");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const retireRate = useMutation({
    mutationFn: retireExchangeRate,
    onSuccess: () => {
      toast.success("汇率版本已退役；历史用量快照保持不变");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const updateBudget = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: BudgetPolicyUpdate;
    }) => updateBudgetPolicy(id, payload),
    onSuccess: () => {
      toast.success("预算策略已更新");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const removeBudget = useMutation({
    mutationFn: deleteBudgetPolicy,
    onSuccess: () => {
      toast.success("预算策略及其告警已删除");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const acknowledge = useMutation({
    mutationFn: acknowledgeBudgetAlert,
    onSuccess: () => {
      toast.success("预算告警已确认");
      refreshBilling();
    },
    onError: (error) => toast.error(error.message),
  });
  const clearEvents = useMutation({
    mutationFn: clearUsageEvents,
    onSuccess: (result) => {
      toast.success(
        result.deleted_count
          ? `已清空 ${result.deleted_count} 条用量事件`
          : "当前没有可清空的用量事件",
      );
      void queryClient.invalidateQueries({ queryKey: ["usage-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["usage-events"] });
      void queryClient.invalidateQueries({ queryKey: ["usage-budget-status"] });
      void queryClient.invalidateQueries({ queryKey: ["usage-budget-alerts"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const updateUsageDisplayCurrency = useMutation({
    mutationFn: (currency: "USD" | "CNY") =>
      updateSetting("usage.display_currency", currency),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const allQueries = [
    summary,
    events,
    prices,
    priceCatalog,
    exchangeRates,
    policies,
    budgetStatuses,
    alerts,
    usageDisplaySetting,
  ];
  if (allQueries.some((query) => query.isPending))
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  const queryError = allQueries.find((query) => query.isError)?.error;
  if (queryError)
    return (
      <PageFrame>
        <ErrorState message={queryError.message} />
      </PageFrame>
    );
  const usageEvents = events.data ?? [];
  const configuredDisplayCurrency = usageDisplaySetting.data?.find(
    (setting) => setting.key === "usage.display_currency",
  )?.value;
  const displayCurrency: "USD" | "CNY" =
    configuredDisplayCurrency === "USD" ? "USD" : "CNY";
  const usageSummary = summary.data!;
  const priceVersions = prices.data ?? [];
  const catalogItems = priceCatalog.data ?? [];
  const rateVersions = exchangeRates.data ?? [];
  const policyItems = policies.data ?? [];
  const statusItems = budgetStatuses.data ?? [];
  const alertItems = alerts.data ?? [];
  const billingBusy =
    createPrice.isPending ||
    createRate.isPending ||
    createBudget.isPending ||
    retirePrice.isPending ||
    retireRate.isPending ||
    updateBudget.isPending ||
    removeBudget.isPending;
  const chart = usageEvents.map((event) => ({
    day: new Date(event.created_at).toLocaleString(),
    cost: displayCurrency === "CNY" ? event.cost_cny : event.cost_usd,
    tokens: event.input_tokens + event.output_tokens,
  }));
  const totalTokens = usageEvents.reduce(
    (total, event) => total + event.input_tokens + event.output_tokens,
    0,
  );
  const usageByFeature = Object.values(
    usageEvents.reduce<
      Record<string, { feature: string; tokens: number; cost: number }>
    >((groups, event) => {
      const current = groups[event.feature] ?? {
        feature: event.feature,
        tokens: 0,
        cost: 0,
      };
      current.tokens += event.input_tokens + event.output_tokens;
      current.cost += displayCurrency === "CNY" ? event.cost_cny : event.cost_usd;
      groups[event.feature] = current;
      return groups;
    }, {}),
  ).sort((left, right) => right.tokens - left.tokens);
  function exportCsv() {
    const escape = (value: string | number) =>
      `"${String(value).replaceAll('"', '""')}"`;
    const rows = [
      [
        "created_at",
        "provider_id",
        "model_id",
        "feature",
        "input_tokens",
        "output_tokens",
        "attempt",
        "cost_usd",
        "cost_cny",
        "cost_status",
        "price_version_id",
        "exchange_rate_version_id",
        "usd_cny_rate",
        "latency_ms",
      ],
      ...usageEvents.map((event) => [
        event.created_at,
        event.provider_id,
        event.model_id,
        event.feature,
        event.input_tokens,
        event.output_tokens,
        event.attempt,
        event.cost_usd,
        event.cost_cny,
        event.cost_status,
        event.price_version_id ?? "",
        event.exchange_rate_version_id ?? "",
        event.usd_cny_rate,
        event.latency_ms,
      ]),
    ];
    const blob = new Blob(
      [`\uFEFF${rows.map((row) => row.map(escape).join(",")).join("\n")}`],
      { type: "text/csv;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `learngraph-usage-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  return (
    <PageFrame>
      <PageIntro
        actions={
          <div className="flex gap-2">
            <Select
              disabled={updateUsageDisplayCurrency.isPending}
              onValueChange={(value) =>
                updateUsageDisplayCurrency.mutate(value as "USD" | "CNY")
              }
              value={displayCurrency}
            >
              <SelectTrigger aria-label="费用显示币种" className="h-9 w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="CNY">显示 CNY</SelectItem>
                <SelectItem value="USD">显示 USD</SelectItem>
              </SelectContent>
            </Select>
            <BillingConfigDialog
              busy={billingBusy}
              onBudget={(payload) => createBudget.mutate(payload)}
              onExchangeRate={(rate) => createRate.mutate(rate)}
              onPrice={(payload) => createPrice.mutate(payload)}
            />
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  disabled={!usageEvents.length || clearEvents.isPending}
                  size="sm"
                  variant="outline"
                >
                  <Trash2 className="size-4" />
                  清空用量
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogMedia className="bg-destructive/10 text-destructive">
                    <Trash2 />
                  </AlertDialogMedia>
                  <AlertDialogTitle>清空当前工作区的用量计费记录？</AlertDialogTitle>
                  <AlertDialogDescription>
                    将永久删除当前工作区全部 {usageEvents.length} 条 UsageEvent。价格版本、汇率版本和预算策略会保留；此操作不可撤销。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                {clearEvents.isError ? (
                  <p className="text-sm text-destructive" role="alert">
                    {clearEvents.error.message}
                  </p>
                ) : null}
                <AlertDialogFooter>
                  <AlertDialogCancel disabled={clearEvents.isPending}>取消</AlertDialogCancel>
                  <AlertDialogAction
                    disabled={clearEvents.isPending}
                    onClick={(event) => {
                      event.preventDefault();
                      clearEvents.mutate();
                    }}
                    variant="destructive"
                  >
                    {clearEvents.isPending ? "正在清空…" : "确认清空"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button
              disabled={!usageEvents.length}
              onClick={exportCsv}
              size="sm"
              variant="outline"
            >
              <Download className="size-4" />
              导出 CSV
            </Button>
          </div>
        }
        description="每次实际 HTTP Attempt 追加一条用量事件，失败重试也单独计入；价格、汇率与预算均绑定持久化快照。"
        eyebrow="Usage ledger"
        title="用量计费与预算"
      />
      <MetricStrip
        items={[
          {
            label: "输入 Token",
            value: usageSummary.input_tokens.toLocaleString(),
            hint: "当前工作区",
          },
          {
            label: "输出 Token",
            value: usageSummary.output_tokens.toLocaleString(),
            hint: "当前工作区",
            tone: "info",
          },
          {
            label: "实际尝试",
            value: usageSummary.attempts,
            hint: `${usageSummary.unpriced_events} 条未定价`,
            tone: "warning",
          },
          {
            label: `费用 ${displayCurrency}`,
            value:
              displayCurrency === "CNY"
                ? `¥${usageSummary.cost_cny.toFixed(4)}`
                : `$${usageSummary.cost_usd.toFixed(4)}`,
            hint: usageSummary.remote_usage_recorded
              ? displayCurrency === "CNY"
                ? `$${usageSummary.cost_usd.toFixed(4)} · 远程用量`
                : `¥${usageSummary.cost_cny.toFixed(4)} · 远程用量`
              : "暂无远程用量",
            tone: "positive",
          },
        ]}
      />
      <div className="grid gap-5 lg:grid-cols-[1.1fr_.9fr]">
        <Surface className="p-5">
          <SectionHeading
            description="仅展示当前工作区持久化的真实用量事件"
            title="费用趋势"
          />
          {chart.length ? (
            <div className="mt-4 h-80">
              <ResponsiveContainer height="100%" width="100%">
                <AreaChart data={chart}>
                  <defs>
                    <linearGradient id="usage-area" x1="0" x2="0" y1="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor="var(--primary)"
                        stopOpacity={0.25}
                      />
                      <stop
                        offset="95%"
                        stopColor="var(--primary)"
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    stroke="var(--border)"
                    strokeDasharray="3 3"
                    vertical={false}
                  />
                  <XAxis
                    axisLine={false}
                    dataKey="day"
                    fontSize={11}
                    tickLine={false}
                  />
                  <YAxis
                    axisLine={false}
                    fontSize={11}
                    tickLine={false}
                    width={38}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--card)",
                      borderColor: "var(--border)",
                      borderRadius: 10,
                    }}
                  />
                  <Area
                    dataKey="cost"
                    fill="url(#usage-area)"
                    stroke="var(--primary)"
                    strokeWidth={2.5}
                    type="monotone"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="grid h-80 place-items-center text-sm text-muted-foreground">
              暂无真实用量事件
            </p>
          )}
        </Surface>
        <Surface className="p-5">
          <SectionHeading
            description="按真实 token 聚合"
            title="功能用量明细"
          />
          <div className="mt-5 space-y-5">
            {usageByFeature.map((item) => {
              const percent = totalTokens
                ? Math.round((item.tokens / totalTokens) * 100)
                : 0;
              return (
                <div key={item.feature}>
                  <div className="flex justify-between text-sm">
                    <span className="font-medium">{item.feature}</span>
                    <span className="font-mono text-xs text-primary">
                      {item.tokens.toLocaleString()} tokens · {displayCurrency === "CNY" ? "¥" : "$"}
                      {item.cost.toFixed(4)}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <Progress value={percent} />
                    <span className="w-8 text-right text-[10px] text-muted-foreground">
                      {percent}%
                    </span>
                  </div>
                </div>
              );
            })}
            {!usageByFeature.length ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                暂无可聚合事件
              </p>
            ) : null}
          </div>
          <div className="mt-6 rounded-xl border bg-muted/25 p-3 text-xs leading-5 text-muted-foreground">
            <p className="flex items-center gap-2 font-semibold text-foreground">
              <CircleDollarSign className="size-4 text-primary" />
              预算状态
            </p>
            {statusItems.length ? (
              <div className="mt-3 space-y-3">
                {statusItems.map((status) => (
                  <div
                    className="rounded-lg border bg-background/80 p-3"
                    key={status.policy_id}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-foreground">
                        {status.name}
                      </span>
                      <StatePill
                        label={
                          status.hard_exceeded
                            ? "已阻断"
                            : status.soft_exceeded
                              ? "已预警"
                              : "正常"
                        }
                        status={
                          status.hard_exceeded
                            ? "failed"
                            : status.soft_exceeded
                              ? "warning"
                              : "approved"
                        }
                      />
                    </div>
                    <p className="mt-1 font-mono">
                      已用 ¥{status.spent_cny.toFixed(4)} · 软上限
                      {status.soft_limit_cny === null
                        ? "未设置"
                        : ` ¥${status.soft_limit_cny.toFixed(2)}`}{" "}
                      · 硬上限
                      {status.hard_limit_cny === null
                        ? "未设置"
                        : ` ¥${status.hard_limit_cny.toFixed(2)}`}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-1">
                尚未配置预算策略；真实用量仍会记录，但不会执行预算阻断。
              </p>
            )}
          </div>
        </Surface>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Surface className="p-5">
          <SectionHeading
            description="事件创建时固化价格与汇率版本，后续调整不会改写历史账本。"
            title="计费版本"
          />
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl border p-4">
              <p className="text-xs font-semibold text-muted-foreground">
                价格版本
              </p>
              <p className="mt-2 text-2xl font-semibold">
                {priceVersions.length}
              </p>
              <div className="mt-3 max-h-64 space-y-3 overflow-auto pr-1">
                {priceVersions.map((price) => (
                  <div className="rounded-lg border bg-muted/15 p-3 text-xs" key={price.id}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="min-w-0 break-all font-medium">
                        {price.provider_id} / {price.model_id} / {price.feature}
                      </p>
                      {price.retired_at ? (
                        <StatePill label="已退役" status="archived" />
                      ) : (
                        <RetireVersionButton
                          actionLabel={`退役价格 v${price.version}`}
                          busy={retirePrice.isPending}
                          description={`这会停止价格版本 v${price.version} 用于未来报价。`}
                          onRetire={() => retirePrice.mutate(price.id)}
                        />
                      )}
                    </div>
                    {price.conditions.pricing_currency === "CNY" ? (
                      <p className="mt-1 font-mono text-muted-foreground">
                        in ¥{String(price.conditions.input_cny_per_million ?? 0)}/M · out ¥
                        {String(price.conditions.output_cny_per_million ?? 0)}/M · 人民币原生定价
                      </p>
                    ) : (
                      <p className="mt-1 font-mono text-muted-foreground">
                        in ${price.input_usd_per_million}/M · out $
                        {price.output_usd_per_million}/M · call $
                        {price.fixed_usd_per_call}
                      </p>
                    )}
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      v{price.version} · 生效 {new Date(price.effective_at).toLocaleString()}
                      {price.retired_at ? ` · 退役 ${new Date(price.retired_at).toLocaleString()}` : ""}
                    </p>
                  </div>
                ))}
                {!priceVersions.length ? (
                  <p className="text-xs text-muted-foreground">尚未配置</p>
                ) : null}
              </div>
            </div>
            <div className="rounded-xl border p-4">
              <p className="text-xs font-semibold text-muted-foreground">
                USD/CNY 汇率版本
              </p>
              <p className="mt-2 text-2xl font-semibold">
                {rateVersions.length}
              </p>
              <div className="mt-3 max-h-64 space-y-3 overflow-auto pr-1">
                {rateVersions.map((rate) => (
                  <div className="rounded-lg border bg-muted/15 p-3 text-xs" key={rate.id}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="font-mono font-medium">
                        1 {rate.base_currency} = {rate.rate} {rate.quote_currency}
                      </p>
                      {rate.retired_at ? (
                        <StatePill label="已退役" status="archived" />
                      ) : (
                        <RetireVersionButton
                          actionLabel={`退役汇率 v${rate.version}`}
                          busy={retireRate.isPending}
                          description={`这会停止汇率版本 v${rate.version} 用于未来费用快照。`}
                          onRetire={() => retireRate.mutate(rate.id)}
                        />
                      )}
                    </div>
                    <p className="mt-1 text-muted-foreground">
                      v{rate.version} · {rate.source} · 生效 {new Date(rate.effective_at).toLocaleString()}
                      {rate.retired_at ? ` · 退役 ${new Date(rate.retired_at).toLocaleString()}` : ""}
                    </p>
                  </div>
                ))}
                {!rateVersions.length ? (
                  <p className="text-xs text-muted-foreground">尚未配置</p>
                ) : null}
              </div>
            </div>
          </div>
        </Surface>
        <Surface className="p-5">
          <SectionHeading
            description={`${policyItems.length} 条策略 · ${alertItems.filter((alert) => alert.status !== "acknowledged").length} 条待确认告警`}
            title="预算策略与告警"
          />
          <div className="mt-4 space-y-5">
            <section>
              <p className="text-sm font-semibold">策略</p>
              <div className="mt-3 space-y-3">
                {policyItems.map((policy) => (
                  <div className="flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center" key={policy.id}>
                    <div className="min-w-0 flex-1 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium">{policy.name}</p>
                        <StatePill label={policy.enabled ? "已启用" : "已停用"} status={policy.enabled ? "approved" : "archived"} />
                      </div>
                      <p className="mt-1 break-all font-mono text-muted-foreground">
                        {policy.provider_id} / {policy.model_id} / {policy.feature} · {policy.period}
                      </p>
                      <p className="mt-1 text-muted-foreground">
                        软上限 {policy.soft_limit_cny === null ? "未设置" : `¥${policy.soft_limit_cny.toFixed(2)}`} · 硬上限 {policy.hard_limit_cny === null ? "未设置" : `¥${policy.hard_limit_cny.toFixed(2)}`}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <EditBudgetPolicyDialog
                        busy={updateBudget.isPending}
                        onUpdate={(payload) =>
                          updateBudget
                            .mutateAsync({ id: policy.id, payload })
                            .then(() => undefined)
                        }
                        policy={policy}
                      />
                      <DeleteBudgetPolicyButton
                        busy={removeBudget.isPending}
                        onDelete={() => removeBudget.mutate(policy.id)}
                        policy={policy}
                      />
                    </div>
                  </div>
                ))}
                {!policyItems.length ? (
                  <p className="rounded-xl border border-dashed py-5 text-center text-sm text-muted-foreground">
                    尚未配置预算策略；真实用量仍会记录，但不会执行预算阻断。
                  </p>
                ) : null}
              </div>
            </section>
            <section className="border-t pt-5">
              <p className="text-sm font-semibold">告警</p>
              <div className="mt-3 space-y-3">
                {alertItems.slice(0, 5).map((alert) => (
                  <div
                    className="flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center"
                    key={alert.id}
                  >
                    <AlertTriangle className="size-4 shrink-0 text-amber-500" />
                    <div className="min-w-0 flex-1 text-xs">
                      <p className="font-medium">
                        {alert.level} · {alert.feature || "全部功能"}
                      </p>
                      <p className="mt-1 font-mono text-muted-foreground">
                        ¥{alert.projected_cost_cny.toFixed(4)} / ¥
                        {alert.limit_cny.toFixed(4)}
                      </p>
                    </div>
                    {alert.status === "acknowledged" ? (
                      <StatePill label="已确认" status="approved" />
                    ) : (
                      <Button
                        disabled={acknowledge.isPending}
                        onClick={() => acknowledge.mutate(alert.id)}
                        size="xs"
                        variant="outline"
                      >
                        确认告警
                      </Button>
                    )}
                  </div>
                ))}
                {!alertItems.length ? (
                  <p className="rounded-xl border border-dashed py-5 text-center text-sm text-muted-foreground">
                    当前没有预算告警
                  </p>
                ) : null}
              </div>
            </section>
          </div>
        </Surface>
      </div>
      <Surface className="p-5">
        <SectionHeading
          description="已知的 Provider 实例与模型在首次真实调用前会自动加载对应官方价格快照；可在“配置计费”中为实际 Provider ID 保存新的人工修正版本。人民币原生目录按人民币计算，不会先按参考汇率改写。"
          title="模型价格映射目录"
        />
        <div className="mt-4 max-h-[34rem] overflow-auto rounded-xl border">
          <table className="w-full min-w-[900px] text-left text-xs">
            <thead className="sticky top-0 bg-muted text-muted-foreground">
              <tr>
                <th className="px-3 py-2">渠道 / 模型</th>
                <th className="px-3 py-2">缓存输入</th>
                <th className="px-3 py-2">普通输入</th>
                <th className="px-3 py-2">输出</th>
                <th className="px-3 py-2">条件</th>
                <th className="px-3 py-2 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {catalogItems.map((item) => (
                <tr className="border-t" key={item.catalog_id}>
                  <td className="px-3 py-2"><p className="font-medium">{item.provider_key}</p><p className="font-mono text-muted-foreground">{item.model_id}</p></td>
                  <td className="px-3 py-2 font-mono">{item.native_cached_input_per_million === null ? "—" : `${item.currency === "USD" ? "$" : "¥"}${item.native_cached_input_per_million}`}</td>
                  <td className="px-3 py-2 font-mono">{item.currency === "USD" ? "$" : "¥"}{item.native_input_per_million}</td>
                  <td className="px-3 py-2 font-mono">{item.currency === "USD" ? "$" : "¥"}{item.native_output_per_million}</td>
                  <td className="max-w-72 px-3 py-2 font-mono text-[10px] text-muted-foreground">{Object.keys(item.conditions).length ? JSON.stringify(item.conditions) : "默认"}</td>
                  <td className="px-3 py-2 text-right text-muted-foreground">自动加载</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">DeepSeek 目录包含 Asia/Shanghai 09:00–12:00、14:00–18:00 的 2 倍峰值规则；实际 UsageEvent 固化调用时倍率。促销和长上下文阶梯保存在条件字段中。</p>
      </Surface>
      <Surface className="overflow-hidden">
        <div className="border-b p-5">
          <SectionHeading title="用量事件" />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[740px] text-left text-xs">
            <thead className="bg-muted/35 text-muted-foreground">
              <tr>
                <th className="px-5 py-3">时间</th>
                <th className="px-5 py-3">Provider / Model</th>
                <th className="px-5 py-3">功能</th>
                <th className="px-5 py-3">Token</th>
                <th className="px-5 py-3">Attempt</th>
                <th className="px-5 py-3">费用</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {usageEvents.map((event) => (
                <tr key={event.id}>
                  <td className="px-5 py-3 font-mono">
                    {new Date(event.created_at).toLocaleString()}
                  </td>
                  <td className="px-5 py-3">
                    {event.provider_id} / {event.model_id}
                  </td>
                  <td className="px-5 py-3">{event.feature}</td>
                  <td className="px-5 py-3 font-mono">
                    {event.input_tokens + event.output_tokens}
                  </td>
                  <td className="px-5 py-3">{event.attempt}</td>
                  <td className="px-5 py-3 font-mono">
                    <p>
                      {displayCurrency === "CNY" ? "¥" : "$"}
                      {(displayCurrency === "CNY" ? event.cost_cny : event.cost_usd).toFixed(4)}
                    </p>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      {displayCurrency === "CNY" ? "$" : "¥"}
                      {(displayCurrency === "CNY" ? event.cost_usd : event.cost_cny).toFixed(4)} · {event.cost_status}
                    </p>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>
    </PageFrame>
  );
}

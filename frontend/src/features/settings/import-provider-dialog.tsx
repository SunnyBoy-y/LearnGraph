import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRightLeft,
  Bot,
  Check,
  CheckCircle2,
  Cpu,
  Database,
  Globe,
  Image as ImageIcon,
  Layers,
  LockKeyhole,
  Mic,
  Plus,
  RefreshCcw,
  Search,
  Sparkles,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import {
  discoverProviders,
  importCcSwitchProviders,
  importDiscoveredProvider,
  importProviderBatch,
} from "@/api/providers";
import { brandIcon } from "@/lib/brand-icons";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type {
  DiscoveredProviderItem,
  DiscoveredRoleOption,
  Provider,
  ProviderRole,
  ProviderTypeCatalogItem,
} from "@/types/providers";

import openAiMark from "@/assets/openai.svg";
import deepseekBrandMark from "@/assets/brands/si-deepseek.svg";
import qwenMark from "@/assets/brands/si-qwen.svg";
import ollamaMark from "@/assets/brands/si-ollama.svg";
import anthropicMark from "@/assets/brands/si-anthropic.svg";

/** 角色对应中文与图标 */
function getRoleMeta(role: ProviderRole | string) {
  switch (role) {
    case "model":
      return { label: "主对话与推理", icon: Bot, badgeColor: "text-blue-600 bg-blue-50 border-blue-200 dark:bg-blue-950/40 dark:border-blue-800 dark:text-blue-300" };
    case "transcription":
      return { label: "语音转写 (ASR)", icon: Mic, badgeColor: "text-emerald-600 bg-emerald-50 border-emerald-200 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-300" };
    case "image_generation":
      return { label: "图像生成", icon: ImageIcon, badgeColor: "text-pink-600 bg-pink-50 border-pink-200 dark:bg-pink-950/40 dark:border-pink-800 dark:text-pink-300" };
    case "embedding":
      return { label: "向量嵌入", icon: Database, badgeColor: "text-indigo-600 bg-indigo-50 border-indigo-200 dark:bg-indigo-950/40 dark:border-indigo-800 dark:text-indigo-300" };
    case "search":
    case "deep_research":
    case "image_search":
      return { label: "联网搜索与研究", icon: Globe, badgeColor: "text-teal-600 bg-teal-50 border-teal-200 dark:bg-teal-950/40 dark:border-teal-800 dark:text-teal-300" };
    case "vision":
      return { label: "多模态视觉", icon: Layers, badgeColor: "text-orange-600 bg-orange-50 border-orange-200 dark:bg-orange-950/40 dark:border-orange-800 dark:text-orange-300" };
    default:
      return { label: role, icon: Cpu, badgeColor: "text-muted-foreground bg-muted border-border" };
  }
}

/** 能力类别短标签（用于流转目标卡片标题）。 */
function roleCategoryLabel(role: ProviderRole | string) {
  switch (role) {
    case "model":
      return "对话模型";
    case "transcription":
      return "语音转写";
    case "image_generation":
      return "图像生成";
    case "embedding":
      return "向量嵌入";
    case "vision":
      return "多模态视觉";
    case "search":
      return "搜索";
    case "image_search":
      return "文搜图/图搜图";
    case "deep_research":
      return "深度研究";
    case "fetch":
      return "网页抓取";
    case "memory":
      return "记忆";
    default:
      return String(role);
  }
}

const ROLE_ORDER: ProviderRole[] = [
  "model",
  "vision",
  "image_generation",
  "search",
  "image_search",
  "fetch",
  "deep_research",
  "transcription",
  "embedding",
  "memory",
];

/** 品牌图标渲染 */
function ProviderBrandMark({ brandId, className = "size-5" }: { brandId?: string | null; className?: string }) {
  const iconSrc = brandId ? (brandIcon(brandId) ?? (
    brandId === "openai" ? openAiMark :
    brandId === "deepseek" ? deepseekBrandMark :
    brandId === "qwen" ? qwenMark :
    brandId === "ollama" ? ollamaMark :
    brandId === "anthropic" ? anthropicMark : null
  )) : null;

  if (iconSrc) {
    return <img alt="" aria-hidden="true" className={`${className} object-contain`} src={iconSrc} />;
  }
  return <Bot className={`${className} text-muted-foreground`} />;
}

export interface ImportProviderDialogProps {
  providers?: Provider[];
  catalog?: ProviderTypeCatalogItem[];
}

export function ImportProviderDialog({
  providers = [],
  catalog = [],
}: ImportProviderDialogProps) {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"discover" | "flow" | "ccswitch">("discover");
  const queryClient = useQueryClient();

  const {
    data: discovered = [],
    isLoading: isDiscovering,
    refetch: refetchDiscover,
  } = useQuery({
    queryKey: ["providers-discovered"],
    queryFn: discoverProviders,
    enabled: open,
    staleTime: 10_000,
  });

  const [discoveredSettings, setDiscoveredSettings] = useState<
    Record<string, { targetType: string; customName: string }>
  >({});

  // 流转：源 Provider + 每个能力类别的「是否流转 + 协议」。
  const [flowSourceId, setFlowSourceId] = useState<string>("");
  const [flowTargets, setFlowTargets] = useState<
    Record<string, { included: boolean; provider_type: string }>
  >({});
  const [ccswitchText, setCcswitchText] = useState("");

  const activeSourceProvider = useMemo(
    () => providers.find((p) => p.id === flowSourceId) ?? providers[0],
    [providers, flowSourceId],
  );

  // 按 role 分组可创建的目标协议。
  const roleGroups = useMemo(() => {
    const groups = new Map<ProviderRole, ProviderTypeCatalogItem[]>();
    for (const spec of catalog) {
      if (!spec.create_allowed) continue;
      const list = groups.get(spec.role) ?? [];
      list.push(spec);
      groups.set(spec.role, list);
    }
    return [...groups.entries()]
      .sort(
        (a, b) =>
          (ROLE_ORDER.indexOf(a[0]) === -1 ? 99 : ROLE_ORDER.indexOf(a[0])) -
          (ROLE_ORDER.indexOf(b[0]) === -1 ? 99 : ROLE_ORDER.indexOf(b[0])),
      )
      .map(([role, specs]) => ({ role, specs }));
  }, [catalog]);

  // 当前某角色选中的协议（provider_type）。
  const protocolFor = (role: ProviderRole, specs: ProviderTypeCatalogItem[]) =>
    flowTargets[role]?.provider_type ?? specs[0]?.provider_type ?? "";

  const selectedTargetCount = useMemo(
    () => Object.values(flowTargets).filter((t) => t.included).length,
    [flowTargets],
  );

  // 探测项导入：后端按 source_id 解析环境凭据/本地服务信息（含密钥注入）。
  const importDiscoveredMutation = useMutation({
    mutationFn: async (item: DiscoveredProviderItem) => {
      const setting = discoveredSettings[item.id];
      return importDiscoveredProvider({
        source_id: item.id,
        target_provider_type: setting?.targetType || item.provider_type,
        display_name: setting?.customName?.trim() || item.display_name,
        base_url: item.base_url,
      });
    },
    onSuccess: (provider) => {
      toast.success(`成功导入「${provider.display_name}」`);
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      void queryClient.invalidateQueries({ queryKey: ["providers-discovered"] });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "导入失败");
    },
  });

  // 批量流转：源 base_url + key 随流转，仅协议（provider_type）可选择。
  const batchFlowMutation = useMutation({
    mutationFn: async () => {
      if (!activeSourceProvider) throw new Error("请先选择源供应商");
      const targets = Object.entries(flowTargets)
        .filter(([, t]) => t.included && t.provider_type)
        .map(([, t]) => ({ target_provider_type: t.provider_type }));
      if (targets.length === 0) throw new Error("请至少勾选一个目标能力");
      return importProviderBatch({
        source_provider_id: activeSourceProvider.id,
        targets,
      });
    },
    onSuccess: (result) => {
      const n = result.created.length;
      const skipped = result.skipped.length;
      toast.success(
        skipped > 0
          ? `已批量流转 ${n} 个能力，跳过 ${skipped} 个`
          : `已批量流转 ${n} 个能力`,
      );
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      setOpen(false);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "批量流转失败");
    },
  });

  // cc-switch 配置导入（导入后由后端探测真机模型）。
  const ccswitchImportMutation = useMutation({
    mutationFn: (configJson: string) =>
      importCcSwitchProviders({ config_json: configJson }),
    onSuccess: (result) => {
      const created = result.created.length;
      const skipped = result.skipped.length;
      toast.success(
        skipped > 0
          ? `从 cc-switch 导入 ${created} 个供应商并探测模型，跳过 ${skipped} 个（重复或不支持）`
          : `从 cc-switch 导入 ${created} 个供应商并探测模型`,
      );
      setCcswitchText("");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      setOpen(false);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "cc-switch 导入失败");
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="gap-1.5 shadow-sm">
          <Sparkles className="size-3.5 text-primary" />
          智能导入与流转
        </Button>
      </DialogTrigger>

      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="shrink-0 border-b px-5 pt-5 pb-4">
          <div className="flex items-center justify-between pr-8">
            <div>
              <DialogTitle className="flex items-center gap-2 text-base font-semibold">
                <Sparkles className="size-4 text-primary" />
                模型 Provider 智能导入与流转
              </DialogTitle>
              <DialogDescription className="mt-1 text-xs text-muted-foreground">
                自动识别环境服务与配置，支持平台内能力批量流转，并兼容 cc-switch 配置导入与模型探测。
              </DialogDescription>
            </div>
          </div>

          <div className="mt-3 flex rounded-lg bg-muted/60 p-1 text-xs font-medium">
            <button
              type="button"
              onClick={() => setActiveTab("discover")}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 transition-all ${
                activeTab === "discover"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Search className="size-3.5" />
              本地与环境识别
              {discovered.length > 0 && (
                <span className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
                  {discovered.length}
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("flow")}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 transition-all ${
                activeTab === "flow"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <ArrowRightLeft className="size-3.5" />
              平台内批量流转
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("ccswitch")}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 transition-all ${
                activeTab === "ccswitch"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Upload className="size-3.5" />
              cc-switch 导入
            </button>
          </div>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {/* 模式 1：无感智能探测 */}
          {activeTab === "discover" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>系统已自动扫描本机运行的本地模型、网关服务与环境预设：</span>
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => refetchDiscover()}
                  disabled={isDiscovering}
                  className="h-7 gap-1 px-2 text-xs"
                >
                  <RefreshCcw className={`size-3 ${isDiscovering ? "animate-spin" : ""}`} />
                  重新扫描
                </Button>
              </div>

              {isDiscovering ? (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <RefreshCcw className="size-6 animate-spin text-primary" />
                  <p className="mt-2 text-xs text-muted-foreground">正在探测本地服务与环境凭据…</p>
                </div>
              ) : discovered.length === 0 ? (
                <div className="rounded-xl border border-dashed p-8 text-center">
                  <Bot className="mx-auto size-8 text-muted-foreground/60" />
                  <p className="mt-2 text-sm font-medium">未发现本地运行的服务或环境变量</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    若你已启动 Ollama (11434) 或 LM Studio (1234)，请点击右上角「重新扫描」；容器部署请先启用 Host Service Bridge。也可切换到「cc-switch 导入」或「平台内批量流转」。
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {discovered.map((item) => {
                    const currentSetting = discoveredSettings[item.id] || {
                      targetType: item.provider_type,
                      customName: item.display_name,
                    };
                    const roleMeta = getRoleMeta(item.role);
                    const RoleIcon = roleMeta.icon;

                    return (
                      <div
                        key={item.id}
                        className="flex flex-col gap-3 rounded-xl border bg-card p-3.5 shadow-sm transition-all hover:border-primary/40"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-center gap-3">
                            <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-muted/40 p-1.5 ring-1 ring-border/50">
                              <ProviderBrandMark brandId={item.brand_id} className="size-6" />
                            </span>
                            <div>
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-semibold">{item.display_name}</p>
                                <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${roleMeta.badgeColor}`}>
                                  <RoleIcon className="size-2.5" />
                                  {roleMeta.label}
                                </span>
                              </div>
                              <p className="mt-0.5 text-xs text-muted-foreground">
                                {item.status_text} · <span className="font-mono">{item.base_url}</span>
                              </p>
                            </div>
                          </div>

                          <div className="flex shrink-0 items-center gap-2">
                            {item.already_imported ? (
                              <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                                <Check className="size-3" /> 已存在
                              </span>
                            ) : null}
                            <Button
                              size="xs"
                              disabled={importDiscoveredMutation.isPending}
                              onClick={() => importDiscoveredMutation.mutate(item)}
                              className="gap-1 shadow-xs"
                            >
                              <Plus className="size-3" />
                              一键导入
                            </Button>
                          </div>
                        </div>

                        <div className="grid grid-cols-1 gap-2 rounded-lg bg-muted/30 p-2 sm:grid-cols-2">
                          <div className="space-y-1">
                            <Label className="text-[11px] text-muted-foreground">导入去向 (选择能力角色)</Label>
                            <Select
                              value={currentSetting.targetType}
                              onValueChange={(val) =>
                                setDiscoveredSettings((prev) => ({
                                  ...prev,
                                  [item.id]: { ...currentSetting, targetType: val },
                                }))
                              }
                            >
                              <SelectTrigger className="h-8 text-xs">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {item.suggested_roles?.map((opt: DiscoveredRoleOption) => {
                                  const optMeta = getRoleMeta(opt.role);
                                  const OptIcon = optMeta.icon;
                                  return (
                                    <SelectItem key={opt.provider_type} value={opt.provider_type} className="text-xs">
                                      <span className="flex items-center gap-1.5">
                                        <OptIcon className="size-3" />
                                        {opt.label}
                                      </span>
                                    </SelectItem>
                                  );
                                })}
                              </SelectContent>
                            </Select>
                          </div>

                          <div className="space-y-1">
                            <Label className="text-[11px] text-muted-foreground">Provider 命名</Label>
                            <Input
                              value={currentSetting.customName}
                              onChange={(e) =>
                                setDiscoveredSettings((prev) => ({
                                  ...prev,
                                  [item.id]: { ...currentSetting, customName: e.target.value },
                                }))
                              }
                              className="h-8 text-xs"
                              placeholder="为新 Provider 命名"
                            />
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* 模式 2：平台内批量流转 */}
          {activeTab === "flow" && (
            <div className="space-y-5">
              {providers.length === 0 ? (
                <div className="rounded-xl border border-dashed p-8 text-center">
                  <Bot className="mx-auto size-8 text-muted-foreground/60" />
                  <p className="mt-2 text-sm font-medium">当前工作区尚未配置任何 Provider</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    请先在「新增 Provider」中配置至少一个供应商，即可在此将其 base_url 与密钥批量流转到多个能力。
                  </p>
                </div>
              ) : (
                <>
                  {/* 源供应商 */}
                  <div className="space-y-1.5">
                    <Label className="text-xs font-semibold text-muted-foreground">源供应商（base_url 与密钥随流转）</Label>
                    <Select value={activeSourceProvider?.id} onValueChange={setFlowSourceId}>
                      <SelectTrigger className="h-12 w-full text-xs">
                        <SelectValue placeholder="选择源供应商" />
                      </SelectTrigger>
                      <SelectContent className="max-h-56">
                        {providers.map((p) => (
                          <SelectItem key={p.id} value={p.id} className="text-xs">
                            <span className="flex items-center gap-2">
                              <ProviderBrandMark brandId={(p.capabilities as Record<string, string>)?.brand_id} className="size-4 shrink-0" />
                              <span className="truncate">{p.display_name}</span>
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {activeSourceProvider ? (
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          <LockKeyhole className="size-3 text-emerald-600" />
                          密钥：<span className="font-mono">{activeSourceProvider.api_key_masked ?? "未保存"}</span>
                        </span>
                        <span className="truncate font-mono">{activeSourceProvider.base_url ?? "（无地址）"}</span>
                      </div>
                    ) : null}
                  </div>

                  {/* 目标能力类别（多选 + 协议） */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs font-semibold text-muted-foreground">
                        选择要流转到的能力类别（可多选，批量流转）
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        已选 {selectedTargetCount} 项
                      </span>
                    </div>

                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {roleGroups.map(({ role, specs }) => {
                        const meta = getRoleMeta(role);
                        const Icon = meta.icon;
                        const included = flowTargets[role]?.included ?? false;
                        const protocol = protocolFor(role, specs);
                        return (
                          <div
                            key={role}
                            className={`rounded-xl border p-3 transition-colors ${
                              included ? "border-primary/50 bg-primary/5" : "border-border bg-card"
                            }`}
                          >
                            <label className="flex cursor-pointer items-center gap-2.5">
                              <Checkbox
                                checked={included}
                                onCheckedChange={(checked) =>
                                  setFlowTargets((prev) => ({
                                    ...prev,
                                    [role]: {
                                      included: checked === true,
                                      provider_type: prev[role]?.provider_type ?? specs[0]?.provider_type ?? "",
                                    },
                                  }))
                                }
                              />
                              <span className={`grid size-7 shrink-0 place-items-center rounded-md border ${meta.badgeColor}`}>
                                <Icon className="size-3.5" />
                              </span>
                              <span className="text-xs font-medium">{roleCategoryLabel(role)}</span>
                            </label>

                            <div className="mt-2.5 space-y-1">
                              <Label className="text-[10px] text-muted-foreground">协议</Label>
                              <Select
                                value={protocol}
                                onValueChange={(val) =>
                                  setFlowTargets((prev) => ({
                                    ...prev,
                                    [role]: { included: prev[role]?.included ?? true, provider_type: val },
                                  }))
                                }
                              >
                                <SelectTrigger className="h-8 w-full text-xs">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {specs.map((spec) => (
                                    <SelectItem key={spec.provider_type} value={spec.provider_type} className="text-xs">
                                      {spec.label}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* 批量流转按钮 */}
                  <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-muted/20 p-4 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <LockKeyhole className="size-3.5 text-emerald-600" />
                      源 base_url 与密钥自动流转，仅协议按所选类别决定
                    </span>
                    <Button
                      size="sm"
                      disabled={batchFlowMutation.isPending || !activeSourceProvider || selectedTargetCount === 0}
                      onClick={() => batchFlowMutation.mutate()}
                      className="gap-1.5"
                    >
                      <CheckCircle2 className="size-4" />
                      {batchFlowMutation.isPending ? "批量流转中…" : `批量流转到 ${selectedTargetCount} 个能力`}
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* 模式 3：cc-switch 配置导入（兼容历史数据，导入后探测真机模型） */}
          {activeTab === "ccswitch" && (
            <div className="space-y-3">
              <div className="space-y-2">
                <Label htmlFor="ccswitch-import-textarea">从 cc-switch 导入</Label>
                <Textarea
                  id="ccswitch-import-textarea"
                  onChange={(event) => setCcswitchText(event.currentTarget.value)}
                  placeholder="粘贴 ~/.cc-switch/config.json 的完整内容"
                  rows={10}
                  value={ccswitchText}
                />
                <p className="text-xs leading-5 text-muted-foreground">
                  后端解析 providers 的 settingsConfig.env，识别 Anthropic / OpenAI 兼容接口并复用凭据；导入后自动调用各供应商 /models 端点探测真机模型列表。同一 base_url 已存在的供应商自动跳过。
                </p>
              </div>
              <div className="flex justify-end">
                <Button
                  disabled={ccswitchImportMutation.isPending || !ccswitchText.trim()}
                  onClick={() => ccswitchImportMutation.mutate(ccswitchText)}
                  size="sm"
                  type="button"
                  className="gap-1.5"
                >
                  <Upload className="size-4" />
                  {ccswitchImportMutation.isPending ? "导入中…" : "从 cc-switch 导入并探测模型"}
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
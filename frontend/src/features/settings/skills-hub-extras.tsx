import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileArchive,
  FilePlus2,
  FolderSearch,
  Globe,
  Languages,
  Link2,
  Plus,
  RefreshCcw,
  Search,
  ShieldCheck,
  Store,
  TerminalSquare,
} from "lucide-react";
import { toast } from "sonner";

import {
  createSkillPackage,
  getSkillLocalProbePolicy,
  importLocalSkill,
  importSkillArchive,
  importSkillManual,
  importSkillNpx,
  installSkillFromMarket,
  installSkillGitHub,
  listSkillCatalogSources,
  listSkillMarket,
  previewSkillGitHub,
  scanSkillLocalProbe,
  searchExternalSkillCatalog,
  translateSkill,
  updateSkillLocalProbePolicy,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  SectionHeading,
  StatePill,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import type {
  ExternalSkillSearchItem,
  Skill,
  SkillGitHubPreview,
  SkillNpxImportResult,
} from "@/types/extensions";

function defaultLocale(): string {
  if (typeof navigator !== "undefined" && navigator.language) {
    return navigator.language;
  }
  return "zh-CN";
}

const TRANSLATE_LANGUAGE_PRESETS = [
  { value: "zh-CN", label: "简体中文" },
  { value: "zh-TW", label: "繁體中文" },
  { value: "en", label: "English · 英语" },
  { value: "ja", label: "日本語 · 日语" },
  { value: "ko", label: "한국어 · 韩语" },
  { value: "fr", label: "Français · 法语" },
  { value: "de", label: "Deutsch · 德语" },
  { value: "es", label: "Español · 西班牙语" },
  { value: "ru", label: "Русский · 俄语" },
  { value: "pt-BR", label: "Português · 葡萄牙语" },
] as const;

const CUSTOM_LOCALE = "custom";

/** Map an arbitrary BCP-47 tag (e.g. en-US) onto a preset option, or null. */
function matchPresetLocale(raw: string): string | null {
  const lower = raw.trim().toLowerCase();
  if (!lower) return null;
  const exact = TRANSLATE_LANGUAGE_PRESETS.find(
    (option) => option.value.toLowerCase() === lower,
  );
  if (exact) return exact.value;
  const prefix = lower.split("-")[0];
  const byPrefix = TRANSLATE_LANGUAGE_PRESETS.find(
    (option) => option.value.toLowerCase().split("-")[0] === prefix,
  );
  return byPrefix?.value ?? null;
}

/** Canonical Agent Skill package template (skills.sh style). */
export const DEFAULT_SKILL_MD_TEMPLATE = `---
name: my-custom-skill
description: >
  Describe when the agent should use this skill. Include trigger phrases like
  "find a skill for X", "help me with Y". Keep it specific so the agent matches correctly.
---

# My custom skill

## When to use
- The user asks questions like "how do I …", "find a skill for …", or "is there a skill that …"
- The user explicitly wants this capability instead of a generic answer
- Prefer this skill when the request matches the description above

## Instructions
1. Confirm the user's goal in one short sentence.
2. Gather only the missing inputs you need.
3. Follow the steps below; do not invent tools or run host code outside the sandbox.
4. Summarize outcomes and remaining risks.

## Steps
1. …
2. …
3. …

## Examples
- **User:** "…"
  **Agent:** …

## Notes
- Keep responses evidence-based; do not claim side effects you did not perform.
- Scripts under \`scripts/\` run only inside the Docker sandbox when authorized.
`;

/** Per-skill translation dialog — opened from skill row actions. */
export function SkillTranslateDialog({
  skill,
  open,
  onOpenChange,
}: {
  skill: Skill;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [preset, setPreset] = useState<string>(
    () => matchPresetLocale(defaultLocale()) ?? CUSTOM_LOCALE,
  );
  const [customLocale, setCustomLocale] = useState("");
  const [translation, setTranslation] = useState("");
  const [cached, setCached] = useState(false);
  const locale = preset === CUSTOM_LOCALE ? customLocale.trim() : preset;

  const applyBrowserLocale = () => {
    const browser = defaultLocale();
    const matched = matchPresetLocale(browser);
    setPreset(matched ?? CUSTOM_LOCALE);
    setCustomLocale(matched ? "" : browser);
  };

  useEffect(() => {
    if (open) {
      setTranslation("");
      setCached(false);
      const browser = defaultLocale();
      const matched = matchPresetLocale(browser);
      setPreset(matched ?? CUSTOM_LOCALE);
      setCustomLocale(matched ? "" : browser);
    }
  }, [open, skill.id]);

  const translate = useMutation({
    mutationFn: () =>
      translateSkill(skill.id, {
        target_locale: locale,
        source_path: "SKILL.md",
      }),
    onSuccess: (result) => {
      setTranslation(result.translated_text);
      setCached(result.cached);
      toast.success(
        result.cached ? "已命中翻译缓存（未计费）" : "翻译完成并已缓存",
      );
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>翻译查看 · {skill.name}</DialogTitle>
          <DialogDescription>
            仅用于查看；运行时仍使用原文。按 content hash + 语言缓存，避免重复计费。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-2">
            <Label>目标语言</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Select onValueChange={setPreset} value={preset}>
                <SelectTrigger className="w-48">
                  <SelectValue placeholder="选择语言" />
                </SelectTrigger>
                <SelectContent>
                  {TRANSLATE_LANGUAGE_PRESETS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                  <SelectItem value={CUSTOM_LOCALE}>自定义…</SelectItem>
                </SelectContent>
              </Select>
              {preset === CUSTOM_LOCALE ? (
                <Input
                  className="w-36"
                  onChange={(event) =>
                    setCustomLocale(event.currentTarget.value)
                  }
                  placeholder="如 it、vi、th"
                  value={customLocale}
                />
              ) : null}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={translate.isPending || !locale}
              onClick={() => translate.mutate()}
              size="sm"
            >
              <Languages className="size-4" />
              {translate.isPending ? "翻译中…" : "翻译"}
            </Button>
            <Button onClick={applyBrowserLocale} size="sm" variant="ghost">
              使用浏览器语言
            </Button>
          </div>
          {translation ? (
            <>
              <Badge variant={cached ? "secondary" : "outline"}>
                {cached ? "缓存命中" : "新生成"}
              </Badge>
              <Textarea
                className="min-h-64 font-mono text-xs"
                readOnly
                value={translation}
              />
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 市场 tab：预缓存市场 + 外部目录发现（ClawHub / skills.sh）。 */
function MarketPanel({ onInstalled }: { onInstalled?: () => void }) {
  const queryClient = useQueryClient();
  const [marketQuery, setMarketQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [marketPage, setMarketPage] = useState(1);
  const [catalogId, setCatalogId] = useState("clawhub");
  const [externalQuery, setExternalQuery] = useState("");
  const [externalItems, setExternalItems] = useState<ExternalSkillSearchItem[]>(
    [],
  );
  const pageSize = 12;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(marketQuery.trim());
      setMarketPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [marketQuery]);

  const market = useQuery({
    queryKey: ["skill-market", debouncedQuery, marketPage, pageSize],
    queryFn: () =>
      listSkillMarket({
        q: debouncedQuery,
        page: marketPage,
        pageSize,
      }),
  });

  const installMarket = useMutation({
    mutationFn: (marketId: string) =>
      installSkillFromMarket({ market_id: marketId }),
    onSuccess: (skill) => {
      toast.success(`已安装「${skill.name}」，请授权后使用`);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      onInstalled?.();
    },
    onError: (error) => toast.error(error.message),
  });

  const refreshMarket = useMutation({
    mutationFn: () =>
      listSkillMarket({
        refresh: true,
        q: debouncedQuery,
        page: 1,
        pageSize,
      }),
    onSuccess: (data) => {
      // Reset to page 1 and write fresh data into the active query key.
      setMarketPage(1);
      queryClient.setQueryData(
        ["skill-market", debouncedQuery, 1, pageSize],
        data,
      );
      void queryClient.invalidateQueries({ queryKey: ["skill-market"] });
      const ready = data.cards.filter((card) => card.fetch_status === "ready")
        .length;
      const failed = data.cards.filter((card) => card.fetch_status === "failed")
        .length;
      toast.success(
        failed
          ? `缓存已刷新：${ready} 就绪，${failed} 失败（见卡片错误）`
          : `市场缓存已刷新（${data.total} 条 · ${ready} 就绪）`,
      );
    },
    onError: (error) => toast.error(`刷新失败：${error.message}`),
  });

  const catalogs = useQuery({
    queryKey: ["skill-catalog-sources"],
    queryFn: listSkillCatalogSources,
  });

  const searchExternal = useMutation({
    mutationFn: () => searchExternalSkillCatalog(catalogId, externalQuery.trim()),
    onSuccess: (result) => {
      setExternalItems(result.items);
      if (!result.items.length) toast.info("外部目录没有匹配结果");
    },
    onError: (error) => toast.error(error.message),
  });

  const totalPages = market.data?.total_pages ?? 0;
  const total = market.data?.total ?? 0;

  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionHeading
            description="预缓存条目 · 安装后需授权"
            title="市场"
          />
          <Button
            disabled={refreshMarket.isPending || market.isFetching}
            onClick={() => refreshMarket.mutate()}
            size="sm"
            variant="outline"
          >
            <RefreshCcw
              className={`size-4 ${refreshMarket.isPending ? "animate-spin" : ""}`}
            />
            {refreshMarket.isPending ? "刷新中…" : "刷新缓存"}
          </Button>
        </div>
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              onChange={(event) => setMarketQuery(event.currentTarget.value)}
              placeholder="搜索名称、来源、描述…"
              value={marketQuery}
            />
          </div>
          <p className="shrink-0 text-xs text-muted-foreground">
            {market.isPending || refreshMarket.isPending
              ? "加载中…"
              : `共 ${total} 条 · 第 ${marketPage}/${Math.max(totalPages, 1)} 页`}
          </p>
        </div>
        {market.isPending && !market.data ? (
          <LoadingState />
        ) : market.isError ? (
          <ErrorState message={market.error.message} />
        ) : (
          <>
            <p className="mt-2 text-xs text-muted-foreground">
              来源 {market.data?.source ?? "—"}
              {market.data?.refreshed_at
                ? ` · ${new Date(market.data.refreshed_at).toLocaleString()}`
                : ""}
              {market.data?.query ? ` · 「${market.data.query}」` : ""}
            </p>
            {market.data?.cards.length ? (
              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {market.data.cards.map((card) => (
                  <div className="rounded-xl border p-4" key={card.market_id}>
                    <div className="flex items-start gap-2">
                      <Store className="mt-0.5 size-4 shrink-0 text-primary" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">
                          {card.name}
                        </p>
                        <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                          {card.source}
                        </p>
                      </div>
                      <StatePill status={card.fetch_status} />
                    </div>
                    <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">
                      {card.description || "暂无简介"}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-1">
                      {card.official ? (
                        <Badge className="gap-1" variant="default">
                          <ShieldCheck className="size-3" />
                          官方
                        </Badge>
                      ) : null}
                      <Badge variant="secondary">
                        {card.installs.toLocaleString()} installs
                      </Badge>
                      {card.has_scripts ? (
                        <Badge variant="outline">scripts</Badge>
                      ) : null}
                      <Badge variant="outline">{card.file_count} files</Badge>
                    </div>
                    {card.fetch_error ? (
                      <p className="mt-2 text-[11px] text-amber-700 dark:text-amber-300">
                        {card.fetch_error}
                      </p>
                    ) : null}
                    <Button
                      className="mt-3"
                      disabled={
                        installMarket.isPending || card.file_count === 0
                      }
                      onClick={() => installMarket.mutate(card.market_id)}
                      size="xs"
                    >
                      <Download className="size-3" />
                      安装
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">
                没有匹配条目。试试刷新缓存或手动导入。
              </p>
            )}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
              <Button
                disabled={marketPage <= 1 || market.isFetching}
                onClick={() => setMarketPage((page) => Math.max(1, page - 1))}
                size="sm"
                variant="outline"
              >
                <ChevronLeft className="size-4" />
                上一页
              </Button>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>跳到</span>
                <Input
                  className="h-8 w-16"
                  min={1}
                  onChange={(event) => {
                    const value = Number(event.currentTarget.value);
                    if (Number.isFinite(value) && value >= 1) {
                      setMarketPage(
                        Math.min(
                          Math.max(1, Math.floor(value)),
                          Math.max(totalPages, 1),
                        ),
                      );
                    }
                  }}
                  type="number"
                  value={marketPage}
                />
                <span>/ {Math.max(totalPages, 1)}</span>
              </div>
              <Button
                disabled={
                  totalPages === 0 ||
                  marketPage >= totalPages ||
                  market.isFetching
                }
                onClick={() =>
                  setMarketPage((page) =>
                    totalPages ? Math.min(totalPages, page + 1) : page,
                  )
                }
                size="sm"
                variant="outline"
              >
                下一页
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </>
        )}
      </div>

      <div className="rounded-xl border p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionHeading
            description="联邦索引：从外部目录发现 Skill，安装仍走固定来源导入"
            title="外部目录发现"
          />
          <div className="flex flex-wrap gap-1.5">
            {(catalogs.data ?? [])
              .filter((source) => source.kind === "skill")
              .map((source) => (
                <Button
                  disabled={!source.enabled}
                  key={source.id}
                  onClick={() => {
                    setCatalogId(source.id);
                    setExternalItems([]);
                  }}
                  size="xs"
                  title={source.enabled ? source.notes : `${source.notes}（未启用）`}
                  variant={catalogId === source.id ? "default" : "outline"}
                >
                  <Globe className="size-3" />
                  {source.label}
                </Button>
              ))}
          </div>
        </div>
        <div className="mt-4 flex gap-2">
          <Input
            onChange={(event) => setExternalQuery(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && externalQuery.trim().length >= 2) {
                searchExternal.mutate();
              }
            }}
            placeholder="搜索外部目录，例如 pdf / research / database…"
            value={externalQuery}
          />
          <Button
            disabled={
              searchExternal.isPending || externalQuery.trim().length < 2
            }
            onClick={() => searchExternal.mutate()}
            size="sm"
            variant="outline"
          >
            {searchExternal.isPending ? "搜索中…" : "搜索"}
          </Button>
        </div>
        {externalItems.length ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {externalItems.map((item) => (
              <div
                className="rounded-xl border p-4"
                key={`${item.catalog}:${item.external_id}`}
              >
                <div className="flex items-start gap-2">
                  <Globe className="mt-0.5 size-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">{item.name}</p>
                    <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                      {item.external_id}
                      {item.version ? ` · v${item.version}` : ""}
                      {item.owner ? ` · ${item.owner}` : ""}
                    </p>
                  </div>
                  <Badge variant="outline">{item.catalog}</Badge>
                </div>
                {item.description ? (
                  <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">
                    {item.description}
                  </p>
                ) : null}
                <p className="mt-2 text-[11px] text-muted-foreground">
                  {item.install_hint}
                </p>
                {item.homepage_url ? (
                  <Button asChild className="mt-3" size="xs" variant="outline">
                    <a
                      href={item.homepage_url}
                      rel="noreferrer noopener"
                      target="_blank"
                    >
                      <ExternalLink className="size-3" />
                      打开来源
                    </a>
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-4 text-xs text-muted-foreground">
            {catalogs.data?.find((source) => source.id === catalogId)?.enabled
              ? "输入至少 2 个字符搜索所选目录。结果仅用于发现；安装请通过市场或 URL/npx 导入以保持内容可审。"
              : "所选目录未启用；可在后端设置中开启（如 LEARNGRAPH_SKILLS_SH_ENABLED）。"}
          </p>
        )}
      </div>
    </div>
  );
}

/** 本机导入 tab：同机只读扫描 + 导入副本。 */
function LocalProbePanel({ onInstalled }: { onInstalled?: () => void }) {
  const queryClient = useQueryClient();
  const probePolicy = useQuery({
    queryKey: ["skill-local-probe-policy"],
    queryFn: getSkillLocalProbePolicy,
  });
  const probeScan = useQuery({
    queryKey: ["skill-local-probe-scan"],
    queryFn: scanSkillLocalProbe,
    enabled: false,
  });

  const updateProbe = useMutation({
    mutationFn: (enabled: boolean) =>
      updateSkillLocalProbePolicy({
        enabled,
        allowed_roots: probePolicy.data?.allowed_roots ?? [],
      }),
    onSuccess: () => {
      toast.success("本机探测策略已更新");
      void queryClient.invalidateQueries({
        queryKey: ["skill-local-probe-policy"],
      });
    },
    onError: (error) => toast.error(error.message),
  });

  const scanProbe = useMutation({
    mutationFn: scanSkillLocalProbe,
    onSuccess: (data) => {
      queryClient.setQueryData(["skill-local-probe-scan"], data);
      if (!data.available) {
        toast.message(data.unavailable_reason || "本机探测不可用");
      } else {
        toast.success(`扫描完成：${data.items.length} 个候选`);
      }
    },
    onError: (error) => toast.error(error.message),
  });

  const importLocal = useMutation({
    mutationFn: importLocalSkill,
    onSuccess: (skill) => {
      toast.success(`已导入本机 Skill「${skill.name}」`);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      onInstalled?.();
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div>
      <SectionHeading
        description="仅前后端同机；路径取自进程用户环境（USERPROFILE/HOME），无硬编码用户名"
        title="本机导入"
      />
      {probePolicy.isPending ? (
        <LoadingState />
      ) : probePolicy.isError ? (
        <ErrorState message={probePolicy.error.message} />
      ) : probePolicy.data ? (
        <div className="mt-4 space-y-4">
          {!probePolicy.data.same_host_available ? (
            <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              {probePolicy.data.unavailable_reason ||
                "本机探测不可用（仅前后端同机部署可用）"}
            </p>
          ) : null}
          <div className="flex items-center gap-3">
            <Switch
              checked={probePolicy.data.enabled}
              disabled={
                !probePolicy.data.same_host_available ||
                updateProbe.isPending
              }
              onCheckedChange={(enabled) => updateProbe.mutate(enabled)}
            />
            <Label>启用本机 Skill 只读扫描</Label>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {probePolicy.data.candidate_roots.map((root) => (
              <div
                className="rounded-lg border p-3 text-xs"
                key={root.path}
              >
                <p className="font-medium">{root.label}</p>
                <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
                  {root.path}
                </p>
                <p className="mt-1 text-muted-foreground">
                  {root.exists
                    ? root.readable
                      ? "存在且可读"
                      : "存在但不可读"
                    : "不存在"}
                </p>
              </div>
            ))}
          </div>
          <Button
            disabled={
              !probePolicy.data.same_host_available ||
              !probePolicy.data.enabled ||
              scanProbe.isPending
            }
            onClick={() => scanProbe.mutate()}
            size="sm"
            variant="outline"
          >
            <FolderSearch className="size-4" />
            扫描本机
          </Button>
          {(probeScan.data ?? scanProbe.data)?.items?.length ? (
            <div className="space-y-2">
              {(probeScan.data ?? scanProbe.data)!.items.map((item) => (
                <div
                  className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center"
                  key={`${item.root_path}:${item.relative_dir}`}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold">{item.name}</p>
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {item.relative_dir}
                    </p>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {item.description}
                    </p>
                  </div>
                  <Button
                    disabled={importLocal.isPending}
                    onClick={() =>
                      importLocal.mutate({
                        root_path: item.root_path,
                        relative_dir: item.relative_dir,
                        skill_key: item.skill_key,
                      })
                    }
                    size="xs"
                  >
                    导入副本
                  </Button>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** URL 导入 tab：GitHub / skills.sh 引用，commit 锁定安装（含安装前扫描预览）。 */
function UrlImportPanel({ onImported }: { onImported?: () => void }) {
  const [reference, setReference] = useState("");
  const [preview, setPreview] = useState<SkillGitHubPreview | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [skillKey, setSkillKey] = useState("");

  const previewMutation = useMutation({
    mutationFn: () => previewSkillGitHub(reference.trim()),
    onSuccess: (result) => {
      setPreview(result);
      setSelectedPath(result.candidates[0]?.path ?? null);
      if (!result.candidates.length) {
        toast.info("该引用下没有找到 SKILL.md");
      }
    },
    onError: (error) => toast.error(error.message),
  });

  const installMutation = useMutation({
    mutationFn: () =>
      installSkillGitHub({
        reference: reference.trim(),
        path: selectedPath ?? undefined,
        commit: preview?.commit,
        ...(skillKey.trim() ? { skill_key: skillKey.trim() } : {}),
      }),
    onSuccess: (skill) => {
      toast.success(
        `已按 commit ${skill.version} 安装「${skill.name}」，请授权后使用`,
      );
      setPreview(null);
      setReference("");
      setSkillKey("");
      setSelectedPath(null);
      onImported?.();
    },
    onError: (error) => toast.error(error.message),
  });

  const selected = preview?.candidates.find(
    (candidate) => candidate.path === selectedPath,
  );

  return (
    <div className="space-y-3">
      <SectionHeading
        description="支持 owner/repo、owner/repo/path@ref 或 github.com URL。安装内容锁定到预览时的 commit，仅导入文本文件，安装后仍需授权。"
        title="URL 导入（commit 锁定）"
      />
      <div className="flex gap-2">
        <Input
          className="font-mono text-xs"
          onChange={(event) => setReference(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && reference.trim().length >= 3) {
              event.preventDefault();
              previewMutation.mutate();
            }
          }}
          placeholder="anthropics/skills/skills/frontend-design 或 https://github.com/…"
          value={reference}
        />
        <Button
          disabled={
            previewMutation.isPending || reference.trim().length < 3
          }
          onClick={() => previewMutation.mutate()}
          size="sm"
          variant="outline"
        >
          {previewMutation.isPending ? "解析中…" : "预览"}
        </Button>
      </div>
      {preview ? (
        <div className="space-y-2">
          <p className="font-mono text-[11px] text-muted-foreground">
            {preview.owner}/{preview.repo} @ {preview.ref} →{" "}
            {preview.commit.slice(0, 12)}
            {preview.tree_truncated ? "（仓库过大，目录列表被截断）" : ""}
          </p>
          <div className="max-h-64 space-y-1.5 overflow-auto">
            {preview.candidates.map((candidate) => (
              <button
                className={`w-full rounded-lg border p-3 text-left text-xs ${
                  selectedPath === candidate.path
                    ? "border-primary bg-primary/5"
                    : "hover:border-primary/50"
                }`}
                key={candidate.path || "(root)"}
                onClick={() => setSelectedPath(candidate.path)}
                type="button"
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-semibold">
                    {candidate.name}
                  </span>
                  {candidate.scan_risk === "high" ||
                  candidate.scan_risk === "medium" ? (
                    <Badge
                      variant={
                        candidate.scan_risk === "high"
                          ? "destructive"
                          : "outline"
                      }
                    >
                      风险{candidate.scan_risk === "high" ? "高" : "中"}
                    </Badge>
                  ) : null}
                  {candidate.has_scripts ? (
                    <Badge variant="outline">scripts</Badge>
                  ) : null}
                  <Badge variant="secondary">
                    {candidate.file_count} files
                  </Badge>
                </div>
                <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                  {candidate.path || "(仓库根目录)"}
                  {candidate.license ? ` · ${candidate.license}` : ""}
                </p>
                {candidate.description ? (
                  <p className="mt-1 line-clamp-2 text-muted-foreground">
                    {candidate.description}
                  </p>
                ) : null}
              </button>
            ))}
          </div>
          {selected ? (
            <div className="rounded-lg border bg-muted/30 p-3 text-xs">
              <p className="font-medium">安装前权限预览</p>
              <ul className="mt-1 space-y-0.5 text-muted-foreground">
                <li>
                  {selected.required_permissions.length
                    ? `✓ 请求权限：${selected.required_permissions.join(", ")}（脚本仅在 Docker 沙箱授权后运行）`
                    : "✓ 纯指令包，不请求脚本执行权限"}
                </li>
                {selected.allowed_tools ? (
                  <li>✓ 声明 allowed-tools：{selected.allowed_tools}</li>
                ) : null}
                <li>
                  ✓ {selected.file_count} 个文本文件（
                  {(selected.total_size_bytes / 1024).toFixed(0)} KB）
                  {selected.skipped_file_count
                    ? ` · ${selected.skipped_file_count} 个非文本文件将被跳过`
                    : ""}
                </li>
                {selected.scan_risk ? (
                  <li>
                    {selected.scan_risk === "low" ? "✓" : "⚠"} SKILL.md
                    快速扫描：风险
                    {selected.scan_risk === "high"
                      ? "高"
                      : selected.scan_risk === "medium"
                        ? "中"
                        : "低"}
                    {selected.scan_finding_count
                      ? `（${selected.scan_finding_count} 处发现，安装后可在编辑器查看全量扫描）`
                      : ""}
                  </li>
                ) : null}
                <li>✗ 安装后未授权前不会注入 Agent 上下文</li>
              </ul>
            </div>
          ) : null}
          <Label>
            Skill Key（可选，默认取 frontmatter name）
            <Input
              className="mt-2 font-mono text-xs"
              onChange={(event) => setSkillKey(event.currentTarget.value)}
              pattern="[a-z0-9][a-z0-9._-]{1,79}"
              placeholder="自动"
              value={skillKey}
            />
          </Label>
        </div>
      ) : null}
      <div className="flex justify-end">
        <Button
          disabled={
            installMutation.isPending || !preview || selectedPath === null
          }
          onClick={() => installMutation.mutate()}
        >
          {installMutation.isPending
            ? "安装中…"
            : preview
              ? `安装 @ ${preview.commit.slice(0, 7)}`
              : "先预览"}
        </Button>
      </div>
    </div>
  );
}

/** npx 命令导入 tab：粘贴 `npx skills add …`，服务端等价安装（不执行 npx）。 */
function NpxImportPanel({ onImported }: { onImported?: () => void }) {
  const [command, setCommand] = useState("");
  const [result, setResult] = useState<SkillNpxImportResult | null>(null);

  const importMutation = useMutation({
    mutationFn: () => importSkillNpx({ command: command.trim() }),
    onSuccess: (data) => {
      setResult(data);
      if (data.installed.length) {
        toast.success(
          `已安装 ${data.installed.length} 个 Skill @ ${data.commit.slice(0, 7)}，请授权后使用`,
        );
        onImported?.();
      } else {
        toast.info("没有安装任何 Skill，请检查跳过原因");
      }
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="space-y-3">
      <SectionHeading
        description="粘贴 skills.sh 的安装命令或仓库地址；服务端解析后经 commit 锁定的 GitHub 导入等价安装，不会在宿主执行 npx。"
        title="npx 命令导入"
      />
      <Textarea
        className="min-h-20 font-mono text-xs"
        onChange={(event) => setCommand(event.currentTarget.value)}
        placeholder={"npx skills add https://github.com/anthropics/skills --skill frontend-design\n也支持 owner/repo、skills.sh URL"}
        value={command}
      />
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          支持 --skill（可重复）与 --all；多个 Skill 未指定时会提示选择。
        </p>
        <Button
          disabled={importMutation.isPending || command.trim().length < 3}
          onClick={() => importMutation.mutate()}
        >
          <TerminalSquare className="size-4" />
          {importMutation.isPending ? "安装中…" : "解析并安装"}
        </Button>
      </div>
      {result ? (
        <div className="space-y-2 rounded-lg border bg-muted/30 p-3 text-xs">
          <p className="font-mono text-[11px] text-muted-foreground">
            {result.owner}/{result.repo} @ {result.commit.slice(0, 12)}
          </p>
          {result.installed.length ? (
            <div>
              <p className="font-medium">已安装（需授权后启用）</p>
              <ul className="mt-1 space-y-0.5">
                {result.installed.map((skill) => (
                  <li key={skill.id}>
                    ✓ {skill.name}{" "}
                    <span className="font-mono text-muted-foreground">
                      {skill.skill_key}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {result.skipped.length ? (
            <div>
              <p className="font-medium">已跳过</p>
              <ul className="mt-1 space-y-0.5 text-muted-foreground">
                {result.skipped.map((item) => (
                  <li key={`${item.target}:${item.reason}`}>
                    ✗ {item.target} — {item.reason}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** 压缩包导入 tab：上传 zip，仅提取 UTF-8 文本文件。 */
function ArchiveImportPanel({ onImported }: { onImported?: () => void }) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [fileName, setFileName] = useState("");
  const [fileSize, setFileSize] = useState(0);
  const [archiveBase64, setArchiveBase64] = useState("");
  const [skillKey, setSkillKey] = useState("");
  const [name, setName] = useState("");

  const pickFile = (file: File | undefined | null) => {
    if (!file) return;
    if (!/\.zip$/i.test(file.name)) {
      toast.error("仅支持 .zip 压缩包");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      toast.error("压缩包超过 20 MB 限制");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      const base64 = text.includes(",") ? text.slice(text.indexOf(",") + 1) : "";
      if (!base64) {
        toast.error("读取压缩包失败");
        return;
      }
      setArchiveBase64(base64);
      setFileName(file.name);
      setFileSize(file.size);
    };
    reader.onerror = () => toast.error("读取压缩包失败");
    reader.readAsDataURL(file);
  };

  const importMutation = useMutation({
    mutationFn: () =>
      importSkillArchive({
        archive_base64: archiveBase64,
        filename: fileName,
        ...(skillKey.trim() ? { skill_key: skillKey.trim() } : {}),
        ...(name.trim() ? { name: name.trim() } : {}),
      }),
    onSuccess: (skill) => {
      toast.success(`已导入压缩包 Skill「${skill.name}」，请授权后使用`);
      setArchiveBase64("");
      setFileName("");
      setFileSize(0);
      setSkillKey("");
      setName("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      onImported?.();
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="space-y-3">
      <SectionHeading
        description="上传包含 SKILL.md 的 zip（根目录或单一顶层文件夹内）。仅导入 UTF-8 文本文件；二进制与超限文件会被跳过并记录。"
        title="压缩包导入"
      />
      <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground hover:border-primary/60">
        <FileArchive className="size-6" />
        {fileName ? (
          <span>
            {fileName}
            <span className="ml-2 font-mono text-xs">
              {(fileSize / 1024).toFixed(0)} KB
            </span>
          </span>
        ) : (
          <span>点击选择或拖入 .zip 文件（≤ 20 MB）</span>
        )}
        <input
          accept=".zip,application/zip,application/x-zip-compressed"
          className="hidden"
          onChange={(event) => pickFile(event.currentTarget.files?.[0])}
          ref={fileInputRef}
          type="file"
        />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <Label>
          Skill Key（可选，默认取 frontmatter name）
          <Input
            className="mt-2 font-mono text-xs"
            onChange={(event) => setSkillKey(event.currentTarget.value)}
            pattern="[a-z0-9][a-z0-9._-]{1,79}"
            placeholder="自动"
            value={skillKey}
          />
        </Label>
        <Label>
          显示名称（可选）
          <Input
            className="mt-2"
            onChange={(event) => setName(event.currentTarget.value)}
            placeholder="自动"
            value={name}
          />
        </Label>
      </div>
      <div className="flex justify-end">
        <Button
          disabled={importMutation.isPending || !archiveBase64}
          onClick={() => importMutation.mutate()}
        >
          <Download className="size-4" />
          {importMutation.isPending ? "导入中…" : "导入压缩包"}
        </Button>
      </div>
    </div>
  );
}

/** 文件编辑 tab：模板化手动编辑导入 + 快速创建空白文件包。 */
function FileEditorPanel({
  onImported,
  onCreatedPackage,
}: {
  onImported?: () => void;
  onCreatedPackage?: (skill: Skill) => void;
}) {
  const [skillKey, setSkillKey] = useState("my-custom-skill");
  const [name, setName] = useState("My custom skill");
  const [extraPath, setExtraPath] = useState("scripts/hello.py");
  const [extraContent, setExtraContent] = useState(
    'print("hello from manual skill")\n',
  );
  const [includeExtra, setIncludeExtra] = useState(false);
  const [skillMd, setSkillMd] = useState(DEFAULT_SKILL_MD_TEMPLATE);
  const [quickKey, setQuickKey] = useState("my-skill");
  const [quickName, setQuickName] = useState("My skill");
  const [quickScript, setQuickScript] = useState(false);

  const importMutation = useMutation({
    mutationFn: importSkillManual,
    onSuccess: (skill) => {
      toast.success(`已导入「${skill.name}」，请授权后使用`);
      onImported?.();
    },
    onError: (error) => toast.error(error.message),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createSkillPackage({
        skill_key: quickKey.trim(),
        name: quickName.trim(),
        description: "",
        with_sample_script: quickScript,
      }),
    onSuccess: (skill) => {
      toast.success(`文件包 Skill「${skill.name}」已创建，可在列表中继续编辑`);
      onCreatedPackage?.(skill);
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <SectionHeading
          description="基于 Agent Skill 标准模板（触发条件 / 正文 / 步骤）。导入为工作区副本，不在宿主执行。"
          title="编辑 SKILL.md 并导入"
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Label>
            Skill Key
            <Input
              className="mt-2 font-mono text-xs"
              onChange={(event) => setSkillKey(event.currentTarget.value)}
              pattern="[a-z0-9][a-z0-9._-]{1,79}"
              value={skillKey}
            />
          </Label>
          <Label>
            显示名称
            <Input
              className="mt-2"
              onChange={(event) => setName(event.currentTarget.value)}
              value={name}
            />
          </Label>
        </div>
        <Label>
          SKILL.md
          <Textarea
            className="mt-2 min-h-56 font-mono text-xs"
            onChange={(event) => setSkillMd(event.currentTarget.value)}
            value={skillMd}
          />
        </Label>
        <Label className="flex items-center gap-2 text-xs">
          <input
            checked={includeExtra}
            onChange={(event) => setIncludeExtra(event.currentTarget.checked)}
            type="checkbox"
          />
          附加另一个文件（例如 scripts/hello.py）
        </Label>
        {includeExtra ? (
          <div className="space-y-2 rounded-lg border p-3">
            <Label>
              相对路径
              <Input
                className="mt-2 font-mono text-xs"
                onChange={(event) => setExtraPath(event.currentTarget.value)}
                value={extraPath}
              />
            </Label>
            <Label>
              内容
              <Textarea
                className="mt-2 min-h-28 font-mono text-xs"
                onChange={(event) =>
                  setExtraContent(event.currentTarget.value)
                }
                value={extraContent}
              />
            </Label>
          </div>
        ) : null}
        <div className="flex justify-end">
          <Button
            disabled={
              importMutation.isPending || !skillKey.trim() || !skillMd.trim()
            }
            onClick={() => {
              const files = [
                { path: "SKILL.md", contents: skillMd },
                ...(includeExtra && extraPath.trim()
                  ? [{ path: extraPath.trim(), contents: extraContent }]
                  : []),
              ];
              importMutation.mutate({
                skill_key: skillKey.trim(),
                name: name.trim() || undefined,
                source: "manual_import",
                version: "1.0.0",
                files,
              });
            }}
          >
            {importMutation.isPending ? "导入中…" : "导入到工作区"}
          </Button>
        </div>
      </div>
      <div className="space-y-3 rounded-xl border p-4">
        <SectionHeading
          description="生成标准 SKILL.md 模板骨架；创建后可在已安装列表中打开文件编辑器继续完善。"
          title="或：快速创建空白文件包"
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Label>
            Skill Key
            <Input
              className="mt-2 font-mono text-xs"
              onChange={(event) => setQuickKey(event.currentTarget.value)}
              pattern="[a-z0-9][a-z0-9._-]{1,79}"
              value={quickKey}
            />
          </Label>
          <Label>
            名称
            <Input
              className="mt-2"
              onChange={(event) => setQuickName(event.currentTarget.value)}
              value={quickName}
            />
          </Label>
        </div>
        <Label className="flex items-center gap-2 text-xs">
          <input
            checked={quickScript}
            onChange={(event) => setQuickScript(event.currentTarget.checked)}
            type="checkbox"
          />
          附带示例 scripts/hello.py（仅沙箱可运行）
        </Label>
        <div className="flex justify-end">
          <Button
            disabled={
              createMutation.isPending || !quickKey.trim() || !quickName.trim()
            }
            onClick={() => createMutation.mutate()}
            variant="secondary"
          >
            <FilePlus2 className="size-4" />
            {createMutation.isPending ? "创建中…" : "创建并进入编辑"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 添加 Skill 统一入口：市场 / URL / npx / 压缩包 / 本机 / 文件编辑。 */
export function AddSkillDialog({
  onInstalled,
  onCreatedPackage,
}: {
  onInstalled?: () => void;
  onCreatedPackage?: (skill: Skill) => void;
}) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const handleInstalled = () => {
    void queryClient.invalidateQueries({ queryKey: ["skills"] });
    onInstalled?.();
  };
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="size-4" />
          添加 Skill
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>添加 Skill</DialogTitle>
          <DialogDescription>
            市场、URL、npx 命令、压缩包、本机与手动编辑统一入口。所有来源均以工作区副本安装，安装后需授权才会注入
            Agent。
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="market">
          <TabsList className="flex h-auto flex-wrap justify-start">
            <TabsTrigger value="market">
              <Store className="size-4" />
              市场
            </TabsTrigger>
            <TabsTrigger value="url">
              <Link2 className="size-4" />
              URL 导入
            </TabsTrigger>
            <TabsTrigger value="npx">
              <TerminalSquare className="size-4" />
              npx 命令
            </TabsTrigger>
            <TabsTrigger value="archive">
              <FileArchive className="size-4" />
              压缩包
            </TabsTrigger>
            <TabsTrigger value="local">
              <FolderSearch className="size-4" />
              本机导入
            </TabsTrigger>
            <TabsTrigger value="editor">
              <FilePlus2 className="size-4" />
              文件编辑
            </TabsTrigger>
          </TabsList>
          <TabsContent className="pt-4" value="market">
            <MarketPanel onInstalled={handleInstalled} />
          </TabsContent>
          <TabsContent className="pt-4" value="url">
            <UrlImportPanel onImported={handleInstalled} />
          </TabsContent>
          <TabsContent className="pt-4" value="npx">
            <NpxImportPanel onImported={handleInstalled} />
          </TabsContent>
          <TabsContent className="pt-4" value="archive">
            <ArchiveImportPanel onImported={handleInstalled} />
          </TabsContent>
          <TabsContent className="pt-4" value="local">
            <LocalProbePanel onInstalled={handleInstalled} />
          </TabsContent>
          <TabsContent className="pt-4" value="editor">
            <FileEditorPanel
              onCreatedPackage={(skill) => {
                handleInstalled();
                setOpen(false);
                onCreatedPackage?.(skill);
              }}
              onImported={handleInstalled}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

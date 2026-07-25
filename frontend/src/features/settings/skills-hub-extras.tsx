import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  Download,
  FilePlus2,
  FolderSearch,
  Languages,
  Play,
  RefreshCcw,
  Search,
  Store,
} from "lucide-react";
import { toast } from "sonner";

import {
  getSkillLocalProbePolicy,
  importLocalSkill,
  importSkillManual,
  installSkillFromMarket,
  listSkillMarket,
  runSkillSandbox,
  scanSkillLocalProbe,
  translateSkill,
  updateSkillLocalProbePolicy,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { Skill } from "@/types/extensions";

function defaultLocale(): string {
  if (typeof navigator !== "undefined" && navigator.language) {
    return navigator.language;
  }
  return "zh-CN";
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
  const [locale, setLocale] = useState(defaultLocale);
  const [translation, setTranslation] = useState("");
  const [cached, setCached] = useState(false);

  useEffect(() => {
    if (open) {
      setTranslation("");
      setCached(false);
      setLocale(defaultLocale());
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
          <Label>
            目标语言
            <Input
              className="mt-2"
              onChange={(event) => setLocale(event.currentTarget.value)}
              placeholder="zh-CN"
              value={locale}
            />
          </Label>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={translate.isPending}
              onClick={() => translate.mutate()}
              size="sm"
            >
              <Languages className="size-4" />
              {translate.isPending ? "翻译中…" : "翻译"}
            </Button>
            <Button
              onClick={() => setLocale(defaultLocale())}
              size="sm"
              variant="ghost"
            >
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

/** Market cards + collapsible advanced tools for Skills Hub. */
export function SkillsHubExtras({
  skills,
  onInstalled,
}: {
  skills: Skill[];
  onInstalled?: () => void;
}) {
  const queryClient = useQueryClient();
  const [runSkillId, setRunSkillId] = useState("");
  const [scriptPath, setScriptPath] = useState("scripts/hello.py");
  const [runOutput, setRunOutput] = useState("");
  const [marketQuery, setMarketQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [marketPage, setMarketPage] = useState(1);
  const [showAdvanced, setShowAdvanced] = useState(false);
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
  const probePolicy = useQuery({
    queryKey: ["skill-local-probe-policy"],
    queryFn: getSkillLocalProbePolicy,
    enabled: showAdvanced,
  });
  const probeScan = useQuery({
    queryKey: ["skill-local-probe-scan"],
    queryFn: scanSkillLocalProbe,
    enabled: false,
  });

  const packageSkills = useMemo(
    () =>
      skills.filter(
        (skill) =>
          skill.kind === "agent_skill_package" ||
          skill.package_format === "skill_md_v1",
      ),
    [skills],
  );

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

  const sandboxRun = useMutation({
    mutationFn: () =>
      runSkillSandbox(runSkillId, {
        script_path: scriptPath,
      }),
    onSuccess: (result) => {
      if (!result.available || result.status === "unavailable") {
        setRunOutput(
          result.error_message ||
            "Docker 沙箱不可用；禁止宿主回退（D-080）",
        );
        toast.error(result.error_message || "沙箱不可用");
        return;
      }
      setRunOutput(
        [
          `status=${result.status} exit=${result.exit_code ?? "—"} latency=${result.latency_ms}ms`,
          result.argv_redacted?.length
            ? `argv: ${result.argv_redacted.join(" ")}`
            : "",
          "--- stdout ---",
          result.stdout_summary || "(empty)",
          "--- stderr ---",
          result.stderr_summary || "(empty)",
        ]
          .filter(Boolean)
          .join("\n"),
      );
      toast.success(
        result.status === "succeeded" ? "沙箱试运行完成" : "沙箱试运行结束",
      );
    },
    onError: (error) => toast.error(error.message),
  });

  const totalPages = market.data?.total_pages ?? 0;
  const total = market.data?.total ?? 0;

  return (
    <div className="space-y-5">
      <Surface className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionHeading
            description="预缓存条目 · 安装后需授权"
            title="市场"
          />
          <div className="flex flex-wrap gap-2">
            <ManualImportDialog
              onImported={() => {
                void queryClient.invalidateQueries({ queryKey: ["skills"] });
                onInstalled?.();
              }}
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
      </Surface>

      <details
        className="group"
        onToggle={(event) =>
          setShowAdvanced((event.currentTarget as HTMLDetailsElement).open)
        }
        open={showAdvanced}
      >
        <summary className="cursor-pointer list-none rounded-xl border px-4 py-3 text-sm font-medium text-muted-foreground hover:bg-muted/40">
          高级 · 本机探测 / 沙箱试运行
          <span className="ml-2 text-xs font-normal">
            （默认收起，不阻塞主流程）
          </span>
        </summary>
        <div className="mt-3 space-y-5">
          <Surface className="p-5">
            <SectionHeading
              description="仅前后端同机；路径取自进程用户环境（USERPROFILE/HOME），无硬编码用户名"
              title="本机探测"
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
          </Surface>

          <Surface className="p-5">
            <SectionHeading
              description="scripts/ 仅 Docker 无网沙箱；无 Docker 时诚实 unavailable"
              title="沙箱试运行"
            />
            <div className="mt-4 space-y-3">
              <Label>
                文件包 Skill
                <select
                  className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm"
                  onChange={(event) => setRunSkillId(event.target.value)}
                  value={runSkillId}
                >
                  <option value="">选择…</option>
                  {packageSkills.map((skill) => (
                    <option key={skill.id} value={skill.id}>
                      {skill.name}
                      {skill.has_scripts ? " · scripts" : ""}
                    </option>
                  ))}
                </select>
              </Label>
              <Label>
                脚本路径
                <Input
                  className="mt-2 font-mono text-xs"
                  onChange={(event) => setScriptPath(event.currentTarget.value)}
                  value={scriptPath}
                />
              </Label>
              <Button
                disabled={!runSkillId || sandboxRun.isPending}
                onClick={() => sandboxRun.mutate()}
                size="sm"
              >
                <Play className="size-4" />
                在沙箱运行
              </Button>
              {runOutput ? (
                <pre className="max-h-56 overflow-auto rounded-lg bg-muted p-3 text-[10px]">
                  {runOutput}
                </pre>
              ) : null}
            </div>
          </Surface>
        </div>
      </details>
    </div>
  );
}

function ManualImportDialog({ onImported }: { onImported?: () => void }) {
  const [open, setOpen] = useState(false);
  const [skillKey, setSkillKey] = useState("my-custom-skill");
  const [name, setName] = useState("My custom skill");
  const [extraPath, setExtraPath] = useState("scripts/hello.py");
  const [extraContent, setExtraContent] = useState(
    'print("hello from manual skill")\n',
  );
  const [includeExtra, setIncludeExtra] = useState(false);
  const [skillMd, setSkillMd] = useState(DEFAULT_SKILL_MD_TEMPLATE);

  const importMutation = useMutation({
    mutationFn: importSkillManual,
    onSuccess: (skill) => {
      toast.success(`已导入「${skill.name}」，请授权后使用`);
      setOpen(false);
      onImported?.();
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <FilePlus2 className="size-4" />
          手动导入
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>手动编辑导入 Skill 包</DialogTitle>
          <DialogDescription>
            基于 Agent Skill 标准模板（触发条件 / 正文 / 步骤）。导入为工作区副本，不在宿主执行。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
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
        </div>
        <DialogFooter>
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
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArchiveRestore,
  ArrowRight,
  Building2,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  Eye,
  FileJson,
  HardDrive,
  History,
  KeyRound,
  MessageSquareText,
  Moon,
  Play,
  RefreshCcw,
  RotateCcw,
  Search,
  Settings2,
  ShieldAlert,
  Sun,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  commitMigration,
  deleteAuditEvent,
  deleteAuditEvents,
  downloadFullBackup,
  discoverProviderModels,
  exportWorkspace,
  getAccountDeletionImpact,
  getMigration,
  listAuditEvents,
  listMigrationAdapters,
  listMigrations,
  listProviders,
  listSettings,
  listWorkspaces,
  preflightMigration,
  rollbackMigration,
  restoreFullBackup,
  startMigration,
  updateSetting,
} from "@/api";
import { authStore } from "@/api/auth-store";
import { useAuth } from "@/features/auth/auth-context-value";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  areChatSuggestedPromptsEnabled,
  CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
  CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
  CHAT_SUGGESTED_PROMPTS_SETTING_KEY,
  readChatFeatureModelSetting,
} from "@/lib/workspace-settings";
import { cn } from "@/lib/utils";
import { DatabaseConfigurationSheet } from "@/features/settings/database-configuration-sheet";
import type { AuditEvent } from "@/types/audit";
import type {
  MigrationDatabaseKind,
  MigrationJob,
  MigrationResourceKind,
} from "@/types/migrations";
import type { Provider, ProviderModel } from "@/types/providers";
import type { WorkspaceSetting } from "@/types/settings";

const AUDIT_PAGE_SIZE = 20;
const MODEL_PROVIDER_TYPES = new Set([
  "openai_responses",
  "openai_compatible_chat",
  "deepseek_chat",
  "anthropic_messages",
]);

const migrationTargetOptions: Record<
  MigrationResourceKind,
  Array<{ value: string; label: string }>
> = {
  database: [
    { value: "postgresql", label: "PostgreSQL" },
    { value: "mysql", label: "MySQL" },
  ],
  object_storage: [{ value: "minio", label: "MinIO" }],
};

const adapterCapabilityLabels: Record<string, string> = {
  database: "数据库",
  queue: "队列",
  object_storage: "对象存储",
};

const adapterProviderLabels: Record<string, string> = {
  sqlite: "SQLite",
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  in_process: "进程内",
  redis: "Redis",
  local: "本地文件",
  minio: "MinIO",
};

const adapterDetailLabels: Record<string, string> = {
  reason: "原因",
  transaction: "事务",
  round_trip: "往返读写",
  durability: "持久性",
  protocol: "协议",
  dialect: "方言",
  driver: "驱动",
  ping: "Ping",
  bucket_verified: "Bucket 已验证",
};

const adapterDetailValueLabels: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  missing_configuration: "缺少配置",
  driver_missing: "缺少驱动",
  connection_failed: "连接失败",
};

function providerCapabilityString(provider: Provider | undefined, key: string) {
  const value = provider?.capabilities[key];
  return typeof value === "string" ? value.trim() : "";
}

function featureModelOptions(
  provider: Provider | undefined,
  discovered: ProviderModel[] | undefined,
) {
  if (!provider) return [] as ProviderModel[];
  const byId = new Map((discovered ?? []).map((model) => [model.id, model]));
  const configured = providerCapabilityString(provider, "default_model");
  if (configured && !byId.has(configured)) {
    byId.set(configured, {
      id: configured,
      roles: ["llm"],
      streaming: true,
      remote: true,
    });
  }
  return [...byId.values()];
}

function featureModelValue(providerId: string | null, modelId: string | null) {
  if (!providerId || !modelId) return "default";
  return `${providerId}::${modelId}`;
}

function parseFeatureModelValue(value: string): {
  provider_id: string | null;
  model_id: string | null;
} {
  if (!value || value === "default") {
    return { provider_id: null, model_id: null };
  }
  const [providerId, modelId] = value.split("::");
  if (!providerId || !modelId) {
    return { provider_id: null, model_id: null };
  }
  return { provider_id: providerId, model_id: modelId };
}

/** Session key used to group related audit events for the same conversation. */
function auditSessionKey(event: AuditEvent): string {
  const details = event.details ?? {};
  const candidates = [
    details.session_id,
    details.chat_session_id,
    details.sandbox_session_id,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  if (event.resource_type === "session" || event.resource_type === "chat_session") {
    return event.resource_id;
  }
  if (event.resource_type === "sandbox_session") {
    return event.resource_id;
  }
  // Trace groups concurrent side-effects that share the same request chain.
  if (event.trace_id) return `trace:${event.trace_id}`;
  return `event:${event.id}`;
}

function auditSessionLabel(sessionKey: string, events: AuditEvent[]): string {
  if (sessionKey.startsWith("trace:")) {
    return `Trace ${sessionKey.slice(6, 18)}`;
  }
  if (sessionKey.startsWith("event:")) {
    return "独立事件";
  }
  const sample = events[0];
  const details = sample?.details ?? {};
  if (typeof details.session_title === "string" && details.session_title.trim()) {
    return details.session_title.trim();
  }
  if (typeof details.title === "string" && details.title.trim()) {
    return details.title.trim();
  }
  return `会话 ${sessionKey.slice(0, 12)}`;
}

/** Human-readable impact of an audit action (what changed / was attempted). */
function auditImpact(event: AuditEvent): { label: string; tone: "neutral" | "write" | "read" | "danger" | "auth" } {
  const action = event.action;
  const details = event.details ?? {};

  if (action.endsWith(".delete") || action.includes("delete") || action.includes("deleted")) {
    return { label: "删除", tone: "danger" };
  }
  if (action.includes("blocked") || event.outcome === "blocked") {
    return { label: "权限阻断", tone: "auth" };
  }
  if (action.includes("failed") || event.outcome === "failure" || event.outcome === "failed") {
    return { label: "执行失败", tone: "danger" };
  }
  if (
    action.includes("create") ||
    action.includes("write") ||
    action.includes("written") ||
    action.includes("update") ||
    action.includes("commit") ||
    action.includes("confirm") ||
    action.includes("assign") ||
    action.includes("link") ||
    action.includes("export") ||
    action.includes("migrate") ||
    action.includes("completed") ||
    action.includes("enabled") ||
    action.includes("disabled") ||
    action.includes("rotate")
  ) {
    const target =
      typeof details.path === "string"
        ? `写入 ${details.path}`
        : action.includes("settings")
          ? "更新设置"
          : action.includes("export")
            ? "导出数据"
            : "写入变更";
    return { label: target, tone: "write" };
  }
  if (
    action.includes("read") ||
    action.includes("list") ||
    action.includes("listed") ||
    action.includes("query") ||
    action.includes("preview") ||
    action.includes("stream") ||
    action.includes("invoke")
  ) {
    return { label: "读取/调用", tone: "read" };
  }
  if (action.includes("login") || action.includes("auth") || action.includes("permission")) {
    return { label: "身份/权限", tone: "auth" };
  }
  return { label: "操作记录", tone: "neutral" };
}

function auditImpactSummary(events: AuditEvent[]): string {
  const writes = events.filter((e) => auditImpact(e).tone === "write").length;
  const dangers = events.filter((e) => auditImpact(e).tone === "danger").length;
  const blocked = events.filter((e) => auditImpact(e).tone === "auth").length;
  const failures = events.filter(
    (e) => e.outcome === "failure" || e.outcome === "failed",
  ).length;
  const parts: string[] = [];
  if (writes) parts.push(`${writes} 次写入`);
  if (dangers) parts.push(`${dangers} 次删除/危险`);
  if (blocked) parts.push(`${blocked} 次权限`);
  if (failures) parts.push(`${failures} 次失败`);
  if (!parts.length) parts.push(`${events.length} 条记录`);
  return parts.join(" · ");
}

const impactToneClass: Record<string, string> = {
  write:
    "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300",
  read: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300",
  danger:
    "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
  auth: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300",
  neutral: "border-border bg-muted text-muted-foreground",
};

const migrationStates = [
  "PREFLIGHT",
  "QUIESCING",
  "SOURCE_FROZEN",
  "SNAPSHOTTED",
  "TARGET_PREPARED",
  "COPYING_DATABASE",
  "COPYING_FILES",
  "VERIFYING_CANONICAL",
  "REBUILDING_DERIVED",
  "VERIFYING_DERIVED",
  "CUTOVER_READ_ONLY",
  "COMMITTED",
  "ROLLED_BACK_TO_SOURCE",
];

/** Human-readable milestone labels for the compact progress strip. */
const migrationMilestoneLabels: Record<string, string> = {
  PREFLIGHT: "预检",
  QUIESCING: "静默",
  SOURCE_FROZEN: "源冻结",
  SNAPSHOTTED: "快照",
  TARGET_PREPARED: "目标就绪",
  COPYING_DATABASE: "复制库",
  COPYING_FILES: "复制文件",
  VERIFYING_CANONICAL: "主数据校验",
  REBUILDING_DERIVED: "重建派生",
  VERIFYING_DERIVED: "派生校验",
  CUTOVER_READ_ONLY: "只读切换",
  COMMITTED: "已提交",
  ROLLED_BACK_TO_SOURCE: "已回滚",
};

const checkKeyLabels: Record<string, string> = {
  source_integrity: "源库完整性",
  source_readable: "源端可读",
  target_adapter: "目标适配器",
  target_empty: "目标为空",
  maintenance_window: "维护窗口",
  dual_write: "双写策略",
};

const checkStatusLabels: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  missing: "缺失",
  blocked: "阻断",
};

type MigrationCheckItem = {
  key: string;
  status: string;
  details?: Record<string, unknown>;
};

type ParsedMigrationReport = {
  checks: MigrationCheckItem[];
  flags: Array<{ key: string; label: string; value: boolean }>;
  verification: Record<string, unknown> | null;
  extras: Array<{ key: string; value: unknown }>;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseMigrationReport(report: Record<string, unknown> | undefined): ParsedMigrationReport {
  if (!report) {
    return { checks: [], flags: [], verification: null, extras: [] };
  }

  const checks: MigrationCheckItem[] = [];
  const rawChecks = report.checks;
  if (Array.isArray(rawChecks)) {
    for (const item of rawChecks) {
      if (!isRecord(item) || typeof item.key !== "string") continue;
      checks.push({
        key: item.key,
        status: typeof item.status === "string" ? item.status : "unknown",
        details: isRecord(item.details) ? item.details : undefined,
      });
    }
  }

  const flags: Array<{ key: string; label: string; value: boolean }> = [];
  if (typeof report.ready === "boolean") {
    flags.push({ key: "ready", label: "预检就绪", value: report.ready });
  }
  if (typeof report.data_copied === "boolean") {
    flags.push({ key: "data_copied", label: "数据已复制", value: report.data_copied });
  }

  const verification = isRecord(report.verification) ? report.verification : null;

  const reserved = new Set(["checks", "ready", "data_copied", "verification"]);
  const extras = Object.entries(report)
    .filter(([key]) => !reserved.has(key))
    .map(([key, value]) => ({ key, value }));

  return { checks, flags, verification, extras };
}

function formatCheckDetails(details: Record<string, unknown> | undefined): string | null {
  if (!details || Object.keys(details).length === 0) return null;
  return Object.entries(details)
    .map(([key, value]) => {
      if (typeof value === "boolean") return `${key}: ${value ? "是" : "否"}`;
      if (value === null || value === undefined) return `${key}: —`;
      if (typeof value === "object") return `${key}: ${JSON.stringify(value)}`;
      return `${key}: ${String(value)}`;
    })
    .join(" · ");
}

function shortJobId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

function migrationPhase(job: MigrationJob | undefined): string {
  if (!job) return "尚未创建预检";
  switch (job.status) {
    case "PREFLIGHT":
      return "预检通过：源端仍可写，尚未开始复制";
    case "preflight_blocked":
      return "预检被阻断：未复制任何业务数据";
    case "CUTOVER_READ_ONLY":
      return "已到达只读切换点：复制和校验完成，尚未提交";
    case "COMMITTED":
      return "已提交：目标端成为活动事实源";
    case "ROLLED_BACK_TO_SOURCE":
      return "已回滚：源端重新成为唯一写入源";
    case "FAILED_SAFE":
      return "失败保护：源端仍是唯一写入源";
    default:
      return job.maintenance_active
        ? "执行中：维护窗口已锁定工作区写入"
        : `当前状态：${job.status}`;
  }
}

function migrationStateIndex(job: MigrationJob | undefined): number {
  if (!job) return -1;
  return migrationStates.indexOf(job.status);
}

function CheckStatusIcon({ status }: { status: string }) {
  if (status === "passed") {
    return <Check className="size-4 shrink-0 text-primary" />;
  }
  if (status === "failed" || status === "missing" || status === "blocked") {
    return <X className="size-4 shrink-0 text-destructive" />;
  }
  return <AlertTriangle className="size-4 shrink-0 text-amber-500" />;
}

function FlagStatusIcon({ value }: { value: boolean }) {
  return value ? (
    <Check className="size-4 shrink-0 text-primary" />
  ) : (
    <AlertTriangle className="size-4 shrink-0 text-amber-500" />
  );
}

export function MigrationPage() {
  const queryClient = useQueryClient();
  const [resourceKind, setResourceKind] =
    useState<MigrationResourceKind>("database");
  const [target, setTarget] = useState("postgresql");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [commitOpen, setCommitOpen] = useState(false);
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [showAllMilestones, setShowAllMilestones] = useState(false);
  const [databaseConfigurationOpen, setDatabaseConfigurationOpen] =
    useState(false);
  const [databaseConfigurationKind, setDatabaseConfigurationKind] =
    useState<MigrationDatabaseKind>("postgresql");
  const migrations = useQuery({
    queryKey: ["migrations"],
    queryFn: listMigrations,
  });
  const adapters = useQuery({
    queryKey: ["migration-adapters"],
    queryFn: listMigrationAdapters,
  });
  const activeJobId = selectedJobId ?? migrations.data?.[0]?.id;
  const selectedJob = useQuery({
    queryKey: ["migration", activeJobId],
    queryFn: () => getMigration(activeJobId ?? ""),
    enabled: Boolean(activeJobId),
  });
  const refreshMigrationData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["migrations"] }),
      queryClient.invalidateQueries({ queryKey: ["migration", activeJobId] }),
      queryClient.invalidateQueries({ queryKey: ["migration-adapters"] }),
    ]);
  };
  const preflight = useMutation({
    mutationFn: () =>
      preflightMigration({
        source_kind: resourceKind === "database" ? "sqlite" : "local",
        target_kind: target,
        resource_kind: resourceKind,
      }),
    onError: (error) => toast.error(error.message),
    onSuccess: async (job) => {
      setSelectedJobId(job.id);
      toast.success("预检完成，未复制任何数据");
      await refreshMigrationData();
    },
  });
  const start = useMutation({
    mutationFn: startMigration,
    onError: (error) => toast.error(error.message),
    onSuccess: async (job) => {
      toast.success(
        job.status === "CUTOVER_READ_ONLY"
          ? "复制与校验完成，已进入只读切换点，尚未提交"
          : `迁移执行完成：${job.status}`,
      );
      await refreshMigrationData();
    },
  });
  const commit = useMutation({
    mutationFn: commitMigration,
    onError: (error) => toast.error(error.message),
    onSuccess: async () => {
      setCommitOpen(false);
      toast.success("已提交切换；后续回退需要显式反向迁移");
      await refreshMigrationData();
    },
  });
  const rollback = useMutation({
    mutationFn: rollbackMigration,
    onError: (error) => toast.error(error.message),
    onSuccess: async () => {
      setRollbackOpen(false);
      toast.success("已安全切回源端，目标端未开放写入");
      await refreshMigrationData();
    },
  });
  const fullBackup = useMutation({
    mutationFn: downloadFullBackup,
    onError: (error) => toast.error(error.message),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `learngraph-full-backup-${authStore.getWorkspaceId() ?? "workspace"}.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success("全盘业务数据备份已生成");
    },
  });
  const restore = useMutation({
    mutationFn: restoreFullBackup,
    onError: (error) => toast.error(error.message),
    onSuccess: async (result) => {
      setRestoreOpen(false);
      setRestoreFile(null);
      toast.success(`恢复完成：${result.records} 条记录、${result.files} 个文件`);
      await refreshMigrationData();
    },
  });
  if (migrations.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (migrations.isError)
    return (
      <PageFrame>
        <ErrorState message={migrations.error.message} />
      </PageFrame>
    );
  const migrationJobs = migrations.data ?? [];
  const job = selectedJob.data ?? migrationJobs.find((item) => item.id === activeJobId);
  const currentStateIndex = migrationStateIndex(job);
  const parsedReport = parseMigrationReport(job?.report);
  const canStart = job?.status === "PREFLIGHT" && !start.isPending;
  const canCommit =
    job?.status === "CUTOVER_READ_ONLY" &&
    job.resource_kind === "object_storage" &&
    !commit.isPending;
  const canRollback =
    Boolean(job?.can_rollback) &&
    ["CUTOVER_READ_ONLY", "FAILED_SAFE"].includes(job?.status ?? "") &&
    !rollback.isPending;
  const databaseCommitBlocked =
    job?.status === "CUTOVER_READ_ONLY" && job.resource_kind === "database";
  const targetLabel =
    resourceKind === "database" ? "数据库目标" : "对象存储目标";
  const passedCount = parsedReport.checks.filter((c) => c.status === "passed").length;
  const totalChecks = parsedReport.checks.length;
  const backupPanel = (
    <Surface className="border-primary/20 bg-primary/[0.03] p-5">
      <SectionHeading
        description="包含当前工作区的数据库记录、对象存储文件和本地 Memory 文件；凭据、登录会话和恢复密钥永不进入备份。"
        title="全盘备份与数据恢复"
      />
      <div className="mt-5 grid gap-3 lg:grid-cols-2">
        <div className="flex items-start gap-3 rounded-xl border bg-background/60 p-4">
          <Download className="mt-0.5 size-5 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">生成全盘业务数据备份</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              下载一个可校验的 ZIP；每张表、每个文件和 Memory 文档都带有清单与 SHA-256。
            </p>
            <Button
              className="mt-3"
              disabled={fullBackup.isPending}
              onClick={() => fullBackup.mutate()}
              size="sm"
              variant="outline"
            >
              <Download className="size-4" />
              {fullBackup.isPending ? "正在生成…" : "下载全盘备份"}
            </Button>
          </div>
        </div>
        <div className="flex items-start gap-3 rounded-xl border bg-background/60 p-4">
          <ArchiveRestore className="mt-0.5 size-5 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">从备份恢复数据</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              恢复在维护窗口内执行，采用合并恢复，不删除当前数据；导入前会校验格式、路径和文件完整性。
            </p>
            <Input
              accept=".zip,application/zip"
              className="mt-3 max-w-md"
              onChange={(event) => setRestoreFile(event.target.files?.[0] ?? null)}
              type="file"
            />
            <Button
              className="mt-3"
              disabled={!restoreFile || restore.isPending}
              onClick={() => setRestoreOpen(true)}
              size="sm"
              variant="outline"
            >
              <Upload className="size-4" />
              {restore.isPending ? "正在恢复…" : "校验并恢复"}
            </Button>
          </div>
        </div>
      </div>
    </Surface>
  );
  return (
    <PageFrame>
      {backupPanel}
      <PageIntro
        description="数据库与对象存储只在维护窗口切换；任何时刻只允许一个活动事实源，不做静默双写。"
        eyebrow="Storage migration"
        title="数据库与文件存储迁移向导"
      />
      <Surface className="p-5">
        <div className="flex flex-wrap items-center gap-3">
          <Select
            onValueChange={(value) => {
              const nextResourceKind = value as MigrationResourceKind;
              setResourceKind(nextResourceKind);
              setTarget(
                migrationTargetOptions[nextResourceKind][0]?.value ?? "",
              );
            }}
            value={resourceKind}
          >
            <SelectTrigger aria-label="迁移资源类型" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="database">数据库</SelectItem>
              <SelectItem value="object_storage">对象存储</SelectItem>
            </SelectContent>
          </Select>
          <div className="flex items-center gap-2 text-lg font-semibold">
            {resourceKind === "database" ? (
              <Database className="size-5 text-primary" />
            ) : (
              <HardDrive className="size-5 text-primary" />
            )}
            {resourceKind === "database" ? "SQLite" : "本地文件"}
            <ArrowRight className="size-4 text-muted-foreground" />
            <Select onValueChange={setTarget} value={target}>
              <SelectTrigger className="w-36">
                <SelectValue placeholder={targetLabel} />
              </SelectTrigger>
              <SelectContent>
                {migrationTargetOptions[resourceKind].map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {resourceKind === "database" ? (
              <Button
                onClick={() => {
                  setDatabaseConfigurationKind(
                    target as MigrationDatabaseKind,
                  );
                  setDatabaseConfigurationOpen(true);
                }}
                size="sm"
                variant="outline"
              >
                <Settings2 className="size-4" />
                配置连接
              </Button>
            ) : null}
          </div>
        </div>
        <p className="mt-3 text-sm text-muted-foreground">
          {resourceKind === "database"
            ? "源端为 SQLite；目标必须选择不同的数据库适配器。外部适配器即使探测可用，也会在执行器未启用时明确阻断。"
            : "源端为本地文件；目标必须选择不同的对象存储适配器。提交前会校验文件完整性与活动 Binding。"}
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-1.5">
          {(showAllMilestones
            ? migrationStates
            : migrationStates.filter((state, index) => {
                const compactKeys = new Set([
                  "PREFLIGHT",
                  "COPYING_DATABASE",
                  "COPYING_FILES",
                  "VERIFYING_CANONICAL",
                  "CUTOVER_READ_ONLY",
                  "COMMITTED",
                  "ROLLED_BACK_TO_SOURCE",
                ]);
                return (
                  compactKeys.has(state) ||
                  state === job?.status ||
                  (currentStateIndex >= 0 && index === currentStateIndex)
                );
              })
          ).map((state) => {
            const index = migrationStates.indexOf(state);
            const isActive = currentStateIndex >= 0 && index === currentStateIndex;
            const isDone = currentStateIndex >= 0 && index < currentStateIndex;
            return (
              <Badge
                className={cn(
                  "text-[10px]",
                  isActive && "border-primary/30 bg-primary/10 text-primary",
                  isDone && "border-primary/15 bg-primary/5 text-primary/80",
                  !isActive && !isDone && "text-muted-foreground",
                )}
                key={state}
                title={state}
                variant="outline"
              >
                {migrationMilestoneLabels[state] ?? state}
              </Badge>
            );
          })}
          <Button
            className="h-6 px-2 text-[10px] text-muted-foreground"
            onClick={() => setShowAllMilestones((v) => !v)}
            size="sm"
            variant="ghost"
          >
            {showAllMilestones ? "收起阶段" : "全部阶段"}
          </Button>
        </div>
      </Surface>
      <div className="grid gap-5 lg:grid-cols-[1.2fr_0.8fr]">
        <Surface className="p-5">
          <SectionHeading
            action={
              totalChecks > 0 ? (
                <Badge className="font-normal" variant="outline">
                  {passedCount}/{totalChecks} 通过
                </Badge>
              ) : null
            }
            description={migrationPhase(job)}
            title="迁移检查清单"
          />
          <div className="mt-5 space-y-2">
            {parsedReport.checks.map((check) => {
              const detailsText = formatCheckDetails(check.details);
              return (
                <div
                  className="flex items-start gap-3 rounded-xl border bg-muted/15 px-3 py-2.5"
                  key={check.key}
                >
                  <div className="mt-0.5">
                    <CheckStatusIcon status={check.status} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {checkKeyLabels[check.key] ?? check.key}
                      </span>
                      <Badge
                        className={cn(
                          "text-[10px] font-normal",
                          check.status === "passed" &&
                            "border-primary/20 bg-primary/10 text-primary",
                          (check.status === "failed" ||
                            check.status === "missing" ||
                            check.status === "blocked") &&
                            "border-destructive/20 bg-destructive/10 text-destructive",
                        )}
                        variant="outline"
                      >
                        {checkStatusLabels[check.status] ?? check.status}
                      </Badge>
                    </div>
                    {detailsText ? (
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        {detailsText}
                      </p>
                    ) : null}
                  </div>
                </div>
              );
            })}
            {parsedReport.flags.map((flag) => (
              <div
                className="flex items-center gap-3 rounded-xl border bg-muted/15 px-3 py-2.5"
                key={flag.key}
              >
                <FlagStatusIcon value={flag.value} />
                <span className="text-sm font-medium">{flag.label}</span>
                <Badge
                  className={cn(
                    "ml-auto text-[10px] font-normal",
                    flag.value
                      ? "border-primary/20 bg-primary/10 text-primary"
                      : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300",
                  )}
                  variant="outline"
                >
                  {flag.value ? "是" : "否"}
                </Badge>
              </div>
            ))}
            {parsedReport.verification ? (
              <div className="rounded-xl border bg-muted/15 px-3 py-2.5">
                <p className="text-sm font-medium">校验结果</p>
                <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                  {Object.entries(parsedReport.verification).map(([key, value]) => (
                    <div className="flex items-baseline justify-between gap-2 text-xs" key={key}>
                      <span className="text-muted-foreground">{key}</span>
                      <span className="font-medium tabular-nums">
                        {typeof value === "boolean"
                          ? value
                            ? "是"
                            : "否"
                          : typeof value === "object"
                            ? JSON.stringify(value)
                            : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {parsedReport.extras.length > 0 ? (
              <div className="rounded-xl border border-dashed px-3 py-2.5">
                <p className="text-xs font-medium text-muted-foreground">其他报告字段</p>
                <div className="mt-2 space-y-1">
                  {parsedReport.extras.map(({ key, value }) => (
                    <div className="flex items-baseline justify-between gap-2 text-xs" key={key}>
                      <span className="text-muted-foreground">{key}</span>
                      <span className="max-w-[60%] truncate font-mono text-[11px]">
                        {typeof value === "object" ? JSON.stringify(value) : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {parsedReport.checks.length === 0 &&
            parsedReport.flags.length === 0 &&
            !parsedReport.verification ? (
              <p className="text-sm text-muted-foreground">尚无服务端预检报告。</p>
            ) : null}
          </div>
          <div className="mt-8 flex flex-wrap gap-2">
            <Button
              disabled={preflight.isPending}
              onClick={() => preflight.mutate()}
              variant="outline"
            >
              <RefreshCcw className="size-4" />
              {preflight.isPending ? "预检中…" : "重新预检"}
            </Button>
            <Button
              disabled={!canStart}
              onClick={() => job && start.mutate(job.id)}
            >
              <Play className="size-4" />
              开始复制
            </Button>
            <Button
              disabled={!canCommit}
              onClick={() => setCommitOpen(true)}
              variant="default"
            >
              <Check className="size-4" />
              {commit.isPending ? "正在提交…" : "提交切换"}
            </Button>
            <Button
              disabled={!canRollback}
              onClick={() => setRollbackOpen(true)}
              variant="outline"
            >
              <RotateCcw className="size-4" />
              回滚
            </Button>
          </div>
          {databaseCommitBlocked ? (
            <p className="mt-3 text-xs leading-5 text-amber-700 dark:text-amber-300">
              数据库副本已通过只读校验，但运行时 SQLAlchemy 尚不能重新绑定到目标库；服务端会保持维护锁并拒绝 commit，不能把该状态称为已切换。
            </p>
          ) : null}
        </Surface>
        <Surface className="p-5">
          <SectionHeading title="迁移原则" />
          <div className="mt-4 space-y-3">
            {[
              "任一时刻只有一个业务数据库和一个对象存储接受写入。",
              "数据库与对象存储分别迁移、分别提交，不把局部成功伪装成整体完成。",
              "FTS、向量、图布局、缩略图等派生数据迁移后重建。",
              "COMMITTED 前可切回源；目标开放新写入后只能反向迁移。",
            ].map((item) => (
              <div
                className="rounded-xl border bg-muted/20 p-3 text-sm leading-6"
                key={item}
              >
                {item}
              </div>
            ))}
          </div>
        </Surface>
      </div>
      {job ? (
        <Surface className="p-5">
          <SectionHeading
            action={
              <div className="flex items-center gap-2">
                <StatePill status={job.status} />
                <Button
                  aria-label="刷新所选迁移任务"
                  disabled={selectedJob.isFetching}
                  onClick={() => void selectedJob.refetch()}
                  size="icon-xs"
                  variant="ghost"
                >
                  <RefreshCcw
                    className={selectedJob.isFetching ? "size-3 animate-spin" : "size-3"}
                  />
                </Button>
              </div>
            }
            title="所选迁移任务"
          />
          <div className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["任务", shortJobId(job.id), job.id],
              [
                "资源",
                job.resource_kind === "database" ? "数据库" : "对象存储",
                `${job.source_kind} → ${job.target_kind}`,
              ],
              ["维护窗口", job.maintenance_active ? "已锁定" : "未锁定", undefined],
              [
                "回滚边界",
                job.reverse_migration_required
                  ? "需反向迁移"
                  : job.can_rollback
                    ? "可直接回滚"
                    : "不可回滚",
                undefined,
              ],
            ].map(([label, value, hint]) => (
              <div className="rounded-xl border p-3" key={label}>
                <p className="text-muted-foreground">{label}</p>
                <p className="mt-1 text-sm font-medium" title={typeof hint === "string" ? hint : undefined}>
                  {value}
                </p>
                {hint && hint !== value ? (
                  <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground" title={hint}>
                    {hint}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
          <Collapsible className="group mt-5">
            <CollapsibleTrigger className="flex w-full items-center justify-between rounded-xl border px-3 py-2.5 text-left text-sm font-medium hover:bg-muted/30">
              <span>
                服务端 Checkpoint
                {job.checkpoints.length ? (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    {job.checkpoints.length} 条
                  </span>
                ) : null}
              </span>
              <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="mt-2 divide-y rounded-xl border">
                {job.checkpoints.map((checkpoint) => {
                  const metricsText =
                    checkpoint.error_message ??
                    (checkpoint.metrics && Object.keys(checkpoint.metrics).length
                      ? Object.entries(checkpoint.metrics)
                          .map(([k, v]) =>
                            typeof v === "object" ? `${k}=…` : `${k}=${String(v)}`,
                          )
                          .join(" · ")
                      : null);
                  return (
                    <div
                      className="flex flex-col gap-2 p-3 text-xs sm:flex-row sm:items-center"
                      key={checkpoint.sequence}
                    >
                      <span className="w-8 font-mono text-muted-foreground">
                        #{checkpoint.sequence}
                      </span>
                      <span className="min-w-36 font-medium">
                        {migrationMilestoneLabels[checkpoint.state] ?? checkpoint.state}
                      </span>
                      <StatePill status={checkpoint.status} />
                      {metricsText ? (
                        <span className="min-w-0 flex-1 truncate text-muted-foreground" title={metricsText}>
                          {metricsText}
                        </span>
                      ) : null}
                    </div>
                  );
                })}
                {!job.checkpoints.length ? (
                  <p className="p-4 text-xs text-muted-foreground">
                    服务端尚未写入 Checkpoint。
                  </p>
                ) : null}
              </div>
            </CollapsibleContent>
          </Collapsible>
        </Surface>
      ) : null}
      {selectedJob.isError ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          所选迁移任务刷新失败：{selectedJob.error.message}
        </div>
      ) : null}
      <Surface className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b p-5">
          <SectionHeading
            description="配置、驱动与连通性均来自服务端实际探测；可用与不可用状态会明确区分。"
            title="基础设施适配器状态"
          />
          <Button
            disabled={adapters.isFetching}
            onClick={() => void adapters.refetch()}
            size="sm"
            variant="outline"
          >
            <RefreshCcw className={adapters.isFetching ? "size-4 animate-spin" : "size-4"} />
            刷新适配器
          </Button>
        </div>
        {adapters.isError ? (
          <p className="p-5 text-sm text-destructive">{adapters.error.message}</p>
        ) : (
          <div className="divide-y">
            {(adapters.data ?? []).map((adapter) => {
              const detailEntries = Object.entries(adapter.details ?? {});
              return (
                <div
                  className="grid gap-3 p-4 text-xs md:grid-cols-[1.2fr_auto_auto_auto_auto_auto] md:items-center"
                  key={`${adapter.capability}-${adapter.provider_kind}`}
                >
                  <div className="min-w-0">
                    <p className="font-medium">
                      {adapterCapabilityLabels[adapter.capability] ?? adapter.capability} / {adapterProviderLabels[adapter.provider_kind] ?? adapter.provider_kind}
                    </p>
                    {detailEntries.length > 0 ? (
                      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                        {detailEntries.slice(0, 4).map(([key, value]) => (
                          <span key={key}>
                            <span className="text-muted-foreground/80">{adapterDetailLabels[key] ?? key}</span>
                            {": "}
                            <span className="text-foreground/80">
                              {typeof value === "boolean"
                                ? value
                                  ? "是"
                                  : "否"
                                : typeof value === "object"
                                  ? "…"
                                  : adapterDetailValueLabels[String(value)] ?? String(value)}
                            </span>
                          </span>
                        ))}
                        {detailEntries.length > 4 ? (
                          <span className="text-muted-foreground/70">
                            +{detailEntries.length - 4}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  <StatePill status={adapter.status} />
                  <span>配置：{adapter.configured ? "是" : "否"}</span>
                  <span>驱动：{adapter.driver_available ? "是" : "否"}</span>
                  <span>连通：{adapter.connection_verified ? "是" : "否"}</span>
                  {adapter.capability === "database" &&
                  (adapter.provider_kind === "postgresql" ||
                    adapter.provider_kind === "mysql") ? (
                    <Button
                      onClick={() => {
                        setDatabaseConfigurationKind(
                          adapter.provider_kind as MigrationDatabaseKind,
                        );
                        setDatabaseConfigurationOpen(true);
                      }}
                      size="sm"
                      variant="ghost"
                    >
                      <Settings2 className="size-3.5" />
                      {adapter.configured ? "编辑" : "配置"}
                    </Button>
                  ) : (
                    <span aria-hidden="true" />
                  )}
                </div>
              );
            })}
            {adapters.isPending ? <LoadingState /> : null}
          </div>
        )}
      </Surface>
      {migrationJobs.length ? (
        <Surface className="p-5">
          <SectionHeading
            description="选择一项后只刷新该 Job，而不是把列表中的第一项当作当前迁移。"
            title="迁移历史"
          />
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-center">
            <Select onValueChange={setSelectedJobId} value={activeJobId ?? migrationJobs[0]?.id}>
              <SelectTrigger aria-label="所选迁移任务">
                <SelectValue placeholder="选择迁移任务" />
              </SelectTrigger>
              <SelectContent>
                {migrationJobs.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.resource_kind === "database" ? "数据库" : "对象存储"} ·{" "}
                    {item.source_kind} → {item.target_kind} ·{" "}
                    {migrationMilestoneLabels[item.status] ?? item.status} ·{" "}
                    {new Date(item.created_at).toLocaleString()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button onClick={() => void refreshMigrationData()} size="sm" variant="outline">
              <RefreshCcw className="size-4" />刷新列表
            </Button>
          </div>
        </Surface>
      ) : null}
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50/55 p-4 text-xs leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200">
        <ShieldAlert className="mt-0.5 size-4 shrink-0" />
        <span>
          页面只展示服务端返回的迁移状态与报告；未配置执行器或回滚命令时会明确禁用/报错，不生成虚假的完成步骤。
        </span>
      </div>
      <DatabaseConfigurationSheet
        onOpenChange={setDatabaseConfigurationOpen}
        onSaved={refreshMigrationData}
        open={databaseConfigurationOpen}
        providerKind={databaseConfigurationKind}
      />
      <Dialog onOpenChange={setRestoreOpen} open={restoreOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认恢复工作区数据？</DialogTitle>
            <DialogDescription>
              将在维护窗口内校验并合并导入所选 ZIP。当前数据不会被删除；如果备份来自其他工作区，记录会归入当前工作区。
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-xl border bg-muted/20 p-3 text-sm">
            <p className="font-medium">{restoreFile?.name ?? "未选择备份文件"}</p>
            {restoreFile ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {(restoreFile.size / 1024 / 1024).toFixed(2)} MB · ZIP
              </p>
            ) : null}
          </div>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setRestoreOpen(false)} variant="outline">
              取消
            </Button>
            <Button
              disabled={!restoreFile || restore.isPending}
              onClick={() => restoreFile && restore.mutate(restoreFile)}
              variant="destructive"
            >
              {restore.isPending ? "正在恢复…" : "确认恢复"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog onOpenChange={setCommitOpen} open={commitOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>提交对象存储切换？</DialogTitle>
            <DialogDescription>
              这会把已校验副本设为活动 ObjectStorage Binding，并关闭直接回滚。若需回到旧源端，之后必须创建反向迁移。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setCommitOpen(false)} variant="outline">取消</Button>
            <Button
              disabled={!canCommit}
              onClick={() => job && commit.mutate(job.id)}
            >
              {commit.isPending ? "正在提交…" : "确认提交切换"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog onOpenChange={setRollbackOpen} open={rollbackOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>切回迁移源端？</DialogTitle>
            <DialogDescription>
              此操作仅在 COMMITTED 前可用。目标副本会被清理，源端重新接受写入；不需要输入确认文字。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setRollbackOpen(false)} variant="outline">
              取消
            </Button>
            <Button
              disabled={!job || rollback.isPending}
              onClick={() => job && rollback.mutate(job.id)}
              variant="destructive"
            >
              {rollback.isPending ? "正在回滚…" : "确认切回源端"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </PageFrame>
  );
}

type AuditSessionGroup = {
  key: string;
  label: string;
  events: AuditEvent[];
  latestAt: number;
  summary: string;
  failureCount: number;
  writeCount: number;
};

export function AuditPage() {
  const queryClient = useQueryClient();
  const [action, setAction] = useState("");
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<
    { kind: "one"; event: AuditEvent } | { kind: "many"; ids: string[] } | null
  >(null);

  const audit = useQuery({
    queryKey: ["audit", action],
    queryFn: () => listAuditEvents(action ? { action } : {}),
  });

  const removeOne = useMutation({
    mutationFn: (eventId: string) => deleteAuditEvent(eventId),
    onError: (error) => toast.error(error.message),
    onSuccess: async () => {
      toast.success("已删除审计记录");
      setDeleteTarget(null);
      setSelectedIds(new Set());
      await queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const removeMany = useMutation({
    mutationFn: (ids: string[]) => deleteAuditEvents(ids),
    onError: (error) => toast.error(error.message),
    onSuccess: async (result) => {
      const count =
        typeof result.details?.deleted === "number"
          ? result.details.deleted
          : Array.isArray(result.details?.ids)
            ? result.details.ids.length
            : selectedIds.size;
      toast.success(`已删除 ${count} 条审计记录`);
      setDeleteTarget(null);
      setSelectedIds(new Set());
      await queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const sessionGroups = useMemo<AuditSessionGroup[]>(() => {
    const events = audit.data ?? [];
    const map = new Map<string, AuditEvent[]>();
    for (const event of events) {
      const key = auditSessionKey(event);
      const bucket = map.get(key);
      if (bucket) bucket.push(event);
      else map.set(key, [event]);
    }
    return [...map.entries()]
      .map(([key, groupEvents]) => {
        const sorted = [...groupEvents].sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
        return {
          key,
          label: auditSessionLabel(key, sorted),
          events: sorted,
          latestAt: new Date(sorted[0]?.created_at ?? 0).getTime(),
          summary: auditImpactSummary(sorted),
          failureCount: sorted.filter(
            (e) => e.outcome === "failure" || e.outcome === "failed",
          ).length,
          writeCount: sorted.filter((e) => auditImpact(e).tone === "write")
            .length,
        };
      })
      .sort((a, b) => b.latestAt - a.latestAt);
  }, [audit.data]);

  const totalPages = Math.max(1, Math.ceil(sessionGroups.length / AUDIT_PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedGroups = sessionGroups.slice(
    (safePage - 1) * AUDIT_PAGE_SIZE,
    safePage * AUDIT_PAGE_SIZE,
  );

  if (audit.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (audit.isError)
    return (
      <PageFrame>
        <ErrorState message={audit.error.message} />
      </PageFrame>
    );

  const auditEvents = audit.data ?? [];
  const knownActions = [
    ...new Set(auditEvents.map((event) => event.action)),
  ].slice(0, 12);

  function exportAudit() {
    const payload = auditEvents.map((event) => ({
      ...event,
      exported_at: new Date().toISOString(),
    }));
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `learngraph-audit-${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function toggleGroup(key: string) {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function toggleEventSelected(eventId: string, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(eventId);
      else next.delete(eventId);
      return next;
    });
  }

  function toggleGroupSelected(group: AuditSessionGroup, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const event of group.events) {
        if (checked) next.add(event.id);
        else next.delete(event.id);
      }
      return next;
    });
  }

  const pageEventIds = pagedGroups.flatMap((group) =>
    group.events.map((event) => event.id),
  );
  const pageSelectedCount = pageEventIds.filter((id) => selectedIds.has(id)).length;
  const pageAllSelected = pageEventIds.length > 0 && pageSelectedCount === pageEventIds.length;

  function togglePageSelected() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const eventId of pageEventIds) {
        if (pageAllSelected) next.delete(eventId);
        else next.add(eventId);
      }
      return next;
    });
  }

  const deleting =
    removeOne.isPending || removeMany.isPending;

  return (
    <PageFrame>
      <PageIntro
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              aria-pressed={pageAllSelected}
              disabled={!pageEventIds.length || deleting}
              onClick={togglePageSelected}
              size="sm"
              variant="outline"
            >
              <CheckCheck className="size-4" />
              {pageAllSelected ? "取消全选本页" : "全选本页"}
            </Button>
            {selectedIds.size > 0 ? (
              <Button
                disabled={deleting}
                onClick={() =>
                  setDeleteTarget({ kind: "many", ids: [...selectedIds] })
                }
                size="sm"
                variant="destructive"
              >
                <Trash2 className="size-4" />
                删除所选 ({selectedIds.size})
              </Button>
            ) : null}
            <Button
              disabled={!auditEvents.length}
              onClick={exportAudit}
              size="sm"
              variant="outline"
            >
              <Download className="size-4" />
              导出当前结果
            </Button>
          </div>
        }
        description="按会话归集模型、工具、图谱、证据、记忆、文件与迁移等副作用；每条记录标注影响与结果，支持分页与删除。"
        eyebrow="Audit trail"
        title="运行与权限审计"
      />

      <Surface className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b p-5">
          <SectionHeading
            description={`${sessionGroups.length} 个会话组 · ${auditEvents.length} 条事件`}
            title="审计日志"
          />
          <div className="relative">
            <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="w-56 pl-9"
              onChange={(event) => {
                setAction(event.target.value);
                setPage(1);
              }}
              placeholder="按 action 精确过滤"
              value={action}
            />
          </div>
        </div>

        <ScrollArea className="h-[min(62vh,720px)]">
          <div className="divide-y">
            {pagedGroups.map((group) => {
              const isOpen = expanded[group.key] ?? false;
              const groupSelectedCount = group.events.filter((e) =>
                selectedIds.has(e.id),
              ).length;
              const allSelected =
                group.events.length > 0 &&
                groupSelectedCount === group.events.length;
              const someSelected =
                groupSelectedCount > 0 && !allSelected;
              return (
                <div key={group.key}>
                  <div className="flex items-start gap-3 px-5 py-3.5 hover:bg-muted/15">
                    <Checkbox
                      aria-label={`选择会话组 ${group.label}`}
                      checked={
                        allSelected
                          ? true
                          : someSelected
                            ? "indeterminate"
                            : false
                      }
                      className="mt-1"
                      onCheckedChange={(value) =>
                        toggleGroupSelected(group, value === true)
                      }
                    />
                    <button
                      className="min-w-0 flex-1 text-left"
                      onClick={() => toggleGroup(group.key)}
                      type="button"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold tracking-tight">
                          {group.label}
                        </span>
                        <Badge className="font-normal" variant="outline">
                          {group.events.length} 条
                        </Badge>
                        {group.failureCount > 0 ? (
                          <Badge
                            className="border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
                            variant="outline"
                          >
                            {group.failureCount} 失败
                          </Badge>
                        ) : null}
                        {group.writeCount > 0 ? (
                          <Badge
                            className="border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
                            variant="outline"
                          >
                            {group.writeCount} 写入
                          </Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {new Date(group.latestAt).toLocaleString()} ·{" "}
                        {group.summary}
                      </p>
                    </button>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        aria-label={isOpen ? "收起会话组" : "展开会话组"}
                        onClick={() => toggleGroup(group.key)}
                        size="icon-xs"
                        variant="ghost"
                      >
                        <ChevronDown
                          className={cn(
                            "size-3.5 transition-transform",
                            isOpen && "rotate-180",
                          )}
                        />
                      </Button>
                    </div>
                  </div>

                  {isOpen ? (
                    <div className="border-t bg-muted/10">
                      <div className="overflow-x-auto">
                        <table className="w-full min-w-[980px] text-left text-xs">
                          <thead className="bg-muted/35 text-muted-foreground">
                            <tr>
                              <th className="w-10 px-4 py-2" />
                              <th className="px-4 py-2">时间</th>
                              <th className="px-4 py-2">Action</th>
                              <th className="px-4 py-2">影响</th>
                              <th className="px-4 py-2">操作者</th>
                              <th className="px-4 py-2">对象</th>
                              <th className="px-4 py-2">结果</th>
                              <th className="px-4 py-2">Trace</th>
                              <th className="px-4 py-2">操作</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y">
                            {group.events.map((event) => {
                              const impact = auditImpact(event);
                              return (
                                <tr
                                  className="hover:bg-muted/20"
                                  key={event.id}
                                >
                                  <td className="px-4 py-2.5">
                                    <Checkbox
                                      aria-label={`选择 ${event.action}`}
                                      checked={selectedIds.has(event.id)}
                                      onCheckedChange={(value) =>
                                        toggleEventSelected(
                                          event.id,
                                          value === true,
                                        )
                                      }
                                    />
                                  </td>
                                  <td className="px-4 py-2.5 font-mono">
                                    {new Date(
                                      event.created_at,
                                    ).toLocaleTimeString()}
                                  </td>
                                  <td className="px-4 py-2.5 font-mono">
                                    {event.action}
                                  </td>
                                  <td className="px-4 py-2.5">
                                    <Badge
                                      className={cn(
                                        "font-normal",
                                        impactToneClass[impact.tone],
                                      )}
                                      variant="outline"
                                    >
                                      {impact.label}
                                    </Badge>
                                  </td>
                                  <td className="px-4 py-2.5">
                                    {event.actor_id}
                                  </td>
                                  <td className="px-4 py-2.5">
                                    {event.resource_type} /{" "}
                                    {event.resource_id.slice(0, 12)}
                                  </td>
                                  <td className="px-4 py-2.5">
                                    <StatePill status={event.outcome} />
                                  </td>
                                  <td className="px-4 py-2.5 font-mono">
                                    {event.trace_id.slice(0, 12)}
                                  </td>
                                  <td className="px-4 py-2.5">
                                    <div className="flex items-center gap-0.5">
                                      <Button
                                        aria-label={`查看 ${event.action} 详情`}
                                        onClick={() => setSelected(event)}
                                        size="icon-xs"
                                        variant="ghost"
                                      >
                                        <Eye className="size-3" />
                                      </Button>
                                      <Button
                                        aria-label={`删除 ${event.action}`}
                                        disabled={deleting}
                                        onClick={() =>
                                          setDeleteTarget({
                                            kind: "one",
                                            event,
                                          })
                                        }
                                        size="icon-xs"
                                        variant="ghost"
                                      >
                                        <Trash2 className="size-3 text-destructive" />
                                      </Button>
                                    </div>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}
            {!pagedGroups.length ? (
              <p className="p-8 text-center text-sm text-muted-foreground">
                当前筛选条件下没有审计事件。
              </p>
            ) : null}
          </div>
        </ScrollArea>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t px-5 py-3">
          <p className="text-xs text-muted-foreground">
            第 {safePage} / {totalPages} 页 · 每页 {AUDIT_PAGE_SIZE} 个会话组
          </p>
          <div className="flex items-center gap-1">
            <Button
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              size="sm"
              variant="outline"
            >
              <ChevronLeft className="size-4" />
              上一页
            </Button>
            <Button
              disabled={safePage >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              size="sm"
              variant="outline"
            >
              下一页
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      </Surface>

      {knownActions.length ? (
        <Surface className="border-blue-200 bg-blue-50/35 p-5 dark:border-blue-900 dark:bg-blue-950/20">
          <SectionHeading title="当前结果中的 Action" />
          <div className="mt-4 flex flex-wrap gap-2">
            {knownActions.map((item) => (
              <Button
                key={item}
                onClick={() => {
                  setAction(item);
                  setPage(1);
                }}
                size="xs"
                variant={action === item ? "default" : "outline"}
              >
                {item}
              </Button>
            ))}
          </div>
        </Surface>
      ) : null}

      <Dialog
        onOpenChange={(open) => !open && setSelected(null)}
        open={Boolean(selected)}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{selected?.action ?? "审计详情"}</DialogTitle>
            <DialogDescription>
              {selected
                ? `${selected.resource_type} / ${selected.resource_id} · trace ${selected.trace_id}`
                : ""}
            </DialogDescription>
          </DialogHeader>
          {selected ? (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge
                  className={cn(
                    "font-normal",
                    impactToneClass[auditImpact(selected).tone],
                  )}
                  variant="outline"
                >
                  影响：{auditImpact(selected).label}
                </Badge>
                <StatePill status={selected.outcome} />
                <Badge className="font-normal" variant="outline">
                  操作者 {selected.actor_id}
                </Badge>
              </div>
              <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-xl bg-muted p-4 font-mono text-xs leading-6">
                {JSON.stringify(selected.details ?? {}, null, 2)}
              </pre>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      <AlertDialog
        onOpenChange={(open) => !open && !deleting && setDeleteTarget(null)}
        open={Boolean(deleteTarget)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {deleteTarget?.kind === "many"
                ? `删除 ${deleteTarget.ids.length} 条审计记录？`
                : "删除这条审计记录？"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.kind === "one"
                ? `将永久删除 ${deleteTarget.event.action}（${deleteTarget.event.resource_type}/${deleteTarget.event.resource_id.slice(0, 12)}）。删除本身会写入一条元审计，原详情不会保留。`
                : "将永久删除所选审计记录。删除本身会写入一条批量元审计，原详情不会保留。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={(event) => {
                event.preventDefault();
                if (!deleteTarget) return;
                if (deleteTarget.kind === "one") {
                  removeOne.mutate(deleteTarget.event.id);
                } else {
                  removeMany.mutate(deleteTarget.ids);
                }
              }}
              variant="destructive"
            >
              {deleting ? "删除中…" : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageFrame>
  );
}

function AccountSecurity({ username }: { username: string }) {
  const auth = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirmation, setNewPasswordConfirmation] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const deletionImpact = useQuery({
    queryKey: ["account-deletion-impact"],
    queryFn: getAccountDeletionImpact,
    enabled: deleteOpen,
  });
  const passwordChange = useMutation({
    mutationFn: () => auth.changePassword(currentPassword, newPassword),
    onError: (error) => toast.error(error.message),
    onSuccess: () => {
      setCurrentPassword("");
      setNewPassword("");
      setNewPasswordConfirmation("");
      toast.success("密码已修改，其他设备上的登录会话已撤销");
    },
  });
  const accountDeletion = useMutation({
    mutationFn: () => auth.deleteAccount(deletePassword, deleteConfirmation),
    onError: (error) => toast.error(error.message),
    onSuccess: () => toast.success("账户已删除"),
  });
  const passwordReady =
    currentPassword.length > 0 &&
    newPassword.length >= 12 &&
    newPassword === newPasswordConfirmation;
  const deletionReady =
    deletionImpact.data?.can_delete === true &&
    deletePassword.length > 0 &&
    deleteConfirmation === username;

  function updateDeleteOpen(open: boolean) {
    if (accountDeletion.isPending) return;
    setDeleteOpen(open);
    if (!open) {
      setDeletePassword("");
      setDeleteConfirmation("");
    }
  }

  return (
    <>
      <Surface className="p-5">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(320px,1.2fr)]">
          <div>
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <KeyRound className="size-5" />
            </div>
            <h2 className="mt-4 text-base font-semibold">修改密码</h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
              新密码至少 12 位，并同时包含字母、数字和足够多的不同字符。修改成功后，当前设备会保持登录，其他设备会退出。
            </p>
          </div>
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (newPassword !== newPasswordConfirmation) {
                toast.error("两次输入的新密码不一致");
                return;
              }
              passwordChange.mutate();
            }}
          >
            <input
              aria-hidden="true"
              autoComplete="username"
              className="sr-only"
              readOnly
              tabIndex={-1}
              value={username}
            />
            <Label className="grid gap-2">
              当前密码
              <Input
                autoComplete="current-password"
                onChange={(event) => setCurrentPassword(event.target.value)}
                required
                type="password"
                value={currentPassword}
              />
            </Label>
            <div className="grid gap-4 sm:grid-cols-2">
              <Label className="grid gap-2">
                新密码
                <Input
                  autoComplete="new-password"
                  minLength={12}
                  onChange={(event) => setNewPassword(event.target.value)}
                  required
                  type="password"
                  value={newPassword}
                />
              </Label>
              <Label className="grid gap-2">
                再次输入新密码
                <Input
                  autoComplete="new-password"
                  minLength={12}
                  onChange={(event) =>
                    setNewPasswordConfirmation(event.target.value)
                  }
                  required
                  type="password"
                  value={newPasswordConfirmation}
                />
              </Label>
            </div>
            {newPasswordConfirmation &&
            newPassword !== newPasswordConfirmation ? (
              <p className="text-xs text-destructive">两次输入的新密码不一致。</p>
            ) : null}
            <Button
              className="w-fit"
              disabled={!passwordReady || passwordChange.isPending}
              type="submit"
            >
              {passwordChange.isPending ? "正在修改…" : "修改密码"}
            </Button>
          </form>
        </div>
      </Surface>

      <Surface className="border-red-200 bg-red-50/45 p-5 dark:border-red-900 dark:bg-red-950/20">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <AlertTriangle className="size-5 shrink-0 text-destructive" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold text-destructive">删除账户</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              删除会立即撤销全部登录和成员权限，并去除账户身份信息。为保留证据与审计完整性，个人工作区中的学习记录不会被伪装成从未发生。
            </p>
          </div>
          <Button onClick={() => setDeleteOpen(true)} variant="destructive">
            <Trash2 className="size-4" />
            删除我的账户
          </Button>
        </div>
      </Surface>

      <AlertDialog onOpenChange={updateDeleteOpen} open={deleteOpen}>
        <AlertDialogContent className="sm:max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>永久删除账户？</AlertDialogTitle>
            <AlertDialogDescription>
              先核对服务端计算的影响范围，再输入当前密码和用户名完成二次确认。
            </AlertDialogDescription>
          </AlertDialogHeader>

          {deletionImpact.isPending ? (
            <LoadingState label="正在检查账户关联…" />
          ) : deletionImpact.isError ? (
            <ErrorState message={deletionImpact.error.message} />
          ) : deletionImpact.data ? (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3 rounded-xl border bg-muted/30 p-3 text-center">
                <div>
                  <p className="text-lg font-semibold">
                    {deletionImpact.data.active_session_count}
                  </p>
                  <p className="text-[11px] text-muted-foreground">登录会话</p>
                </div>
                <div>
                  <p className="text-lg font-semibold">
                    {deletionImpact.data.active_membership_count}
                  </p>
                  <p className="text-[11px] text-muted-foreground">成员关系</p>
                </div>
                <div>
                  <p className="text-lg font-semibold">
                    {deletionImpact.data.personal_workspace_count}
                  </p>
                  <p className="text-[11px] text-muted-foreground">个人工作区</p>
                </div>
              </div>
              {deletionImpact.data.blockers.length > 0 ? (
                <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
                  <p className="font-medium">暂时无法删除</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    {deletionImpact.data.blockers.map((blocker) => (
                      <li key={blocker}>{blocker}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="grid gap-3">
                  <Label className="grid gap-2">
                    当前密码
                    <Input
                      autoComplete="current-password"
                      onChange={(event) => setDeletePassword(event.target.value)}
                      type="password"
                      value={deletePassword}
                    />
                  </Label>
                  <Label className="grid gap-2">
                    输入用户名 <span className="font-mono">{username}</span>
                    <Input
                      autoComplete="off"
                      onChange={(event) =>
                        setDeleteConfirmation(event.target.value)
                      }
                      value={deleteConfirmation}
                    />
                  </Label>
                </div>
              )}
            </div>
          ) : null}

          <AlertDialogFooter>
            <AlertDialogCancel disabled={accountDeletion.isPending}>
              取消
            </AlertDialogCancel>
            <Button
              disabled={!deletionReady || accountDeletion.isPending}
              onClick={() => accountDeletion.mutate()}
              variant="destructive"
            >
              {accountDeletion.isPending ? "正在删除…" : "永久删除账户"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function WorkspaceSettingsPage() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const session = authStore.getSession();
  const settings = useQuery({ queryKey: ["settings"], queryFn: listSettings });
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: listWorkspaces,
  });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });
  const modelProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          MODEL_PROVIDER_TYPES.has(provider.provider_type),
      ),
    [providers.data],
  );
  const discoveredByProvider = useQuery({
    queryKey: [
      "settings-feature-models",
      modelProviders.map((item) => item.id).join(","),
    ],
    queryFn: async () => {
      const entries = await Promise.all(
        modelProviders.map(async (provider) => {
          try {
            const models = await discoverProviderModels(provider.id);
            return [provider.id, models.models] as const;
          } catch {
            return [provider.id, [] as ProviderModel[]] as const;
          }
        }),
      );
      return Object.fromEntries(entries) as Record<string, ProviderModel[]>;
    },
    enabled: modelProviders.length > 0,
    staleTime: 60_000,
  });
  const featureModelChoices = useMemo(() => {
    const choices: Array<{
      value: string;
      label: string;
      providerId: string;
      modelId: string;
    }> = [];
    for (const provider of modelProviders) {
      const models = featureModelOptions(
        provider,
        discoveredByProvider.data?.[provider.id],
      );
      for (const model of models) {
        choices.push({
          value: featureModelValue(provider.id, model.id),
          label: `${provider.display_name} · ${model.id}`,
          providerId: provider.id,
          modelId: model.id,
        });
      }
    }
    return choices;
  }, [discoveredByProvider.data, modelProviders]);
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const save = useMutation({
    mutationFn: (value: unknown) => updateSetting("ui.preferences", value),
    onError: (error) => toast.error(error.message),
    onSuccess: () => {
      toast.success("工作区设置已更新");
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });
  const saveSuggestedPrompts = useMutation({
    mutationFn: (enabled: boolean) =>
      updateSetting(CHAT_SUGGESTED_PROMPTS_SETTING_KEY, { enabled }),
    onError: (error) => toast.error(error.message),
    onSuccess: (setting) => {
      queryClient.setQueryData<WorkspaceSetting[]>(["settings"], (current) => [
        ...(current ?? []).filter((item) => item.key !== setting.key),
        setting,
      ]);
      queryClient.removeQueries({ queryKey: ["suggested-prompts"] });
      toast.success("对话问题提示设置已更新");
    },
  });
  const saveFeatureModel = useMutation({
    mutationFn: ({
      key,
      provider_id,
      model_id,
    }: {
      key: string;
      provider_id: string | null;
      model_id: string | null;
    }) => updateSetting(key, { provider_id, model_id }),
    onError: (error) => toast.error(error.message),
    onSuccess: (setting) => {
      queryClient.setQueryData<WorkspaceSetting[]>(["settings"], (current) => [
        ...(current ?? []).filter((item) => item.key !== setting.key),
        setting,
      ]);
      if (setting.key === CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY) {
        queryClient.removeQueries({ queryKey: ["suggested-prompts"] });
      }
      toast.success("功能模型设置已更新");
    },
  });
  const workspaceExport = useMutation({
    mutationFn: exportWorkspace,
    onError: (error) => toast.error(error.message),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `learngraph-workspace-${auth.workspaceId}.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success("完整工作区导出已生成");
    },
  });
  const current = useMemo(
    () => settings.data?.find((item) => item.key === "ui.preferences")?.value,
    [settings.data],
  );
  // Keep the switch in sync when settings load / refresh from the server.
  useEffect(() => {
    if (!current || typeof current !== "object" || !("theme" in current)) return;
    const isDark = (current as { theme?: unknown }).theme === "dark";
    setDark(isDark);
    document.documentElement.classList.toggle("dark", isDark);
    try {
      window.localStorage.setItem("lg-theme", isDark ? "dark" : "light");
    } catch {
      // Class still applied even if storage is unavailable.
    }
  }, [current]);
  const suggestedPromptsEnabled = areChatSuggestedPromptsEnabled(settings.data);
  const autoTitleModel = useMemo(
    () =>
      readChatFeatureModelSetting(
        settings.data,
        CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
      ),
    [settings.data],
  );
  const suggestedPromptsModel = useMemo(
    () =>
      readChatFeatureModelSetting(
        settings.data,
        CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
      ),
    [settings.data],
  );
  const activeWorkspace = useMemo(
    () =>
      (workspaces.data ?? []).find((item) => item.id === auth.workspaceId) ??
      null,
    [workspaces.data, auth.workspaceId],
  );
  const accountUsername = session?.username ?? auth.username;
  const displayName = session?.displayName ?? auth.username;
  const userId = session?.userId;
  const workspaceKindLabel =
    activeWorkspace?.workspace_kind === "organization" ? "组织工作区" : "个人学习区";
  const isOwner =
    Boolean(userId) &&
    Boolean(activeWorkspace?.owner_user_id) &&
    userId === activeWorkspace?.owner_user_id;

  if (settings.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (settings.isError)
    return (
      <PageFrame>
        <ErrorState message={settings.error.message} />
      </PageFrame>
    );

  const workspaceSettings = settings.data ?? [];

  function toggleTheme(value: boolean) {
    setDark(value);
    document.documentElement.classList.toggle("dark", value);
    try {
      window.localStorage.setItem("lg-theme", value ? "dark" : "light");
    } catch {
      // Class still applied even if storage is unavailable.
    }
    save.mutate({
      ...(typeof current === "object" && current && !Array.isArray(current)
        ? current
        : {}),
      theme: value ? "dark" : "light",
    });
  }

  function exportSettings() {
    const blob = new Blob(
      [
        JSON.stringify(
          { workspace_id: auth.workspaceId, settings: workspaceSettings },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `learngraph-settings-${auth.workspaceId}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <PageFrame>
      <PageIntro
        description="账号身份、所属工作区与可操作开关集中管理；危险操作必须二次确认并说明影响范围。"
        eyebrow="Workspace settings"
        title="用户与工作区设置"
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Surface className="p-5">
          <SectionHeading
            description="当前登录账号与身份信息"
            title="身份"
          />
          <div className="mt-5 space-y-3">
            <div className="flex items-start gap-3 rounded-xl border p-4">
              <UserRound className="mt-0.5 size-5 shrink-0 text-primary" />
              <div className="min-w-0 space-y-2 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-muted-foreground">显示名</span>
                  <span className="font-medium">{displayName || "—"}</span>
                </div>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-muted-foreground">用户名</span>
                  <span className="font-mono text-xs">{accountUsername || "—"}</span>
                </div>
                {userId ? (
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-muted-foreground">用户 ID</span>
                    <span className="max-w-[60%] truncate font-mono text-[11px] text-muted-foreground" title={userId}>
                      {userId}
                    </span>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </Surface>

        <Surface className="p-5">
          <SectionHeading
            description="当前工作区归属与角色"
            title="所属"
          />
          <div className="mt-5 space-y-3">
            <div className="flex items-start gap-3 rounded-xl border p-4">
              <Building2 className="mt-0.5 size-5 shrink-0 text-primary" />
              <div className="min-w-0 space-y-2 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-muted-foreground">工作区</span>
                  <span className="font-medium">{auth.workspaceName || "—"}</span>
                </div>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-muted-foreground">类型</span>
                  <Badge className="font-normal" variant="outline">
                    {workspaceKindLabel}
                  </Badge>
                </div>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-muted-foreground">角色</span>
                  <Badge
                    className={cn(
                      "font-normal",
                      isOwner
                        ? "border-primary/20 bg-primary/10 text-primary"
                        : undefined,
                    )}
                    variant="outline"
                  >
                    {isOwner ? "所有者" : "成员"}
                  </Badge>
                </div>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-muted-foreground">工作区 ID</span>
                  <span
                    className="max-w-[60%] truncate font-mono text-[11px] text-muted-foreground"
                    title={auth.workspaceId}
                  >
                    {auth.workspaceId}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </Surface>
      </div>

      <AccountSecurity username={accountUsername} />

      <Surface className="p-5">
        <SectionHeading
          description="这些开关会立即保存到当前工作区"
          title="可操作开关"
        />
        <div className="mt-5 grid gap-3 lg:grid-cols-2">
          <div className="flex items-center justify-between gap-4 rounded-xl border p-4">
            <div className="flex min-w-0 items-center gap-3">
              {dark ? (
                <Moon className="size-5 shrink-0 text-primary" />
              ) : (
                <Sun className="size-5 shrink-0 text-primary" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium">深色主题</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  长时间学习时可切换；会写入工作区外观偏好
                </p>
              </div>
            </div>
            <Switch
              aria-label="深色主题"
              checked={dark}
              disabled={save.isPending}
              onCheckedChange={toggleTheme}
            />
          </div>
          <div className="flex items-center justify-between gap-4 rounded-xl border p-4">
            <div className="flex min-w-0 items-center gap-3">
              <MessageSquareText className="size-5 shrink-0 text-primary" />
              <div className="min-w-0">
                <p className="text-sm font-medium">生成下一步问题提示</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  关闭后不会请求提示生成
                </p>
              </div>
            </div>
            <Switch
              aria-label="生成下一步问题提示"
              checked={suggestedPromptsEnabled}
              disabled={saveSuggestedPrompts.isPending}
              onCheckedChange={(enabled) => saveSuggestedPrompts.mutate(enabled)}
            />
          </div>
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading
          description="可分别为自动标题与下一步问题提示选择模型；留空则跟随对话当前模型。"
          title="功能模型"
        />
        <div className="mt-5 grid gap-3 lg:grid-cols-2">
          <div className="rounded-xl border p-4">
            <p className="text-sm font-medium">生成标题模型</p>
            <p className="mt-1 text-xs text-muted-foreground">
              首条用户消息后的自动命名
            </p>
            <Select
              disabled={saveFeatureModel.isPending || providers.isPending}
              onValueChange={(value) => {
                const parsed = parseFeatureModelValue(value);
                saveFeatureModel.mutate({
                  key: CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
                  ...parsed,
                });
              }}
              value={featureModelValue(
                autoTitleModel.provider_id,
                autoTitleModel.model_id,
              )}
            >
              <SelectTrigger aria-label="生成标题模型" className="mt-3">
                <SelectValue placeholder="跟随对话模型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">跟随对话模型</SelectItem>
                {featureModelChoices.map((choice) => (
                  <SelectItem key={choice.value} value={choice.value}>
                    {choice.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="rounded-xl border p-4">
            <p className="text-sm font-medium">生成下一步问题提示模型</p>
            <p className="mt-1 text-xs text-muted-foreground">
              空会话与每轮完成后的追问建议
            </p>
            <Select
              disabled={saveFeatureModel.isPending || providers.isPending}
              onValueChange={(value) => {
                const parsed = parseFeatureModelValue(value);
                saveFeatureModel.mutate({
                  key: CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
                  ...parsed,
                });
              }}
              value={featureModelValue(
                suggestedPromptsModel.provider_id,
                suggestedPromptsModel.model_id,
              )}
            >
              <SelectTrigger aria-label="生成下一步问题提示模型" className="mt-3">
                <SelectValue placeholder="跟随对话模型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">跟随对话模型</SelectItem>
                {featureModelChoices.map((choice) => (
                  <SelectItem key={choice.value} value={choice.value}>
                    {choice.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading
          description="导出当前工作区配置或完整归档；凭据与认证密文不会进入导出。"
          title="其他设置"
        />
        <div className="mt-4 flex flex-wrap gap-2">
          <Button onClick={exportSettings} variant="outline">
            <FileJson className="size-4" />
            导出设置 JSON
          </Button>
          <Button
            disabled={workspaceExport.isPending}
            onClick={() => workspaceExport.mutate()}
            variant="outline"
          >
            <HardDrive className="size-4" />
            {workspaceExport.isPending ? "正在生成…" : "导出完整工作区 ZIP"}
          </Button>
          <Button asChild variant="outline">
            <a
              href={`/w/${encodeURIComponent(auth.workspaceId)}/settings/audit`}
            >
              <History className="size-4" />
              查看审计
            </a>
          </Button>
        </div>
      </Surface>

      <Surface className="border-red-200 bg-red-50/45 p-5 dark:border-red-900 dark:bg-red-950/20">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <AlertTriangle className="size-5 shrink-0 text-destructive" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold text-destructive">危险区</p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              记忆删除提供恢复窗口；Provider 停用会立即阻止新的远程调用。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline">
              <a href={`/w/${auth.workspaceId}/memory`}>管理可恢复记忆</a>
            </Button>
            <Button asChild variant="outline">
              <a href={`/w/${auth.workspaceId}/settings/providers`}>
                管理 Provider
              </a>
            </Button>
          </div>
        </div>
      </Surface>
    </PageFrame>
  );
}

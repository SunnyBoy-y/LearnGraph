/**
 * Pure task-state reduction for the voice surface.
 *
 * The backend may still send legacy sandbox statuses (`SUCCEEDED`) while newer
 * voice events use task lifecycle names (`task.result_ready`). This module keeps
 * those contracts from leaking into components and makes replay de-duplication
 * testable without a browser or network.
 */

export type VoiceTaskStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "finalizing"
  | "ready"
  | "partial"
  | "failed"
  | "cancel_requested"
  | "cancelled"
  | "interrupted"
  | "stale";

export type VoiceTaskDeliveryStatus =
  | "pending_validation"
  | "ready"
  | "waiting_for_floor"
  | "speaking"
  | "interrupted"
  | "delivered"
  | "obsolete";

export type VoiceTaskKind = "research" | "reasoning" | "tool" | "unknown";

export interface VoiceTaskSource {
  id: string;
  title: string;
  domain?: string;
  snippet?: string;
  retrievedAt?: string;
}

export interface VoiceTaskArtifact {
  id: string;
  name: string;
  kind?: string;
  exists?: boolean;
  verified?: boolean;
}

export interface VoiceTask {
  taskId: string;
  status: VoiceTaskStatus;
  kind: VoiceTaskKind;
  title: string;
  summary?: string;
  progress?: number;
  createdAt: string;
  updatedAt: string;
  requirementVersion: number;
  deliveryStatus: VoiceTaskDeliveryStatus;
  sources: VoiceTaskSource[];
  artifacts: VoiceTaskArtifact[];
  limitations: string[];
  resultAvailable: boolean;
  eventSeq?: number;
}

export interface VoiceTaskActivity {
  taskId: string;
  eventId?: string;
  eventSeq?: number;
  text: string;
  createdAt: string;
}

export interface VoiceTaskState {
  tasks: VoiceTask[];
  cursorByTask: Record<string, number>;
  seenEventIds: string[];
  activityByTask: Record<string, string>;
}

export interface VoiceTaskReduction {
  state: VoiceTaskState;
  changed: boolean;
  activity?: VoiceTaskActivity;
}

export type VoiceTaskPatch = Partial<Omit<VoiceTask, "taskId">> & {
  taskId: string;
};

const MAX_SEEN_TASK_EVENTS = 2000;
const TERMINAL_STATUSES = new Set<VoiceTaskStatus>([
  "ready",
  "partial",
  "failed",
  "cancelled",
  "interrupted",
  "stale",
]);
const ACTIVE_STATUSES = new Set<VoiceTaskStatus>([
  "queued",
  "running",
  "waiting_approval",
  "finalizing",
  "cancel_requested",
]);

const STATUS_ALIASES: Record<string, VoiceTaskStatus> = {
  ACCEPTED: "queued",
  BLOCKED: "waiting_approval",
  CANCELED: "cancelled",
  CANCELLED: "cancelled",
  CANCEL_REQUESTED: "cancel_requested",
  COMPLETED: "ready",
  ERROR: "failed",
  FAILED: "failed",
  FINALIZING: "finalizing",
  INTERRUPTED: "interrupted",
  OBSOLETE: "stale",
  PARTIAL: "partial",
  PENDING: "queued",
  QUEUED: "queued",
  READY: "ready",
  RESULT_READY: "ready",
  RUNNING: "running",
  STALE: "stale",
  STARTED: "running",
  SUCCEEDED: "ready",
  TIMED_OUT: "failed",
  WAITING_APPROVAL: "waiting_approval",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

function booleanValue(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function normalizedText(value: unknown, maxLength = 1600): string | undefined {
  const text = firstString(value);
  if (!text) return undefined;
  if (/^\s*[{[]/.test(text)) return undefined;
  const redacted = text
    .replace(/\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi, "Bearer [redacted]")
    .replace(/\b(sk|rk|pk)-[A-Za-z0-9_-]{12,}\b/gi, "[redacted]")
    .replace(
      /^\s*(?:provider|token|access[_ -]?token|job[_ -]?id|tool[_ -]?json)\s*[:=].*$/gim,
      "",
    )
    .trim();
  if (!redacted) return undefined;
  return redacted.length > maxLength ? `${redacted.slice(0, maxLength - 1)}…` : redacted;
}

function safeTitle(value: unknown): string {
  return normalizedText(value, 160) ?? "后台任务";
}

function recordOrEmpty(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function payloadOf(raw: Record<string, unknown>): Record<string, unknown> {
  return recordOrEmpty(raw.payload);
}

function eventTypeOf(raw: Record<string, unknown>): string {
  return firstString(raw.type, raw.event_type, raw.phase) ?? "";
}

function taskIdOf(raw: Record<string, unknown>, fallbackTaskId?: string): string | undefined {
  const payload = recordOrEmpty(raw.payload);
  const result = recordOrEmpty(raw.result);
  return firstString(
    raw.task_id,
    raw.taskId,
    raw.subagent_id,
    raw.subagentId,
    raw.id,
    payload.task_id,
    payload.taskId,
    result.task_id,
    result.taskId,
    fallbackTaskId,
  );
}

function requirementVersionOf(
  raw: Record<string, unknown>,
  payload: Record<string, unknown>,
  result: Record<string, unknown>,
): number | undefined {
  return firstNumber(
    raw.requirement_version,
    raw.requirementVersion,
    raw.request_revision,
    raw.requestRevision,
    payload.requirement_version,
    payload.requirementVersion,
    payload.request_revision,
    payload.requestRevision,
    result.requirement_version,
    result.requirementVersion,
    result.request_revision,
    result.requestRevision,
  );
}

function eventSeqOf(raw: Record<string, unknown>): number | undefined {
  const value = firstNumber(raw.task_event_seq, raw.taskEventSeq, raw.event_seq, raw.eventSeq, raw.seq);
  return value === undefined ? undefined : Math.max(0, Math.trunc(value));
}

function eventIdOf(raw: Record<string, unknown>): string | undefined {
  return firstString(raw.event_id, raw.eventId);
}

function normalizeKind(raw: Record<string, unknown>, payload: Record<string, unknown>): VoiceTaskKind {
  const value = (
    firstString(
      raw.kind,
      raw.task_type,
      raw.taskType,
      raw.role_key,
      raw.roleKey,
      raw.agent_role,
      raw.agentRole,
      payload.kind,
      payload.task_type,
      payload.taskType,
      payload.role_key,
      payload.roleKey,
      payload.agent_role,
      payload.agentRole,
    ) ?? ""
  ).toLowerCase();
  if (/(research|search|web|browse|资料|研究|搜索)/.test(value)) return "research";
  if (/(reason|think|analysis|analy|推理|分析)/.test(value)) return "reasoning";
  if (/(tool|code|file|sandbox|执行|工具|文件)/.test(value)) return "tool";
  return "unknown";
}

function domainOf(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return undefined;
  }
}

function sourceKey(source: VoiceTaskSource): string {
  return source.id || source.domain || source.title;
}

function artifactKey(artifact: VoiceTaskArtifact): string {
  return artifact.id || artifact.name;
}

function dedupeBy<T>(values: T[], keyOf: (value: T) => string): T[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = keyOf(value).toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function parseSource(value: unknown, index: number): VoiceTaskSource | null {
  if (typeof value === "string") {
    const title = normalizedText(value, 300);
    return title ? { id: `source-${index}`, title } : null;
  }
  if (!isRecord(value)) return null;
  const url = firstString(value.url, value.href, value.source_url, value.sourceUrl);
  const title =
    normalizedText(value.title, 300) ??
    normalizedText(value.name, 300) ??
    domainOf(url) ??
    normalizedText(value.source_ref, 300);
  if (!title) return null;
  return {
    id: firstString(value.id, value.source_ref, value.sourceRef, url, title) ?? `source-${index}`,
    title,
    domain: normalizedText(value.domain, 120) ?? domainOf(url),
    snippet: normalizedText(value.snippet, 600) ?? normalizedText(value.summary, 600),
    retrievedAt: firstString(value.retrieved_at, value.retrievedAt, value.updated_at, value.updatedAt),
  };
}

function parseArtifact(value: unknown, index: number): VoiceTaskArtifact | null {
  if (typeof value === "string") {
    const name = normalizedText(value, 240);
    return name ? { id: `artifact-${index}`, name } : null;
  }
  if (!isRecord(value)) return null;
  const rawName = firstString(value.name, value.title, value.path, value.filename, value.file_name);
  const name = normalizedText(rawName, 240);
  if (!name) return null;
  const displayName = name.split(/[\\/]/).filter(Boolean).at(-1) ?? name;
  return {
    id: firstString(value.id, value.artifact_id, value.file_id, value.path, displayName) ?? `artifact-${index}`,
    name: displayName,
    kind: normalizedText(value.kind, 80) ?? normalizedText(value.type, 80) ?? normalizedText(value.mime_type, 80),
    exists: booleanValue(value.exists),
    verified: booleanValue(value.verified),
  };
}

function parseSources(raw: Record<string, unknown>, payload: Record<string, unknown>, result: Record<string, unknown>): VoiceTaskSource[] {
  return dedupeBy(
    [
      ...arrayValue(result.sources),
      ...arrayValue(result.source_refs),
      ...arrayValue(payload.sources),
      ...arrayValue(payload.source_refs),
      ...arrayValue(raw.sources),
    ]
      .map(parseSource)
      .filter((source): source is VoiceTaskSource => source !== null),
    sourceKey,
  );
}

function parseArtifacts(raw: Record<string, unknown>, payload: Record<string, unknown>, result: Record<string, unknown>): VoiceTaskArtifact[] {
  return dedupeBy(
    [
      ...arrayValue(result.artifacts),
      ...arrayValue(recordOrEmpty(result.deliverables).artifacts),
      ...arrayValue(payload.artifacts),
      ...arrayValue(recordOrEmpty(payload.deliverables).artifacts),
      ...arrayValue(payload.deliverables),
      ...arrayValue(recordOrEmpty(raw.deliverables).artifacts),
      ...arrayValue(raw.artifacts),
    ]
      .map(parseArtifact)
      .filter((artifact): artifact is VoiceTaskArtifact => artifact !== null),
    artifactKey,
  );
}

function parseLimitations(raw: Record<string, unknown>, payload: Record<string, unknown>, result: Record<string, unknown>): string[] {
  const values = [
    ...arrayValue(result.limitations),
    ...arrayValue(result.unresolved_questions),
    ...arrayValue(payload.limitations),
    ...arrayValue(payload.unresolved_questions),
    ...arrayValue(raw.limitations),
  ];
  return dedupeBy(
    values
      .map((value) => normalizedText(value, 500))
      .filter((value): value is string => Boolean(value)),
    (value) => value,
  );
}

function resultRecord(raw: Record<string, unknown>, payload: Record<string, unknown>): Record<string, unknown> {
  if (isRecord(raw.result)) return raw.result;
  if (isRecord(raw.result_payload)) return raw.result_payload;
  if (isRecord(raw.resultPayload)) return raw.resultPayload;
  if (isRecord(payload.result)) return payload.result;
  if (isRecord(payload.result_payload)) return payload.result_payload;
  if (isRecord(payload.resultPayload)) return payload.resultPayload;
  if (isRecord(payload.deliverables)) return payload.deliverables;
  return {};
}

function summaryOf(raw: Record<string, unknown>, payload: Record<string, unknown>, result: Record<string, unknown>): string | undefined {
  return normalizedText(
    raw.summary ??
      raw.short_answer ??
      raw.shortAnswer ??
      payload.summary ??
      payload.short_answer ??
      payload.shortAnswer ??
      result.summary ??
      result.short_answer ??
      result.shortAnswer ??
      (typeof raw.result === "string" ? raw.result : undefined),
    1800,
  );
}

export function normalizeVoiceTaskStatus(value: unknown): VoiceTaskStatus {
  const normalized = String(value ?? "").trim().toUpperCase().replace(/[\s-]+/g, "_");
  if (!normalized) return "queued";
  return STATUS_ALIASES[normalized] ?? (
    [
      "queued",
      "running",
      "waiting_approval",
      "finalizing",
      "ready",
      "partial",
      "failed",
      "cancel_requested",
      "cancelled",
      "interrupted",
      "stale",
    ] as VoiceTaskStatus[]
  ).find((status) => status === normalized.toLowerCase()) ?? "queued";
}

function normalizeDeliveryStatus(value: unknown): VoiceTaskDeliveryStatus | undefined {
  const normalized = String(value ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (!normalized) return undefined;
  const aliases: Record<string, VoiceTaskDeliveryStatus> = {
    pending: "pending_validation",
    pending_validation: "pending_validation",
    validating: "pending_validation",
    ready: "ready",
    result_ready: "ready",
    waiting_for_floor: "waiting_for_floor",
    waiting: "waiting_for_floor",
    speaking: "speaking",
    speaking_started: "speaking",
    interrupted: "interrupted",
    delivered: "delivered",
    spoken: "delivered",
    obsolete: "obsolete",
    stale: "obsolete",
  };
  return aliases[normalized];
}

function statusFromEvent(eventType: string, rawStatus: unknown): VoiceTaskStatus | undefined {
  const explicit = firstString(rawStatus);
  if (explicit) return normalizeVoiceTaskStatus(explicit);
  switch (eventType) {
    case "task.accepted":
    case "task.queued":
      return "queued";
    case "task.progress":
    case "task.running":
      return "running";
    case "task.approval_required":
    case "task.blocked":
      return "waiting_approval";
    case "task.finalizing":
      return "finalizing";
    case "task.result_ready":
    case "result.ready":
      return "ready";
    case "task.partial":
      return "partial";
    case "task.failed":
      return "failed";
    case "task.cancel_requested":
      return "cancel_requested";
    case "task.cancelled":
      return "cancelled";
    case "task.interrupted":
      return "interrupted";
    case "task.stale":
    case "task.obsolete":
      return "stale";
    case "task.requirement_revised":
    case "requirement.revised":
      return "running";
    default:
      return undefined;
  }
}

function deliveryFromEvent(eventType: string, incoming: VoiceTaskDeliveryStatus | undefined): VoiceTaskDeliveryStatus | undefined {
  switch (eventType) {
    case "task.result_ready":
    case "result.ready":
      return incoming ?? "ready";
    case "result.queued":
      return incoming ?? "waiting_for_floor";
    case "speech.started":
      return incoming ?? "speaking";
    case "speech.interrupted":
      return incoming ?? "interrupted";
    case "speech.finished":
      return incoming ?? "delivered";
    case "task.requirement_revised":
    case "requirement.revised":
    case "task.stale":
    case "task.obsolete":
      return "obsolete";
    default:
      return incoming;
  }
}

function normalizeProgress(value: unknown): number | undefined {
  const progress = firstNumber(value);
  if (progress === undefined) return undefined;
  const percentage = progress >= 0 && progress <= 1 ? progress * 100 : progress;
  return Math.max(0, Math.min(100, Math.round(percentage)));
}

function timeValue(raw: Record<string, unknown>, payload: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const source of [raw, payload]) {
    for (const key of keys) {
      const value = source[key];
      if (typeof value !== "string" || !value.trim()) continue;
      const parsed = new Date(value);
      if (!Number.isNaN(parsed.getTime())) return parsed.toISOString();
    }
  }
  return undefined;
}

function sameTask(a: VoiceTask, b: VoiceTask): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function mergeUnique<T>(current: T[], incoming: T[], keyOf: (value: T) => string): T[] {
  return dedupeBy([...incoming, ...current], keyOf);
}

function normalizeTask(
  raw: Record<string, unknown>,
  existing: VoiceTask | undefined,
  fallbackTaskId: string | undefined,
  eventType: string,
  now: string,
): VoiceTask | null {
  const taskId = taskIdOf(raw, fallbackTaskId);
  if (!taskId) return null;
  const payload = payloadOf(raw);
  const result = resultRecord(raw, payload);
  const incomingRevision = requirementVersionOf(raw, payload, result);
  const currentRevision = Math.max(existing?.requirementVersion ?? 1, incomingRevision ?? 1);
  const explicitStatus = statusFromEvent(eventType, raw.status);
  let status = explicitStatus ?? normalizeVoiceTaskStatus(raw.status ?? payload.status);
  let deliveryStatus =
    deliveryFromEvent(eventType, normalizeDeliveryStatus(raw.delivery_status ?? raw.deliveryStatus ?? payload.delivery_status ?? payload.deliveryStatus)) ??
    existing?.deliveryStatus ??
    "pending_validation";
  let resultAvailable =
    booleanValue(raw.result_available ?? raw.resultAvailable ?? payload.result_available ?? payload.resultAvailable) ??
    Boolean(summaryOf(raw, payload, result) || Object.keys(result).length > 0);
  const incomingBeforeCurrent =
    Boolean(eventType) &&
    incomingRevision !== undefined &&
    existing !== undefined &&
    incomingRevision < existing.requirementVersion;

  if (incomingBeforeCurrent && !["task.requirement_revised", "requirement.revised"].includes(eventType)) {
    status = "stale";
    deliveryStatus = "obsolete";
    resultAvailable = false;
  } else if (incomingBeforeCurrent) {
    status = existing?.status ?? "running";
  }

  if (existing && TERMINAL_STATUSES.has(existing.status)) {
    if ((existing.status === "cancelled" || existing.status === "failed") && !TERMINAL_STATUSES.has(status)) {
      status = existing.status;
      deliveryStatus = existing.deliveryStatus;
      resultAvailable = existing.resultAvailable;
    } else if (!["task.requirement_revised", "requirement.revised", "task.result_ready", "result.ready", "task.partial"].includes(eventType)) {
      status = existing.status;
      deliveryStatus = existing.deliveryStatus;
      resultAvailable = existing.resultAvailable;
    }
  }

  if (!["ready", "partial"].includes(status)) {
    resultAvailable = existing?.resultAvailable ?? resultAvailable;
  }

  const sources = parseSources(raw, payload, result);
  const artifacts = parseArtifacts(raw, payload, result);
  const limitations = parseLimitations(raw, payload, result);
  const summary = summaryOf(raw, payload, result) ?? existing?.summary;

  return {
    taskId,
    status,
    kind: normalizeKind(raw, payload) === "unknown" ? existing?.kind ?? "unknown" : normalizeKind(raw, payload),
    title: safeTitle(raw.title ?? raw.display_title ?? payload.title ?? existing?.title),
    summary,
    progress: normalizeProgress(raw.progress ?? payload.progress) ?? existing?.progress,
    createdAt:
      timeValue(raw, payload, "created_at", "createdAt") ??
      existing?.createdAt ??
      now,
    updatedAt:
      timeValue(raw, payload, "updated_at", "updatedAt", "completed_at", "completedAt") ??
      now,
    requirementVersion: currentRevision,
    deliveryStatus,
    sources: mergeUnique(existing?.sources ?? [], sources, sourceKey),
    artifacts: mergeUnique(existing?.artifacts ?? [], artifacts, artifactKey),
    limitations: mergeUnique(existing?.limitations ?? [], limitations, (value) => value),
    resultAvailable,
    eventSeq: eventSeqOf(raw) ?? existing?.eventSeq,
  };
}

function activityKind(kind: VoiceTaskKind): string {
  if (kind === "research") return "正在检索相关资料…";
  if (kind === "reasoning") return "正在深入分析…";
  if (kind === "tool") return "正在执行任务并整理结果…";
  return "后台任务进行中…";
}

export function voiceTaskActivityText(task: VoiceTask, eventType = ""): string {
  if (["task.requirement_revised", "requirement.revised"].includes(eventType)) {
    return "需求已更新，正在按新的要求继续处理。";
  }
  switch (task.status) {
    case "queued":
      return "已接受任务，正在排队…";
    case "running":
      return activityKind(task.kind);
    case "waiting_approval":
      return "等待你确认后继续。";
    case "finalizing":
      return "正在整理结果…";
    case "ready": {
      const sourceCount = task.sources.length;
      const artifactCount = task.artifacts.length;
      if (artifactCount > 0) return `结果和 ${artifactCount} 个产物已准备好。`;
      if (sourceCount > 0) return `已找到 ${sourceCount} 个来源，结果已准备好。`;
      return "结果已准备好。";
    }
    case "partial":
      return "已有部分结果，仍有内容待确认。";
    case "failed":
      return "任务未完成，可以重试。";
    case "cancel_requested":
      return "正在取消后台任务…";
    case "cancelled":
      return "后台任务已取消。";
    case "interrupted":
      return "后台任务已中断。";
    case "stale":
      return "需求已更新，旧结果已标记为过期。";
  }
}

export function voiceTaskStatusLabel(task: VoiceTask): string {
  switch (task.status) {
    case "queued":
      return "排队中";
    case "running":
      return task.kind === "research"
        ? "正在查资料"
        : task.kind === "reasoning"
          ? "正在分析"
          : task.kind === "tool"
            ? "正在处理"
            : "进行中";
    case "waiting_approval":
      return "等待确认";
    case "finalizing":
      return "正在整理";
    case "ready":
      return "结果已准备好";
    case "partial":
      return "部分完成";
    case "failed":
      return "未完成";
    case "cancel_requested":
      return "正在取消";
    case "cancelled":
      return "已取消";
    case "interrupted":
      return "已中断";
    case "stale":
      return "旧结果已过期";
  }
}

export function voiceTaskIsActive(task: VoiceTask): boolean {
  return ACTIVE_STATUSES.has(task.status);
}

export function voiceTaskHasDetail(task: VoiceTask): boolean {
  return Boolean(
    task.summary ||
      task.sources.length ||
      task.artifacts.length ||
      task.limitations.length ||
      task.status === "failed",
  );
}

export function emptyVoiceTaskState(): VoiceTaskState {
  return {
    tasks: [],
    cursorByTask: {},
    seenEventIds: [],
    activityByTask: {},
  };
}

function rememberTaskEvent(state: VoiceTaskState, eventId: string | undefined): VoiceTaskState {
  if (!eventId || state.seenEventIds.includes(eventId)) return state;
  const next = [...state.seenEventIds, eventId];
  return {
    ...state,
    seenEventIds:
      next.length > MAX_SEEN_TASK_EVENTS ? next.slice(-MAX_SEEN_TASK_EVENTS) : next,
  };
}

export function reduceVoiceTaskEvent(
  state: VoiceTaskState,
  raw: Record<string, unknown>,
  fallbackTaskId?: string,
): VoiceTaskReduction {
  const eventId = eventIdOf(raw);
  if (eventId && state.seenEventIds.includes(eventId)) {
    return { state, changed: false };
  }

  const taskId = taskIdOf(raw, fallbackTaskId);
  if (!taskId) return { state, changed: false };

  const eventSeq = eventSeqOf(raw);
  const previousCursor = state.cursorByTask[taskId] ?? -1;
  if (eventSeq !== undefined && eventSeq <= previousCursor) {
    return { state, changed: false };
  }

  const eventType = eventTypeOf(raw);
  const existing = state.tasks.find((task) => task.taskId === taskId);
  const payload = payloadOf(raw);
  const result = resultRecord(raw, payload);
  const rawRevision = requirementVersionOf(raw, payload, result);
  const normalized = normalizeTask(raw, existing, taskId, eventType, new Date().toISOString());
  if (!normalized) return { state, changed: false };

  const incomingBeforeCurrent =
    Boolean(eventType) &&
    existing !== undefined &&
    rawRevision !== undefined &&
    rawRevision < existing.requirementVersion;
  const nextTask = incomingBeforeCurrent && existing ? existing : normalized;

  let next: VoiceTaskState = rememberTaskEvent(state, eventId);
  const changedTask = !existing || !sameTask(existing, nextTask);
  next = {
    ...next,
    tasks: existing
      ? next.tasks.map((task) => (task.taskId === taskId ? nextTask : task))
      : [...next.tasks, nextTask],
    cursorByTask: {
      ...next.cursorByTask,
      [taskId]: Math.max(previousCursor, eventSeq ?? previousCursor),
    },
  };

  const activityText = incomingBeforeCurrent
    ? "需求已更新，旧结果已标记为过期。"
    : voiceTaskActivityText(nextTask, eventType);
  const changedActivity = next.activityByTask[taskId] !== activityText;
  if (changedActivity) {
    next = {
      ...next,
      activityByTask: { ...next.activityByTask, [taskId]: activityText },
    };
  }

  const changed = changedTask || changedActivity || (eventSeq ?? previousCursor) > previousCursor;
  return {
    state: next,
    changed,
    activity:
      changedActivity && eventType
        ? {
            taskId,
            eventId,
            eventSeq,
            text: activityText,
            createdAt: nextTask.updatedAt,
          }
        : undefined,
  };
}

export function mergeVoiceTaskSnapshots(
  state: VoiceTaskState,
  rawTasks: readonly Record<string, unknown>[],
): VoiceTaskState {
  let tasks = state.tasks;
  for (const raw of rawTasks) {
    const taskId = taskIdOf(raw);
    if (!taskId) continue;
    const existing = tasks.find((task) => task.taskId === taskId);
    const payload = payloadOf(raw);
    const result = resultRecord(raw, payload);
    const rawRevision = requirementVersionOf(raw, payload, result);
    const normalized = normalizeTask(raw, existing, taskId, "", new Date().toISOString());
    if (!normalized) continue;
    const next =
      existing && rawRevision !== undefined && rawRevision < existing.requirementVersion
        ? existing
        : normalized;
    tasks = existing
      ? tasks.map((task) => (task.taskId === taskId ? next : task))
      : [...tasks, next];
  }
  return { ...state, tasks };
}

export function mergeVoiceTaskResults(
  state: VoiceTaskState,
  rawResults: readonly Record<string, unknown>[],
): VoiceTaskState {
  const snapshots = rawResults
    .filter(isRecord)
    .map((result) => {
      const resultPayload = recordOrEmpty(result.payload);
      const agentResult = recordOrEmpty(resultPayload.agent_result);
      const resultStatus = String(result.status ?? "").trim().toLowerCase();
      const status =
        resultStatus === "failed"
          ? "failed"
          : resultStatus === "stale"
            ? "stale"
            : "ready";
      const deliveryStatus =
        resultStatus === "delivered"
          ? "delivered"
          : resultStatus === "dismissed" || resultStatus === "stale"
            ? "obsolete"
            : "ready";
      return {
        task_id: result.subagent_id ?? result.task_id,
        subagent_id: result.subagent_id ?? result.task_id,
        status,
        delivery_status: deliveryStatus,
        requirement_version: result.requirement_version,
        result_version: result.result_version,
        latest_result_version: result.result_version,
        summary: result.summary ?? result.safe_error,
        result: agentResult,
        deliverables: resultPayload,
        source_count: result.source_count,
        updated_at: result.available_at ?? result.captured_at,
      };
    });
  return mergeVoiceTaskSnapshots(state, snapshots);
}

export function mergeVoiceTaskPatches(
  state: VoiceTaskState,
  patches: readonly VoiceTaskPatch[],
): VoiceTaskState {
  return mergeVoiceTaskSnapshots(
    state,
    patches.map((patch) => ({ ...patch, task_id: patch.taskId })),
  );
}

export function voiceTaskSourceLabel(source: VoiceTaskSource): string {
  return source.title || source.domain || "未命名来源";
}

export function voiceTaskArtifactLabel(artifact: VoiceTaskArtifact): string {
  return artifact.name || "未命名产物";
}

export function voiceTaskArtifactIsVerified(artifact: VoiceTaskArtifact): boolean {
  if (artifact.verified !== undefined) return artifact.verified;
  return artifact.exists === true;
}

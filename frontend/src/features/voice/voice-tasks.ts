/**
 * Pure task-state reduction for the voice surface.
 *
 * The backend may still send legacy sandbox statuses (`SUCCEEDED`) while newer
 * voice events use task lifecycle names (`task.result_ready`). This module keeps
 * those contracts from leaking into components and makes replay de-duplication
 * testable without a browser or network.
 */

import { parseUISnapshot, type UISnapshot } from "@/lib/stream-ui";

export type VoiceTaskStatus =
  | "queued"
  | "running"
  | "pausing"
  | "paused"
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

export type VoiceTaskPlaybackStatus =
  | "claimed"
  | "enqueued"
  | "speaking"
  | "played"
  | "interrupted"
  | "failed";

export type VoiceTaskKind = "research" | "reasoning" | "tool" | "unknown";

export interface VoiceTaskSource {
  id: string;
  title: string;
  url?: string;
  prompt?: string;
  domain?: string;
  snippet?: string;
  retrievedAt?: string;
}

export interface VoiceTaskArtifact {
  id: string;
  name: string;
  kind?: string;
  path?: string;
  fileId?: string;
  mimeType?: string;
  sizeBytes?: number;
  sha256?: string;
  exists?: boolean;
  verified?: boolean;
}

export interface VoiceTask {
  taskId: string;
  triggerTurnId?: string;
  parentMessageId?: string;
  triggerUserText?: string;
  triggerCreatedAt?: string;
  agentSessionId?: string;
  linkId?: string;
  resultId?: string;
  resultVersion?: number;
  autoDelivery?: boolean;
  focusAnchor?: string;
  focusId?: string;
  focusTurnId?: string;
  presentationMode?: "display" | "explain";
  findings?: string[];
  visuals?: UISnapshot[];
  speechId?: string;
  capabilityId?: string;
  reason?: string;
  approvalStatus?: string;
  approvalExpiresAt?: string;
  approvalRequestPreview?: string;
  activities?: VoiceTaskActivity[];
  status: VoiceTaskStatus;
  kind: VoiceTaskKind;
  title: string;
  prompt?: string;
  summary?: string;
  progress?: number;
  createdAt: string;
  updatedAt: string;
  requirementVersion: number;
  deliveryStatus: VoiceTaskDeliveryStatus;
  playbackStatus?: VoiceTaskPlaybackStatus | null;
  playbackEndedAt?: string;
  evidenceRefs: string[];
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
  eventType?: string;
  toolName?: string;
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
const MAX_TASK_ACTIVITIES = 80;
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
  "pausing",
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
  PAUSING: "pausing",
  PAUSED: "paused",
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
    fallbackTaskId,
    raw.task_id,
    raw.taskId,
    raw.subagent_id,
    raw.subagentId,
    raw.id,
    payload.task_id,
    payload.taskId,
    result.task_id,
    result.taskId,
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
    url: url && /^https?:\/\//i.test(url) ? url : undefined,
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
  const rawSize = firstNumber(value.size_bytes, value.sizeBytes, value.size);
  return {
    id: firstString(value.id, value.artifact_id, value.file_id, value.path, displayName) ?? `artifact-${index}`,
    name: displayName,
    kind: normalizedText(value.kind, 80) ?? normalizedText(value.type, 80) ?? normalizedText(value.mime_type, 80),
    path: normalizedText(value.path, 500),
    fileId: firstString(value.file_id, value.fileId),
    mimeType: normalizedText(value.mime_type, 160) ?? normalizedText(value.mimeType, 160),
    sizeBytes: rawSize,
    sha256: firstString(value.sha256, value.blob_sha256),
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

function normalizePlaybackStatus(value: unknown): VoiceTaskPlaybackStatus | null | undefined {
  if (value === null) return null;
  const normalized = String(value ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (!normalized) return undefined;
  const aliases: Record<string, VoiceTaskPlaybackStatus> = {
    claimed: "claimed",
    enqueued: "enqueued",
    queued: "enqueued",
    speaking: "speaking",
    started: "speaking",
    played: "played",
    delivered: "played",
    finished: "played",
    interrupted: "interrupted",
    failed: "failed",
    error: "failed",
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
    case "started":
    case "progress":
    case "tool_started":
    case "tool_call":
    case "network_policy":
      return "running";
    case "task.pausing":
      return "pausing";
    case "task.paused":
      return "paused";
    case "task.resumed":
      return "queued";
    case "task.approval_required":
    case "task.blocked":
      return "waiting_approval";
    case "task.finalizing":
      return "finalizing";
    case "task.result_ready":
    case "result.ready":
    case "succeeded":
      return "ready";
    case "task.partial":
    case "partial":
      return "partial";
    case "task.failed":
    case "failed":
      return "failed";
    case "task.cancel_requested":
      return "cancel_requested";
    case "task.cancelled":
    case "cancelled":
      return "cancelled";
    case "task.interrupted":
    case "interrupted":
      return "interrupted";
    case "task.stale":
    case "task.obsolete":
    case "stale":
      return "stale";
    case "task.requirement_revised":
    case "requirement.revised":
      return "running";
    default:
      return undefined;
  }
}

function taskActivityFromEvent(
  raw: Record<string, unknown>,
  eventType: string,
  task: VoiceTask,
): VoiceTaskActivity | undefined {
  const payload = payloadOf(raw);
  const normalizedType = eventType.toLowerCase();
  const toolName = firstString(
    raw.tool_name,
    raw.toolName,
    payload.tool_name,
    payload.toolName,
  );
  const progressSummary = normalizedText(
    payload.progress_summary ?? payload.progressSummary ?? raw.progress_summary,
    320,
  );
  let text: string | undefined;
  if (toolName) {
    text = normalizedType === "tool_started"
      ? `正在调用工具：${toolName}`
      : normalizedType === "tool_call"
        ? `已完成工具调用：${toolName}`
        : `工具 ${toolName}：${normalizedType}`;
  } else if (progressSummary) {
    text = `后台执行摘要：${progressSummary}`;
  } else {
    const labels: Record<string, string> = {
      created: "任务已创建",
      queued: "任务已进入队列",
      started: "后台 Agent 已开始执行",
      network_policy: "已应用网络访问策略",
      progress: "后台 Agent 正在处理",
      partial: "已生成部分结果",
      succeeded: "后台 Agent 已完成",
      failed: "后台 Agent 执行失败",
      cancelled: "后台 Agent 已取消",
      interrupted: "后台 Agent 被中断",
      stale: "任务需求已更新，旧执行已过期",
      approval_requested: "等待用户确认后执行",
      "task.approval_required": "等待用户确认后执行",
    };
    text = labels[normalizedType] ?? voiceTaskActivityText(task, eventType);
  }
  if (!text) return undefined;
  return {
    taskId: task.taskId,
    eventId: eventIdOf(raw),
    eventSeq: eventSeqOf(raw),
    eventType,
    toolName,
    text,
    createdAt:
      timeValue(raw, payload, "created_at", "createdAt", "timestamp", "finished_at") ??
      task.updatedAt,
  };
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

function playbackFromEvent(
  eventType: string,
  incoming: VoiceTaskPlaybackStatus | null | undefined,
): VoiceTaskPlaybackStatus | null | undefined {
  switch (eventType.toLowerCase()) {
    case "result.claimed":
      return "claimed";
    case "result.queued":
    case "speech.enqueued":
      return "enqueued";
    case "speech.started":
      return "speaking";
    case "speech.finished":
    case "speech.ended":
      return "played";
    case "speech.interrupted":
      return "interrupted";
    case "speech.failed":
      return "failed";
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
  const newRequirement = Boolean(existing && incomingRevision !== undefined && incomingRevision > existing.requirementVersion);
  const previousResult = newRequirement ? undefined : existing;
  const explicitStatus = statusFromEvent(eventType, raw.status);
  const incomingStatus = raw.status ?? payload.status;
  let status =
    explicitStatus ??
    (incomingStatus !== undefined
      ? normalizeVoiceTaskStatus(incomingStatus)
      : existing?.status ?? "queued");
  let deliveryStatus =
    deliveryFromEvent(eventType, normalizeDeliveryStatus(raw.delivery_status ?? raw.deliveryStatus ?? payload.delivery_status ?? payload.deliveryStatus)) ??
    existing?.deliveryStatus ??
    "pending_validation";
  const incomingPlaybackStatus = playbackFromEvent(
    eventType,
    normalizePlaybackStatus(
      raw.playback_status ?? raw.playbackStatus ?? payload.playback_status ?? payload.playbackStatus ?? result.playback_status ?? result.playbackStatus,
    ),
  );
  const playbackStatus = incomingPlaybackStatus === undefined
    ? previousResult?.playbackStatus
    : incomingPlaybackStatus;
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

  if (existing && !newRequirement && TERMINAL_STATUSES.has(existing.status)) {
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
    resultAvailable = previousResult?.resultAvailable ?? resultAvailable;
  }

  const sources = parseSources(raw, payload, result);
  const artifacts = parseArtifacts(raw, payload, result);
  const limitations = parseLimitations(raw, payload, result);
  const incomingVisuals = [
    ...arrayValue(raw.visuals), ...arrayValue(payload.visuals),
    ...arrayValue(result.visuals), ...arrayValue(recordOrEmpty(raw.deliverables).visuals),
    ...(eventType === "artifact.upsert" ? [payload.snapshot] : []),
  ].map(parseUISnapshot).filter((item): item is UISnapshot => item !== null);
  const visuals = new Map((previousResult?.visuals ?? []).map((item) => [item.block_id, item]));
  for (const item of incomingVisuals) {
    const previous = visuals.get(item.block_id);
    if (!previous || item.revision > previous.revision || (item.revision === previous.revision
      && previous.state !== "completed" && item.state === "completed")) visuals.set(item.block_id, item);
  }
  const findings = arrayValue(result.findings ?? payload.findings ?? raw.findings)
    .slice(0, 20).filter((item): item is string => typeof item === "string").map((item) => item.slice(0, 2000));
  const summary = summaryOf(raw, payload, result) ?? previousResult?.summary;
  const approvalRequest = recordOrEmpty(
    raw.approval_request ?? raw.approvalRequest ?? raw.approval_request_json ?? raw.approvalRequestJson ?? payload.approval_request ?? payload.approvalRequest ?? payload.approval_request_json ?? payload.approvalRequestJson,
  );
  const approvalRequestPreview =
    normalizedText(
      approvalRequest.summary ?? approvalRequest.preview ?? approvalRequest.title ??
        raw.approval_request_preview ?? payload.approval_request_preview,
      500,
    ) ?? existing?.approvalRequestPreview;
  const evidenceRefs = dedupeBy(
    [
      ...arrayValue(raw.evidence_refs ?? raw.evidenceRefs),
      ...arrayValue(payload.evidence_refs ?? payload.evidenceRefs),
      ...arrayValue(result.evidence_refs ?? result.evidenceRefs),
    ]
      .map((value) => firstString(value, isRecord(value) ? value.id : undefined))
      .filter((value): value is string => Boolean(value)),
    (value) => value,
  );

  return {
    taskId,
    triggerTurnId: firstString(raw.trigger_turn_id, raw.triggerTurnId, payload.trigger_turn_id,
      recordOrEmpty(recordOrEmpty(raw.spec_json).association).turn_id,
      recordOrEmpty(recordOrEmpty(raw.spec_json).input_snapshot).turn_id,
      ["task.accepted", "created"].includes(eventType) ? payload.turn_id ?? raw.turn_id : undefined) ?? existing?.triggerTurnId,
    parentMessageId: firstString(raw.parent_message_id, raw.parentMessageId,
      recordOrEmpty(recordOrEmpty(raw.spec_json).association).parent_message_id,
      recordOrEmpty(recordOrEmpty(raw.spec_json).input_snapshot).parent_message_id) ?? existing?.parentMessageId,
    triggerUserText: firstString(raw.triggerUserText) ?? existing?.triggerUserText,
    triggerCreatedAt: firstString(raw.triggerCreatedAt, raw.created_at, payload.created_at) ?? existing?.triggerCreatedAt,
    agentSessionId: firstString(raw.agentSessionId, recordOrEmpty(raw.spec_json).agent_session_id, raw.agent_session_id, payload.agent_session_id) ?? existing?.agentSessionId,
    resultVersion: firstNumber(raw.result_version, raw.resultVersion, payload.result_version) ?? previousResult?.resultVersion,
    autoDelivery: booleanValue(raw.auto_delivery ?? raw.autoDelivery ?? payload.auto_delivery) ?? existing?.autoDelivery ?? true,
    focusAnchor: firstString(raw.focusAnchor, payload.focusAnchor, eventType === "scene.focus" ? raw.anchor_id ?? payload.anchor_id : undefined) ?? previousResult?.focusAnchor,
    focusId: eventType === "scene.focus" ? firstString(raw.event_id, raw.id) : previousResult?.focusId,
    focusTurnId: eventType === "scene.focus" ? firstString(raw.turn_id, payload.turn_id) : previousResult?.focusTurnId,
    presentationMode: eventType === "scene.focus" ? ((raw.presentation_mode ?? payload.presentation_mode) === "display" ? "display" : "explain") : previousResult?.presentationMode,
    findings: findings.length ? findings : previousResult?.findings ?? [],
    visuals: [...visuals.values()].slice(0, 8),
    linkId:
      firstString(raw.link_id, raw.linkId, payload.link_id, payload.linkId, result.link_id, result.linkId) ?? existing?.linkId,
    resultId:
      firstString(raw.result_id, raw.resultId, payload.result_id, payload.resultId, result.result_id, result.resultId) ?? previousResult?.resultId,
    speechId:
      firstString(raw.speech_id, raw.speechId, payload.speech_id, payload.speechId, result.speech_id, result.speechId) ?? existing?.speechId,
    capabilityId:
      firstString(
        raw.capability_id,
        raw.capabilityId,
        payload.capability_id,
        payload.capabilityId,
        result.capability_id,
        result.capabilityId,
      ) ?? existing?.capabilityId,
    reason:
      firstString(
        raw.status_reason,
        raw.statusReason,
        raw.safe_error,
        raw.reason,
        payload.status_reason,
        payload.statusReason,
        payload.safe_error,
        payload.reason,
        result.status_reason,
        result.safe_error,
        result.reason,
        recordOrEmpty(result.error).message,
      ) ?? existing?.reason,
    approvalStatus:
      firstString(
        raw.approval_status,
        raw.approvalStatus,
        payload.approval_status,
        payload.approvalStatus,
      ) ?? existing?.approvalStatus,
    approvalExpiresAt:
      timeValue(raw, payload, "approval_expires_at", "approvalExpiresAt") ??
      existing?.approvalExpiresAt,
    approvalRequestPreview,
    activities: existing?.activities ?? [],
    status,
    kind: normalizeKind(raw, payload) === "unknown" ? existing?.kind ?? "unknown" : normalizeKind(raw, payload),
    title: safeTitle(raw.title ?? raw.display_title ?? payload.title ?? existing?.title),
    prompt:
      firstString(raw.prompt, payload.prompt, result.prompt, approvalRequest.prompt) ?? existing?.prompt,
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
    playbackStatus,
    playbackEndedAt:
      timeValue(raw, payload, "playback_ended_at", "playbackEndedAt", "delivered_at", "deliveredAt") ??
      existing?.playbackEndedAt,
    evidenceRefs: mergeUnique(previousResult?.evidenceRefs ?? [], evidenceRefs, (value) => value),
    sources: mergeUnique(previousResult?.sources ?? [], sources, sourceKey),
    artifacts: mergeUnique(previousResult?.artifacts ?? [], artifacts, artifactKey),
    limitations: mergeUnique(previousResult?.limitations ?? [], limitations, (value) => value),
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
    case "pausing":
      return "正在保存安全检查点，本轮工具执行结束后暂停。";
    case "paused":
      return "任务已暂停，已完成的工具结果和页面已保留。";
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
      if (task.reason) {
        const reason = task.reason.toLowerCase();
        if (reason.includes("permission") || reason.includes("权限")) {
          return "当前账号没有执行这项语音 Agent 能力的权限。";
        }
        if (reason.includes("provider") || reason.includes("unavailable") || reason.includes("不可用")) {
          return `当前能力暂不可用：${task.reason}`;
        }
        if (reason.includes("sandbox") || reason.includes("沙箱")) {
          return `沙箱任务未能启动：${task.reason}`;
        }
        return `任务未完成：${task.reason}`;
      }
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

export function voiceTaskPlaybackLabel(task: VoiceTask): string | null {
  switch (task.playbackStatus) {
    case "claimed":
      return "已领取，等待播报";
    case "enqueued":
      return "已排入播报队列";
    case "speaking":
      return "正在播报";
    case "played":
      return "服务端已播放完成";
    case "interrupted":
      return "播报被打断，可继续讲解";
    case "failed":
      return "播报失败，可重试";
    default:
      return null;
  }
}

export function voiceTaskTitle(task: VoiceTask): string {
  return task.title === "artifact_create" ? "子线程产物" : task.title;
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
    case "pausing":
      return "正在暂停";
    case "paused":
      return "已暂停";
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
    task.reason ||
    task.summary ||
      task.sources.length ||
      task.artifacts.length ||
      task.evidenceRefs.length ||
      task.limitations.length ||
      task.playbackStatus ||
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

export function mergeVoiceScenePresentation(state: VoiceTaskState, value: unknown): VoiceTaskState {
  const presentation = recordOrEmpty(value);
  return { ...state, tasks: state.tasks.map(task => {
    const matches = presentation.task_id === task.taskId && presentation.requirement_version === task.requirementVersion
      && (!presentation.result_version || presentation.result_version === task.resultVersion);
    return { ...task, focusAnchor: matches ? firstString(presentation.anchor_id) : undefined,
      focusId: matches ? firstString(presentation.id) : undefined, focusTurnId: matches ? firstString(presentation.turn_id) : undefined,
      presentationMode: matches ? (presentation.presentation_mode === "display" ? "display" : "explain") : undefined };
  }) };
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

  const eventActivity = taskActivityFromEvent(raw, eventType, nextTask);
  const activityKey = eventActivity
    ? `${eventActivity.eventId ?? ""}:${eventActivity.eventSeq ?? ""}:${eventActivity.text}`
    : "";
  const hasActivity = Boolean(
    eventActivity &&
      !(nextTask.activities ?? []).some(
        (item) => `${item.eventId ?? ""}:${item.eventSeq ?? ""}:${item.text}` === activityKey,
      ),
  );
  const taskWithActivity =
    hasActivity && eventActivity
      ? {
          ...nextTask,
          activities: [...(nextTask.activities ?? []), eventActivity].slice(-MAX_TASK_ACTIVITIES),
        }
      : nextTask;

  let next: VoiceTaskState = rememberTaskEvent(state, eventId);
  const changedTask = !existing || !sameTask(existing, taskWithActivity);
  next = {
    ...next,
    tasks: existing
      ? next.tasks.map((task) => (task.taskId === taskId ? taskWithActivity : task))
      : [...next.tasks, taskWithActivity],
    cursorByTask: {
      ...next.cursorByTask,
      [taskId]: Math.max(previousCursor, eventSeq ?? previousCursor),
    },
  };

  const activityText = incomingBeforeCurrent
    ? "需求已更新，旧结果已标记为过期。"
    : eventActivity?.text ?? voiceTaskActivityText(taskWithActivity, eventType);
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
            createdAt: eventActivity?.createdAt ?? taskWithActivity.updatedAt,
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
    const incomingResultVersion = firstNumber(raw.result_version, raw.resultVersion, payload.result_version);
    if (existing && rawRevision === existing.requirementVersion && incomingResultVersion !== undefined
        && incomingResultVersion < (existing.resultVersion ?? 0)) continue;
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
  let nextState: VoiceTaskState = { ...state, tasks };
  for (const raw of rawTasks) {
    const taskId = taskIdOf(raw);
    const events = Array.isArray(raw.events) ? raw.events : [];
    if (!taskId || !events.length) continue;
    for (const event of events) {
      if (!isRecord(event)) continue;
      nextState = reduceVoiceTaskEvent(nextState, event, taskId).state;
    }
  }
  return nextState;
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
        link_id: result.link_id ?? result.linkId,
        result_id: result.result_id ?? result.id,
        speech_id: result.speech_id ?? result.speechId ?? resultPayload.speech_id ?? resultPayload.speechId,
        status,
        delivery_status: deliveryStatus,
        playback_status: result.playback_status ?? result.playbackStatus ?? resultPayload.playback_status ?? resultPayload.playbackStatus,
        playback_ended_at: result.playback_ended_at ?? result.playbackEndedAt ?? resultPayload.playback_ended_at ?? resultPayload.playbackEndedAt,
        requirement_version: result.requirement_version,
        result_version: result.result_version,
        latest_result_version: result.result_version,
        summary: result.summary ?? result.safe_error,
        result: agentResult,
        visuals: resultPayload.visuals,
        deliverables: resultPayload,
        source_count: result.source_count,
        evidence_refs: result.evidence_refs ?? result.evidenceRefs ?? resultPayload.evidence_refs ?? resultPayload.evidenceRefs,
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

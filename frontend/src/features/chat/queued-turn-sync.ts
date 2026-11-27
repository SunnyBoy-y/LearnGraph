import type { QueuedTurnRecord } from "@/api/queued-turns";

export interface SyncableQueuedItem {
  id: string;
  serverTurnId?: string;
  workspaceId: string;
  sessionId: string;
  text: string;
  files: unknown[];
  pendingFiles: unknown[];
  stickers: unknown[];
  longPaste: string | null;
  generationMode: "text" | "image";
  imageSize: string;
  queuedAt: number;
}

export function serverTurnToQueueItem<T extends SyncableQueuedItem>(
  record: QueuedTurnRecord,
  defaults: {
    workspaceId: string;
    sessionId: string;
    defaultImageSize: T["imageSize"];
  },
): T {
  const payload = record.payload ?? {};
  const generationMode =
    payload.generation_mode === "image" ? "image" : "text";
  const imageSize =
    typeof payload.image_size === "string" && payload.image_size
      ? payload.image_size
      : defaults.defaultImageSize;
  const parsedTime = Date.parse(record.created_at);
  return {
    id: record.client_request_id || record.id,
    serverTurnId: record.id,
    workspaceId: defaults.workspaceId,
    sessionId: defaults.sessionId,
    text: record.content,
    files: [],
    pendingFiles: Array.isArray(payload.pending_files)
      ? payload.pending_files
      : [],
    stickers: Array.isArray(payload.stickers) ? payload.stickers : [],
    longPaste: null,
    generationMode,
    imageSize,
    queuedAt: Number.isFinite(parsedTime) ? parsedTime : Date.now(),
  } as unknown as T;
}

/**
 * Merge server-backed queued turns with local-only items (such as unsent local
 * File blobs that cannot survive cross-device serialization). Server ordering
 * is authoritative for server-backed items.
 */
export function mergeServerQueuedTurns<T extends SyncableQueuedItem>(
  localItems: readonly T[],
  serverRecords: readonly QueuedTurnRecord[] | null | undefined,
  defaults: {
    workspaceId: string;
    sessionId: string;
    defaultImageSize: T["imageSize"];
  },
): T[] {
  const safeLocal = Array.isArray(localItems) ? localItems : [];
  const safeServer = Array.isArray(serverRecords) ? serverRecords : [];
  const localByServerId = new Map<string, T>();
  const localByClientId = new Map<string, T>();
  for (const item of safeLocal) {
    if (item.serverTurnId) localByServerId.set(item.serverTurnId, item);
    localByClientId.set(item.id, item);
  }

  const matchedLocalIds = new Set<string>();
  const merged: T[] = [];

  for (const record of safeServer) {
    if (!record || record.status !== "queued") continue;
    const existing =
      localByServerId.get(record.id) ??
      localByClientId.get(record.client_request_id);
    if (existing) {
      matchedLocalIds.add(existing.id);
      merged.push({
        ...existing,
        serverTurnId: record.id,
        text: record.content,
      });
    } else {
      merged.push(serverTurnToQueueItem<T>(record, defaults));
    }
  }

  // Retain local-only items that have not been assigned a serverTurnId yet
  // (raw local File blobs, offline items, or in-flight enqueue calls).
  // Items that HAD a serverTurnId but disappeared from the server list were
  // cancelled/dispatched on another tab or worker and are dropped cleanly.
  for (const item of safeLocal) {
    if (matchedLocalIds.has(item.id)) continue;
    if (item.serverTurnId) continue;
    merged.push(item);
  }

  return merged;
}

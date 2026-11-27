import { describe, expect, it } from "vitest";

import type { QueuedTurnRecord } from "@/api/queued-turns";
import {
  mergeServerQueuedTurns,
  type SyncableQueuedItem,
} from "@/features/chat/queued-turn-sync";

function makeRecord(overrides: Partial<QueuedTurnRecord> = {}): QueuedTurnRecord {
  return {
    id: "turn-1",
    workspace_id: "ws-1",
    session_id: "sess-1",
    created_by: "u-1",
    client_request_id: "req-1",
    position: 0,
    status: "queued",
    content: "follow up from server",
    payload: {},
    dispatched_message_id: null,
    created_at: "2026-11-26T10:00:00Z",
    updated_at: "2026-11-26T10:00:00Z",
    ...overrides,
  };
}

describe("mergeServerQueuedTurns", () => {
  it("hydrates server turns on reload and drops stale server-backed shadows", () => {
    const local: SyncableQueuedItem[] = [
      {
        id: "req-stale",
        serverTurnId: "turn-dispatched",
        workspaceId: "ws-1",
        sessionId: "sess-1",
        text: "already dispatched on another tab",
        files: [],
        pendingFiles: [],
        stickers: [],
        longPaste: null,
        generationMode: "text",
        imageSize: "1024x1024",
        queuedAt: 1,
      },
      {
        id: "req-local-blob",
        workspaceId: "ws-1",
        sessionId: "sess-1",
        text: "with raw file",
        files: [{ type: "file", filename: "a.png" }],
        pendingFiles: [],
        stickers: [],
        longPaste: null,
        generationMode: "text",
        imageSize: "1024x1024",
        queuedAt: 2,
      },
    ];

    const merged = mergeServerQueuedTurns(
      local,
      [makeRecord({ id: "turn-2", client_request_id: "req-2", content: "from mobile" })],
      { workspaceId: "ws-1", sessionId: "sess-1", defaultImageSize: "1024x1024" },
    );

    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({
      id: "req-2",
      serverTurnId: "turn-2",
      text: "from mobile",
    });
    expect(merged[1]).toMatchObject({
      id: "req-local-blob",
      text: "with raw file",
    });
  });

  it("updates local item text when edited on another tab without duplicating", () => {
    const local: SyncableQueuedItem[] = [
      {
        id: "req-1",
        serverTurnId: "turn-1",
        workspaceId: "ws-1",
        sessionId: "sess-1",
        text: "old text",
        files: [],
        pendingFiles: [],
        stickers: [],
        longPaste: null,
        generationMode: "text",
        imageSize: "1024x1024",
        queuedAt: 1,
      },
    ];

    const merged = mergeServerQueuedTurns(
      local,
      [makeRecord({ content: "updated text" })],
      { workspaceId: "ws-1", sessionId: "sess-1", defaultImageSize: "1024x1024" },
    );

    expect(merged).toHaveLength(1);
    expect(merged[0].text).toBe("updated text");
  });
});

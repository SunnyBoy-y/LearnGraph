import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/checkpoints", () => ({
  previewTurnCheckpointRewind: vi.fn(),
  executeTurnCheckpointRewind: vi.fn(),
}));

import {
  executeTurnCheckpointRewind,
  previewTurnCheckpointRewind,
} from "@/api/checkpoints";
import { CheckpointRewindDialog } from "@/components/chat/checkpoint-rewind-dialog";

describe("CheckpointRewindDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads preview counts, highlights conflicts, and submits expected_current_hashes", async () => {
    vi.mocked(previewTurnCheckpointRewind).mockResolvedValue({
      chat_session_id: "sess-1",
      turn_id: "turn-1",
      restore_count: 1,
      delete_count: 1,
      conflict_count: 1,
      noop_count: 0,
      expected_current_hashes: {
        "work/a.py": "hash-a1",
        "work/new.py": "hash-new",
        "work/conflict.py": "hash-modified",
      },
      items: [
        {
          path: "work/a.py",
          action: "restore",
          existed_before: true,
          pre_sha256: "hash-a0",
          expected_sha256: "hash-a1",
          current_sha256: "hash-a1",
          conflict: false,
          conflict_reason: null,
        },
        {
          path: "work/new.py",
          action: "delete",
          existed_before: false,
          pre_sha256: null,
          expected_sha256: "hash-new",
          current_sha256: "hash-new",
          conflict: false,
          conflict_reason: null,
        },
        {
          path: "work/conflict.py",
          action: "restore",
          existed_before: true,
          pre_sha256: "hash-c0",
          expected_sha256: "hash-c1",
          current_sha256: "hash-modified",
          conflict: true,
          conflict_reason: "文件当前内容与记录的轮次终态不一致，已跳过。",
        },
      ],
    });
    vi.mocked(executeTurnCheckpointRewind).mockResolvedValue({
      chat_session_id: "sess-1",
      turn_id: "turn-1",
      restored_paths: ["work/a.py"],
      deleted_paths: ["work/new.py"],
      skipped_paths: ["work/conflict.py"],
      conflicts: [
        {
          path: "work/conflict.py",
          action: "restore",
          existed_before: true,
          pre_sha256: "hash-c0",
          expected_sha256: "hash-c1",
          current_sha256: "hash-modified",
          conflict: true,
          conflict_reason: "conflict",
        },
      ],
    });

    const onOpenChange = vi.fn();
    const onRewound = vi.fn();
    render(
      <CheckpointRewindDialog
        open
        sessionId="sess-1"
        turnId="turn-1"
        onOpenChange={onOpenChange}
        onRewound={onRewound}
      />,
    );

    await screen.findByText("恢复旧版本：1");
    expect(screen.getByText("删除新建文件：1")).toBeInTheDocument();
    expect(screen.getByText("冲突将跳过：1")).toBeInTheDocument();
    expect(screen.getByText("work/conflict.py")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /确认安全回退 \(2\)/ }));
    await waitFor(() =>
      expect(executeTurnCheckpointRewind).toHaveBeenCalledWith("sess-1", "turn-1", {
        expected_current_hashes: {
          "work/a.py": "hash-a1",
          "work/new.py": "hash-new",
          "work/conflict.py": "hash-modified",
        },
        include_subsequent_turns: true,
      }),
    );
    expect(onRewound).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

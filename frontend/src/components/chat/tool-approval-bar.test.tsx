import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/control", () => ({
  createSandboxDestructiveGrant: vi.fn().mockResolvedValue({ ok: true }),
}));
vi.mock("@/api/egress", () => ({
  decideEgressApproval: vi.fn().mockResolvedValue({ status: "approved" }),
  resumeEgressApproval: vi.fn().mockResolvedValue({ status: "resumed" }),
}));
vi.mock("@/api/fetch-authorizations", () => ({
  decideFetchAuthorization: vi.fn().mockResolvedValue(undefined),
  resumeFetchAuthorization: vi.fn().mockResolvedValue({ status: "resumed" }),
}));

import { createSandboxDestructiveGrant } from "@/api/control";
import { decideEgressApproval, resumeEgressApproval } from "@/api/egress";
import { decideFetchAuthorization } from "@/api/fetch-authorizations";
import { ToolApprovalBar } from "@/components/chat/tool-approval-bar";
import {
  collectPendingApprovals,
  type PendingApprovalItem,
} from "@/features/chat/pending-approvals";

describe("ToolApprovalBar and collectPendingApprovals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(createSandboxDestructiveGrant).mockResolvedValue({} as never);
    vi.mocked(decideEgressApproval).mockResolvedValue({ status: "approved" } as never);
    vi.mocked(resumeEgressApproval).mockResolvedValue({ status: "resumed", assistant_message_id: "m1" });
    vi.mocked(decideFetchAuthorization).mockResolvedValue(undefined);
  });

  it("collects and deduplicates sandbox_delete, egress, and fetch approvals including backend hydration", () => {
    const items = collectPendingApprovals({
      sessionId: "sess-1",
      messages: [
        {
          id: "m1",
          session_id: "sess-1",
          role: "assistant",
          status: "completed",
          version: 1,
          content: "",
          created_at: "2026-11-26T00:00:00Z",
          provider_trace: {},
          parts: [
            {
              id: "p1",
              type: "egress_authorization",
              status: "completed",
              data: {
                authorization_request_id: "eg-1",
                hostname: "api.github.com",
                authorization_status: "pending",
                resume_mode: "server",
              },
            },
          ],
        },
      ],
      sandboxAuthRequest: {
        chatSessionId: "sess-1",
        sandboxSessionId: "sb-1",
        commandIntentDigest: "digest-1",
        paths: ["work/tmp.txt"],
      },
      backendEgress: [
        {
          id: "eg-1",
          workspace_id: "w1",
          hostname: "api.github.com",
          capability: "agent_egress",
          requested_by: "u1",
          chat_session_id: "sess-1",
          status: "pending",
          decision: null,
          allow_always: false,
          decided_by: null,
          decided_at: null,
          expires_at: "2026-11-26T01:00:00Z",
          ttl_seconds: 300,
          request_context: null,
          consumed_at: null,
          created_at: "2026-11-26T00:00:00Z",
          updated_at: "2026-11-26T00:00:00Z",
        },
      ],
      backendFetch: [
        {
          id: "ft-1",
          workspace_id: "w1",
          chat_session_id: "sess-1",
          tool_call_id: "tc-1",
          tool_name: "fetch_web_page",
          requested_url: "https://arxiv.org/abs/1234",
          hostname: "arxiv.org",
          status: "pending",
          decision: null,
          requested_by: "u1",
          decided_by: null,
          decided_at: null,
          created_at: "2026-11-26T00:00:00Z",
          updated_at: "2026-11-26T00:00:00Z",
        },
      ],
    });

    expect(items.map((item) => item.id)).toEqual([
      "delete:digest-1",
      "egress:eg-1",
      "fetch:ft-1",
    ]);
  });

  it("does not steal focus from another focused input when approvals appear", () => {
    const onResolved = vi.fn();
    const { rerender } = render(
      <div>
        <input aria-label="侧栏重命名" />
        <ToolApprovalBar items={[]} onResolved={onResolved} />
      </div>,
    );
    const externalInput = screen.getByLabelText("侧栏重命名");
    externalInput.focus();
    expect(document.activeElement).toBe(externalInput);

    const items: PendingApprovalItem[] = [
      {
        id: "egress:eg-1",
        requestId: "eg-1",
        kind: "egress",
        chatSessionId: "sess-1",
        hostname: "pypi.org",
        resumeMode: "server",
      },
    ];
    rerender(
      <div>
        <input aria-label="侧栏重命名" />
        <ToolApprovalBar items={items} onResolved={onResolved} />
      </div>,
    );

    expect(screen.getByText("网络出站审批")).toBeInTheDocument();
    expect(document.activeElement).toBe(externalInput);
  });

  it("executes sandbox_delete, egress allow_once + server resume, and batch reject", async () => {
    const onResolved = vi.fn();
    const items: PendingApprovalItem[] = [
      {
        id: "delete:d1",
        kind: "sandbox_delete",
        chatSessionId: "sess-1",
        sandboxSessionId: "sb-1",
        commandIntentDigest: "d1",
        paths: ["work/a.txt"],
        action: "delete_path",
      },
      {
        id: "egress:eg-1",
        requestId: "eg-1",
        kind: "egress",
        chatSessionId: "sess-1",
        hostname: "pypi.org",
        resumeMode: "server",
      },
      {
        id: "fetch:ft-1",
        requestId: "ft-1",
        kind: "fetch",
        chatSessionId: "sess-1",
        hostname: "arxiv.org",
        requestedUrl: "https://arxiv.org/abs/1",
      },
    ];

    render(<ToolApprovalBar items={items} onResolved={onResolved} />);
    expect(screen.getByText("待审批 1 / 3")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "允许本次" }));
    await waitFor(() =>
      expect(createSandboxDestructiveGrant).toHaveBeenCalledWith(
        expect.objectContaining({
          path_prefix: "work/a.txt",
          command_intent_digest: "d1",
        }),
      ),
    );
    expect(onResolved).toHaveBeenCalledWith("delete:d1", "approved");

    fireEvent.click(screen.getByRole("button", { name: "下一条审批" }));
    expect(screen.getByText("网络出站审批")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "允许本次" }));
    await waitFor(() => expect(decideEgressApproval).toHaveBeenCalledWith("eg-1", "allow_once"));
    expect(resumeEgressApproval).toHaveBeenCalledWith("eg-1");

    fireEvent.click(screen.getByRole("button", { name: /全部拒绝/ }));
    await waitFor(() => expect(decideFetchAuthorization).toHaveBeenCalledWith("ft-1", "deny"));
  });
});

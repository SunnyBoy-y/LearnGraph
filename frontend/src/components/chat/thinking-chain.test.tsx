import "@/test/pdfjs-polyfills";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatStreamPartRenderer } from "@/components/chat/chat-stream-part-renderer";
import { ReasoningSummaryRow } from "@/components/chat/reasoning-summary-row";
import { ThinkingChain } from "@/components/chat/thinking-chain";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isReasoningTextPart } from "@/features/chat/chat-message-parts";
import type { MessagePart } from "@/types/sessions";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "ws-test" }),
}));

const part = (
  id: string,
  type: MessagePart["type"],
  overrides: Partial<MessagePart> = {},
): MessagePart => ({
  id,
  type,
  status: "completed",
  ...overrides,
});

function renderTimeline(
  chainParts: MessagePart[],
  options: {
    messageStatus?: string;
    defaultOpen?: boolean;
    finalAnswerStarted?: boolean;
    onRenderPart?: (part: MessagePart) => void;
  } = {},
) {
  return (
    <TooltipProvider>
      <ThinkingChain
        chainParts={chainParts}
        completedDurationSec={14}
        defaultOpen={options.defaultOpen}
        finalAnswerStarted={options.finalAnswerStarted}
        messageStatus={options.messageStatus ?? "completed"}
        renderPart={(item) => {
          options.onRenderPart?.(item);
          return isReasoningTextPart(item) ? (
            <ReasoningSummaryRow
              key={item.id}
              part={item}
              streaming={options.messageStatus === "streaming"}
            />
          ) : (
            <ChatStreamPartRenderer
              key={item.id}
              part={item}
              siblingParts={chainParts}
              streaming={
                options.messageStatus === "streaming" &&
                (item.status === "streaming" || item.status === "pending")
              }
            />
          );
        }}
        toolCallCount={chainParts.filter((item) => item.type === "tool_call").length}
      />
    </TooltipProvider>
  );
}

describe("ThinkingChain two-level collapsible process timeline", () => {
  const twoStageParts: MessagePart[] = [
    part("p0", "text", {
      content: "第一阶段：检索概念背景",
      data: { kind: "plan_narration" },
      sequence: 0,
    }),
    part("r0", "reasoning_content", {
      content: "先确定搜索关键词",
      sequence: 1,
    }),
    part("t0", "tool_call", {
      data: {
        tool_name: "search_web",
        input: { query: "spaced repetition" },
        output: { hits: 4 },
      },
      sequence: 2,
    }),
    part("p1", "text", {
      content: "第二阶段：读取网页验证",
      data: { kind: "plan_narration" },
      sequence: 3,
    }),
    part("t1", "tool_call", {
      data: {
        tool_name: "fetch_web_page",
        input: { url: "https://example.com/memory" },
        output: { ok: true },
      },
      sequence: 4,
    }),
  ];

  it("shows only collapsed stage titles, summaries, and statuses at Level 1 and mounts parts on demand", () => {
    const renderedIds: string[] = [];
    render(
      renderTimeline(twoStageParts, {
        messageStatus: "completed",
        onRenderPart: (item) => renderedIds.push(item.id),
      }),
    );

    expect(renderedIds).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "展开思维链条" }));

    const stage0 = document.querySelector('[data-stage-id="stage-0"]')!;
    const stage1 = document.querySelector('[data-stage-id="stage-1"]')!;
    expect(stage0).toHaveAttribute("data-stage-open", "false");
    expect(stage1).toHaveAttribute("data-stage-open", "false");
    expect(renderedIds).toEqual([]);

    expect(stage0).toHaveTextContent("第一阶段：检索概念背景");
    expect(stage0).toHaveTextContent("联网搜索");
    expect(stage1).toHaveTextContent("第二阶段：读取网页验证");
    expect(stage1).toHaveTextContent("读取网页");
  });

  it("expands multiple stages independently at Level 2 and preserves Level 3 tool I/O expansion", () => {
    render(renderTimeline(twoStageParts, { messageStatus: "completed" }));

    fireEvent.click(screen.getByRole("button", { name: "展开思维链条" }));

    const stage0Btn = document.querySelector('[data-stage-id="stage-0"] .process-stage__trigger')!;
    const stage1Btn = document.querySelector('[data-stage-id="stage-1"] .process-stage__trigger')!;

    fireEvent.click(stage0Btn);
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "true");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-open", "false");
    expect(document.querySelector('[data-stage-id="stage-0"] .process-stage__body')).toHaveTextContent("第一阶段：检索概念背景");
    // Level 3 tool detail starts collapsed
    expect(screen.queryByText(/"spaced repetition"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"hits"/)).not.toBeInTheDocument();

    // Opening stage-1 keeps stage-0 open
    fireEvent.click(stage1Btn);
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "true");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-open", "true");
    expect(document.querySelector('[data-stage-id="stage-1"] .process-stage__body')).toHaveTextContent("第二阶段：读取网页验证");

    // Expanding Level 3 tool details inside stage-0
    const toolTrigger = document.querySelector('[data-stage-id="stage-0"] .chat-tool-call__header')!;
    fireEvent.click(toolTrigger);
    expect(screen.getByText(/"spaced repetition"/)).toBeInTheDocument();
    expect(screen.getByText(/"hits"/)).toBeInTheDocument();
  });

  it("auto-collapses previous stage on new Commentary, preserves manual expand lock and manual collapse lock", () => {
    const stage0Initial: MessagePart[] = [
      part("p0", "text", {
        content: "阶段一：搜索",
        data: { kind: "plan_narration" },
        sequence: 0,
      }),
      part("t0", "tool_call", {
        status: "streaming",
        data: { tool_name: "search_web", input: { query: "q1" } },
        sequence: 1,
      }),
    ];

    const view = render(
      renderTimeline(stage0Initial, {
        messageStatus: "streaming",
        defaultOpen: true,
      }),
    );

    // Current stage auto-expands
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "true");

    // Tool completion does not collapse current stage; attaching a thinking summary stays in stage-0
    const stage0WithDoneToolAndReasoning: MessagePart[] = [
      stage0Initial[0],
      part("t0", "tool_call", {
        status: "completed",
        data: { tool_name: "search_web", input: { query: "q1" } },
        sequence: 1,
      }),
      part("r0", "reasoning_summary", {
        content: "继续整理搜索结果",
        sequence: 2,
      }),
    ];
    view.rerender(
      renderTimeline(stage0WithDoneToolAndReasoning, {
        messageStatus: "streaming",
        defaultOpen: true,
      }),
    );
    expect(document.querySelectorAll(".process-stage")).toHaveLength(1);
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "true");

    // New Commentary creates stage-1 (auto-open) and auto-collapses stage-0
    const withStage1: MessagePart[] = [
      ...stage0WithDoneToolAndReasoning,
      part("p1", "text", {
        content: "阶段二：验证结论",
        data: { kind: "plan_narration" },
        sequence: 3,
      }),
      part("t1", "tool_call", {
        status: "streaming",
        data: { tool_name: "fetch_web_page" },
        sequence: 4,
      }),
    ];
    view.rerender(
      renderTimeline(withStage1, {
        messageStatus: "streaming",
        defaultOpen: true,
      }),
    );
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "false");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-open", "true");

    // User manually expands historical stage-0 and manually collapses running stage-1
    fireEvent.click(document.querySelector('[data-stage-id="stage-0"] .process-stage__trigger')!);
    fireEvent.click(document.querySelector('[data-stage-id="stage-1"] .process-stage__trigger')!);
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "true");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-open", "false");

    // Incremental deltas + new stage-2 + answer.started must preserve BOTH manual locks
    const withStage2: MessagePart[] = [
      ...withStage1.slice(0, 4),
      part("t1", "tool_call", {
        status: "completed",
        data: { tool_name: "fetch_web_page" },
        sequence: 4,
      }),
      part("p2", "text", {
        content: "阶段三：整理汇总",
        data: { kind: "plan_narration" },
        sequence: 5,
      }),
    ];
    view.rerender(
      renderTimeline(withStage2, {
        messageStatus: "streaming",
        defaultOpen: true,
      }),
    );
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "true");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-open", "false");
    expect(document.querySelector('[data-stage-id="stage-2"]')).toHaveAttribute("data-stage-open", "true");

    // Manually re-open outer chain if needed and check answer.started collapses stage-2 while keeping stage-0 locked open
    fireEvent.click(screen.getByRole("button", { name: "收起思维链条" }));
    fireEvent.click(screen.getByRole("button", { name: "展开思维链条" }));
    view.rerender(
      renderTimeline(withStage2, {
        messageStatus: "streaming",
        defaultOpen: true,
        finalAnswerStarted: true,
      }),
    );
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "true");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-open", "false");
    expect(document.querySelector('[data-stage-id="stage-2"]')).toHaveAttribute("data-stage-open", "false");
  });

  it("keeps the outer chain and failed stage expanded when agent execution fails", () => {
    const runningParts: MessagePart[] = [
      part("p0", "text", {
        content: "第一阶段：准备环境",
        data: { kind: "plan_narration" },
        sequence: 0,
      }),
      part("p1", "text", {
        content: "第二阶段：执行命令",
        data: { kind: "plan_narration" },
        sequence: 1,
      }),
      part("t1", "tool_call", {
        status: "streaming",
        data: { tool_name: "sandbox_exec" },
        sequence: 2,
      }),
    ];

    const view = render(
      renderTimeline(runningParts, {
        messageStatus: "streaming",
        defaultOpen: false,
      }),
    );
    expect(screen.getByRole("button", { name: "展开思维链条" })).toHaveAttribute("data-state", "closed");

    const failedParts: MessagePart[] = [
      runningParts[0],
      runningParts[1],
      part("t1", "tool_call", {
        status: "failed",
        content: "命令退出码 1",
        data: { tool_name: "sandbox_exec" },
        sequence: 2,
      }),
    ];

    view.rerender(
      renderTimeline(failedParts, {
        messageStatus: "failed",
        defaultOpen: false,
      }),
    );

    expect(screen.getByRole("button", { name: "收起思维链条" })).toHaveAttribute("data-state", "open");
    expect(document.querySelector('[data-stage-id="stage-0"]')).toHaveAttribute("data-stage-open", "false");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-open", "true");
    expect(document.querySelector('[data-stage-id="stage-1"]')).toHaveAttribute("data-stage-status", "failed");
  });
});

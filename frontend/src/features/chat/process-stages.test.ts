import { describe, expect, it } from "vitest";

import { buildProcessStages, describeToolIntent } from "@/features/chat/process-stages";
import type { MessagePart } from "@/types/sessions";

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

describe("buildProcessStages", () => {
  it("splits stages by Commentary (plan_narration) and keeps leading reasoning in stage-0", () => {
    const parts: MessagePart[] = [
      part("empty", "text", { content: "   ", sequence: 0 }),
      part("r0", "reasoning_content", {
        content: "**梳理排查方向**\n先确认错误来源",
        sequence: 1,
      }),
      part("p0", "text", {
        content: "我去查最接近你这套构想的论文。",
        data: { kind: "plan_narration" },
        sequence: 2,
      }),
      part("t0", "tool_call", {
        data: { tool_name: "get_current_time" },
        sequence: 3,
      }),
      part("t1", "tool_call", {
        data: { tool_name: "search_papers", input: { query: "T-Mem" } },
        sequence: 4,
      }),
      part("t2", "tool_call", {
        data: { tool_name: "search_papers", input: { query: "DPCA" } },
        sequence: 5,
      }),
      part("r1", "reasoning_summary", {
        content: "比较记忆图谱论文",
        sequence: 6,
      }),
      part("p1", "text", {
        content: "目前已经找到两篇接近的工作，我继续读取原文细节。",
        data: { kind: "plan_narration" },
        sequence: 7,
      }),
      part("t3", "tool_call", {
        status: "streaming",
        data: { tool_name: "read_paper" },
        sequence: 8,
      }),
    ];

    const stages = buildProcessStages(parts, {
      messageStatus: "streaming",
      finalAnswerStarted: false,
    });

    expect(stages).toHaveLength(2);
    expect(stages[0]).toMatchObject({
      id: "stage-0",
      title: "梳理排查方向",
      summary: "获取当前时间 · 搜索论文 2 次",
      status: "completed",
      autoOpen: false,
      toolCount: 3,
    });
    expect(stages[0].parts.map((p) => p.id)).toEqual([
      "r0",
      "p0",
      "t0",
      "t1",
      "t2",
      "r1",
    ]);

    expect(stages[1]).toMatchObject({
      id: "stage-1",
      title: "目前已经找到两篇接近的工作，我继续读取原文细节",
      summary: "读取论文",
      status: "running",
      autoOpen: true,
      isCurrent: true,
      toolCount: 1,
    });
    expect(stages[1].parts.map((p) => p.id)).toEqual(["p1", "t3"]);
  });

  it("keeps the current stage open after tool completion and collapses finished stages on answer.started", () => {
    const parts: MessagePart[] = [
      part("p0", "text", {
        content: "先检索资料",
        data: { kind: "plan_narration" },
        sequence: 0,
      }),
      part("t0", "tool_call", {
        status: "completed",
        data: { tool_name: "search_web", input: { query: "React 19" } },
        sequence: 1,
      }),
    ];

    const stillThinking = buildProcessStages(parts, {
      messageStatus: "streaming",
      finalAnswerStarted: false,
    });
    expect(stillThinking[0]).toMatchObject({
      status: "running",
      autoOpen: true,
    });

    const answered = buildProcessStages(parts, {
      messageStatus: "streaming",
      finalAnswerStarted: true,
    });
    expect(answered[0]).toMatchObject({
      status: "completed",
      autoOpen: false,
    });
  });

  it("keeps failed stages marked failed and autoOpen=true", () => {
    const parts: MessagePart[] = [
      part("p0", "text", {
        content: "第一步：查找数据",
        data: { kind: "plan_narration" },
        sequence: 0,
      }),
      part("t0", "tool_call", {
        status: "failed",
        data: { tool_name: "sandbox_exec" },
        sequence: 1,
      }),
      part("p1", "text", {
        content: "第二步：重试方案",
        data: { kind: "plan_narration" },
        sequence: 2,
      }),
      part("t1", "tool_call", {
        status: "completed",
        data: { tool_name: "search_web" },
        sequence: 3,
      }),
    ];

    const stages = buildProcessStages(parts, {
      messageStatus: "completed",
      finalAnswerStarted: true,
    });

    expect(stages[0]).toMatchObject({
      status: "failed",
      hasFailure: true,
      autoOpen: true,
    });
    expect(stages[1]).toMatchObject({
      status: "completed",
      hasFailure: false,
      autoOpen: false,
    });
  });

  it("reuses tool intent and formats skill trigger without middle dot", () => {
    expect(
      describeToolIntent(
        part("sk", "skill_trigger", { data: { skill_name: "node-learning" } }),
      ),
    ).toBe("技能 node-learning");

    const singleToolStage = buildProcessStages(
      [
        part("t0", "tool_call", {
          data: { tool_name: "search_web", input: { query: "Vitest" } },
          sequence: 0,
        }),
        part("t1", "tool_call", {
          data: { tool_name: "search_web", input: { query: "React" } },
          sequence: 1,
        }),
      ],
      { messageStatus: "completed" },
    );

    expect(singleToolStage[0]).toMatchObject({
      title: "联网搜索",
      summary: "共 2 次调用",
      autoOpen: false,
    });
  });
});

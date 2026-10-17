import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/render";
import { AnswerParts } from "@/components/chat/answer-parts";
import { managedAnswerParts } from "./managed-agent-answer";
import type { Message, MessagePart } from "@/types/sessions";

vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => ({ workspaceId: "ws" }) }));
const message = (id: string, parts: MessagePart[], patch: Partial<Message> = {}): Message => ({
  id, workspace_id: "ws", session_id: "child", parent_message_id: null, role: "assistant", version: 1,
  status: "completed", content: "", parts, provider_trace: {}, created_at: "2026-10-17T00:00:00Z", ...patch,
});
const text = (id: string, content: string): MessagePart => ({ id, type: "text", status: "completed", content });

describe("ordinary child answer rendering", () => {
  it("renders real Markdown using the same answer body as the parent", async () => {
    const { container } = renderWithProviders(<AnswerParts parts={[text("t", "**核对说明**\n\n| 项目 | 结果 |\n| --- | --- |\n| 差值 | 63 |")]} />);
    expect(await screen.findByRole("table")).toHaveTextContent("差值63");
    expect(screen.getByText("核对说明")).toBeInTheDocument();
    expect(container.textContent).not.toContain("**");
  });
  it("preserves every public component type and excludes execution details and old answers", () => {
    const types: MessagePart["type"][] = ["chart", "quiz", "component", "magic_card", "image", "sandbox_artifact", "subapp_artifact", "ui", "attachment", "source_list"];
    const outputs = types.map((type, index): MessagePart => ({ id: `output-${index}`, type, status: "completed" }));
    const parts = managedAnswerParts([
      message("early", [text("old", "Old answer"), outputs[0]]),
      message("user", [text("instruction", "Internal prompt")], { role: "user" }),
      message("final", [...outputs.slice(1), text("answer", "Final answer"), { id: "reason", type: "reasoning_content", status: "completed", content: "Process" }]),
    ]);
    expect(parts.map(part => part.type)).toEqual(expect.arrayContaining(types));
    for (const id of ["old", "instruction", "reason"]) expect(parts.map(part => part.id)).not.toContain(id);
    expect(parts.find(part => part.id === "answer")?.content).toBe("Final answer");
  });
  it("uses the newest committed UI after takeover and keeps earlier file outputs", () => {
    const ui = (id: string, revision: number): MessagePart => ({ id, type: "ui", status: "completed", data: { block_id: "table", revision } });
    const parts = managedAnswerParts([
      message("first", [ui("a", 1), { id: "file", type: "sandbox_artifact", status: "completed", data: { file_id: "file" } }]),
      message("takeover", [ui("b", 2), text("done", "Updated")]),
    ]);
    expect(parts.filter(part => part.type === "ui")).toEqual([ui("b", 2)]);
    expect(parts.some(part => part.id === "file")).toBe(true);
  });
  it("applies the ordinary answer boundary and places referenced components inline", () => {
    const parts = managedAnswerParts([message("final", [
      { ...text("plan", "先查询资料。"), data: { kind: "plan_narration" } },
      { ...text("answer", "下面是表：[[artifact:block_id:table]]结束。"), data: { kind: "final_answer" } },
      { id: "ui", type: "ui", status: "completed", data: { block_id: "table" } },
    ])]);
    expect(parts.some(part => part.id === "plan")).toBe(false);
    expect(parts.map(part => part.type)).toEqual(["text", "ui", "text"]);
    expect(parts.filter(part => part.type === "text").map(part => part.content)).toEqual(["下面是表：", "结束。"]);
  });
});

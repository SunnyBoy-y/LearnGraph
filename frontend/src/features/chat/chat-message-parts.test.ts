import { describe, expect, it } from "vitest";
import type { MessagePart } from "@/types/sessions";
import { groupPartsForDisplay } from "./chat-message-parts";

function part(id: string, type: MessagePart["type"], sequence: number, kind?: string): MessagePart {
  return { id, type, sequence, content: id, status: "streaming", data: kind ? { kind } : {} };
}
const visible = (parts: MessagePart[], live = true, finalPartId?: string) =>
  groupPartsForDisplay(parts, { live, boundary: finalPartId ? { finalPartId } : undefined })
    .flatMap((segment) => segment.parts.filter((p) => p.content?.trim()).map((p) => p.content));

describe("text/reasoning stream presentation", () => {
  it("reproduces the old premature final_answer classification jump", () => {
    const draft = part("session1", "text", 1, "final_answer");
    const reasoning = part("think1", "reasoning_content", 2);
    const tool = part("tool1", "tool_call", 3);
    expect(visible([draft, reasoning, tool])).toEqual(["think1", "tool1", "session1"]);
    expect(visible([{ ...draft, data: { kind: "plan_narration" } }, reasoning, tool]))
      .toEqual(["session1", "think1", "tool1"]);
  });

  it("keeps unclassified narration in place as reasoning and tools arrive", () => {
    const stream = [part("session1", "text", 1), part("think1", "reasoning_content", 2), part("tool1", "tool_call", 3)];
    for (let end = 1; end <= stream.length; end++) {
      expect(visible(stream.slice(0, end))).toEqual(stream.slice(0, end).map((p) => p.id));
    }
    const promoted = [{ ...stream[0], id: "plan1", data: { kind: "plan_narration" } }, ...stream.slice(1)];
    expect(visible(promoted)).toEqual(visible(stream));
  });

  it("preserves the requested five-block order through final boundary and reload", () => {
    const stream = [
      part("session1", "text", 1, "plan_narration"),
      part("think1", "reasoning_content", 2),
      part("tool1", "tool_call", 3),
      part("think2", "reasoning_content", 5),
      part("session2", "text", 6),
    ];
    const expected = ["session1", "think1", "tool1", "think2", "session2"];
    expect(visible(stream)).toEqual(expected);
    expect(visible(stream, true, "session2")).toEqual(expected);
    const history = stream.map((p) => ({ ...p, status: "completed" as const, data: p.id === "session2" ? { kind: "final_answer" } : p.data }));
    expect(visible(history, false)).toEqual(expected);
  });

  it("keeps ordinary no-tool final answers outside the thinking fold", () => {
    const stream = [part("answer", "text", 1, "final_answer"), part("think", "reasoning_content", 2)];
    expect(groupPartsForDisplay(stream, { live: true, boundary: { finalPartId: "answer" } })
      .map((s) => [s.kind, s.parts.map((p) => p.id)]))
      .toEqual([["chain", ["think"]], ["parts", ["answer"]]]);
  });
});

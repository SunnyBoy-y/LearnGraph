import { expect, it } from "vitest";
import { parseUISnapshot } from "@/lib/stream-ui";
import { voiceSceneExplanation } from "./voice-scene-explanation";

it("preserves visible values and provenance without exposing rendering metadata", () => {
  const scene = parseUISnapshot({ protocol: "suip/0.1", block_id: "internal-block", component: "table", revision: 3, state: "completed",
    props: { title: "验收结果", source_note: "实际命令输出", columns: [{ key: "value", label: "数值" }] },
    rows: [{ id: "output", label: "命令输出", values: { value: 14 } }] });
  expect(scene).not.toBeNull();
  const text = voiceSceneExplanation(scene!);
  expect(text).toContain("命令输出：数值：14");
  expect(text).toContain("数据来源：实际命令输出");
  expect(text).not.toMatch(/block_id|chart_type|protocol|internal-block/);
});

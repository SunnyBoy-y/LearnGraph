import { describe, expect, it } from "vitest";
import { parseUISnapshot } from "@/lib/stream-ui";
import { documentMarkdown } from "./document-export";

describe("document exports", () => {
  it("preserves hierarchy, source labels, missing data and code fences", () => {
    const snapshot = parseUISnapshot({protocol: "suip/0.1", block_id: "report", component: "document", state: "completed", revision: 2,
      props: {title: "报告", density: "editorial", toc: true}, blocks: [
        {id: "s", type: "section", label: "结论"},
        {id: "h", type: "heading", parent_id: "s", level: 2, text: "增长"},
        {id: "c", type: "code", text: "```\nhello", language: "text"},
        {id: "t", type: "chart", visual: {props: {title: "月活", columns: [{key: "v", label: "用户数"}], source_note: "用户提供", data_kind: "example"}, rows: [{id: "may", label: "5月", values: {v: 1200}}, {id: "jun", label: "6月", values: {}}]}},
      ]});
    expect(snapshot).not.toBeNull();
    const text = documentMarkdown(snapshot!);
    expect(text).toContain("# 报告\n\n结论\n\n## 增长");
    expect(text).toContain("````text\n```\nhello\n````");
    expect(text).toContain("| 6月 | — |");
    expect(text).toContain("用户提供");
    expect(text).toContain("示例数据");
    expect(text).not.toContain("suip/0.1");
  });
});

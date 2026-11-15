import { describe, expect, it } from "vitest";
import type { MessagePart } from "@/types/sessions";
import { messageCopyText } from "./message-copy";

const text = (id: string, content: string, sequence?: number): MessagePart => ({ id, type: "text", status: "completed", content, sequence });
const ui = (block_id: string, component: string, data: Record<string, unknown> = {}): MessagePart => ({
  id: block_id, type: "ui", status: "completed",
  data: { protocol: "suip/0.1", block_id, component, state: "completed", revision: 1, props: { title: block_id }, ...data },
});

describe("messageCopyText", () => {
  it("copies rich documents in placement order exactly once, including hidden sections", () => {
    const doc = ui("document", "document", { blocks: [
      { id: "heading", type: "heading", level: 2, text: "六个阶段" },
      { id: "tabs", type: "tabs" },
      { id: "first", parent_id: "tabs", type: "section", label: "第一个标签" },
      { id: "code", parent_id: "first", type: "code", language: "js", text: "const answer = 42;" },
      { id: "second", parent_id: "tabs", type: "section", label: "未选中的标签" },
      { id: "math", parent_id: "second", type: "math", text: "x^2", inline: false },
      { id: "accordion", type: "accordion" },
      { id: "collapsed", parent_id: "accordion", type: "section", label: "折叠内容", text: "仍然完整复制" },
    ] });
    const result = messageCopyText([doc, text("body", "开头\n[[artifact:block_id:document]]\n结尾")]);
    expect(result.indexOf("开头")).toBeLessThan(result.indexOf("# document"));
    expect(result.indexOf("# document")).toBeLessThan(result.indexOf("结尾"));
    expect(result.match(/# document/g)).toHaveLength(1);
    for (const content of ["## 六个阶段", "第一个标签", "```js\nconst answer = 42;\n```", "未选中的标签", "$$", "x^2", "折叠内容", "仍然完整复制"]) expect(result).toContain(content);
    expect(result).not.toContain("artifact:");
    expect(result).not.toContain("suip/0.1");
  });

  it.each(["chart", "table"])("copies all %s rows, missing values and provenance", component => {
    const result = messageCopyText([ui(component, component, {
      props: { title: "数据", description: "完整数据", columns: [{ key: "v", label: "人数" }], unit: "人", source_note: "用户提供", time_range: "2026年" },
      rows: [{ id: "a", label: "一月", values: { v: 12 } }, { id: "b", label: "二月", values: {} }],
    })]);
    for (const content of ["数据", "完整数据", "| 项目 | 人数 |", "| 一月 | 12 |", "| 二月 | — |", "单位：人", "用户提供", "2026年"]) expect(result).toContain(content);
  });

  it("copies graph relationships, timelines and metrics alongside ordered text", () => {
    const graph = ui("graph", "graph", { nodes: [{ id: "a", label: "起点", description: "节点说明" }, { id: "b", label: "终点" }], edges: [{ id: "ab", source: "a", target: "b", label: "连接" }] });
    const timeline = ui("timeline", "timeline", { items: [{ id: "event", label: "事件", date: "2026-11-15", description: "事件说明" }] });
    const metric = ui("metric", "metric", { items: [{ id: "count", label: "数量", value: 42 }] });
    expect(messageCopyText([text("last", "结尾", 5), { ...graph, sequence: 2 }, text("first", "开头", 1), { ...timeline, sequence: 3 }, { ...metric, sequence: 4 }])).toMatch(/^开头[\s\S]*起点：节点说明[\s\S]*起点 → 终点：连接[\s\S]*2026-11-15[\s\S]*事件说明[\s\S]*数量 · 42[\s\S]*结尾$/);
  });

  it("excludes removed/invalid snapshots, process data and transport markers", () => {
    const result = messageCopyText([
      text("thinking", "正在思考"),
      { id: "ack", type: "acknowledgement", status: "completed", content: "正在思考" },
      { id: "tool", type: "tool_call", status: "completed", data: { secret: "internal payload" } },
      { id: "invalid", type: "ui", status: "completed", data: { protocol: "invalid" } },
      ui("removed", "document", { state: "removed", blocks: [{ id: "gone", type: "paragraph", text: "已删除" }] }),
      text("answer", "正文[[artifact:missing]]"),
      { id: "sticker", type: "sticker", status: "completed", data: { alt: "开心" } },
    ]);
    expect(result).toBe("正文\n\n[表情：开心]");
  });

  it("retains image URLs, captions and source attribution", () => {
    const result = messageCopyText([ui("images", "document", { blocks: [
      { id: "image", type: "image", alt: "图片说明", caption: "图片注释", media: [{ url: "https://example.com/photo.png", title: "图片标题", source_url: "https://example.com/article", attribution: "作者" }] },
    ] })]);
    for (const value of ["![图片标题](https://example.com/photo.png)", "来源：https://example.com/article", "作者", "图片注释"]) expect(result).toContain(value);
  });

  it("copies only committed numbers from live previews", () => {
    const committed = ui("chart", "chart", { props: { title: "统计", columns: [{ key: "v", label: "人数" }] }, rows: [{ id: "a", label: "当前", values: { v: 12 } }] });
    const result = messageCopyText([ui("chart", "chart", {
      ...committed.data, state: "streaming", rows: [{ id: "a", label: "当前", values: { v: 123456 } }],
      preview: { tool_call_id: "call", sequence: 1, base: committed.data },
    })]);
    expect(result).toContain("| 当前 | 12 |");
    expect(result).not.toContain("123456");
  });

  it("retains plain-text history fallback and removes unfinished placement syntax", () => {
    expect(messageCopyText([], "历史消息")).toBe("历史消息");
    expect(messageCopyText([{ ...text("live", "回答[[artifact:block_id:"), status: "streaming" }])).toBe("回答");
    expect(messageCopyText([text("answer", "消息正文")], "过期的消息正文")).toBe("消息正文");
  });
});

import type { DocumentNode, UISnapshot } from "@/lib/stream-ui";
import { mathMarkdown } from "@/lib/markdown";

import { simplifyDocument } from "./presentation-simplifier";

const cell = (value: unknown): string => String(value ?? "—").replace(/\|/g, "\\|").replace(/[\r\n]+/g, " ");

/** Export content and provenance, never executable presentation instructions. */
export function documentMarkdown(snapshot: UISnapshot): string {
  const blocks = simplifyDocument(snapshot);
  function visual(data: Record<string, unknown>): string {
    const props = data.props as UISnapshot["props"];
    const rows = (data.rows ?? []) as UISnapshot["rows"];
    const items = (data.items ?? []) as UISnapshot["items"];
    const nodes = (data.nodes ?? []) as UISnapshot["nodes"];
    const edges = (data.edges ?? []) as UISnapshot["edges"];
    const columns = props.columns ?? [];
    const table = rows.length ? [
      `| 项目 | ${columns.map(column => cell(column.label)).join(" | ")} |`,
      `| --- | ${columns.map(() => "---").join(" | ")} |`,
      ...rows.map(row => `| ${cell(row.label)} | ${columns.map(column => cell(row.values[column.key])).join(" | ")} |`),
    ].join("\n") : "";
    return [props.title, table,
      ...items.map(item => `- ${[item.date, item.label, item.value, item.description].filter(value => value != null && value !== "").map(cell).join(" · ")}${props.unit || ""}`),
      ...nodes.map(node => `- ${node.label}${node.description ? `：${node.description}` : ""}`),
      ...edges.map(edge => `- ${nodes.find(node => node.id === edge.source)?.label ?? edge.source} → ${nodes.find(node => node.id === edge.target)?.label ?? edge.target}${edge.label ? `：${edge.label}` : ""}`),
      props.data_kind === "example" ? "示例数据" : "", props.source_note, props.time_range,
    ].filter(Boolean).join("\n\n");
  }
  function render(node: DocumentNode): string {
    const children = blocks.filter(child => child.parent_id === node.id).map(render).join("\n\n");
    let text = node.text || node.label;
    if (node.type === "rail_item") {
      const parent = blocks.find(parent => parent.id === node.parent_id);
      const siblings = blocks.filter(child => child.parent_id === node.parent_id) ?? [];
      const marker = parent?.type === "step_list" ? `${siblings.findIndex(item => item.id === node.id) + 1}.` : "-";
      const state = parent?.type === "status_list" ? ` [${{pending: "待处理", active: "进行中", completed: "已完成", error: "未完成"}[node.item_status ?? "pending"]}]` : "";
      text = `${marker}${state} **${node.label}**${node.text ? `\n\n  ${node.text.replace(/\n/g, "\n  ")}` : ""}`;
    }
    if (node.type === "heading") text = `${"#".repeat(node.level)} ${text}`;
    if (node.type === "quote") text = text.split("\n").map(line => `> ${line}`).join("\n");
    if (node.type === "code") {
      const fence = "`".repeat(Math.max(3, ...Array.from(text.matchAll(/`+/g), match => match[0].length + 1)));
      text = `${fence}${node.language}\n${text}\n${fence}`;
    }
    if (node.type === "math") text = mathMarkdown(text, node.inline);
    if (node.type === "divider") text = "---";
    if (node.visual) text = visual(node.visual);
    if (node.media.length) text = node.media.map(media => `${media.title || node.alt}${media.source_url ? `（${media.source_url}）` : ""}${media.attribution ? ` · ${media.attribution}` : ""}`).join("\n\n");
    if ((node.state === "pending" || node.state === "error") && node.type !== "rail_item") text = `${node.label || "内容"}（${node.state === "error" ? "生成失败" : "尚未完成"}）`;
    return [text, children, node.caption].filter(Boolean).join("\n\n");
  }
  return [`# ${snapshot.props.title}`, snapshot.props.description,
    ...blocks.filter(node => !node.parent_id).map(render),
    snapshot.props.data_kind === "example" ? "示例数据" : "", snapshot.props.source_note, snapshot.props.time_range,
  ].filter(Boolean).join("\n\n") + "\n";
}

export function downloadDocument(snapshot: UISnapshot) {
  const url = URL.createObjectURL(new Blob([documentMarkdown(snapshot)], {type: "text/markdown;charset=utf-8"}));
  const link = document.createElement("a");
  link.href = url;
  const name = Array.from(snapshot.props.title, char => char.charCodeAt(0) < 32 ? "_" : char).join("").replace(/[<>:"/\\|?*]/g, "_").slice(0, 100);
  link.download = `${name || "LearnGraph"}.md`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

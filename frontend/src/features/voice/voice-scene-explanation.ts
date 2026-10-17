import type { UISnapshot } from "@/lib/stream-ui";

/** Speak about visible content, without reading the rendering protocol aloud. */
export function voiceSceneExplanation(scene: UISnapshot): string {
  const columns = scene.props.columns ?? [];
  const labels = new Map((scene.nodes ?? []).map(node => [node.id, node.label]));
  return [scene.props.title, scene.props.description,
    scene.props.source_note ? `数据来源：${scene.props.source_note}` : "",
    ...(scene.rows ?? []).map(row => `${row.label}：${Object.entries(row.values).map(([key, value]) =>
      `${columns.find(column => column.key === key)?.label ?? key}：${value ?? "未提供"}`).join("；")}`),
    ...(scene.items ?? []).map(item => [item.label, item.value, item.date, item.description].filter(value => value != null && value !== "").join("；")),
    ...(scene.nodes ?? []).map(node => [node.label, node.description].filter(Boolean).join("：")),
    ...(scene.edges ?? []).map(edge => `${labels.get(edge.source) ?? edge.source} → ${labels.get(edge.target) ?? edge.target}：${edge.label ?? ""}`),
  ].filter(Boolean).join("\n").slice(0, 8000);
}

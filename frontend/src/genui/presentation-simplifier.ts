import { parseUISnapshot, type DocumentNode, type UISnapshot } from "@/lib/stream-ui";

/** Pure view policy. Persisted snapshots, revisions and action targets are untouched. */
export function presentation(s: UISnapshot) {
  const chain = s.props.graph_layout === "mindmap" || s.props.interaction === "explore" || (s.component === "graph" && s.state === "streaming") ? null : linearChain(s);
  return {
    form: s.component === "graph" && !chain ? "canvas" : s.component === "chart" || (s.component === "table" && s.rows.length > 8) ? "data" : "editorial",
    tableExplore: s.props.interaction === "explore" || (s.props.interaction !== "static" && s.rows.length > 8),
    chain,
  } as const;
}

/** Only a small, connected, directed chain qualifies; branches and cycles retain the canvas. */
export function linearChain(s: Pick<UISnapshot, "nodes" | "edges">): UISnapshot["nodes"] | null {
  if (!s.nodes.length || s.nodes.length > 6 || s.edges.length !== s.nodes.length - 1) return null;
  const incoming = new Set<string>(), outgoing = new Map<string, string>();
  for (const e of s.edges) {
    if (incoming.has(e.target) || outgoing.has(e.source) || e.source === e.target) return null;
    incoming.add(e.target); outgoing.set(e.source, e.target);
  }
  const starts = s.nodes.filter(n => !incoming.has(n.id));
  if (starts.length !== 1) return null;
  const byId = new Map(s.nodes.map(n => [n.id, n]));
  const result: UISnapshot["nodes"] = [], seen = new Set<string>();
  let current: string | undefined = starts[0].id;
  while (current && !seen.has(current)) {
    const node = byId.get(current);
    if (!node) return null;
    result.push(node); seen.add(current); current = outgoing.get(current);
  }
  return result.length === s.nodes.length ? result : null;
}

/** Conservative deduplication: adjacent sibling step/graph representations only.
 * Every title and description must match exactly, and no relation/provenance,
 * caption, extra text or group information may be lost. Never deduplicate previews.
 */
export function simplifyDocument(snapshot: UISnapshot): DocumentNode[] {
  const blocks = snapshot.blocks ?? [];
  if (snapshot.state !== "completed" || snapshot.preview) return blocks;
  const hidden = new Set<string>();
  const normalize = (text: string) => text.trim().replace(/\s+/g, " ");
  const siblings = new Map<string | null, DocumentNode[]>();
  for (const b of blocks) {
    const key = b.parent_id ?? null;
    const group = siblings.get(key) ?? [];
    group.push(b); siblings.set(key, group);
  }
  for (const group of siblings.values()) for (let i = 1; i < group.length; i++) {
    const a = group[i - 1], b = group[i];
    const steps = a.type === "step_list" ? a : b.type === "step_list" ? b : null;
    const graph = ["graph", "diagram"].includes(a.type) ? a : ["graph", "diagram"].includes(b.type) ? b : null;
    if (!steps || !graph || !graph.visual || graph.state !== "completed" || steps.state !== "completed" || graph.text || graph.caption || graph.label || steps.text) continue;
    const parsed = parseUISnapshot({...graph.visual, protocol: "suip/0.1", component: "graph", block_id: graph.id, revision: 0, state: "completed"});
    if (!parsed || parsed.props.source_note || parsed.props.time_range || parsed.props.description || parsed.props.data_ref || parsed.props.data_kind === "example" || parsed.props.interaction === "explore" || parsed.edges.some(e => e.label)) continue;
    const chain = linearChain(parsed), items = siblings.get(steps.id) ?? [];
    if (chain && chain.length === items.length && chain.every((n, j) => !n.group && items[j].state === "completed" && normalize(n.label) === normalize(items[j].label) && normalize(n.description) === normalize(items[j].text))) hidden.add(graph.id);
  }
  return blocks.filter(b => !hidden.has(b.id));
}

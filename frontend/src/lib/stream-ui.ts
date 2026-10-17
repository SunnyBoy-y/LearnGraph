import { z } from "zod";
import { parseIllustrationSvg } from "./safe-illustration";

const id = z
  .string()
  .min(1)
  .max(100)
  .regex(/^[a-zA-Z0-9_-]+$/)
  .refine(
    (value) => !["__proto__", "prototype", "constructor"].includes(value),
  );
const scalar = z.union([
  z.string().max(2000),
  z.number().finite(),
  z.boolean(),
  z.null(),
]);
const column = z
  .object({ key: id, label: z.string().min(1).max(100) })
  .strict();
const row = z
  .object({
    id,
    label: z.string().max(240).default(""),
    values: z
      .record(id, scalar)
      .refine((value) => Object.keys(value).length <= 12),
  })
  .strict();
const node = z
  .object({
    id,
    label: z.string().min(1).max(120),
    description: z.string().max(2000).default(""),
    group: z.string().max(80).default(""),
  })
  .strict();
const edge = z
  .object({
    id,
    source: id,
    target: id,
    label: z.string().max(120).default(""),
  })
  .strict();
const item = z
  .object({
    id,
    label: z.string().min(1).max(120),
    value: scalar.default(null),
    description: z.string().max(2000).default(""),
    date: z.string().max(80).default(""),
  })
  .strict();

const publicMediaURL = z.string().max(2000).refine((value) => {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password &&
      !url.port && !/[[\]:]/.test(url.hostname) &&
      !/^\d+(?:\.\d+)*$/.test(url.hostname) &&
      !/(^|\.)(localhost|local|internal|test|invalid)$/.test(url.hostname) &&
      url.hostname.includes(".") && !/\.svg(?:$|[?#])/i.test(url.pathname);
  } catch { return false; }
});
export const documentNodeSchema = z.object({
  id, parent_id: id.nullish(),
  state: z.enum(["pending", "streaming", "completed", "error"]).default("completed"),
  revision: z.number().int().nonnegative().default(0),
  type: z.enum(["paragraph", "heading", "list", "quote", "code", "math", "table", "divider",
    "image", "image_group", "gallery", "figure", "chart", "diagram", "graph", "timeline",
    "simulation", "interactive", "section", "stack", "row", "columns", "grid", "media_text", "tabs", "accordion"]),
  text: z.string().max(16000).default(""), label: z.string().max(240).default(""),
  level: z.number().int().min(2).max(6).default(3), columns: z.number().int().min(1).max(3).default(2),
  align: z.enum(["start", "center", "end"]).default("start"), width: z.enum(["reading", "full"]).default("reading"),
  language: z.string().max(40).regex(/^[a-zA-Z0-9_+#.-]*$/).default(""),
  query: z.string().max(500).default(""), alt: z.string().max(500).default(""), caption: z.string().max(1000).default(""),
  query_alternatives: z.array(z.string().min(1).max(150)).max(2).default([]),
  fallback_svg: z.string().max(24000).default("").refine(value => !value || parseIllustrationSvg(value) !== null, "Unsafe illustration SVG"),
  engine: z.enum(["chase", "quadratic", "sort_steps"]).default("chase"),
  inline: z.boolean().default(false),
  values: z.array(z.number().finite().min(-1000).max(1000)).max(30).default([]),
  visual: z.record(z.string(), z.unknown()).nullish(),
  media: z.array(z.object({url: publicMediaURL, file_id: id.nullish(), source_url: z.union([publicMediaURL, z.literal("")]).default(""),
    title: z.string().max(500).default(""), attribution: z.string().max(500).default("")}).strict()).max(6).default([]),
  resource_state: z.enum(["pending", "ready", "unavailable"]).default("pending"),
  resource_reason: z.enum(["", "ready", "no_results", "search_failed", "download_failed", "timeout", "disabled", "provider_unavailable", "capacity", "storage_failed"]).default(""),
}).strict();
export type DocumentNode = z.infer<typeof documentNodeSchema>;
const layoutTypes = new Set(["section", "stack", "row", "columns", "grid", "media_text", "tabs", "accordion", "figure"]);

export const uiSnapshotSchema = z
  .object({
    protocol: z.literal("suip/0.1"),
    block_id: id,
    component: z.enum(["chart", "graph", "timeline", "table", "metric", "document"]),
    revision: z.number().int().nonnegative(),
    resource_revision: z.number().int().nonnegative().optional(),
    preview: z.object({
      tool_call_id: z.string().min(1).max(200),
      sequence: z.number().int().positive(),
      base: z.record(z.string(), z.unknown()).nullable(),
    }).strict().nullish(),
    state: z.enum(["streaming", "completed", "removed"]),
    props: z
      .object({
        title: z.string().min(1).max(240),
        description: z.string().max(2000).default(""),
        chart_type: z.enum(["line", "bar"]).default("line"),
        columns: z.array(column).max(12).default([]),
        unit: z.string().max(40).default(""),
        source_note: z.string().max(500).default(""),
      })
      .strict(),
    rows: z.array(row).max(500).default([]),
    nodes: z.array(node).max(100).default([]),
    edges: z.array(edge).max(300).default([]),
    items: z.array(item).max(100).default([]),
    blocks: z.array(documentNodeSchema).max(160).optional(),
    // Artifact placement is added by the existing answer orchestration layer.
    layout: z.enum(["inline", "block", "side"]).nullish(),
  })
  .strict()
  .superRefine((snapshot, ctx) => {
    const blocks = snapshot.blocks ?? [];
    const byId = new Map(blocks.map((node) => [node.id, node]));
    const bad = (message: string) => ctx.addIssue({ code: "custom", message });
    if (byId.size !== blocks.length || (blocks.length && snapshot.component !== "document")) bad("Invalid document blocks");
    const counts = new Map<string | null, number>();
    for (const node of blocks) {
      if (node.fallback_svg && !["image", "image_group", "gallery"].includes(node.type)) bad("Illustration fallback requires image node");
      const parentId = node.parent_id ?? null;
      counts.set(parentId, (counts.get(parentId) ?? 0) + 1);
      const seen = new Set([node.id]);
      let parent = parentId;
      while (parent != null) {
        const entry = byId.get(parent);
        if (seen.has(parent) || !entry || seen.size >= 7 || !layoutTypes.has(entry.type)) {
          bad("Invalid parent, cycle or document depth"); break;
        }
        seen.add(parent); parent = entry.parent_id ?? null;
      }
      if (node.visual) {
        if (!["chart", "table", "graph", "diagram", "timeline"].includes(node.type) ||
          Object.keys(node.visual).some(key => !["props", "rows", "nodes", "edges", "items"].includes(key)) ||
          !parseUISnapshot({ ...node.visual, protocol: "suip/0.1", block_id: node.id,
            component: node.type === "diagram" ? "graph" : node.type, revision: 0, state: "completed" })) bad("Invalid embedded visual");
      }
    }
    if ([...counts.values()].some(count => count > 40)) bad("Too many children");
    for (const key of ["rows", "nodes", "edges", "items"] as const) {
      if (
        new Set(snapshot[key].map((entry) => entry.id)).size !==
        snapshot[key].length
      )
        ctx.addIssue({ code: "custom", message: `Duplicate ${key} ids` });
    }
    const keys = new Set(snapshot.props.columns.map((column) => column.key));
    if (keys.size !== snapshot.props.columns.length)
      ctx.addIssue({ code: "custom", message: "Duplicate columns" });
    const nodes = new Set(snapshot.nodes.map((node) => node.id));
    if (
      snapshot.edges.some(
        (edge) => !nodes.has(edge.source) || !nodes.has(edge.target),
      )
    )
      ctx.addIssue({ code: "custom", message: "Unknown edge endpoint" });
    if (
      snapshot.component === "chart" &&
      (!keys.size ||
        snapshot.rows.some((row) =>
          Object.entries(row.values).some(
            ([key, value]) =>
              !keys.has(key) ||
              typeof value !== "number" ||
              Math.abs(value) > 1e15,
          ),
        ))
    )
      ctx.addIssue({ code: "custom", message: "Invalid chart values" });
    const allowed: Record<string, string[]> = {
      chart: ["rows"],
      table: ["rows"],
      graph: ["nodes", "edges"],
      timeline: ["items"],
      metric: ["items"],
      document: [],
    };
    if (
      (["rows", "nodes", "edges", "items"] as const).some(
        (key) =>
          snapshot[key].length && !allowed[snapshot.component].includes(key),
      )
    )
      ctx.addIssue({
        code: "custom",
        message: "Collection does not belong to component",
      });
  });

export type UISnapshot = z.infer<typeof uiSnapshotSchema>;
export type UIActionRequest = {
  part_id: string;
  revision: number;
  action: "explain" | "graph.node.explain";
  node_id?: string;
};

/** A snapshot is the recovery boundary: never parse unfinished JSON on the client. */
export function parseUISnapshot(data: unknown): UISnapshot | null {
  try {
    if (new TextEncoder().encode(JSON.stringify(data)).byteLength > 192_000) return null;
    const parsed = uiSnapshotSchema.safeParse(data);
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

/** All artifact placement markers are transport syntax, even unresolved card IDs. */
export function visibleStreamUIText(
  content: string,
  streaming: boolean,
): string {
  const visible = content.replace(
    /\[\[\s*artifact\s*:[^\]\r\n]*\]\]/gi,
    "",
  );
  if (!streaming) return visible;
  const start = visible.lastIndexOf("[[");
  if (start < 0) return visible;
  const tail = visible.slice(start).toLowerCase();
  return "[[artifact:".startsWith(tail.replace(/\s/g, "")) ||
    /^\[\[\s*artifact\s*:[^\]\r\n]*\]?$/.test(tail)
    ? visible.slice(0, start)
    : visible;
}

import { describe, expect, it } from "vitest";
import { documentNodeSchema, parseUISnapshot } from "./stream-ui";
import { legacyRailIconSchema, railIconSchema } from "@/genui/rail-list-schema";

const snapshot = (extra: Record<string, unknown> = {}) => ({
  protocol: "suip/0.1", block_id: "icons", component: "document", revision: 0,
  state: "completed", props: {title: "Icons", icon: "fluent:direct-hit"}, blocks: [], ...extra,
});

describe("Stream UI icon contract", () => {
  it("round-trips Fluent icons on props, items and every document node", () => {
    const doc = parseUISnapshot(snapshot({blocks: [
      {id: "heading", type: "heading", icon: "fluent:brain", text: "Heading"},
      {id: "paragraph", type: "paragraph", icon: "fluent:light-bulb", text: "Body"},
      {id: "icon", type: "icon", icon: "fluent:direct-hit", alt: "Target"},
    ]}));
    expect(doc).not.toBeNull();
    expect(parseUISnapshot(JSON.parse(JSON.stringify(doc)))).toEqual(doc);
    for (const component of ["timeline", "metric"]) {
      const parsed = parseUISnapshot(snapshot({component, blocks: undefined,
        items: [{id: "item", label: "Item", icon: "fluent:rocket", value: 42}]}));
      expect(parsed?.items[0].icon).toBe("fluent:rocket");
      expect(parseUISnapshot(JSON.parse(JSON.stringify(parsed)))).toEqual(parsed);
    }
  });

  it("keeps every legacy Lucide identifier and the exact 100-character bound", () => {
    for (const icon of legacyRailIconSchema.options) expect(railIconSchema.parse(icon)).toBe(icon);
    expect(railIconSchema.safeParse(`fluent:${"a".repeat(93)}`).success).toBe(true);
    expect(railIconSchema.safeParse(`fluent:${"a".repeat(94)}`).success).toBe(false);
    expect(parseUISnapshot(snapshot({props: {title: "Old document"}}))).not.toBeNull();
  });

  it.each(["https://evil.example/icon.svg", "//evil.example/a", "data:image/svg+xml,x", "javascript:alert(1)",
    "fluent:../rocket", "fluent:rocket/evil", "fluent:rocket?url=x", "fluent:rocket#x", "fluent:Rocket",
    "fluent:-rocket", "fluent:rocket-", "fluent:rocket--ship", "fluent:rocket_ship", "fluent:", "fluent:🚀",
    "fluent:rocket\n", "constructor", "__proto__", `fluent:${"a".repeat(94)}`])("rejects unsafe icon %s at every input surface", icon => {
    expect(parseUISnapshot(snapshot({props: {title: "Bad", icon}}))).toBeNull();
    expect(parseUISnapshot(snapshot({blocks: [{id: "bad", type: "paragraph", icon}]}))).toBeNull();
    expect(parseUISnapshot(snapshot({component: "metric", items: [{id: "bad", label: "Bad", icon}]}))).toBeNull();
  });

  it("requires standalone icons and keeps item_status rail-only", () => {
    expect(documentNodeSchema.safeParse({id: "missing", type: "icon"}).success).toBe(false);
    expect(parseUISnapshot(snapshot({blocks: [{id: "missing", type: "icon"}]}))).toBeNull();
    expect(documentNodeSchema.safeParse({id: "icon", type: "icon", icon: "zap"}).success).toBe(true);
    expect(parseUISnapshot(snapshot({blocks: [{id: "bad", type: "heading", icon: "zap", item_status: "completed"}]}))).toBeNull();
    expect(parseUISnapshot(snapshot({blocks: [{id: "steps", type: "step_list", icon: "fluent:rocket"},
      {id: "step", type: "rail_item", parent_id: "steps", label: "Step", icon: "zap", item_status: "completed"}]}))).not.toBeNull();
  });
});

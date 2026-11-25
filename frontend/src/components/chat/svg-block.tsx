import { createElement, memo, useMemo, type ReactNode } from "react";

import { parseIllustrationSvg, type IllustrationNode } from "@/lib/safe-illustration";

const ID = /^[a-zA-Z0-9_-]{1,70}$/;
const SAFE_NAME = /^[a-zA-Z][a-zA-Z0-9-]*$/;
const PARENTS = new Set(["g", "text", "tspan"]);
const escapeXml = (s: string) => s.replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
})[c]!);

type CanvasNode = { id: string; tag: string; attrs: IllustrationNode["attrs"]; text: string[]; children: CanvasNode[]; depth: number };

function svgTree(viewBox: string, incoming: unknown) {
  const box = parseIllustrationSvg(`<svg viewBox="${escapeXml(viewBox)}"><rect/></svg>`);
  if (!box) return null;
  const roots: CanvasNode[] = [];
  const nodes = new Map<string, CanvasNode>();
  for (const value of Array.isArray(incoming) ? incoming.slice(0, 95) : []) {
    if (!value || typeof value !== "object") continue;
    const { id, tag, attrs, text, parent_id } = value;
    if (typeof id !== "string" || !ID.test(id) || nodes.has(id) || typeof tag !== "string" || !SAFE_NAME.test(tag) || tag === "svg") continue;
    if (!attrs || typeof attrs !== "object" || Array.isArray(attrs) || typeof text !== "string" || text.length > 4000) continue;
    const entries = Object.entries(attrs);
    if (entries.length > 24 || entries.some(([name, content]) => !SAFE_NAME.test(name) || name === "id" || name === "xmlns" || typeof content !== "string")) continue;
    const parent = typeof parent_id === "string" ? nodes.get(parent_id) : undefined;
    if (parent_id != null && (!parent || !PARENTS.has(parent.tag) || (parent.tag !== "g" && tag !== "tspan"))) continue;
    const depth = (parent?.depth ?? 0) + 1;
    if (depth > 8) continue;
    const attributes = entries.map(([name, content]) => ` ${name}="${escapeXml(content as string)}"`).join("");
    const parsed = parseIllustrationSvg(`<svg viewBox="${escapeXml(viewBox)}"><rect/><${tag}${attributes}>${escapeXml(text)}</${tag}></svg>`)?.children[1];
    if (!parsed || typeof parsed === "string") continue;
    const node: CanvasNode = { id, tag: parsed.tag, attrs: parsed.attrs, text: parsed.children.filter((child): child is string => typeof child === "string"), children: [], depth };
    nodes.set(id, node);
    (parent?.children ?? roots).push(node);
  }
  return { viewBox: box.attrs.viewBox, roots };
}

function renderNode(node: CanvasNode): ReactNode {
  return createElement(node.tag, { ...node.attrs, key: node.id, "data-svg-node": node.id }, ...node.text, ...node.children.map(renderNode));
}

/** One stable SVG canvas; keying by protocol IDs preserves existing DOM nodes. */
export const SVGBlock = memo(function SVGBlock({ viewBox, nodes, label }: { viewBox: unknown; nodes: unknown; label: string }) {
  const tree = useMemo(() => typeof viewBox === "string" ? svgTree(viewBox, nodes) : null, [viewBox, nodes]);
  if (!tree) return null;
  return <svg
    aria-label={label}
    className="unified-artifact-card__native-svg"
    preserveAspectRatio="xMidYMid meet"
    role="img"
    viewBox={tree.viewBox}
  >{tree.roots.map(renderNode)}</svg>;
});

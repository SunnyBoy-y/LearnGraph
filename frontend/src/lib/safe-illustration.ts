/** Static SVG only. Return a React-compatible tree, never HTML to inject. */
export type IllustrationNode = {tag: string; attrs: Record<string, string>; children: (IllustrationNode | string)[]};
const tags = new Set(["svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title", "desc"]);
const numeric = new Set(["x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height", "stroke-width", "font-size", "dx", "dy", "letter-spacing", "opacity", "fill-opacity", "stroke-opacity"]);
const enums: Record<string, string[]> = {"text-anchor": ["start", "middle", "end"], "dominant-baseline": ["auto", "middle", "hanging", "alphabetic", "central"], "font-weight": ["normal", "bold", "400", "500", "600", "700"], "stroke-linecap": ["butt", "round", "square"], "stroke-linejoin": ["miter", "round", "bevel"], "fill-rule": ["nonzero", "evenodd"], "vector-effect": ["non-scaling-stroke"]};
const number = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
function numbers(value: string): number[] {
  const parts = value.trim().split(/[\s,]+/);
  if (!parts.length || parts.length > 256 || parts.some(v => !number.test(v))) throw Error("Invalid SVG numbers");
  const result = parts.map(Number);
  if (result.some(v => !Number.isFinite(v) || Math.abs(v) > 10000)) throw Error("SVG bounds");
  return result;
}
const color = /^(?:none|transparent|currentColor|#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,24}|(?:rgb|rgba|hsl|hsla)\([0-9.,%\s+-]+\))$/;
const attrNames: Record<string, string> = {"stroke-width":"strokeWidth","font-size":"fontSize","font-family":"fontFamily","font-weight":"fontWeight","text-anchor":"textAnchor","dominant-baseline":"dominantBaseline","stroke-linecap":"strokeLinecap","stroke-linejoin":"strokeLinejoin","stroke-dasharray":"strokeDasharray","fill-opacity":"fillOpacity","stroke-opacity":"strokeOpacity","fill-rule":"fillRule","vector-effect":"vectorEffect","letter-spacing":"letterSpacing"};

export function parseIllustrationSvg(value: string): IllustrationNode | null {
  if (!value || new TextEncoder().encode(value).length > 24000 || /<!|<\?/.test(value)) return null;
  try {
    const doc = new DOMParser().parseFromString(value, "image/svg+xml");
    const root = doc.documentElement;
    if (doc.querySelector("parsererror") || root.localName !== "svg" || !root.hasAttribute("viewBox")) return null;
    let count = 0, drawable = false;
    const visit = (node: Element, depth: number): IllustrationNode => {
      const tag = node.localName;
      if (++count > 96 || depth > 8 || !tags.has(tag) || (tag === "svg" && depth !== 0)
        || (node.namespaceURI && node.namespaceURI !== "http://www.w3.org/2000/svg")) throw Error("Unsupported SVG element");
      drawable ||= ["path", "rect", "circle", "ellipse", "line", "polyline", "polygon"].includes(tag);
      if (node.attributes.length > 25) throw Error("Too many SVG attributes");
      const attrs: Record<string, string> = {};
      for (const attr of Array.from(node.attributes)) {
        const key = attr.name, v = attr.value.trim();
        if (key === "xmlns" && depth === 0 && v === "http://www.w3.org/2000/svg") continue;
        if (numeric.has(key)) {
          const n = numbers(v.replace(/%$/, ""));
          if (n.length !== 1 || (v.endsWith("%") && (n[0] < 0 || n[0] > 100))) throw Error("Invalid coordinate");
          if (["width", "height", "r", "rx", "ry", "stroke-width", "font-size"].includes(key) && n[0] < 0) throw Error("Negative size");
          if (key.endsWith("opacity") && (n[0] < 0 || n[0] > 1)) throw Error("Invalid opacity");
        } else if (key === "viewBox" && depth === 0) {
          const b = numbers(v);
          if (b.length !== 4 || b[2] < 80 || b[2] > 1600 || b[3] < 80 || b[3] > 1200 || b[2]/b[3] < .4 || b[2]/b[3] > 4) throw Error("Invalid viewBox");
        } else if (key === "fill" || key === "stroke") {
          if (v.length > 100 || !color.test(v)) throw Error("Unsupported paint");
        } else if (enums[key]) {
          if (!enums[key].includes(v)) throw Error("Unsupported attribute value");
        } else if (key === "d" && tag === "path") {
          if (!v || v.length > 6000 || !/^[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]+$/.test(v)) throw Error("Invalid path");
          numbers((v.match(/[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/g) ?? []).join(" "));
        } else if (key === "points" || key === "stroke-dasharray") {
          if (!(key === "stroke-dasharray" && v === "none")) {
            const n = numbers(v);
            if (key === "points" && (n.length < 4 || n.length % 2)) throw Error("Invalid points");
          }
        } else if (key === "transform") {
          const pattern = /(translate|scale|rotate|matrix)\(([^()]*)\)/g;
          const parts = [...v.matchAll(pattern)];
          if (!parts.length || parts.length > 8 || v.replace(pattern, "").trim()) throw Error("Invalid transform");
          const arity: Record<string, number[]> = {translate:[1,2],scale:[1,2],rotate:[1,3],matrix:[6]};
          for (const p of parts) if (!arity[p[1]].includes(numbers(p[2]).length)) throw Error("Invalid transform");
        } else if (key === "font-family") {
          if (v.length > 100 || !/^[a-zA-Z0-9_ ,'-]+$/.test(v)) throw Error("Unsupported font");
        } else if (key === "id" && /^[a-zA-Z][a-zA-Z0-9_-]{0,79}$/.test(v)) {
          continue; // No references are allowed; do not collide with host IDs.
        } else if (key === "preserveAspectRatio" && depth === 0 && v === "xMidYMid meet") {
          // The renderer also pins this value.
        } else if (!((key === "role" && v === "img") || (key === "aria-label" && v.length <= 240))) {
          throw Error("Unsupported SVG attribute");
        }
        attrs[attrNames[key] || key] = v;
      }
      const children = Array.from(node.childNodes).flatMap<IllustrationNode | string>(child => {
        if (child.nodeType === 1) return [visit(child as Element, depth + 1)];
        if (child.nodeType !== 3) return [];
        if (child.textContent?.trim() && !["text", "tspan", "title", "desc"].includes(tag)) throw Error("Unsupported SVG text");
        return child.textContent?.trim() ? [child.textContent] : [];
      });
      return {tag, attrs, children};
    };
    const result = visit(root, 0);
    return drawable && (root.textContent?.length ?? 0) <= 4000 ? result : null;
  } catch { return null; }
}

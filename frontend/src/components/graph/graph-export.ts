/**
 * Knowledge-graph export helpers: render the current React Flow graph as a
 * standalone SVG (vector) and rasterize it to PNG (2x) via canvas.
 *
 * React Flow renders nodes as HTML and edges as SVG, so a DOM snapshot cannot
 * be reused directly. Instead we rebuild a clean, self-contained SVG from the
 * live node/edge geometry — no extra dependencies, crisp at any zoom, and
 * fully offline (no external font/resources, so canvas rasterization never
 * taints).
 */

import { saveBlobViaNative } from "@/lib/native-download";

export type GraphExportNode = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  label: string;
  description?: string;
  nodeType?: string;
  kind?: string;
  depth?: number;
  root?: boolean;
  rootEmphasis?: boolean;
  tree?: boolean;
  /** Mastery/evidence label rendered as a status chip. */
  statusLabel?: string;
  step?: number;
  stepTotal?: number;
  collapsed?: boolean;
  hiddenCount?: number;
};

export type GraphExportEdge = {
  id: string;
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  targetWidth: number;
  targetHeight: number;
  spine?: boolean;
  active?: boolean;
  dim?: boolean;
};

const FONT_STACK =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';

const CANVAS_BG = "#f7f8f5";
const INK = "#173d31";
const GREEN = "#0b8f70";

function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function round(value: number, digits = 1): string {
  return value.toFixed(digits);
}

/** Split long CJK/mixed text into clamped single-line rows. */
function splitLines(text: string, maxChars: number, maxLines: number): string[] {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return [];
  const rows: string[] = [];
  let rest = clean;
  for (let index = 0; index < maxLines; index += 1) {
    if (!rest) break;
    if (rest.length <= maxChars) {
      rows.push(rest);
      break;
    }
    if (index === maxLines - 1) {
      rows.push(rest.slice(0, Math.max(1, maxChars - 1)) + "…");
      break;
    }
    rows.push(rest.slice(0, maxChars));
    rest = rest.slice(maxChars);
  }
  return rows;
}

/** Intersect a ray (source center -> target center) with the target box edge. */
function trimToBox(
  sx: number,
  sy: number,
  tx: number,
  ty: number,
  targetWidth: number,
  targetHeight: number,
  pad = 4,
): { x: number; y: number } {
  const dx = tx - sx;
  const dy = ty - sy;
  const len = Math.hypot(dx, dy);
  if (len < 1) return { x: tx, y: ty };
  const ux = dx / len;
  const uy = dy / len;
  const t = Math.min(
    Math.abs(ux) > 1e-6 ? (targetWidth / 2 + pad) / Math.abs(ux) : Infinity,
    Math.abs(uy) > 1e-6 ? (targetHeight / 2 + pad) / Math.abs(uy) : Infinity,
  );
  return { x: tx + ux * Math.min(t, len), y: ty + uy * Math.min(t, len) };
}

function edgeStroke(edge: GraphExportEdge): { color: string; width: number } {
  if (edge.active) return { color: "rgba(11,143,112,0.72)", width: 2.2 };
  if (edge.spine) {
    return edge.dim
      ? { color: "rgba(23,61,49,0.46)", width: 2.2 }
      : { color: "rgba(23,61,49,0.55)", width: 2.4 };
  }
  if (edge.dim) return { color: "rgba(23,61,49,0.14)", width: 1.3 };
  return { color: "rgba(23,61,49,0.28)", width: 1.6 };
}

function renderEdge(edge: GraphExportEdge, arrow: boolean): string {
  const { color, width } = edgeStroke(edge);
  const end = trimToBox(
    edge.sourceX,
    edge.sourceY,
    edge.targetX,
    edge.targetY,
    edge.targetWidth,
    edge.targetHeight,
  );
  const dx = end.x - edge.sourceX;
  const path =
    Math.abs(dx) < 16
      ? `M ${round(edge.sourceX)} ${round(edge.sourceY)} L ${round(end.x)} ${round(end.y)}`
      : (() => {
          const midX = edge.sourceX + dx * 0.46;
          return `M ${round(edge.sourceX)} ${round(edge.sourceY)} C ${round(midX)} ${round(edge.sourceY)}, ${round(midX)} ${round(end.y)}, ${round(end.x)} ${round(end.y)}`;
        })();
  return (
    `<path d="${path}" fill="none" stroke="${color}" stroke-width="${width}"` +
    (arrow ? ' marker-end="url(#lg-arrow)"' : "") +
    "/>"
  );
}

const TYPE_LABELS: Record<string, string> = {
  root: "目标",
  concept: "概念",
  practice: "练习",
  assessment: "测评",
};

function typeLabel(node: GraphExportNode): string {
  return TYPE_LABELS[node.nodeType ?? (node.root ? "root" : "concept")] ?? node.nodeType ?? "概念";
}

function renderNode(node: GraphExportNode): string {
  const { x, y, width, height } = node;
  const cx = x + width / 2;
  const cy = y + height / 2;

  // Tree root: green seed circle.
  if (node.tree && node.root) {
    const r = width / 2;
    return (
      `<circle cx="${round(cx)}" cy="${round(cy)}" r="${round(r)}" fill="#f4fbf7" stroke="rgba(23,61,49,0.35)" stroke-width="1.5"/>` +
      `<text x="${round(cx)}" y="${round(cy)}" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="700" fill="${INK}">${esc("根")}</text>`
    );
  }

  // Emphasized round root (free layouts).
  if (node.rootEmphasis && !node.tree) {
    const r = width / 2;
    const labelRows = splitLines(node.label, 7, 2);
    const fontSize = 15;
    const lineHeight = fontSize + 3;
    const startY = cy - ((labelRows.length - 1) * lineHeight) / 2;
    const body = labelRows
      .map(
        (row, index) =>
          `<tspan x="${round(cx)}" y="${round(startY + index * lineHeight)}">${esc(row)}</tspan>`,
      )
      .join("");
    return (
      `<circle cx="${round(cx)}" cy="${round(cy)}" r="${round(r)}" fill="#e8f5f1" stroke="${GREEN}" stroke-width="1.6"/>` +
      `<text text-anchor="middle" dominant-baseline="central" font-size="${fontSize}" font-weight="700" fill="${INK}">${body}</text>`
    );
  }

  // Tree branch/main card.
  if (node.tree) {
    const main = node.kind === "main";
    const px = 16;
    const top = y + 14;
    const lineHeight = 15;
    const labelRows = splitLines(node.label, Math.max(4, Math.floor((width - px * 2 - 4) / 14)), 2);
    const descRows = splitLines(node.description ?? "", Math.max(4, Math.floor((width - px * 2 - 4) / 11)), 2);
    const descTop = top + 2 + labelRows.length * lineHeight + 4;
    const chipY = descTop + descRows.length * lineHeight + 8;
    const parts: string[] = [];
    parts.push(
      `<rect x="${round(x)}" y="${round(y)}" width="${round(width)}" height="${round(height)}" rx="14" fill="rgba(255,255,255,0.98)" stroke="${main ? "#b8c4bb" : "#c9d1c9"}" stroke-width="1.4"/>`,
    );
    // Step chip (main spine cards only).
    if (main && typeof node.step === "number") {
      const text = `第 ${node.step}${node.stepTotal && node.stepTotal > 1 ? ` / ${node.stepTotal}` : ""} 步`;
      const chipW = text.length * 10 + 16;
      parts.push(
        `<rect x="${round(x + 12)}" y="${round(y - 11)}" width="${round(chipW)}" height="19" rx="9.5" fill="#173d31"/>` +
          `<text x="${round(x + 12 + chipW / 2)}" y="${round(y + 0.5)}" text-anchor="middle" dominant-baseline="central" font-size="10" font-weight="700" fill="#f4fbf7">${esc(text)}</text>`,
      );
    }
    // Meta row: type label (+ depth).
    const levelText = (node.depth ?? 0) >= 2 ? `第 ${node.depth} 层` : "";
    parts.push(
      `<text x="${round(x + px)}" y="${round(top + 2)}" font-size="8" letter-spacing="1" fill="#929692">${esc(typeLabel(node))}</text>` +
        (levelText
          ? `<text x="${round(x + width - px)}" y="${round(top + 2)}" text-anchor="end" font-size="8" font-weight="700" fill="#68716b">${esc(levelText)}</text>`
          : ""),
    );
    // Label rows.
    labelRows.forEach((row, index) => {
      parts.push(
        `<text x="${round(x + px)}" y="${round(top + 4 + (index + 1) * (lineHeight + 1))}" font-size="14" font-weight="700" fill="#1c2622">${esc(row)}</text>`,
      );
    });
    // Description rows.
    descRows.forEach((row, index) => {
      parts.push(
        `<text x="${round(x + px)}" y="${round(descTop + index * 15.4 + 11)}" font-size="11" fill="#6f766f">${esc(row)}</text>`,
      );
    });
    // Status chip.
    if (node.statusLabel) {
      const chipText = node.statusLabel;
      const chipW = chipText.length * 9 + 16;
      const due = chipText.includes("复习") || chipText.includes("待");
      const mastered = chipText.includes("掌握稳定");
      const chipBg = mastered ? "#e6f6ef" : due ? "#fff0cf" : "#eef2ef";
      const chipFg = mastered ? "#0b8f70" : due ? "#9a6200" : "#4a554e";
      parts.push(
        `<rect x="${round(x + px)}" y="${round(chipY)}" width="${round(chipW)}" height="17" rx="8.5" fill="${chipBg}"/>` +
          `<text x="${round(x + px + chipW / 2)}" y="${round(chipY + 9)}" text-anchor="middle" dominant-baseline="central" font-size="9" font-weight="700" fill="${chipFg}">${esc(chipText)}</text>`,
      );
    }
    // Collapsed count badge.
    if (node.collapsed && (node.hiddenCount ?? 0) > 0) {
      const text = `+${node.hiddenCount}`;
      const chipW = text.length * 8 + 12;
      parts.push(
        `<rect x="${round(x + width - chipW - 10)}" y="${round(y - 9)}" width="${round(chipW)}" height="16" rx="8" fill="#eef4ef" stroke="#c9d5cc" stroke-width="1"/>` +
          `<text x="${round(x + width - 10 - chipW / 2)}" y="${round(y)}" text-anchor="middle" dominant-baseline="central" font-size="9" font-weight="700" fill="#2f4a3d">${esc(text)}</text>`,
      );
    }
    return parts.join("");
  }

  // Plain card (spatial / flat layouts).
  const px = 14;
  const top = y + 12;
  const lineHeight = 17;
  const labelRows = splitLines(node.label, Math.max(4, Math.floor((width - px * 2 - 4) / 14)), 2);
  const smallText = node.statusLabel ?? "";
  const smallRows = splitLines(smallText, Math.max(4, Math.floor((width - px * 2 - 4) / 9)), 2);
  const parts: string[] = [];
  parts.push(
    `<rect x="${round(x)}" y="${round(y)}" width="${round(width)}" height="${round(height)}" rx="22" fill="rgba(255,255,255,0.97)" stroke="#cfd2ce" stroke-width="1.2"/>`,
  );
  if (!node.root) {
    parts.push(
      `<text x="${round(x + px)}" y="${round(top + 2)}" font-size="8" letter-spacing="1" fill="#929692">${esc(typeLabel(node))}</text>`,
    );
  }
  labelRows.forEach((row, index) => {
    parts.push(
      `<text x="${round(x + px)}" y="${round(top + 4 + (index + 1) * lineHeight)}" font-size="14" font-weight="700" fill="#1c2622">${esc(row)}</text>`,
    );
  });
  smallRows.forEach((row, index) => {
    parts.push(
      `<text x="${round(x + px)}" y="${round(top + 4 + labelRows.length * lineHeight + 6 + index * 12)}" font-size="9" fill="#818682">${esc(row)}</text>`,
    );
  });
  return parts.join("");
}

export type GraphExportOptions = {
  /** Optional caption drawn at the top-left of the exported image. */
  title?: string;
  /** Hide the dotted background (e.g. for dark/print contexts). */
  plain?: boolean;
};

/**
 * Build a standalone SVG document from the given node/edge geometry.
 * Coordinates are React Flow flow-space pixels (top-left origin).
 */
export function buildGraphSvg(
  nodes: GraphExportNode[],
  edges: GraphExportEdge[],
  options: GraphExportOptions = {},
): string {
  const padding = 28;
  const labelPadding = options.title ? 46 : 28;
  if (!nodes.length) {
    const empty =
      `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400" font-family="${esc(FONT_STACK)}">` +
      `<rect width="640" height="400" fill="${CANVAS_BG}"/>` +
      `<text x="320" y="200" text-anchor="middle" dominant-baseline="central" font-size="14" fill="#878c88">图谱暂无节点</text>` +
      `</svg>`;
    return empty;
  }

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    maxX = Math.max(maxX, node.x + node.width);
    maxY = Math.max(maxY, node.y + node.height);
  }
  const left = Math.floor(minX - padding);
  const top = Math.floor(minY - labelPadding);
  const width = Math.ceil(maxX - minX + padding * 2);
  const height = Math.ceil(maxY - minY + labelPadding + padding);

  const arrow = nodes.some((node) => !node.tree) || edges.some((edge) => !edge.spine);
  const dots = options.plain
    ? ""
    : `<pattern id="lg-dots" width="32" height="32" patternUnits="userSpaceOnUse"><circle cx="16" cy="16" r="1.1" fill="rgba(24,40,32,0.22)"/></pattern>`;

  const defs =
    `<defs>` +
    dots +
    (arrow
      ? `<marker id="lg-arrow" viewBox="0 0 10 10" refX="7.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(23,61,49,0.4)"/></marker>`
      : "") +
    `</defs>`;

  const edgeBody = edges.map((edge) => renderEdge(edge, arrow)).join("");
  const nodeBody = nodes.map((node) => renderNode(node)).join("");
  const titleBody = options.title
    ? `<text x="${round(left + 18)}" y="${round(top + 20)}" font-size="13" font-weight="700" fill="#2f3a35">${esc(options.title)}</text>`
    : "";

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${left} ${top} ${width} ${height}" font-family="${esc(FONT_STACK)}">` +
    defs +
    `<rect x="${left}" y="${top}" width="${width}" height="${height}" fill="${CANVAS_BG}"/>` +
    (dots ? `<rect x="${left}" y="${top}" width="${width}" height="${height}" fill="url(#lg-dots)"/>` : "") +
    titleBody +
    `<g>${edgeBody}</g>` +
    `<g>${nodeBody}</g>` +
    `</svg>`
  );
}

export function sanitizeFilename(value: string): string {
  const cleaned = value
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return cleaned || "学习图谱";
}

async function triggerDownload(blob: Blob, filename: string): Promise<void> {
  // 移动端 WebView：纯前端生成的 blob 交给原生 base64 通道
  if (await saveBlobViaNative(blob, filename)) return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/** Download the SVG document as a .svg file. */
export function downloadSvgFile(svg: string, filename: string): void {
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  void triggerDownload(blob, filename);
}

/** Rasterize the SVG at 2x and download as a .png file. */
export async function downloadPngFile(svg: string, filename: string): Promise<void> {
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.decoding = "async";
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("SVG 渲染失败"));
      image.src = url;
    });
    const scale = 2;
    // Browser canvases cap out around 16384px per side (and ~2^28 px area);
    // clamp the scale so very large graphs still rasterize instead of failing
    // silently when drawImage/toBlob hits the canvas size limit.
    const MAX_CANVAS_SIDE = 16384;
    const effectiveScale = Math.min(
      scale,
      image.width > MAX_CANVAS_SIDE ? MAX_CANVAS_SIDE / image.width : scale,
      image.height > MAX_CANVAS_SIDE ? MAX_CANVAS_SIDE / image.height : scale,
    );
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.width * effectiveScale));
    canvas.height = Math.max(1, Math.round(image.height * effectiveScale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas 不可用");
    context.scale(effectiveScale, effectiveScale);
    context.drawImage(image, 0, 0);
    const png = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (result) => (result ? resolve(result) : reject(new Error("PNG 编码失败"))),
        "image/png",
      );
    });
    await triggerDownload(png, filename);
  } finally {
    URL.revokeObjectURL(url);
  }}

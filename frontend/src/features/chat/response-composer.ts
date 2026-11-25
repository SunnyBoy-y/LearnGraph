import type { MessagePart } from "@/types/sessions";

export type ArtifactKind =
  | "svg"
  | "image"
  | "html"
  | "magic_card"
  | "chart"
  | "subapp_artifact"
  | "ui"
  | "component"
  | "file";

export interface ArtifactCapabilities {
  inlinePreview: boolean;
  download: boolean;
  fullscreen: boolean;
  wideLayout: boolean;
}

export interface UnifiedArtifactMeta {
  canonicalId: string;
  artifactType: ArtifactKind;
  title: string;
  fileId?: string;
  path?: string;
  mimeType?: string;
  sizeBytes?: number;
  sha256?: string;
  cardId?: string;
  cardInstanceId?: string;
  bundleId?: string;
  blockId?: string;
  previewHtml?: string;
  svgMarkup?: string;
  contentFingerprint?: string;
  isStaticSvgCard: boolean;
  primaryDeliverable: boolean;
  capabilities: ArtifactCapabilities;
  mergedFromIds: string[];
  layout?: string;
}

export interface ArtifactEntry {
  part: MessagePart;
  meta: UnifiedArtifactMeta;
  suppressed: boolean;
  mergedInto?: string;
}

const ARTIFACT_PART_TYPES = new Set<MessagePart["type"]>([
  "sticker",
  "image",
  "sandbox",
  "sandbox_artifact",
  "subapp_artifact",
  "magic_card",
  "component",
  "chart",
  "svg",
  "ui",
]);

const NON_PRIMARY_COMPONENT_TYPES = new Set([
  "graph_update_proposal",
  "option_group",
  "single_choice",
  "multiple_choice",
  "fill_blank",
  "short_answer_table",
  "quiz",
]);

const PRIMARY_DELIVERABLE_ARTIFACT_TYPES = new Set<ArtifactKind>([
  "svg",
  "image",
  "html",
  "magic_card",
  "chart",
  "subapp_artifact",
  "ui",
]);

const SVG_BLOCK_RE = /<svg\b[^>]*>[\s\S]*?<\/svg>/i;
const INTERACTIVE_HTML_RE =
  /<script\b|<canvas\b|<form\b|<input\b|<button\b|<select\b|<textarea\b|\bon(?:click|change|input|submit|pointerdown|keydown)\s*=/i;

export function extractSvgMarkup(source: unknown): string | undefined {
  if (typeof source !== "string" || !source.toLowerCase().includes("<svg")) {
    return undefined;
  }
  const match = SVG_BLOCK_RE.exec(source);
  return match ? match[0].trim() : undefined;
}

export function isStaticSvgHtml(source: unknown): boolean {
  if (typeof source !== "string") return false;
  const svg = extractSvgMarkup(source);
  if (!svg) return false;
  return !INTERACTIVE_HTML_RE.test(source);
}

/** Deterministic structural fingerprint matching backend `svg_content_fingerprint`. */
export function svgContentFingerprint(source: unknown): string | undefined {
  const svg = extractSvgMarkup(source);
  if (!svg || svg.length > 256_000 || /<!|<\?/.test(svg) || typeof DOMParser === "undefined") return undefined;
  try {
    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    if (doc.querySelector("parsererror")) return undefined;
    const walk = (node: Element, depth: number): unknown[] => {
      if (depth > 32) throw Error("SVG too deep");
      const attrs = Array.from(node.attributes)
        .filter(({name}) => !["xmlns", "font-family"].includes(name) && !(depth === 0 && ["width", "height"].includes(name)))
        .map(({ name, value }) => [name, value]).sort(([a], [b]) => a.localeCompare(b));
      const children = Array.from(node.childNodes).flatMap<unknown>(child => {
        if (child.nodeType === 1) return [walk(child as Element, depth + 1)];
        const text = child.textContent?.trim();
        return (child.nodeType === 3 || child.nodeType === 4) && text ? [text] : [];
      });
      return [node.namespaceURI === "http://www.w3.org/2000/svg" ? "" : node.namespaceURI ?? "", node.localName, attrs, children];
    };
    return JSON.stringify(walk(doc.documentElement, 0));
  } catch { return undefined; }
}

function fileExtension(pathOrTitle: unknown): string {
  if (typeof pathOrTitle !== "string") return "";
  const name = pathOrTitle.trim().split("/").pop() ?? "";
  const idx = name.lastIndexOf(".");
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : "";
}

function basename(path: unknown): string {
  if (typeof path !== "string") return "";
  return (path.trim().split("/").pop() ?? "").toLowerCase();
}

export function isSvgFileData(data: Record<string, unknown> | undefined): boolean {
  if (!data) return false;
  const mime = typeof data.mime_type === "string" ? data.mime_type.trim().toLowerCase() : "";
  if (mime === "image/svg+xml" || mime.startsWith("image/svg")) return true;
  return fileExtension(data.path) === "svg" || fileExtension(data.title) === "svg";
}

function isHtmlFileData(data: Record<string, unknown> | undefined): boolean {
  if (!data) return false;
  const mime = typeof data.mime_type === "string" ? data.mime_type.trim().toLowerCase() : "";
  if (mime === "text/html" || mime === "application/xhtml+xml") return true;
  const ext = fileExtension(data.path) || fileExtension(data.title);
  return ext === "html" || ext === "htm";
}

function isImageFileData(data: Record<string, unknown> | undefined): boolean {
  if (!data) return false;
  const mime = typeof data.mime_type === "string" ? data.mime_type.trim().toLowerCase() : "";
  if (mime.startsWith("image/")) return true;
  const ext = fileExtension(data.path) || fileExtension(data.title);
  return ["png", "jpg", "jpeg", "webp", "gif", "svg", "avif"].includes(ext);
}

function collectToolSources(parts: MessagePart[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const part of parts) {
    if (part.type !== "tool_call") continue;
    const data = part.data;
    if (!data || typeof data !== "object") continue;
    const toolName = typeof data.tool_name === "string" ? data.tool_name : "";
    if (toolName !== "sandbox_write_file" && toolName !== "sandbox_create_file") continue;
    const input = data.input;
    if (!input || typeof input !== "object" || Array.isArray(input)) continue;
    const rec = input as Record<string, unknown>;
    if (typeof rec.path === "string" && typeof rec.content === "string" && rec.content.trim()) {
      map.set(rec.path.trim(), rec.content);
      map.set(basename(rec.path), rec.content);
    }
  }
  return map;
}

function stringField(data: Record<string, unknown>, key: string): string | undefined {
  const value = data[key];
  return typeof value === "string" && value ? value : undefined;
}

function isBackendSuppressedPart(part: MessagePart): boolean {
  const reg = part.data?.artifact_registry;
  return Boolean(
    reg &&
      typeof reg === "object" &&
      !Array.isArray(reg) &&
      (reg as Record<string, unknown>).suppressed === true,
  );
}

function buildEntry(
  part: MessagePart,
  toolSources: Map<string, string>,
): ArtifactEntry {
  const data = (part.data ?? {}) as Record<string, unknown>;
  const fileId = stringField(data, "file_id");
  const path = stringField(data, "path");
  const mimeType = stringField(data, "mime_type");
  const sizeBytes =
    typeof data.size_bytes === "number" && Number.isFinite(data.size_bytes)
      ? data.size_bytes
      : undefined;
  const sha256 = stringField(data, "sha256") ?? stringField(data, "blob_sha256");
  const cardId = stringField(data, "card_id");
  const cardInstanceId = stringField(data, "card_instance_id");
  const bundleId = stringField(data, "bundle_id");
  const blockId = stringField(data, "block_id");
  const previewHtml = stringField(data, "preview_html");
  const existingSvgMarkup = stringField(data, "svg_markup");

  const title =
    stringField(data, "title")?.trim() ||
    (path ? path.split("/").pop() ?? "" : "") ||
    part.content?.trim() ||
    "产物";

  let svgMarkup = existingSvgMarkup;
  let isStaticSvgCard = false;
  let artifactType: ArtifactKind;
  let primaryDeliverable: boolean;

  if (
    (part.type === "sandbox_artifact" || part.type === "sandbox") &&
    isSvgFileData(data)
  ) {
    const rawSource = path
      ? toolSources.get(path) ?? toolSources.get(basename(path))
      : undefined;
    svgMarkup =
      svgMarkup ?? extractSvgMarkup(rawSource) ?? extractSvgMarkup(previewHtml);
    artifactType = "svg";
    primaryDeliverable = true;
  } else if (part.type === "magic_card") {
    if (isStaticSvgHtml(previewHtml) || Boolean(svgMarkup)) {
      svgMarkup = svgMarkup ?? extractSvgMarkup(previewHtml);
      isStaticSvgCard = true;
      artifactType = "svg";
    } else {
      artifactType = "magic_card";
    }
    primaryDeliverable = true;
  } else if (
    part.type === "image" ||
    ((part.type === "sandbox_artifact" || part.type === "sandbox") &&
      isImageFileData(data))
  ) {
    artifactType = "image";
    primaryDeliverable = true;
  } else if (
    (part.type === "sandbox_artifact" || part.type === "sandbox") &&
    isHtmlFileData(data)
  ) {
    artifactType = "html";
    primaryDeliverable = true;
  } else if (part.type === "sandbox_artifact" || part.type === "sandbox") {
    artifactType = "file";
    primaryDeliverable = Boolean(previewHtml || bundleId);
  } else if (part.type === "component") {
    const componentType = stringField(data, "component_type") ?? "";
    artifactType = "component";
    primaryDeliverable = !NON_PRIMARY_COMPONENT_TYPES.has(componentType);
  } else {
    artifactType = (part.type as ArtifactKind) || "file";
    primaryDeliverable = PRIMARY_DELIVERABLE_ARTIFACT_TYPES.has(artifactType);
  }

  const fingerprint =
    svgContentFingerprint(svgMarkup ?? previewHtml) ??
    (sha256 ? `sha256:${sha256.slice(0, 24)}` : undefined);

  const canonicalId =
    fileId ?? cardId ?? bundleId ?? blockId ?? fingerprint ?? part.id;

  const capabilities: ArtifactCapabilities = {
    inlinePreview: [
      "svg",
      "image",
      "magic_card",
      "html",
      "chart",
      "subapp_artifact",
      "ui",
    ].includes(artifactType),
    download: Boolean(fileId) || ["svg", "image", "html"].includes(artifactType),
    fullscreen: ["svg", "image", "magic_card", "html", "subapp_artifact"].includes(
      artifactType,
    ),
    wideLayout: ["svg", "magic_card", "html", "chart", "subapp_artifact"].includes(
      artifactType,
    ),
  };

  const reg = data.artifact_registry;
  const mergedFromIds =
    reg &&
    typeof reg === "object" &&
    !Array.isArray(reg) &&
    Array.isArray((reg as Record<string, unknown>).merged_from_ids)
      ? ((reg as Record<string, unknown>).merged_from_ids as unknown[]).filter(
          (item): item is string => typeof item === "string",
        )
      : [];

  return {
    part,
    suppressed: isBackendSuppressedPart(part),
    meta: {
      canonicalId,
      artifactType,
      title,
      fileId,
      path,
      mimeType,
      sizeBytes,
      sha256,
      cardId,
      cardInstanceId,
      bundleId,
      blockId,
      previewHtml,
      svgMarkup,
      contentFingerprint: fingerprint,
      isStaticSvgCard,
      primaryDeliverable,
      capabilities,
      mergedFromIds,
      layout: stringField(data, "layout"),
    },
  };
}

function canMergeEntries(left: ArtifactEntry, right: ArtifactEntry): boolean {
  if (left.suppressed || right.suppressed) return false;
  const a = left.meta;
  const b = right.meta;
  if (a.fileId && b.fileId && a.fileId === b.fileId) return true;
  if (a.sha256 && b.sha256 && a.sha256 === b.sha256) return true;
  if (a.bundleId && b.bundleId && a.bundleId === b.bundleId) return true;
  if (a.cardId && b.cardId && a.cardId === b.cardId) return true;
  if (left.part.type === "svg" && right.part.type === "svg") return false;
  return Boolean(a.artifactType === "svg" && b.artifactType === "svg" && a.contentFingerprint &&
    a.contentFingerprint === b.contentFingerprint);
}

function pickWinner(
  left: ArtifactEntry,
  right: ArtifactEntry,
): [ArtifactEntry, ArtifactEntry] {
  if (left.part.type === "svg") return [left, right];
  if (right.part.type === "svg") return [right, left];
  const leftScore =
    (left.meta.fileId ? 2 : 0) + (left.meta.previewHtml || left.meta.svgMarkup ? 1 : 0);
  const rightScore =
    (right.meta.fileId ? 2 : 0) + (right.meta.previewHtml || right.meta.svgMarkup ? 1 : 0);
  return rightScore > leftScore ? [right, left] : [left, right];
}

function mergeEntries(winner: ArtifactEntry, loser: ArtifactEntry): void {
  if (!winner.meta.mergedFromIds.includes(loser.part.id)) {
    winner.meta.mergedFromIds.push(loser.part.id);
  }
  if (!winner.meta.fileId && loser.meta.fileId) winner.meta.fileId = loser.meta.fileId;
  if (!winner.meta.path && loser.meta.path) winner.meta.path = loser.meta.path;
  if (winner.meta.sizeBytes === undefined && loser.meta.sizeBytes !== undefined) {
    winner.meta.sizeBytes = loser.meta.sizeBytes;
  }
  if (!winner.meta.mimeType && loser.meta.mimeType) winner.meta.mimeType = loser.meta.mimeType;
  if (!winner.meta.sha256 && loser.meta.sha256) winner.meta.sha256 = loser.meta.sha256;
  if (!winner.meta.previewHtml && loser.meta.previewHtml) {
    winner.meta.previewHtml = loser.meta.previewHtml;
  }
  if (!winner.meta.svgMarkup && loser.meta.svgMarkup) {
    winner.meta.svgMarkup = loser.meta.svgMarkup;
  }
  if (!winner.meta.cardId && loser.meta.cardId) winner.meta.cardId = loser.meta.cardId;
  if (!winner.meta.cardInstanceId && loser.meta.cardInstanceId) {
    winner.meta.cardInstanceId = loser.meta.cardInstanceId;
  }
  if (!winner.meta.bundleId && loser.meta.bundleId) winner.meta.bundleId = loser.meta.bundleId;
  if (
    fileExtension(winner.meta.title) &&
    !fileExtension(loser.meta.title) &&
    loser.meta.title
  ) {
    winner.meta.title = loser.meta.title;
  }
  winner.meta.capabilities = {
    inlinePreview:
      winner.meta.capabilities.inlinePreview || loser.meta.capabilities.inlinePreview,
    download: winner.meta.capabilities.download || loser.meta.capabilities.download,
    fullscreen:
      winner.meta.capabilities.fullscreen || loser.meta.capabilities.fullscreen,
    wideLayout:
      winner.meta.capabilities.wideLayout || loser.meta.capabilities.wideLayout,
  };
  winner.meta.primaryDeliverable =
    winner.meta.primaryDeliverable || loser.meta.primaryDeliverable;
  if (!winner.meta.layout && loser.meta.layout) {
    winner.meta.layout = loser.meta.layout;
  }
}

function applyMetaToPart(entry: ArtifactEntry): MessagePart {
  const { part, meta } = entry;
  const existingData = (part.data ?? {}) as Record<string, unknown>;
  const existingReg =
    existingData.artifact_registry &&
    typeof existingData.artifact_registry === "object" &&
    !Array.isArray(existingData.artifact_registry)
      ? (existingData.artifact_registry as Record<string, unknown>)
      : {};
  const nextData: Record<string, unknown> = {
    ...existingData,
    ...(meta.fileId && !existingData.file_id ? { file_id: meta.fileId } : {}),
    ...(meta.path && !existingData.path ? { path: meta.path } : {}),
    ...(meta.sizeBytes !== undefined && existingData.size_bytes === undefined
      ? { size_bytes: meta.sizeBytes }
      : {}),
    ...(meta.mimeType && !existingData.mime_type ? { mime_type: meta.mimeType } : {}),
    ...(meta.sha256 && !existingData.sha256 ? { sha256: meta.sha256 } : {}),
    ...(meta.previewHtml && !existingData.preview_html
      ? { preview_html: meta.previewHtml }
      : {}),
    ...(meta.svgMarkup && !existingData.svg_markup
      ? { svg_markup: meta.svgMarkup }
      : {}),
    ...(meta.title &&
    (!existingData.title || fileExtension(existingData.title) === "svg")
      ? { title: meta.title }
      : {}),
    artifact_registry: {
      ...existingReg,
      canonical_id: meta.canonicalId,
      artifact_type: meta.artifactType,
      title: meta.title,
      capabilities: {
        inline_preview: meta.capabilities.inlinePreview,
        download: meta.capabilities.download,
        fullscreen: meta.capabilities.fullscreen,
        wide_layout: meta.capabilities.wideLayout,
      },
      merged_from_ids: meta.mergedFromIds,
      primary_deliverable: meta.primaryDeliverable,
      layout: meta.capabilities.wideLayout
        ? "wide"
        : meta.layout ?? existingReg.layout ?? "block",
    },
  };
  return { ...part, data: nextData };
}

/**
 * Deduplicate overlapping artifacts in a single message (or part list) and
 * merge their capabilities/metadata onto the surviving deliverable part.
 */
export function reconcileArtifacts(parts: MessagePart[]): {
  parts: MessagePart[];
  entriesByPartId: Map<string, ArtifactEntry>;
} {
  const toolSources = collectToolSources(parts);
  const artifactEntries: ArtifactEntry[] = [];
  const entryByPartId = new Map<string, ArtifactEntry>();

  for (const part of parts) {
    if (!ARTIFACT_PART_TYPES.has(part.type) || part.status === "failed") {
      continue;
    }
    const entry = buildEntry(part, toolSources);
    artifactEntries.push(entry);
    entryByPartId.set(part.id, entry);
  }

  const mergedWinnerIds = new Set<string>();
  for (let i = 0; i < artifactEntries.length; i += 1) {
    let primary = artifactEntries[i];
    if (primary.suppressed) continue;
    for (let j = i + 1; j < artifactEntries.length; j += 1) {
      const candidate = artifactEntries[j];
      if (!canMergeEntries(primary, candidate)) continue;
      const [winner, loser] = pickWinner(primary, candidate);
      mergeEntries(winner, loser);
      mergedWinnerIds.add(winner.part.id);
      loser.suppressed = true;
      loser.mergedInto = winner.part.id;
      if (winner === candidate) {
        primary = candidate;
      }
    }
  }

  const reconciled: MessagePart[] = [];
  for (const part of parts) {
    if (isBackendSuppressedPart(part)) continue;
    const entry = entryByPartId.get(part.id);
    if (!entry) {
      reconciled.push(part);
      continue;
    }
    if (entry.suppressed) continue;
    if (!mergedWinnerIds.has(part.id)) {
      reconciled.push(part);
      continue;
    }
    const updatedPart = applyMetaToPart(entry);
    entry.part = updatedPart;
    reconciled.push(updatedPart);
  }

  return { parts: reconciled, entriesByPartId: entryByPartId };
}

function isTrailingNonPrimary(part: MessagePart): boolean {
  if (part.type === "quiz") return true;
  if (part.type !== "component") return false;
  const ct = typeof part.data?.component_type === "string" ? part.data.component_type : "";
  return NON_PRIMARY_COMPONENT_TYPES.has(ct);
}

/**
 * Deliverable-first composition for an already-filtered answer-body array.
 *
 * When a primary deliverable (e.g. an SVG, chart, magic_card, or HTML preview)
 * sits AFTER explanatory final-answer prose (either from legacy DB storage or
 * mid-stream before server splicing) without an explicit `side`/`inline` anchor,
 * hoist it immediately before the first explanatory text block.
 *
 * Preserves:
 * - explicit `layout: "side"` / `layout: "inline"` pairings
 * - interleaved `introText -> artifact -> followUpText` flows where the
 *   artifact is already ahead of `followUpText`
 * - trailing `graph_update_proposal` and interactive quiz components
 */
export function composeAnswerOrdering(
  answerParts: MessagePart[],
  entriesByPartId?: Map<string, ArtifactEntry>,
): MessagePart[] {
  if (answerParts.length <= 1) return answerParts;

  const isPrimary = (part: MessagePart): boolean => {
    if (part.type === "svg" || part.data?.state === "removed") return false;
    const registry = part.data?.artifact_registry as { placement?: string } | undefined;
    if (registry?.placement === "referenced") return false;
    const layout = typeof part.data?.layout === "string" ? part.data.layout : "";
    if (layout === "side" || layout === "inline") return false;
    const entry = entriesByPartId?.get(part.id);
    if (entry) return entry.meta.primaryDeliverable && !entry.suppressed;
    if (part.type === "component") {
      const ct = typeof part.data?.component_type === "string" ? part.data.component_type : "";
      return !NON_PRIMARY_COMPONENT_TYPES.has(ct);
    }
    return (
      part.type === "image" ||
      part.type === "magic_card" ||
      part.type === "chart" ||
      part.type === "subapp_artifact" ||
      part.type === "ui" ||
      ((part.type === "sandbox_artifact" || part.type === "sandbox") &&
        (isSvgFileData(part.data) ||
          isImageFileData(part.data) ||
          isHtmlFileData(part.data) ||
          typeof part.data?.preview_html === "string"))
    );
  };

  // Find the last explanatory text part; only primary deliverables sitting
  // AFTER every explanatory text part (i.e. sunk to the bottom) are hoisted
  // ahead of the main explanation. Multi-step `n1 -> img1 -> a1` flows stay
  // completely untouched.
  let lastTextIndex = -1;
  for (let i = answerParts.length - 1; i >= 0; i -= 1) {
    if (answerParts[i].type === "text" && answerParts[i].content?.trim()) {
      lastTextIndex = i;
      break;
    }
  }
  if (lastTextIndex < 0) return answerParts;

  // Target insertion index: before the first final_answer text, or before the
  // last text block if there is no explicit kind="final_answer" mark.
  const firstFinalIndex = answerParts.findIndex(
    (part) => part.type === "text" && part.data?.kind === "final_answer",
  );
  const insertAt = firstFinalIndex >= 0 ? firstFinalIndex : lastTextIndex;

  const hoisted: MessagePart[] = [];
  const hoistedIds = new Set<string>();
  for (let i = lastTextIndex + 1; i < answerParts.length; i += 1) {
    const part = answerParts[i];
    if (isPrimary(part) && !isTrailingNonPrimary(part)) {
      hoisted.push(part);
      hoistedIds.add(part.id);
    }
  }

  if (!hoisted.length) return answerParts;

  const result: MessagePart[] = [];
  for (let i = 0; i < answerParts.length; i += 1) {
    if (i === insertAt) {
      result.push(...hoisted);
    }
    const part = answerParts[i];
    if (!hoistedIds.has(part.id)) {
      result.push(part);
    }
  }
  return result;
}

export function resolveArtifactMeta(
  part: MessagePart,
  siblingParts?: MessagePart[],
): UnifiedArtifactMeta {
  const toolSources = collectToolSources(siblingParts ?? [part]);
  return buildEntry(part, toolSources).meta;
}

import { unified } from "unified";
import remarkParse from "remark-parse";
import type { RootContent } from "mdast";

/**
 * Parse and rewrite inline document citation markers emitted by the model.
 *
 * Expected forms (full-width or half-width punctuation):
 *   （依据文件：{file_id}，位置：paragraph:1）
 *   （引用文件摘录：文件 {file_id}，位置 paragraph:7、8、9）
 *   (source: file {file_id}, locator: paragraph:1)
 *   （网页引用：1） / [1] when paired with a source_list of URLs
 *
 * Citations are rewritten to internal markdown links under `/__lgcite__/…`
 * or `/__lgwebcite__/…` so Streamdown's URL sanitizer keeps them; the React
 * `a` override turns those links into hover/click badges.
 */

export type DocumentCitation = {
  fileId: string;
  locators: string;
  raw: string;
  index: number;
};

export type WebCitation = {
  index: number;
  raw: string;
  /** false when the model cited a source index missing from the list. */
  known?: boolean;
};

const FILE_ID =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/** Match the Chinese citation markers that the backend prompt requests. */
const CITATION_PATTERN = new RegExp(
  [
    // （依据文件：uuid，位置：loc） / （引用文件摘录：文件 uuid，位置 loc）
    `（\\s*(?:引用文件摘录[:：]?\\s*文件|依据文件)[:：]?\\s*(${FILE_ID})\\s*[，,]\\s*位置[:：]?\\s*([^）]+?)\\s*）`,
    // （引用文件：uuid · paragraph:1） looser fallback
    `（\\s*引用文件[:：]?\\s*(${FILE_ID})\\s*[，,·]\\s*(?:位置[:：]?\\s*)?([^）]+?)\\s*）`,
    // (source file uuid, locator: paragraph:1)
    `\\(\\s*(?:source|cite|citation)\\s*(?:file)?[:：]?\\s*(${FILE_ID})\\s*[,，]\\s*(?:locator|位置)[:：]?\\s*([^)]+?)\\s*\\)`,
  ].join("|"),
  "giu",
);

const CITE_PREFIX = "/__lgcite__/";
const WEB_CITE_PREFIX = "/__lgwebcite__/";

/** Backend-injected or model-emitted web citation markers. */
const WEB_CITATION_PATTERN =
  /(?<!\\)(?:（\s*网页引用[:：]?\s*(\d{1,3})\s*）|\[ref_(\d{1,3})\]|\[(\d{1,3})\](?!\())/giu;

const citationParser = unified().use(remarkParse);

/** Rewrite prose only: code, real links, images and reference definitions keep
 * their original Markdown bytes. Slice by source offsets rather than serialize. */
function rewriteCitationProse(text: string, rewrite: (prose: string) => string): string {
  const ranges: Array<[number, number]> = [];
  const visit = (node: RootContent) => {
    if (node.type === "text") {
      const start = node.position?.start.offset;
      const end = node.position?.end.offset;
      if (start !== undefined && end !== undefined) ranges.push([start, end]);
    } else if ("children" in node && node.type !== "link" && node.type !== "linkReference") {
      node.children.forEach((child) => visit(child as RootContent));
    }
  };
  citationParser.parse(text).children.forEach(visit);
  let cursor = 0;
  let result = "";
  for (const [start, end] of ranges) {
    result += text.slice(cursor, start) + rewrite(text.slice(start, end));
    cursor = end;
  }
  return result + text.slice(cursor);
}

/** Only consecutive markers in one prose run form a group. Punctuation,
 * sentence text and newlines always retain separate evidence associations. */
function groupWebCitations(text: string): string {
  return text.replace(
    /\[\d+\]\(\/__lgwebcite__\/\d+\)(?:[ \t]*\[\d+\]\(\/__lgwebcite__\/\d+\))+/g,
    (group) => {
      const indexes = [...new Set([...group.matchAll(/\/__lgwebcite__\/(\d+)/g)].map((match) => match[1]))];
      return `[${indexes.join(",")}](${WEB_CITE_PREFIX}${indexes[0]}?refs=${indexes.join(",")})`;
    },
  );
}

export function isDocumentCitationHref(href: string | null | undefined): boolean {
  return typeof href === "string" && href.startsWith(CITE_PREFIX);
}

export function isWebCitationHref(href: string | null | undefined): boolean {
  return typeof href === "string" && href.startsWith(WEB_CITE_PREFIX);
}

export function parseDocumentCitationHref(href: string): {
  fileId: string;
  locators: string;
  index: number;
} | null {
  if (!isDocumentCitationHref(href)) return null;
  try {
    // Use a base so relative paths parse cleanly in Node/jsdom.
    const url = new URL(href, "https://learngraph.local");
    const fileId = url.pathname.slice(CITE_PREFIX.length);
    if (!fileId) return null;
    return {
      fileId,
      locators: url.searchParams.get("loc") ?? "",
      index: Number(url.searchParams.get("i") || "0") || 0,
    };
  } catch {
    return null;
  }
}

export function parseWebCitationHref(href: string): {
  index: number;
  indexes: number[];
  missing?: boolean;
} | null {
  if (!isWebCitationHref(href)) return null;
  try {
    const url = new URL(href, "https://learngraph.local");
    const index = Number(url.pathname.slice(WEB_CITE_PREFIX.length) || "0") || 0;
    if (index < 1) return null;
    const indexes = [...new Set((url.searchParams.get("refs") ?? String(index))
      .split(",").map(Number).filter((value) => Number.isInteger(value) && value > 0))];
    return { index, indexes: indexes.length ? indexes : [index], missing: url.searchParams.get("missing") === "1" };
  } catch {
    return null;
  }
}

/**
 * Replace citation markers with markdown links that Streamdown can render.
 * The link text is a compact numeric badge label; the href carries metadata.
 */
export function rewriteDocumentCitations(text: string): {
  markdown: string;
  citations: DocumentCitation[];
} {
  if (!text || text.search(CITATION_PATTERN) < 0) return { markdown: text, citations: [] };
  const citations: DocumentCitation[] = [];
  const indexByKey = new Map<string, number>();

  const markdown = rewriteCitationProse(text, (prose) => prose.replace(CITATION_PATTERN, (...args) => {
    const match = args[0] as string;
    // rebuild groups from replace callback args
    const groups = args.slice(1, -2) as string[];
    const fileId = groups[0] || groups[2] || groups[4];
    const locators = (groups[1] || groups[3] || groups[5] || "").trim();
    if (!fileId) return match;
    const key = `${fileId}|${locators}`;
    let index = indexByKey.get(key);
    if (index === undefined) {
      index = indexByKey.size + 1;
      indexByKey.set(key, index);
      citations.push({ fileId, locators, raw: match, index });
    }
    const href = `${CITE_PREFIX}${fileId}?loc=${encodeURIComponent(locators)}&i=${index}`;
    return `[${index}](${href})`;
  }));

  return { markdown, citations };
}

/**
 * Rewrite web citation markers to internal links when a source_list of URLs
 * is available. Numeric ``[n]`` / ``[ref_n]`` only rewrites when ``n`` is a
 * known source index.
 */
export function rewriteWebCitations(
  text: string,
  knownIndexes?: Set<number> | number[],
): {
  markdown: string;
  citations: WebCitation[];
} {
  if (!text || text.search(WEB_CITATION_PATTERN) < 0) return { markdown: text, citations: [] };
  const allowed =
    knownIndexes instanceof Set
      ? knownIndexes
      : knownIndexes
        ? new Set(knownIndexes)
        : null;
  const citations: WebCitation[] = [];
  const seen = new Set<number>();
  const markdown = rewriteCitationProse(text, (prose) => groupWebCitations(prose.replace(WEB_CITATION_PATTERN, (match, g1, g2, g3) => {
    const index = Number(g1 || g2 || g3 || "0") || 0;
    if (index < 1) return match;
    const isBareNumeric = match.startsWith("[");
    const isKnown = !allowed || allowed.has(index);
    // A bare [n] with no source list at all stays untouched (it may be an
    // ordinary markdown reference). Once a source list exists, every numeric
    // marker is a citation intent — a known index becomes a live badge, an
    // unknown index still renders as a grey "来源不可用" badge instead of
    // leaking raw [n] text into the answer.
    if (isBareNumeric && !allowed) {
      return match;
    }
    if (!seen.has(index)) {
      seen.add(index);
      citations.push({ index, raw: match, known: isKnown });
    }
    return `[${index}](${WEB_CITE_PREFIX}${index}${isKnown ? "" : "?missing=1"})`;
  })));
  return { markdown, citations };
}

/**
 * Apply document then web citation rewrites. Document markers take precedence
 * because they are rewritten first into links that web patterns will not rematch.
 */
export function rewriteAllCitations(
  text: string,
  webSourceIndexes?: Set<number> | number[],
): {
  markdown: string;
  documentCitations: DocumentCitation[];
  webCitations: WebCitation[];
} {
  const docs = rewriteDocumentCitations(text);
  const webs = rewriteWebCitations(docs.markdown, webSourceIndexes);
  return {
    markdown: webs.markdown,
    documentCitations: docs.citations,
    webCitations: webs.citations,
  };
}

export function documentHref(
  workspaceId: string,
  fileId: string,
  options?: { chunkId?: string; locator?: string },
): string {
  if (!workspaceId || !fileId) return "";
  const params = new URLSearchParams();
  if (options?.chunkId) params.set("chunk", options.chunkId);
  if (options?.locator) params.set("locator", options.locator);
  const query = params.toString();
  return `/w/${workspaceId}/documents/${fileId}${query ? `?${query}` : ""}`;
}

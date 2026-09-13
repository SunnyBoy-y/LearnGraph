import { createElement, useMemo, useRef, type ReactNode } from "react";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import type { Root, RootContent } from "mdast";
import {
  LazyStreamdown,
  type CodeHighlightMode,
} from "@/components/ai-elements/lazy-streamdown";
import { normalizeLatexDelimiters } from "@/lib/markdown";

/**
 * Parser-level incremental markdown rendering for an append-only stream.
 *
 * Evolution of the block-frozen port: instead of re-parsing (and re-rendering)
 * the whole unstable tail every chunk, the parser keeps a persistent stable
 * block list and only the LAST top-level block is ever re-parsed — CommonMark
 * guarantees appended text can only reshape the parse frontier (the final
 * block: an unclosed fence swallowing lines, a paragraph becoming a setext
 * heading, a list continuing after a blank line). Earlier blocks are final,
 * so each source region is parsed exactly once over the stream.
 *
 * Rendering mirrors the parser: every block (top-level or nested) is cached
 * by its absolute source offsets AND verified against its source slice, so a
 * block re-renders only when its text actually changed. Each frame therefore
 * re-renders just the active path (the last top-level block, recursing into
 * the last child of containers), which is O(added text + last block) instead
 * of O(document). Fenced code renders as plain line rows with an internal
 * row cache, so a large streaming fence costs O(added lines) per frame.
 *
 * Blocks render through the same Streamdown component pipeline as settled
 * messages. A final full render still resolves constructs that depend on
 * distant text, such as reference links, footnotes, and late math fences.
 */

/** Trailing blocks kept unstable as a safety margin for the parse frontier. */
const UNSTABLE_TAIL_BLOCKS = 2;
const MAX_ACTIVE_PARSE_CHARS = 8_192;

/** Maximum container recursion depth for nested block freezing. */
const MAX_CONTAINER_DEPTH = 6;

/** The shared grammar: same remark-parse + remark-gfm pipeline streamdown uses. */
const remark = unified().use(remarkParse).use(remarkGfm);

type StreamTailNode = {
  type: "raw_stream";
  position?: { start: { offset: number }; end: { offset: number } };
  streamInitial?: string;
  streamAppend?: string;
  streamRevision?: number;
} | (RootContent & {
  type: "code";
  streamInitial?: string;
  streamAppend?: string;
  streamRevision?: number;
});

export interface PositionedBlock {
  /** The parsed mdast block. */
  readonly node: RootContent;
  /** Absolute start offset in the full source (stable render key). */
  readonly start: number;
  /** Absolute end offset (exclusive) at freeze/parse time. */
  readonly end: number;
  /** Stream-stable key: the absolute start offset. */
  readonly key: number;
}

export interface IncrementalBlocks {
  /** Blocks that can no longer change; grows monotonically per generation. */
  readonly frozen: readonly PositionedBlock[];
  /** The re-parsed unstable tail (at most UNSTABLE_TAIL_BLOCKS blocks). */
  readonly tail: readonly PositionedBlock[];
  /** Bumped whenever non-append input discards the frozen prefix. */
  readonly generation: number;
}

/**
 * Parser-level incremental parser over the remark grammar. One instance
 * accumulates one streaming document; non-append input resets it.
 *
 * The persistent `stable` list holds every top-level block whose parse is
 * final (all but the last). `update` re-parses only `text.slice(activeStart)`
 * — the last block plus whatever was appended — so stable blocks are never
 * re-parsed: each region is parsed O(1) times over the stream.
 */
export class IncrementalMarkdownParser {
  private prevText = "";
  private stable: PositionedBlock[] = [];
  private activeStart = 0;
  private generation = 0;
  private cached: IncrementalBlocks | null = null;
  private prevEpoch: object | undefined;
  private overflow = false;
  private overflowCodeStart: number | null = null;
  private overflowFence: { char: "`" | "~"; length: number } | null = null;
  private overflowInitial = "";
  private overflowRevision = 0;

  /** Fold the current accumulated text and return the frozen/tail split. */
  update(text: string, epoch?: object): IncrementalBlocks {
    if (this.cached !== null && text === this.prevText) return this.cached;
    const previousLength = this.prevText.length;
    // Stream parts carry append provenance. This makes the hot path O(delta);
    // snapshots and edits retain the safe prefix check and reset on mismatch.
    const trustedAppend = epoch !== undefined && epoch === this.prevEpoch && text.length >= this.prevText.length;
    const streamReplacement = epoch !== undefined && this.prevEpoch !== undefined && epoch !== this.prevEpoch;
    if (streamReplacement || (!trustedAppend && !text.startsWith(this.prevText))) {
      this.prevText = "";
      this.stable = [];
      this.activeStart = 0;
      this.generation += 1;
      this.overflow = false;
      this.overflowCodeStart = null;
      this.overflowFence = null;
      this.overflowInitial = "";
      this.overflowRevision = 0;
    }
    this.prevEpoch = epoch;
    this.prevText = text;
    const regionStart = this.activeStart;
    let closedOverflowFence = false;
    if (this.overflow && this.overflowFence !== null) {
      const { char, length } = this.overflowFence;
      // Include the preceding fence-width plus indentation in the scan. The
      // server may split a closing marker across two SSE chunks, so looking
      // only at the newly appended suffix can leave an already-closed block
      // in the raw streaming state forever.
      const closeScanStart = Math.max(0, previousLength - length - 8);
      const close = new RegExp(`(?:^|\\n)[ \\t]{0,3}${char}{${length},}[ \\t]*(?:\\n|$)`, "u").test(
        text.slice(closeScanStart),
      );
      if (close) {
        // The exact parser runs once when the fence closes; later updates use
        // its bounded active frontier again, so prose after the fence is not
        // accidentally rendered inside the code block.
        this.overflow = false;
        this.overflowCodeStart = null;
        this.overflowFence = null;
        this.overflowInitial = "";
        closedOverflowFence = true;
      }
    }
    // An unfinished paragraph/list can be arbitrarily large. Stop feeding it
    // to remark on every token; keep its source visible as a raw tail until a
    // final render performs the exact full-document pass.
    if (!closedOverflowFence && (this.overflow || text.length - regionStart > MAX_ACTIVE_PARSE_CHARS)) {
      const hadOverflow = this.overflow;
      if (!hadOverflow) {
        const firstBreak = text.indexOf("\n", regionStart);
        const firstLineEnd = firstBreak < 0 ? text.length : firstBreak;
        const opening = /^\s{0,3}(`{3,}|~{3,})/u.exec(
          text.slice(regionStart, firstLineEnd),
        );
        const openingFence = opening !== null;
        this.overflowFence = opening
          ? { char: opening[1][0] as "`" | "~", length: opening[1].length }
          : null;
        this.overflowCodeStart = openingFence
          ? Math.min(firstLineEnd + 1, text.length)
          : null;
        this.overflowInitial = this.overflowCodeStart === null
          ? text.slice(regionStart)
          : text.slice(this.overflowCodeStart);
      }
      this.overflow = true;
      this.overflowRevision += 1;
      const append = hadOverflow ? text.slice(previousLength) : "";
      const node = this.overflowCodeStart !== null
        ? ({ type: "code", lang: undefined, meta: undefined, value: this.overflowInitial, streamInitial: this.overflowInitial, streamAppend: append, streamRevision: this.overflowRevision, position: { start: { offset: 0 }, end: { offset: text.length - regionStart } } } as unknown as RootContent)
        : ({ type: "raw_stream", streamInitial: this.overflowInitial, streamAppend: append, streamRevision: this.overflowRevision, position: { start: { offset: 0 }, end: { offset: text.length - regionStart } } } as unknown as RootContent);
      this.cached = {
        frozen: [...this.stable],
        tail: [{ node, start: regionStart, end: text.length, key: regionStart }],
        generation: this.generation,
      };
      return this.cached!;
    }
    const tree = remark.parse(text.slice(regionStart)) as Root;
    const blocks = tree.children;
    // Positions are required for incremental cuts. remark-parse provides them
    // by default; if a grammar ever disables them, fall back to a full
    // re-parse so correctness never depends on our slicing.
    const positioned = (node: RootContent) =>
      node.position?.start.offset !== undefined &&
      node.position?.end.offset !== undefined;
    if (!blocks.every(positioned)) {
      this.stable = [];
      this.activeStart = 0;
      const full = remark.parse(text) as Root;
      return this.settle(full, text);
    }
    return this.settle(tree, text, regionStart);
  }

  /** Fold a freshly parsed root into stable + tail, advancing the frontier. */
  private settle(
    tree: Root,
    text: string,
    regionStart = 0,
  ): IncrementalBlocks {
    const blocks = tree.children;
    // All but the trailing UNSTABLE_TAIL_BLOCKS are final now. The safety
    // margin keeps the second-to-last block in the unstable region so a
    // frontier reshape (e.g. a list item absorbing a following line) can
    // never corrupt a block we already froze.
    let firstUnstable = Math.max(0, blocks.length - UNSTABLE_TAIL_BLOCKS);
    for (const node of blocks.slice(0, firstUnstable)) {
      const start = node.position?.start.offset;
      const end = node.position?.end.offset;
      this.stable.push({
        node,
        start: regionStart + (start ?? 0),
        end: regionStart + (end ?? 0),
        key: regionStart + (start ?? 0),
      });
    }
    // Advance the parse frontier to the first unstable block's start.
    const frontier = blocks[firstUnstable];
    if (frontier !== undefined && frontier.position?.start.offset !== undefined) {
      this.activeStart = regionStart + frontier.position.start.offset;
    } else if (firstUnstable > 0) {
      // No frontier block (empty tail) — everything parsed is final.
      const last = blocks[blocks.length - 1];
      if (last?.position?.end.offset !== undefined) {
        this.activeStart = regionStart + last.position.end.offset;
      }
    } else {
      // Whole region is the unstable tail; keep the frontier where it was so
      // the same region re-parses next frame (it is still growing).
    }
    const tail = blocks.slice(firstUnstable).map((node, index) => {
      const start = node.position?.start.offset;
      const end = node.position?.end.offset;
      return {
        node,
        start: regionStart + (start ?? 0),
        end: regionStart + (end ?? text.length),
        key: regionStart + (start ?? -(index + 1)),
      };
    });
    this.cached = { frozen: [...this.stable], tail, generation: this.generation };
      return this.cached!;
  }
}

/**
 * Renders an mdast code block (fence or indented) as plain preformatted text,
 * with an internal per-instance scanner. It advances only over the newly
 * appended source, so an open multi-megabyte fence never repeatedly splits
 * every already-rendered line.
 */
function CodeBlockPlain({
  node,
  appendOnly,
  initialValue,
  appendChunk,
  appendRevision,
}: {
  node: RootContent & { type: "code" };
  appendOnly: boolean;
  initialValue?: string;
  appendChunk?: string;
  appendRevision?: number;
}) {
  const cacheRef = useRef<{
    sourceLength: number;
    rows: ReactNode[];
    lineChunks: string[];
    lastRevision?: number;
  } | null>(null);
  if (cacheRef.current === null)
    cacheRef.current = { sourceLength: 0, rows: [], lineChunks: [] };
  const cache = cacheRef.current;
  const isFirstStreamRevision = appendRevision !== undefined && cache.lastRevision === undefined;
  const sameStreamRevision = appendRevision !== undefined &&
    cache.lastRevision === appendRevision;
  const olderStreamRevision = appendRevision !== undefined &&
    cache.lastRevision !== undefined && appendRevision < cache.lastRevision;
  if (!appendOnly || isFirstStreamRevision || olderStreamRevision ||
      (appendChunk === undefined && node.value.length < cache.sourceLength)) {
    cache.sourceLength = 0;
    cache.rows = [];
    cache.lineChunks = [];
  }
  const added = sameStreamRevision
    ? ""
    : appendOnly && appendChunk !== undefined && !isFirstStreamRevision
    ? appendChunk
    : (cache.sourceLength === 0 && initialValue !== undefined ? initialValue : node.value.slice(cache.sourceLength));
  let cursor = 0;
  for (;;) {
    const newline = added.indexOf("\n", cursor);
    if (newline < 0) {
      if (cursor < added.length) cache.lineChunks.push(added.slice(cursor));
      break;
    }
    if (newline > cursor) cache.lineChunks.push(added.slice(cursor, newline));
    const line = cache.rows.length;
    cache.rows.push(
      createElement("div", { key: line }, cache.lineChunks),
    );
    cache.lineChunks = [];
    cursor = newline + 1;
  }
  cache.sourceLength += added.length;
  if (appendRevision !== undefined) cache.lastRevision = appendRevision;
  return (
    <pre className="incremental-markdown-fence overflow-x-auto rounded-lg border bg-muted/30 p-3 text-[13px] leading-6">
      <code>
        {cache.rows}
        <div>{cache.lineChunks}</div>
      </code>
    </pre>
  );
}

function RawStreamBlock({
  initialValue = "",
  appendChunk = "",
  appendRevision,
}: {
  initialValue?: string;
  appendChunk?: string;
  appendRevision?: number;
}) {
  const chunksRef = useRef<{ chunks: string[]; revision: number } | null>(null);
  if (chunksRef.current === null) chunksRef.current = { chunks: [initialValue], revision: 0 };
  if (appendRevision !== undefined && appendRevision > chunksRef.current.revision) {
    if (appendChunk) chunksRef.current.chunks.push(appendChunk);
    chunksRef.current.revision = appendRevision;
  }
  return <div className="whitespace-pre-wrap">{chunksRef.current.chunks}</div>;
}

function isCodeBlock(node: RootContent): node is RootContent & { type: "code" } {
  return node.type === "code";
}

const CONTAINER_TYPES: ReadonlySet<string> = new Set(["list", "blockquote"]);

function isContainerBlock(node: RootContent): boolean {
  return CONTAINER_TYPES.has(node.type);
}

/**
 * Component-scoped renderer: caches block elements keyed by their absolute
 * source offsets and verified against the current source slice, so each frame
 * re-renders only blocks whose text actually changed (the active path).
 * Idempotent per text value, so React may re-execute the calling render freely.
 */
class IncrementalRenderer {
  private readonly parser = new IncrementalMarkdownParser();
  private readonly codeHighlight: CodeHighlightMode;
  private readonly components?: Record<string, unknown>;
  private generation = -1;
  private frozenElements: ReactNode[] = [];
  private lastFrozenCount = 0;
  /** Stable-block element cache: absolute start offset -> source slice + element. */
  private blockCache = new Map<number, { src: string; element: ReactNode }>();
  private lastText: string | null = null;
  private lastEpoch: object | undefined;
  private lastRendered: ReactNode[] = [];

  constructor(codeHighlight: CodeHighlightMode, components?: Record<string, unknown>) {
    this.codeHighlight = codeHighlight;
    this.components = components;
  }

  render(text: string, epoch?: object): ReactNode[] {
    if (text === this.lastText) return this.lastRendered;
    const appendOnly =
      epoch !== undefined &&
      epoch === this.lastEpoch &&
      this.lastText !== null &&
      text.length >= this.lastText.length;
    const { frozen, tail, generation } = this.parser.update(text, epoch);
    if (generation !== this.generation) {
      this.generation = generation;
      this.lastFrozenCount = 0;
      this.frozenElements = [];
      this.blockCache.clear();
    }
    // Newly frozen blocks render once and are cached by source offset. The
    // safety margin (UNSTABLE_TAIL_BLOCKS) guarantees frozen blocks never
    // reshape, but the cache is still verified against the source slice so
    // any parser edge case self-heals on the next frame.
    for (let index = this.lastFrozenCount; index < frozen.length; index += 1) {
      const block = frozen[index];
      const element = this.renderBlockElement(text, block, 0, appendOnly);
      if (this.frozenElements.length > 0) this.frozenElements.push("\n");
      this.frozenElements.push(element);
    }
    this.lastFrozenCount = frozen.length;
    // The unstable tail: cache each block by its source offsets + slice, so
    // only blocks whose text changed re-render (the active path).
    const children = [...this.frozenElements];
    for (const block of tail) {
      if (children.length > 0) children.push("\n");
      children.push(this.renderBlockElement(text, block, 0, appendOnly));
    }
    this.lastText = text;
    this.lastEpoch = epoch;
    this.lastRendered = children;
    return children;
  }

  /** Render one positioned block, recursing into containers for nested freezing. */
  private renderBlockElement(
    text: string,
    block: PositionedBlock,
    depth: number,
    appendOnly: boolean,
  ): ReactNode {
    const streamNode = block.node as Partial<StreamTailNode>;
    const streamedTail =
      (streamNode.type === "raw_stream" || streamNode.type === "code") &&
      streamNode.streamInitial !== undefined;
    // Synthetic overflow tails carry their own append chunks; avoid copying
    // the entire accumulated message just to compare/cache them.
    const src = streamedTail ? `stream:${text.length}` : text.slice(block.start, block.end);
    const cached = this.blockCache.get(block.key);
    if (cached !== undefined && cached.src === src) return cached.element;
    const element = this.buildBlockElement(text, block, src, depth, appendOnly);
    this.blockCache.set(block.key, { src, element });
    return element;
  }

  /** Build (and cache) the element for a block; containers freeze their children. */
  private buildBlockElement(
    text: string,
    block: PositionedBlock,
    src: string,
    depth: number,
    appendOnly: boolean,
  ): ReactNode {
    const node = block.node;
    // A full part replacement resets parser state. Include that generation in
    // the React key so code-row caches cannot survive a retry/edit with the
    // same source offset.
    const renderKey = `${this.generation}:${block.key}`;
    if (isCodeBlock(node)) {
      const streamNode = node as unknown as StreamTailNode;
      return createElement(CodeBlockPlain, {
        appendChunk: streamNode.streamAppend,
        appendRevision: streamNode.streamRevision,
        appendOnly,
        initialValue: streamNode.streamInitial,
        key: renderKey,
        node,
      });
    }
    if ((node as { type?: string }).type === "raw_stream") {
      const streamNode = node as unknown as StreamTailNode;
      return createElement(RawStreamBlock, {
        appendChunk: streamNode.streamAppend,
        initialValue: streamNode.streamInitial,
        appendRevision: streamNode.streamRevision,
        key: renderKey,
      });
    }
    if (depth < MAX_CONTAINER_DEPTH && isContainerBlock(node)) {
      return this.renderContainer(text, block, node, depth, appendOnly);
    }
    // Leaf block: render its source slice through the exact streamdown
    // pipeline settled messages use, keyed by the absolute start offset so
    // React reconciles (never remounts) when a block crosses a freeze edge.
    return createElement(LazyStreamdown, {
      codeHighlight: this.codeHighlight,
      components: this.components,
      key: renderKey,
      children: normalizeLatexDelimiters(src),
    });
  }

  /** Freeze a container's children the same way the top level freezes blocks. */
  private renderContainer(
    text: string,
    block: PositionedBlock,
    node: RootContent & {
      children?: Array<RootContent & { position?: unknown }>;
      ordered?: boolean | null;
      start?: number | null;
    },
    depth: number,
    appendOnly: boolean,
  ): ReactNode {
    const children = node.children ?? [];
    const items: PositionedBlock[] = [];
    // remark positions are relative to the parser's active slice. Translate
    // nested positions back into the full message before slicing source.
    const nodeStart = (node.position as { start?: { offset?: number } } | undefined)
      ?.start?.offset ?? 0;
    const positionBase = block.start - nodeStart;
    for (const child of children) {
      const start = (child.position as { start?: { offset?: number } } | undefined)
        ?.start?.offset;
      const end = (child.position as { end?: { offset?: number } } | undefined)
        ?.end?.offset;
      if (start === undefined || end === undefined) continue;
      items.push({
        node: child as RootContent,
        start: positionBase + start,
        end: positionBase + end,
        key: positionBase + start,
      });
    }
    // Stable items hit the shared block cache (zero re-render); the active
    // tail items re-render through it every frame. Container markup uses
    // native ul/ol/blockquote elements with a minimal class so the streaming
    // view stays cheap; the settled full streamdown render replaces it once
    // the message completes.
    const renderedItems: ReactNode[] = [];
    for (const item of items) {
      if (renderedItems.length > 0) renderedItems.push("\n");
      renderedItems.push(this.renderBlockElement(text, item, depth + 1, appendOnly));
    }
    if (node.type === "list") {
      const ordered = node.ordered === true;
      return createElement(
        ordered ? "ol" : "ul",
        {
          className: "incremental-markdown-list",
          key: block.key,
          ...(ordered && typeof node.start === "number"
            ? { start: node.start }
            : {}),
        },
        renderedItems,
      );
    }
    return createElement(
      "blockquote",
      { className: "incremental-markdown-blockquote", key: block.key },
      renderedItems,
    );
  }
}

/**
 * Streaming markdown renderer with parser-level incremental parsing and
 * rendering. While streaming a large text part, only the active path (the
 * last top-level block and its growing children) re-parses and re-renders per
 * frame; everything else keeps cached element identity so React skips it, and
 * fenced code renders as plain incremental rows.
 */
export function IncrementalMarkdown({
  text,
  codeHighlight = "shiki",
  components,
  epoch,
}: {
  text: string;
  codeHighlight?: CodeHighlightMode;
  components?: Record<string, unknown>;
  epoch?: object;
}): ReactNode {
  const renderer = useMemo(
    () => new IncrementalRenderer(codeHighlight, components),
    [codeHighlight, components],
  );
  return useMemo(() => renderer.render(text, epoch), [renderer, text, epoch]);
}

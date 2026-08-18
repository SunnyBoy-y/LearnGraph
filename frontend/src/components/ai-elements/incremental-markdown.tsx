import { createElement, useMemo, useRef, type ReactNode } from "react";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import type { Root, RootContent } from "mdast";
import {
  LazyStreamdown,
  type CodeHighlightMode,
} from "@/components/ai-elements/lazy-streamdown";

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
 * Blocks render through the SAME streamdown pipeline as settled messages
 * (each block's source slice → LazyStreamdown), so the incremental path is
 * visually identical to the full render. Known deviation, shared with dsh and
 * any prefix-freeze scheme: reference links/footnotes whose definitions land
 * on the other side of a freeze boundary render literally while streaming;
 * the settled full render self-heals them.
 */

/** Trailing blocks kept unstable as a safety margin for the parse frontier. */
const UNSTABLE_TAIL_BLOCKS = 2;

/** Maximum container recursion depth for nested block freezing. */
const MAX_CONTAINER_DEPTH = 6;

/** The shared grammar: same remark-parse + remark-gfm pipeline streamdown uses. */
const remark = unified().use(remarkParse).use(remarkGfm);

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

  /** Fold the current accumulated text and return the frozen/tail split. */
  update(text: string): IncrementalBlocks {
    if (this.cached !== null && text === this.prevText) return this.cached;
    // Sound divergence detection: appended text keeps the whole retained
    // prefix byte-identical. startsWith is O(prefix) memcmp — two orders of
    // magnitude cheaper than parsing — so a full verify is fine per update.
    if (!text.startsWith(this.prevText)) {
      this.prevText = "";
      this.stable = [];
      this.activeStart = 0;
      this.generation += 1;
    }
    this.prevText = text;
    const regionStart = this.activeStart;
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
    return this.cached;
  }
}

/**
 * Renders an mdast code block (fence or indented) as plain preformatted text,
 * with an internal per-instance row cache: only newly streamed lines produce
 * new React elements each frame (O(added lines)), and a non-append rewrite
 * (retry/reset) is detected by a prefix mismatch and rebuilds the rows.
 */
function CodeBlockPlain({ node }: { node: RootContent & { type: "code" } }) {
  const cacheRef = useRef<{ rows: ReactNode[]; count: number } | null>(null);
  if (cacheRef.current === null) cacheRef.current = { rows: [], count: 0 };
  const cache = cacheRef.current;
  const lines = node.value.split("\n");
  // Non-append rewrite guard: the cached prefix must still match; otherwise
  // the whole block changed (retry / generation reset) and rows rebuild.
  let mismatch = lines.length < cache.count;
  for (let i = 0; !mismatch && i < cache.count; i += 1) {
    if (cache.rows[i] === undefined) {
      mismatch = true;
      break;
    }
  }
  if (mismatch || lines.length > cache.count) {
    // Prefix-verify cheaply: compare the cached row texts against the source.
    if (!mismatch) {
      for (let i = 0; i < cache.count; i += 1) {
        if (String((cache.rows[i] as { props?: { children?: unknown } }).props?.children) !== lines[i]) {
          mismatch = true;
          break;
        }
      }
    }
    if (mismatch) {
      cache.rows = [];
      cache.count = 0;
    }
    for (let i = cache.count; i < lines.length; i += 1) {
      cache.rows.push(createElement("div", { key: i, children: lines[i] }));
    }
    cache.count = lines.length;
  }
  return (
    <pre className="incremental-markdown-fence overflow-x-auto rounded-lg border bg-muted/30 p-3 text-[13px] leading-6">
      <code>{cache.rows}</code>
    </pre>
  );
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
  private generation = -1;
  private frozenElements: ReactNode[] = [];
  private lastFrozenCount = 0;
  /** Stable-block element cache: absolute start offset -> source slice + element. */
  private blockCache = new Map<number, { src: string; element: ReactNode }>();
  private lastText: string | null = null;
  private lastRendered: ReactNode[] = [];

  constructor(codeHighlight: CodeHighlightMode) {
    this.codeHighlight = codeHighlight;
  }

  render(text: string): ReactNode[] {
    if (text === this.lastText) return this.lastRendered;
    const { frozen, tail, generation } = this.parser.update(text);
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
      const element = this.renderBlockElement(text, block, 0);
      if (this.frozenElements.length > 0) this.frozenElements.push("\n");
      this.frozenElements.push(element);
    }
    this.lastFrozenCount = frozen.length;
    // The unstable tail: cache each block by its source offsets + slice, so
    // only blocks whose text changed re-render (the active path).
    const children = [...this.frozenElements];
    for (const block of tail) {
      if (children.length > 0) children.push("\n");
      children.push(this.renderBlockElement(text, block, 0));
    }
    this.lastText = text;
    this.lastRendered = children;
    return children;
  }

  /** Render one positioned block, recursing into containers for nested freezing. */
  private renderBlockElement(
    text: string,
    block: PositionedBlock,
    depth: number,
  ): ReactNode {
    const src = text.slice(block.start, block.end);
    const cached = this.blockCache.get(block.key);
    if (cached !== undefined && cached.src === src) return cached.element;
    const element = this.buildBlockElement(text, block, src, depth);
    this.blockCache.set(block.key, { src, element });
    return element;
  }

  /** Build (and cache) the element for a block; containers freeze their children. */
  private buildBlockElement(
    text: string,
    block: PositionedBlock,
    src: string,
    depth: number,
  ): ReactNode {
    const node = block.node;
    if (isCodeBlock(node)) {
      return createElement(CodeBlockPlain, { key: block.key, node });
    }
    if (depth < MAX_CONTAINER_DEPTH && isContainerBlock(node)) {
      return this.renderContainer(text, block, node, depth);
    }
    // Leaf block: render its source slice through the exact streamdown
    // pipeline settled messages use, keyed by the absolute start offset so
    // React reconciles (never remounts) when a block crosses a freeze edge.
    return createElement(LazyStreamdown, {
      codeHighlight: this.codeHighlight,
      key: block.key,
      children: src,
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
  ): ReactNode {
    const children = node.children ?? [];
    const items: PositionedBlock[] = [];
    for (const child of children) {
      const start = (child.position as { start?: { offset?: number } } | undefined)
        ?.start?.offset;
      const end = (child.position as { end?: { offset?: number } } | undefined)
        ?.end?.offset;
      if (start === undefined || end === undefined) continue;
      items.push({
        node: child as RootContent,
        start,
        end,
        key: start,
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
      renderedItems.push(this.renderBlockElement(text, item, depth + 1));
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
}: {
  text: string;
  codeHighlight?: CodeHighlightMode;
}): ReactNode {
  const renderer = useMemo(
    () => new IncrementalRenderer(codeHighlight),
    [codeHighlight],
  );
  return useMemo(() => renderer.render(text), [renderer, text]);
}

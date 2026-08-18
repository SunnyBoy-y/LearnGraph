import { useEffect, useState, type ReactNode } from "react";

// Lazy-load the streamdown/hast/parse5 subtree. @streamdown/mermaid is
// removed on purpose: it statically imports mermaid, which imports the d3 v7
// hub (src/index.js re-exports ~30 mutually-importing submodules). Rolldown
// flattens that circular ESM graph into shared vendor chunks that are
// modulepreloaded as soon as the first streamdown message renders, crashing
// module evaluation (TypeError: Cannot read properties of undefined (reading
// 'axis')). Upgrading mermaid cannot help (11.x pins d3 ^7.9.0); without the
// plugin, ```mermaid blocks degrade to plain code blocks handled by the code
// plugin.
type StreamdownLike = (props: Record<string, unknown>) => ReactNode;
type StreamdownModule = {
  Streamdown: StreamdownLike;
  plugins: Record<string, unknown>;
};

/**
 * Code-highlighting mode:
 * - "shiki": full syntax highlighting via @streamdown/code (loads shiki).
 * - "plain": no highlighting — the code fence renders as plain preformatted
 *   text with line numbers. Used while a large text part is actively
 *   streaming: re-tokenizing a multi-KB code block on every frame with shiki
 *   costs ~0.4-1s CPU and ~35MB of garbage per render (the plugin's cache key
 *   is content-based, so every streamed frame misses), which is the dominant
 *   memory/CPU driver in long agentic threads. The part re-renders with shiki
 *   once streaming finishes.
 */
export type CodeHighlightMode = "shiki" | "plain";

/** A no-op highlighter: streamdown renders code fences as plain text + line numbers. */
export function createPlainCodeHighlighter() {
  return {
    name: "plain",
    type: "code-highlighter" as const,
    supportsLanguage: () => true,
    getSupportedLanguages: () => [],
    getThemes: () => ["github-light"],
    highlight: () => null,
  };
}

function loadStreamdown(mode: CodeHighlightMode): Promise<StreamdownModule> {
  const codePlugin =
    mode === "plain"
      ? Promise.resolve(createPlainCodeHighlighter())
      : import("@streamdown/code").then((m) => m.code);
  return Promise.all([
    import("streamdown"),
    import("@streamdown/cjk"),
    codePlugin,
    import("@streamdown/math"),
  ]).then(([streamdown, cjk, code, math]) => ({
    Streamdown: streamdown.Streamdown as StreamdownLike,
    plugins: {
      cjk: cjk.cjk,
      code,
      math: math.createMathPlugin({ singleDollarTextMath: true }),
    },
  }));
}

let shikiCached: Promise<StreamdownModule> | undefined;
let plainCached: Promise<StreamdownModule> | undefined;

export function LazyStreamdown({
  children,
  codeHighlight = "shiki",
  ...props
}: {
  children: ReactNode;
  codeHighlight?: CodeHighlightMode;
  [key: string]: unknown;
}) {
  const [module, setModule] = useState<StreamdownModule | null>(null);

  useEffect(() => {
    let cancelled = false;
    const promise =
      codeHighlight === "plain"
        ? (plainCached ??= loadStreamdown("plain"))
        : (shikiCached ??= loadStreamdown("shiki"));
    promise
      .then((loaded) => {
        if (!cancelled) setModule(loaded);
      })
      .catch(() => {
        if (!cancelled) setModule(null);
      });
    return () => {
      cancelled = true;
    };
  }, [codeHighlight]);

  if (!module) {
    // Raw fallback while the renderer loads (or if dynamic import fails).
    return (
      <div className="whitespace-pre-wrap">
        {typeof children === "string" ? children : children}
      </div>
    );
  }

  const { Streamdown, plugins } = module;
  return (
    <Streamdown {...props} plugins={plugins}>
      {children}
    </Streamdown>
  );
}

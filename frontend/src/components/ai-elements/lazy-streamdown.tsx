import { createElement, useEffect, useMemo, useState, type ComponentProps, type ReactNode } from "react";
import { Streamdown, defaultRehypePlugins } from "streamdown";
import { FluentEmoji } from "@/components/chat/answer-icon";
import { getFluentEmoji } from "@/lib/fluent-emoji";
import { rehypeFluentEmoji } from "@/lib/rehype-fluent-emoji";
import { cjk } from "@streamdown/cjk";
import { code } from "@streamdown/code";
import { createMathPlugin } from "@streamdown/math";

// Mermaid remains lazy because it is only needed for diagram fences.
type StreamdownModule = {
  Streamdown: typeof Streamdown;
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

function loadStreamdown(_mode: CodeHighlightMode): Promise<StreamdownModule> {
  return Promise.resolve({
    Streamdown,
    plugins: {
      cjk,
      code,
      math: createMathPlugin({ singleDollarTextMath: true }),
    },
  });
}

let shikiCached: Promise<StreamdownModule> | undefined;
let plainCached: Promise<StreamdownModule> | undefined;
let mermaidCached: Promise<typeof import("@streamdown/mermaid")> | undefined;

export function LazyStreamdown({
  children,
  codeHighlight = "shiki",
  ...props
}: {
  children: ReactNode;
  codeHighlight?: CodeHighlightMode;
  [key: string]: unknown;
}) {
  const components = props.components as ComponentProps<typeof Streamdown>["components"];
  const renderComponents = useMemo(() => {
    const OriginalSpan = components?.span ?? "span";
    const EmojiSpan: NonNullable<ComponentProps<typeof Streamdown>["components"]>["span"] = ({ node, ...spanProps }) => {
      const codepoint = node?.properties?.dataFluentEmoji;
      const emoji = typeof codepoint === "string" ? getFluentEmoji(`fluent:${codepoint}`) : undefined;
      if (emoji) return <FluentEmoji emoji={emoji} />;
      return typeof OriginalSpan === "string" ? createElement(OriginalSpan, spanProps) : <OriginalSpan node={node} {...spanProps} />;
    };
    return { ...components, span: EmojiSpan };
  }, [components]);
  const suppliedRehypePlugins = props.rehypePlugins as ComponentProps<typeof Streamdown>["rehypePlugins"];
  const rehypePlugins = useMemo(() => [
    ...(suppliedRehypePlugins ?? Object.values(defaultRehypePlugins)), rehypeFluentEmoji,
  ], [suppliedRehypePlugins]);
  const [module, setModule] = useState<StreamdownModule | null>(null);
  const [diagramPlugin, setDiagramPlugin] = useState<unknown>(null);
  const hasMermaid = typeof children === "string" &&
    /(?:^|\n)[ \t]*(?:>[ \t]*)*(?:`{3,}|~{3,})mermaid(?:[ \t\r\n]|$)/u.test(children);

  useEffect(() => {
    if (!hasMermaid) return;
    let cancelled = false;
    const promise = mermaidCached ??= import("@streamdown/mermaid");
    promise.then((loaded) => {
      if (!cancelled) setDiagramPlugin(loaded.mermaid);
    }).catch(() => {
      // Keep source readable on a failed import and allow a later retry.
      mermaidCached = undefined;
    });
    return () => { cancelled = true; };
  }, [hasMermaid]);

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
    <Streamdown {...props} components={renderComponents} rehypePlugins={rehypePlugins} plugins={diagramPlugin ? { ...plugins, mermaid: diagramPlugin } : plugins}>
      {typeof children === "string" ? children : ""}
    </Streamdown>
  );
}

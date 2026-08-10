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

function loadStreamdown(): Promise<StreamdownModule> {
  return Promise.all([
    import("streamdown"),
    import("@streamdown/cjk"),
    import("@streamdown/code"),
    import("@streamdown/math"),
  ]).then(([streamdown, cjk, code, math]) => ({
    Streamdown: streamdown.Streamdown as StreamdownLike,
    plugins: {
      cjk: cjk.cjk,
      code: code.code,
      math: math.createMathPlugin({ singleDollarTextMath: true }),
    },
  }));
}

let cached: Promise<StreamdownModule> | undefined;

export function LazyStreamdown({
  children,
  ...props
}: {
  children: ReactNode;
  [key: string]: unknown;
}) {
  const [module, setModule] = useState<StreamdownModule | null>(null);

  useEffect(() => {
    let cancelled = false;
    const promise = cached ?? (cached = loadStreamdown());
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
  }, []);

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

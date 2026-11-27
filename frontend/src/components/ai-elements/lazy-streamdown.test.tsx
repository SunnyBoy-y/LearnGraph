import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { code as shikiCodePlugin } from "@streamdown/code";
import {
  LazyStreamdown,
  resolveStreamdownPlugins,
} from "@/components/ai-elements/lazy-streamdown";

describe("LazyStreamdown codeHighlight mode", () => {
  it("uses the no-op plain highlighter for plain mode and shiki for shiki mode", () => {
    const plainPlugins = resolveStreamdownPlugins("plain");
    const shikiPlugins = resolveStreamdownPlugins("shiki");

    const plainCode = plainPlugins.code as {
      name: string;
      highlight: (...args: unknown[]) => unknown;
    };
    expect(plainCode.name).toBe("plain");
    expect(plainCode.highlight({ code: "const x = 1;", language: "ts", themes: ["github-light", "github-dark"] })).toBeNull();
    expect(shikiPlugins.code).toBe(shikiCodePlugin);
  });

  it("does not invoke shiki highlight while codeHighlight='plain' is active", async () => {
    const spy = vi.spyOn(shikiCodePlugin, "highlight");
    try {
      const view = render(
        <LazyStreamdown codeHighlight="plain">
          {"```ts\nconst answer = 42;\n```"}
        </LazyStreamdown>,
      );

      await waitFor(() =>
        expect(
          view.container.querySelector('[data-streamdown="code-block-body"]'),
        ).toBeInTheDocument(),
      );
      expect(
        view.container.querySelector('[data-streamdown="code-block-body"]'),
      ).toHaveTextContent("const answer = 42;");
      expect(spy).not.toHaveBeenCalled();

      view.rerender(
        <LazyStreamdown codeHighlight="shiki">
          {"```ts\nconst answer = 42;\n```"}
        </LazyStreamdown>,
      );

      await waitFor(() => expect(spy).toHaveBeenCalled());
    } finally {
      spy.mockRestore();
    }
  });
});

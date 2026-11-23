import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { evaluateChecks, isRegisteredCatalogFunction } from "./catalog-functions";
import { StreamUI } from "@/components/chat/stream-ui";

vi.mock("@/components/chat/message-part-renderer", () => ({
  TextWithCitations: ({ content }: { content: string }) => <p>{content}</p>,
}));

describe("local catalog functions & @checks", () => {
  it("evaluates bounded catalog check functions safely", () => {
    expect(isRegisteredCatalogFunction("range")).toBe(true);
    expect(isRegisteredCatalogFunction("eval")).toBe(false);

    const results = evaluateChecks(
      [
        { id: "r1", fn: "required", path: "@/name", args: [], message: "需要名称", severity: "error" },
        { id: "r2", fn: "min_length", path: "@/name", args: [3], message: "名称过短", severity: "warning" },
        { id: "r3", fn: "range", path: "@/score", args: [0, 100], message: "分数越界", severity: "error" },
        { id: "r4", fn: "one_of", path: "@/mode", args: ["fast", "deep"], message: "模式非法", severity: "info" },
      ],
      { name: "AB", score: 120, mode: "fast" },
    );

    expect(results.map((r) => r.passed)).toEqual([true, false, false, true]);
    expect(results.filter((r) => !r.passed).map((r) => r.id)).toEqual(["r2", "r3"]);
  });

  it("renders failed @checks live in StreamUI and clears them when data_model satisfies checks", async () => {
    const base = {
      protocol: "suip/0.1",
      block_id: "doc_chk",
      component: "document",
      revision: 0,
      state: "streaming",
      props: { title: "服务健康" },
      data_model: { cpu: 94 },
      checks: [
        { id: "cpu_ok", fn: "max_value", path: "@/cpu", args: [85], message: "CPU 负载超过 85% 警戒线", severity: "warning" },
      ],
      blocks: [
        { id: "r", type: "row", columns: 2 },
        { id: "p", parent_id: "r", type: "paragraph", text: "当前 CPU：@{/cpu}%", weight: 2 },
        {
          id: "img",
          parent_id: "r",
          type: "image",
          query: "server rack",
          image_variant: "hero",
          fit: "cover",
          resource_state: "ready",
          resource_reason: "ready",
          media: [{ url: "https://example.com/server.png", title: "服务器机柜" }],
        },
      ],
    };

    const { container, rerender } = render(<StreamUI data={base} partId="part_chk" />);
    expect(await screen.findByText("当前 CPU：94%")).toBeInTheDocument();
    expect(screen.getByText("CPU 负载超过 85% 警戒线")).toBeInTheDocument();
    expect(container.querySelector('[data-weight="2"]')).toBeInTheDocument();
    expect(container.querySelector('[data-image-variant="hero"][data-fit="cover"]')).toBeInTheDocument();

    rerender(
      <StreamUI
        data={{ ...base, revision: 1, state: "completed", data_model: { cpu: 55 } }}
        partId="part_chk"
      />,
    );
    expect(await screen.findByText("当前 CPU：55%")).toBeInTheDocument();
    expect(screen.queryByText("CPU 负载超过 85% 警戒线")).toBeNull();
  });
});

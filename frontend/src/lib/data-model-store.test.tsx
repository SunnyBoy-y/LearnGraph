import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  DataModelStore,
  DataModelStoreContext,
  extractBindingPaths,
  readDataPath,
  useBoundText,
  useBoundValue,
} from "./data-model-store";
import { StreamUI } from "@/components/chat/stream-ui";

vi.mock("@/components/chat/message-part-renderer", () => ({
  TextWithCitations: ({ content }: { content: string }) => <p>{content}</p>,
}));

describe("DataModelStore", () => {
  it("resolves nested paths safely and rejects prototype keys", () => {
    const data = {
      metrics: { cpu: 42, memory: { used: 64, total: 128 } },
      items: [{ name: "Alpha" }],
    };
    expect(readDataPath(data, "@/metrics/cpu")).toBe(42);
    expect(readDataPath(data, "/metrics/memory/used")).toBe(64);
    expect(readDataPath(data, "@/items/0/name")).toBe("Alpha");
    expect(readDataPath(data, "@/__proto__/polluted")).toBeUndefined();
    expect(readDataPath(data, "@/constructor/prototype")).toBeUndefined();
    expect(extractBindingPaths("使用 @{/metrics/memory/used} / @{/metrics/memory/total} GB")).toEqual([
      "/metrics/memory/used",
      "/metrics/memory/total",
    ]);
  });

  it("notifies only subscribers whose bound path actually changed", () => {
    const store = new DataModelStore({ cpu: 40, mem: 60 });
    const cpuSpy = vi.fn();
    const memSpy = vi.fn();
    store.subscribe("/cpu", cpuSpy);
    store.subscribe("/mem", memSpy);

    store.setDataModel({ cpu: 85, mem: 60 });
    expect(cpuSpy).toHaveBeenCalledTimes(1);
    expect(memSpy).toHaveBeenCalledTimes(0);

    store.setDataModel({ cpu: 85, mem: 60 });
    expect(cpuSpy).toHaveBeenCalledTimes(1);
    expect(memSpy).toHaveBeenCalledTimes(0);
  });

  it("drives fine-grained React re-renders via useSyncExternalStore", () => {
    const store = new DataModelStore({ cpu: 15, mem: 50 });
    let cpuRenders = 0;
    let memRenders = 0;

    function CpuLeaf() {
      cpuRenders += 1;
      const val = useBoundValue("@/cpu");
      return <span data-testid="cpu">{String(val)}</span>;
    }

    function MemLeaf() {
      memRenders += 1;
      const text = useBoundText("内存 @{/mem}%");
      return <span data-testid="mem">{text}</span>;
    }

    render(
      <DataModelStoreContext.Provider value={store}>
        <CpuLeaf />
        <MemLeaf />
      </DataModelStoreContext.Provider>,
    );

    expect(screen.getByTestId("cpu")).toHaveTextContent("15");
    expect(screen.getByTestId("mem")).toHaveTextContent("内存 50%");
    expect(cpuRenders).toBe(1);
    expect(memRenders).toBe(1);

    act(() => {
      store.setDataModel({ cpu: 92, mem: 50 });
    });

    expect(screen.getByTestId("cpu")).toHaveTextContent("92");
    expect(cpuRenders).toBe(2);
    expect(memRenders).toBe(1);
  });

  it("renders StreamUI metric and document with data_model bindings and updates on op=data snapshot", async () => {
    const baseSnapshot = {
      protocol: "suip/0.1",
      block_id: "doc_dm",
      component: "document",
      revision: 0,
      state: "streaming",
      props: { title: "系统监控" },
      data_model: { cpu: 25, status: "正常" },
      blocks: [
        { id: "p1", type: "paragraph", text: "当前状态：@{/status}，CPU @{/cpu}%", state: "completed" },
        {
          id: "m1",
          type: "metric",
          state: "completed",
          visual: {
            props: { title: "核心指标", unit: "%" },
            items: [{ id: "cpu", label: "CPU 负载", value: "@/cpu" }],
          },
        },
      ],
    };

    const { rerender } = render(<StreamUI data={baseSnapshot} partId="part_dm" />);
    expect(await screen.findByText(/当前状态：正常，CPU 25%/)).toBeInTheDocument();
    expect(screen.getByText("25")).toBeInTheDocument();

    rerender(
      <StreamUI
        data={{
          ...baseSnapshot,
          revision: 1,
          state: "completed",
          data_model: { cpu: 78, status: "高负载" },
        }}
        partId="part_dm"
      />,
    );

    expect(await screen.findByText(/当前状态：高负载，CPU 78%/)).toBeInTheDocument();
    expect(screen.getByText("78")).toBeInTheDocument();
  });
});

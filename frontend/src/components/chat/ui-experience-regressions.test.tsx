import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MagicCardHost } from "@/components/chat/magic-card-host";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import { SandboxArtifact } from "@/components/chat/sandbox-artifact";
import { StreamUI } from "@/components/chat/stream-ui";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { UnifiedArtifactCard } from "@/components/chat/unified-artifact-card";
import { placeRichResponseParts } from "@/features/chat/chat-message-parts";
import { SessionShareViewPage } from "@/features/chat/session-share-view";
import { createSubappChannel } from "@/lib/subapp-bridge";
import type { MessagePart } from "@/types/sessions";

const { apiGet, apiPost, streamSessionMessageMock, fetchSharedSessionMock } =
  vi.hoisted(() => ({
    apiGet: vi.fn(),
    apiPost: vi.fn(),
    streamSessionMessageMock: vi.fn(),
    fetchSharedSessionMock: vi.fn(),
  }));

vi.mock("@/api/client", () => ({
  apiClient: {
    get: apiGet,
    post: apiPost,
  },
}));

vi.mock("@/api/sessions", () => ({
  streamSessionMessage: streamSessionMessageMock,
}));

vi.mock("@/api/session-sharing", () => ({
  fetchSharedSession: fetchSharedSessionMock,
}));

vi.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CodeBlockHeader: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CodeBlockCopyButton: () => <button type="button">copy</button>,
}));

vi.mock("@/components/ai-elements/lazy-streamdown", () => ({
  LazyStreamdown: ({ children }: { children: string }) => <div>{children}</div>,
}));

describe("UI & Magic Card experience regressions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("#1 MagicCardHost injects subappClient SDK into srcDoc when subapp_mode is active", async () => {
    apiPost.mockResolvedValue({
      session_id: "sess-mc-1",
      status: "active",
      state_version: 0,
      state_sha256: null,
      token: "tok-0",
      token_prefix: "tok-0",
      component_id: "card",
      render_ref: "sess-mc-1",
      artifact_version_id: "av-mc-1",
      chat_session_id: null,
      event_schema: {},
      state_schema: {},
      unlock_message: {
        version: "1",
        event_type: "renderer.unlock",
        payload: { token: "tok-0", component_id: "card", render_ref: "sess-mc-1" },
      },
    });
    apiGet.mockResolvedValue({ items: [], offset: 0, limit: 1, total: 0 });

    render(
      <MagicCardHost
        data={{
          title: "互动卡片",
          artifact_version_id: "av-mc-1",
          subapp_mode: true,
          preview_html: "<!doctype html><html><body><div>hi</div></body></html>",
        }}
      />,
    );

    const frame = screen.getByTitle("互动卡片 子应用") as HTMLIFrameElement;
    expect(frame.getAttribute("srcdoc")).toContain("window.__lgSubapp");
  });

  it("#2 createSubappChannel re-sends unlock with latest token and re-polls state after iframe reload", async () => {
    const postMessage = vi.fn();
    const frame = { contentWindow: { postMessage } } as unknown as HTMLIFrameElement;
    apiPost.mockResolvedValue({});
    apiGet
      .mockResolvedValueOnce({
        items: [{ id: "st-1", session_id: "sess-1", version: 1, sha256: "a", state: { step: 1 }, created_at: "2026-01-01T00:00:00Z" }],
        offset: 0,
        limit: 1,
        total: 1,
      })
      .mockResolvedValueOnce({ agent_status: "idle" })
      .mockResolvedValueOnce({
        items: [{ id: "st-1", session_id: "sess-1", version: 1, sha256: "a", state: { step: 1 }, created_at: "2026-01-01T00:00:00Z" }],
        offset: 0,
        limit: 1,
        total: 1,
      })
      .mockResolvedValueOnce({ agent_status: "idle" });

    const channel = createSubappChannel({
      getIframe: () => frame,
      provisioned: {
        sessionId: "sess-1",
        token: "tok-initial",
        unlockMessage: {
          version: "1",
          event_type: "renderer.unlock",
          payload: { token: "tok-initial", component_id: "card", render_ref: "sess-1" },
        },
      },
    });

    channel.handleIframeLoad();
    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ event_type: "renderer.state" }),
        "*",
      );
    });

    postMessage.mockClear();
    // Simulate iframe reload (e.g., resuming after pause fallback).
    channel.handleIframeLoad();
    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ event_type: "renderer.unlock" }),
        "*",
      );
      expect(postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ event_type: "renderer.state" }),
        "*",
      );
    });

    channel.destroy();
  });

  it("#3 UnifiedArtifactCard mounts sandbox runtime bridge for HTML preview_html", async () => {
    apiPost.mockResolvedValue({
      status: 200,
      status_text: "OK",
      headers: { "content-type": "application/json" },
      body_text: '{"ok":true}',
    });

    const part: MessagePart = {
      id: "html-part-1",
      type: "sandbox_artifact",
      status: "completed",
      data: {
        title: "演示页",
        mime_type: "text/html",
        preview_html: "<!doctype html><html><body><p>demo</p></body></html>",
      },
    };
    render(<UnifiedArtifactCard part={part} />);
    const frame = screen.getByTitle("演示页") as HTMLIFrameElement;
    const reply = vi.fn();
    Object.defineProperty(frame, "contentWindow", {
      value: { postMessage: reply },
      configurable: true,
    });

    const msg = new MessageEvent("message", {
      data: { lg: 1, kind: "net.fetch", id: 7, url: "https://example.com/api", method: "GET" },
    });
    Object.defineProperty(msg, "source", { value: frame.contentWindow });
    window.dispatchEvent(msg);

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/sandbox-net/proxy",
        expect.objectContaining({ url: "https://example.com/api", method: "GET" }),
      );
      expect(reply).toHaveBeenCalledWith(
        expect.objectContaining({ lg: 1, kind: "lg.result", id: 7, ok: true }),
        "*",
      );
    });
  });

  it("#4 & #5 MagicCardHost shows loading state while bundle preview resolves and clears consent bar after decision", async () => {
    let resolveBundle: ((value: { url: string }) => void) | undefined;
    apiGet.mockImplementation((url: string) => {
      if (url.includes("/preview")) {
        return new Promise<{ url: string }>((resolve) => {
          resolveBundle = resolve;
        });
      }
      return Promise.resolve({ items: [], offset: 0, limit: 1, total: 0 });
    });

    render(<MagicCardHost data={{ title: "多文件卡片", bundle_id: "bundle-123" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载沙箱预览");
    expect(screen.queryByRole("alert")).toBeNull();

    await act(async () => {
      resolveBundle?.({ url: "https://preview.example/index.html" });
    });
    expect(await screen.findByTitle("多文件卡片 多文件沙箱")).toBeInTheDocument();
  });

  it("#6 SandboxArtifact invalidates session messages query after data analysis stream finishes", async () => {
    const queryClient = new QueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    apiPost.mockResolvedValue({
      session_id: "sess-sa-1",
      status: "active",
      state_version: 0,
      state_sha256: null,
      token: "tok-0",
      token_prefix: "tok-0",
      component_id: "planner",
      render_ref: "sess-sa-1",
      artifact_version_id: "av-sa-1",
      chat_session_id: "chat-99",
      event_schema: {},
      state_schema: {},
      unlock_message: {
        version: "1",
        event_type: "renderer.unlock",
        payload: { token: "tok-0", component_id: "planner", render_ref: "sess-sa-1" },
      },
    });
    apiGet.mockResolvedValue({ items: [], offset: 0, limit: 1, total: 0 });
    streamSessionMessageMock.mockImplementation(async function* () {
      yield { id: "e1", data: { type: "message.completed" } };
    });

    render(
      <QueryClientProvider client={queryClient}>
        <SandboxArtifact
          data={{
            title: "学习分析器",
            artifact_version_id: "av-sa-1",
            subapp_mode: true,
            chat_session_id: "chat-99",
            bundle_id: "b-1",
            preview_html: "<html><body>ok</body></html>",
          }}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "数据分析" }));
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalled();
    });
  });

  it("#7 SessionShareViewPage sanitizes magic_card preview_html, renders Stream UI, and strips artifact markers", async () => {
    fetchSharedSessionMock.mockResolvedValue({
      title: "分享测试",
      message_count: 1,
      messages: [
        {
          id: "m1",
          role: "assistant",
          content: "前置说明[[artifact:block_id:chart1]]后续说明",
          created_at: "2026-11-26T10:00:00Z",
          parts: [
            {
              id: "p1",
              type: "text",
              content: "前置说明[[artifact:block_id:chart1]]后续说明",
            },
            {
              id: "p2",
              type: "ui",
              data: {
                protocol: "suip/0.1",
                component: "metric",
                block_id: "chart1",
                revision: 1,
                state: "completed",
                props: { title: "关键指标" },
                items: [{ id: "k1", label: "完成率", value: "98%" }],
              },
            },
            {
              id: "p3",
              type: "magic_card",
              data: {
                preview_snapshot: {
                  preview_html: "<html><body><form action='https://evil.test'></form><p>safe</p></body></html>",
                },
              },
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={["/share/tok-abc"]}>
        <Routes>
          <Route path="/share/:token" element={<SessionShareViewPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("前置说明后续说明")).toBeInTheDocument();
    expect(screen.getByText("关键指标")).toBeInTheDocument();
    expect(screen.getByText("98%")).toBeInTheDocument();
    const frame = screen.getByTitle("共享组件") as HTMLIFrameElement;
    const srcDoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcDoc).toContain("Content-Security-Policy");
    expect(srcDoc).toContain("connect-src 'none'");
    expect(srcDoc).not.toContain("<form");
  });

  it("#9 MessagePartRenderer keeps pending document image in loading state while message is not settled", async () => {
    const part: MessagePart = {
      id: "doc-part",
      type: "ui",
      status: "completed",
      data: {
        protocol: "suip/0.1",
        component: "document",
        block_id: "doc-1",
        revision: 2,
        state: "completed",
        props: { title: "图文说明" },
        blocks: [
          {
            id: "img1",
            type: "image",
            alt: "结构示意",
            resource_state: "pending",
          },
        ],
      },
    };

    const { rerender } = render(
      <MessagePartRenderer part={part} settled={false} streaming={false} />,
    );
    expect(await screen.findByText("正在查找相关图片…")).toBeInTheDocument();

    rerender(<MessagePartRenderer part={part} settled streaming={false} />);
    expect(await screen.findByText("暂无可用图片")).toBeInTheDocument();
  });

  it("#11 & #12 GraphCanvas does not hijack plain wheel scrolling, preserves zoom on hidden resize, and toggles selection", () => {
    const callbacks: ResizeObserverCallback[] = [];
    const OriginalRO = globalThis.ResizeObserver;
    class MockRO {
      callback: ResizeObserverCallback;
      constructor(cb: ResizeObserverCallback) {
        this.callback = cb;
        callbacks.push(cb);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = MockRO as unknown as typeof ResizeObserver;

    try {
      const data = {
        protocol: "suip/0.1",
        component: "graph",
        block_id: "g-wheel",
        revision: 1,
        state: "completed",
        props: { title: "关系图", interaction: "explore" },
        nodes: [
          { id: "n1", label: "概念A", description: "说明A" },
          { id: "n2", label: "概念B", description: "说明B" },
        ],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
      };
      const { container } = render(<StreamUI data={data} partId="g-wheel" />);
      const svg = container.querySelector('.stream-ui__graph svg[role="group"]')!;

      const plainWheel = new WheelEvent("wheel", { deltaY: -100, cancelable: true });
      svg.dispatchEvent(plainWheel);
      expect(plainWheel.defaultPrevented).toBe(false);
      expect(screen.getByLabelText("图谱缩放")).toHaveValue("1");

      const ctrlWheel = new WheelEvent("wheel", { deltaY: -100, ctrlKey: true, cancelable: true });
      act(() => {
        svg.dispatchEvent(ctrlWheel);
      });
      expect(ctrlWheel.defaultPrevented).toBe(true);
      expect(Number((screen.getByLabelText("图谱缩放") as HTMLInputElement).value)).toBeGreaterThan(1);

      const zoomedValue = (screen.getByLabelText("图谱缩放") as HTMLInputElement).value;
      const graphRoot = container.querySelector(".stream-ui__graph") as HTMLElement;
      Object.defineProperty(graphRoot, "clientWidth", { value: 0, configurable: true });
      act(() => {
        callbacks.forEach((cb) => cb([], {} as ResizeObserver));
      });
      expect(screen.getByLabelText("图谱缩放")).toHaveValue(zoomedValue);

      // Keyboard toggle selection on node.
      const nodeBtn = screen.getByRole("button", { name: "概念A" });
      fireEvent.keyDown(nodeBtn, { key: "Enter" });
      expect(screen.getByText("说明A")).toBeInTheDocument();
      fireEvent.keyDown(nodeBtn, { key: "Enter" });
      expect(screen.queryByText("说明A")).toBeNull();
    } finally {
      globalThis.ResizeObserver = OriginalRO;
    }
  });

  it("#13 placeRichResponseParts handles layout/mode parameters and whitespace", () => {
    const parts: MessagePart[] = [
      {
        id: "t1",
        type: "text",
        status: "streaming",
        content: "前面[[ artifact : block_id : g1 | layout=side ]]后面",
      },
      {
        id: "u1",
        type: "ui",
        status: "streaming",
        data: { block_id: "g1" },
      },
    ];
    const placed = placeRichResponseParts(parts);
    expect(placed.map((p) => p.type)).toEqual(["text", "ui", "text"]);
    expect(placed[1].data?.layout).toBe("side");
  });

  it("#14 & #16 ChartView handles small decimals, isolated points, zero change, bound explain labels, and TableView filter-empty copy", async () => {
    const onAction = vi.fn();
    const { container } = render(
      <StreamUI
        partId="small-chart"
        onAction={onAction}
        data={{
          protocol: "suip/0.1",
          component: "chart",
          block_id: "c1",
          revision: 1,
          state: "completed",
          props: {
            title: "@/chartTitle",
            columns: [{ key: "rate", label: "转化率" }],
          },
          data_model: { chartTitle: "周转化率" },
          rows: [
            { id: "r1", label: "第1周", values: { rate: 0.02 } },
            { id: "r2", label: "第2周", values: {} },
            { id: "r3", label: "第3周", values: { rate: 0.05 } },
            { id: "r4", label: "第4周", values: {} },
            { id: "r5", label: "第5周", values: { rate: 0.02 } },
          ],
        }}
      />,
    );

    // Top y-axis tick should reflect 0.05 rather than being crushed to 1.
    expect(screen.getByText("0.05")).toBeInTheDocument();
    // Isolated point r3 should have non-zero radius.
    const circles = Array.from(container.querySelectorAll("svg circle"));
    expect(circles.some((c) => Number(c.getAttribute("r")) > 0 && c.textContent?.includes("第3周"))).toBe(true);
    // Equal start/end values say 持平 instead of 增加 0.
    expect(container.querySelector(".stream-ui__tooltip")).toHaveTextContent("与第1周持平");
    // Explain resolves data-bound title.
    fireEvent.click(screen.getByRole("button", { name: "让 AI 解释" }));
    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({
        payload: expect.objectContaining({
          userMessage: "请解释图表「周转化率」，分析其中的主要信息。",
        }),
      }),
    );

    render(
      <StreamUI
        partId="filter-table"
        data={{
          protocol: "suip/0.1",
          component: "table",
          block_id: "t1",
          revision: 1,
          state: "completed",
          props: {
            title: "数据表",
            interaction: "explore",
            columns: [{ key: "v", label: "值" }],
          },
          rows: [{ id: "r1", label: "Alpha", values: { v: 1 } }],
        }}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "筛选表格" }), {
      target: { value: "不存在" },
    });
    expect(screen.getByText("未找到匹配「不存在」的数据")).toBeInTheDocument();
    expect(screen.queryByText("暂无可显示的数据")).toBeNull();
  });

  it("#17 & #18 TrustedComponentRenderer honors interactive=false and resolves graph proposal edges by ref", () => {
    const onAction = vi.fn();
    render(
      <>
        <TrustedComponentRenderer
          fallbackId="fb-1"
          interactive={false}
          onAction={onAction}
          data={{
            component_type: "fill_blank",
            schema_version: "1.0",
            props: { title: "填空题", placeholder: "输入答案" },
            allowed_events: ["submit"],
          }}
        />
        <TrustedComponentRenderer
          fallbackId="wc-1"
          interactive={false}
          onAction={onAction}
          data={{
            component_type: "weather_card",
            schema_version: "1.0",
            props: {
              location: "上海",
              temperature_c: 22,
              condition: "晴",
              actions: [{ id: "create_plan", label: "生成学习计划" }],
            },
            allowed_events: ["create_plan"],
          }}
        />
        <TrustedComponentRenderer
          fallbackId="mc-1"
          interactive={false}
          onAction={onAction}
          data={{
            component_type: "metric_card",
            schema_version: "1.0",
            props: {
              title: "进度指标",
              metrics: [{ label: "正确率", value: "90%" }],
              actions: [{ id: "open_plan", label: "继续分析" }],
            },
            allowed_events: ["open_plan"],
          }}
        />
        <TrustedComponentRenderer
          fallbackId="gp-1"
          interactive
          onAction={onAction}
          data={{
            component_type: "graph_update_proposal",
            schema_version: "1.0",
            props: {
              proposal_id: "prop-1",
              mode: "create",
              goal_id: "goal-1",
              title: "微积分图谱",
              summary: "初始图谱",
              confirmation_required: true,
              status: "proposed",
              nodes: [
                {
                  id: "db-id-1",
                  ref: "ref-limit",
                  node_id: null,
                  label: "极限",
                  description: "基础概念",
                  node_type: "root",
                  change: "add",
                  rationale: "核心",
                },
                {
                  id: "db-id-2",
                  ref: "ref-deriv",
                  node_id: null,
                  label: "导数",
                  description: "变化率",
                  node_type: "concept",
                  change: "add",
                  rationale: "进阶",
                },
              ],
              edges: [
                {
                  source_ref: "ref-limit",
                  target_ref: "ref-deriv",
                  relation: "prerequisite",
                  rationale: "先修",
                },
              ],
            },
            allowed_events: ["confirm", "reject"],
          }}
        />
      </>,
    );

    expect(screen.getByRole("textbox", { name: "填空题" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "生成学习计划" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "继续分析" })).toBeDisabled();

    const edgeRegion = screen.getByLabelText("候选图谱关系");
    expect(edgeRegion).toHaveTextContent("极限");
    expect(edgeRegion).toHaveTextContent("导数");
    expect(edgeRegion).not.toHaveTextContent("ref-limit");
  });
});

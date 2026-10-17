import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { Toaster, toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { McpRegistryDetail, McpRegistrySearchResult } from "@/types/extensions";
import "@/index.css";

type Fixture = {
  lists: Record<string, McpRegistrySearchResult[]>;
  details: Record<string, McpRegistryDetail>;
  errors: Record<string, string>;
  filesystem?: McpRegistrySearchResult;
};
const originalFetch = window.fetch.bind(window);
const fixture: Fixture = await originalFetch("/src/dev/mcp-market-fixture.json").then((response) => response.json());
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.href);
  const catalog = url.searchParams.get("catalog") || "official";
  // Only the public catalog is snapshotted. Translation calls the actual authenticated backend.
  if (url.pathname.endsWith("/mcp/registry/translate")) {
    const response = await originalFetch(input, init);
    if (response.status === 401) return Response.json({ error: { code: "preview_translation_login", message: "请先登录 LearnGraph 工作区，再使用真实模型翻译。" } }, { status: 401 });
    return response;
  }
  if (url.pathname.endsWith("/mcp/registry/browse")) {
    const q = url.searchParams.get("q")?.toLowerCase() || "";
    if (q) {
      const all = [...fixture.lists[catalog].flatMap((page) => page.items), ...(catalog === "official" ? fixture.filesystem?.items || [] : [])];
      const items = all.filter((item) => `${item.name} ${item.title} ${item.description}`.toLowerCase().includes(q));
      return Response.json({ ...fixture.lists[catalog][0], items, query: q, next_cursor: null });
    }
    const second = url.searchParams.has("cursor");
    const page = fixture.lists[catalog][second ? 1 : 0];
    return Response.json({ ...page, next_cursor: second ? null : page.next_cursor });
  }
  if (url.pathname.endsWith("/mcp/registry/detail")) {
    const key = `${catalog}:${url.searchParams.get("name")}`;
    return fixture.details[key] ? Response.json(fixture.details[key]) : Response.json({ error: { code: "preview_detail_missing", message: fixture.errors[key] || "该条目的详情不在本次预览快照中" } }, { status: 404 });
  }
  return originalFetch(input, init);
};
const { McpMarketPanel } = await import("@/features/settings/mcp-market-panel");
const { InstallMcpDialog } = await import("@/features/settings/extension-pages");
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const beforeClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
function Demo() {
  const [before, setBefore] = useState(false);
  return <main className="mx-auto max-w-5xl px-4 py-8">
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-semibold">MCP 详情 · 模型翻译</h1><p className="mt-2 text-xs text-muted-foreground">{before ? "修改前：原文详情（按原界面重建）" : "修改后：实际组件 · 真实模型翻译 · 默认中文"}</p></div><div className="flex flex-wrap gap-2"><Button variant={before ? "default" : "outline"} onClick={() => setBefore(true)}>修改前</Button><Button variant={!before ? "default" : "outline"} onClick={() => setBefore(false)}>修改后</Button><Button variant="outline" onClick={() => document.documentElement.classList.toggle("dark")}>切换主题</Button></div></div>
    <section className="rounded-2xl border bg-background p-5 shadow-sm sm:p-6">
      {before ? <QueryClientProvider client={beforeClient}><div data-mcp-market-before><style>{"body:has([data-mcp-market-before]) [data-mcp-market-translation] { display: none; }"}</style><McpMarketPanel /></div></QueryClientProvider> : <McpMarketPanel onSelect={() => toast.info("详情与预填交互预览；正式注册请打开下方实际注册弹窗。")} />}

    </section>
    <div className="mt-5 flex flex-wrap items-center gap-4"><InstallMcpDialog busy={false} onInstall={() => toast.info("这是预览，未向工作区注册服务器。")} /><span className="text-xs text-muted-foreground">上方展示实际市场组件；此按钮可体验完整注册弹窗。</span></div>
    <p className="mt-5 text-xs leading-6 text-muted-foreground">列表与详情使用两个市场的真实 API 快照，翻译请求真实工作区模型。点击“查看详情”后可选择模型和目标语言；原始 JSON、连接地址和参数保留原文。预览不会注册服务器或连接 MCP 端点。</p>
    <Toaster />
  </main>;
}
createRoot(document.getElementById("root")!).render(<MemoryRouter><QueryClientProvider client={client}><Demo /></QueryClientProvider></MemoryRouter>);

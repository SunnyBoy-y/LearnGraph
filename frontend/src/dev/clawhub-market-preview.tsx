import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { Button } from "@/components/ui/button";
import fixture from "./clawhub-market-fixture.json";
import type { SkillMarketDetail } from "@/types/extensions";
import "@/index.css";

// Catalog/details use public API snapshots. Translation uses the real application API.
// The preview never installs packages, and never simulates model translations.
const originalFetch = window.fetch.bind(window);
const details: { details: Record<string, SkillMarketDetail>; errors: Record<string, string> } = await originalFetch("/src/dev/clawhub-market-details-fixture.json").then((response) => response.json());
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.href);
  if (url.pathname.endsWith("/skills/market/translate")) {
    const response = await originalFetch(input, init);
    if (response.status === 401) return Response.json({ error: { code: "preview_translation_login", message: "请先登录 LearnGraph 工作区，再使用真实模型翻译。" } }, { status: 401 });
    if (response.status === 404 && (await response.clone().json()).detail === "Not Found") return Response.json({ error: { code: "preview_translation_configuration", message: "当前后端尚未提供市场翻译接口，请启动翻译预览服务或更新 LearnGraph 后端。" } }, { status: 503 });
    return response;
  }
  if (url.pathname.endsWith("/skills/market/install")) return Response.json({ error: { code: "preview_only", message: "这是交互预览；安装不会写入工作区。" } }, { status: 409 });
  if (url.pathname.endsWith("/skills/market/detail")) {
    const id = url.searchParams.get("market_id") || "";
    return details.details[id] ? Response.json(details.details[id]) : Response.json({ error: { code: "preview_detail_missing", message: details.errors[id] || "该条目的详情不在本次预览快照中；可查看前两页热门条目。" } }, { status: 404 });
  }
  if (url.pathname.endsWith("/skills/market")) {
    const q = url.searchParams.get("q")?.toLowerCase() ?? "";
    if (q) {
      const all = [...fixture.first.cards, ...fixture.second.cards, ...fixture.search.cards];
      const cards = all.filter((card, index) => all.findIndex((item) => item.market_id === card.market_id) === index && `${card.name} ${card.slug} ${card.description}`.toLowerCase().includes(q));
      const page = Number(url.searchParams.get("page") ?? 1);
      return Response.json({ ...fixture.search, query: q, cards: cards.slice((page - 1) * 12, page * 12), total: cards.length, total_pages: Math.ceil(cards.length / 12), page });
    }
    return Response.json(url.searchParams.has("cursor") ? { ...fixture.second, next_cursor: null } : fixture.first);
  }
  return originalFetch(input, init);
};
const { MarketPanel } = await import("@/features/settings/skills-hub-extras");
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const beforeClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
function Demo() {
  const [before, setBefore] = useState(false);
  return <main className="mx-auto max-w-6xl px-4 py-8">
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="text-xl font-semibold">Skill 详情 · 模型翻译</h1><p className="mt-2 text-xs text-muted-foreground">{before ? "修改前：原文详情（按原界面重建）" : "修改后：实际组件 · 默认翻译为中文，可选择其他语言"} · 相同宽度预览</p></div>
      <div className="flex gap-2"><Button variant={before ? "default" : "outline"} onClick={() => setBefore(true)}>修改前</Button><Button variant={!before ? "default" : "outline"} onClick={() => setBefore(false)}>修改后</Button><Button variant="outline" onClick={() => document.documentElement.classList.toggle("dark")}>切换主题</Button></div>
    </div>
    <section className="rounded-2xl border bg-background p-5 shadow-sm sm:p-6">
      <h2 className="text-lg font-semibold">添加 Skill</h2><p className="mt-1 text-sm text-muted-foreground">市场、URL、npx 命令、压缩包、本机与手动编辑统一入口。安装后需授权才会注入 Agent。</p>
      <div className="mt-4 mb-7 flex flex-wrap gap-1 rounded-lg bg-muted p-1 text-sm w-fit">{["市场", "URL 导入", "npx 命令", "压缩包", "本机导入", "文件编辑"].map((label, i) => <span key={label} className={`rounded-md px-3 py-1 ${!i ? "bg-background shadow-xs" : "text-muted-foreground"}`}>{label}</span>)}</div>
      {before ? <QueryClientProvider client={beforeClient}><div data-skill-market-before><style>{"body:has([data-skill-market-before]) [data-skill-market-translation] { display: none; }"}</style><MarketPanel /></div></QueryClientProvider> : <MarketPanel />}
    </section>
    <p className="mt-5 text-xs leading-6 text-muted-foreground">预览数据采集于 {new Date(fixture.first.refreshed_at!).toLocaleString("zh-CN")}。列表和详情使用真实接口快照；点击安装仅展示提示。模型翻译请求真实 LearnGraph 后端，需要登录并配置文本模型；此预览不提供模拟译文。</p>
    <Toaster />
  </main>;
}
createRoot(document.getElementById("root")!).render(<QueryClientProvider client={client}><Demo /></QueryClientProvider>);

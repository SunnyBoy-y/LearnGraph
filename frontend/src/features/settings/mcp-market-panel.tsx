import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ExternalLink, Network, RefreshCcw, Search } from "lucide-react";
import { browseMcpRegistry, getMcpRegistryDetail, translateMcpMarket } from "@/api/extensions";
import { MarketTranslationControls, useMarketTranslation } from "./market-translation";
import { ErrorState, LoadingState } from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { McpRegistryDetail, McpRegistrySearchItem } from "@/types/extensions";

type Catalog = "official" | "smithery";
const labels = { official: "官方 MCP Registry", smithery: "Smithery" };
function safeUrl(url?: string | null) {
  try { const parsed = new URL(url ?? ""); return ["https:", "http:"].includes(parsed.protocol) && !parsed.username ? parsed.href : null; }
  catch { return null; }
}
function SourceLink({ url, children }: { url?: string | null; children: React.ReactNode }) {
  const href = safeUrl(url);
  return href ? <a className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" href={href} target="_blank" rel="noreferrer">{children}<ExternalLink className="size-3 shrink-0" /></a> : null;
}

export function McpMarketPanel({ onSelect }: { onSelect?: (detail: McpRegistryDetail) => void }) {
  const [catalog, setCatalog] = useState<Catalog>("official");
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const [selected, setSelected] = useState<McpRegistrySearchItem | null>(null);
  const market = useQuery({
    queryKey: ["mcp-market", catalog, query, cursors[page]],
    queryFn: () => browseMcpRegistry({ catalog, q: query, cursor: cursors[page], limit: 8 }),
    staleTime: 60_000,
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["mcp-market-detail", selected?.catalog ?? catalog, selected?.name, selected?.version],
    queryFn: () => getMcpRegistryDetail(selected!.name, selected?.catalog ?? catalog, selected?.version || "latest"),
    enabled: Boolean(selected),
    staleTime: 60_000,
    retry: false,
  });
  const search = (event: FormEvent) => {
    event.preventDefault();
    if (input.trim() === query && page === 0) void market.refetch();
    setQuery(input.trim()); setPage(0); setCursors([undefined]);
  };
  const current = detail.data;
  const translation = useMarketTranslation({
    queryKey: ["mcp-market-translation", selected?.catalog ?? catalog, current?.item.name, current?.item.version, current?.translation_source_hash],
    request: async (selection) => {
      const result = await translateMcpMarket({ name: current!.item.name, catalog: selected?.catalog ?? catalog,
        version: current!.item.version || "latest", source_hash: current!.translation_source_hash || undefined, ...selection });
      if (result.name !== current!.item.name || result.catalog !== (selected?.catalog ?? catalog) || result.version !== current!.item.version ||
          (current!.translation_source_hash && result.source_hash !== current!.translation_source_hash) ||
          result.configuration_descriptions.length !== current!.configuration.length || result.tool_descriptions.length !== current!.tools.length) throw new Error("目录说明已更新，请重新打开详情后翻译。");
      return result;
    },
  });
  const translated = translation.translated;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="text-base font-semibold">MCP 市场</h2><p className="mt-1 text-sm text-muted-foreground">实时浏览目录，查看用途、连接方式与配置要求。</p></div>
      <Button variant="outline" size="sm" disabled={market.isFetching} onClick={() => void market.refetch()}><RefreshCcw className={market.isFetching ? "size-4 animate-spin" : "size-4"} />刷新列表</Button>
    </div>
    <div className="flex flex-wrap gap-2" aria-label="MCP 市场来源">
      {(["official", "smithery"] as const).map((source) => <Button key={source} size="sm" variant={source === catalog ? "default" : "outline"} aria-pressed={source === catalog} onClick={() => { setCatalog(source); setPage(0); setCursors([undefined]); setSelected(null); }}>{labels[source]}</Button>)}
    </div>
    <form className="flex gap-2" onSubmit={search}>
      <div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索 MCP 市场" className="pl-9" value={input} onChange={(event) => setInput(event.target.value)} placeholder={catalog === "official" ? "按名称搜索，例如 github / postgres / browser" : "搜索名称或用途，例如 web search"} /></div>
      <Button type="submit" variant="outline" disabled={market.isFetching}>搜索</Button>
    </form>
    <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground"><SourceLink url={catalog === "official" ? "https://registry.modelcontextprotocol.io" : "https://smithery.ai"}>来源 {labels[catalog]}</SourceLink><span>{query ? `「${query}」 · ` : ""}第 {page + 1} 页</span></div>
    {market.isPending ? <LoadingState /> : market.isError ? <ErrorState message={market.error.message} /> : market.data.items.length ? <div className="grid gap-3 sm:grid-cols-2">
      {market.data.items.map((item) => <article className="flex min-w-0 flex-col rounded-xl border p-4" key={item.name}>
        <div className="flex gap-2"><Network className="mt-0.5 size-4 shrink-0 text-primary" /><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold" title={item.title || item.name}>{item.title || item.name.split("/").pop()}</h3><p className="mt-1 truncate font-mono text-[10px] text-muted-foreground" title={item.name}>{item.name}</p></div>{item.version && <Badge variant="outline">v{item.version}</Badge>}</div>
        <p className="mt-3 line-clamp-3 text-xs leading-5 text-muted-foreground">{item.description || "目录未提供简介"}</p>
        <div className="mt-3 flex flex-wrap gap-1"><Badge variant="secondary">{item.supported ? "Streamable HTTP" : item.catalog === "smithery" ? "查看连接方式" : item.packages.length ? "本地安装包" : item.transport || "连接信息待补充"}</Badge>{item.env_hints.length > 0 && <Badge variant="outline">需配置</Badge>}{item.status && <Badge variant="outline">{item.status}</Badge>}</div>
        <div className="mt-auto flex items-center gap-3 pt-4"><Button size="xs" variant="outline" onClick={() => setSelected(item)}>查看详情</Button><SourceLink url={item.catalog_url}>来源页</SourceLink></div>
      </article>)}
    </div> : <p className="py-10 text-center text-sm text-muted-foreground">没有匹配的 MCP，试试其他关键词或切换市场。</p>}
    <div className="flex items-center justify-between gap-2"><Button size="sm" variant="outline" disabled={page === 0 || market.isFetching} onClick={() => setPage((value) => value - 1)}><ChevronLeft className="size-4" />上一页</Button><span className="text-xs text-muted-foreground">第 {page + 1} 页</span><Button size="sm" variant="outline" disabled={!market.data?.next_cursor || market.isFetching || market.isError} onClick={() => { setCursors((previous) => [...previous.slice(0, page + 1), market.data!.next_cursor!]); setPage((value) => value + 1); }}>下一页<ChevronRight className="size-4" /></Button></div>
    <p className="text-xs text-muted-foreground">也可浏览 <SourceLink url="https://glama.ai/mcp/servers">Glama 目录</SourceLink>。注册后需刷新能力并授权，才可调用。</p>
    <Dialog open={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}>
      <DialogContent className="max-h-[88dvh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader><DialogTitle>{current?.item.title || selected?.title || selected?.name || "MCP 详情"}</DialogTitle><DialogDescription className="break-all">{selected?.name} · {labels[selected?.catalog ?? catalog]}</DialogDescription></DialogHeader>
        {detail.isPending ? <LoadingState /> : detail.isError ? <div className="space-y-3"><ErrorState message={detail.error.message} /><Button variant="outline" size="sm" onClick={() => void detail.refetch()}>重试详情</Button></div> : current && <div className="space-y-5 text-sm">
          <div data-mcp-market-translation><MarketTranslationControls controller={translation} description="使用所选模型翻译简介、配置说明与工具说明；点击后调用模型。" /></div>
          <p className="whitespace-pre-wrap break-words leading-6">{(translated?.description ?? current.item.description) || "目录未提供简介"}</p>
          <div className="flex flex-wrap gap-2">{current.item.version && <Badge variant="outline">v{current.item.version}</Badge>}{current.item.status && <Badge variant="outline">{current.item.status}</Badge>}<SourceLink url={current.item.catalog_url}>市场来源页</SourceLink><SourceLink url={current.item.repository_url}>源代码</SourceLink><SourceLink url={current.item.website_url}>项目网站</SourceLink></div>
          {(current.published_at || current.updated_at) && <p className="text-xs text-muted-foreground">{current.published_at && `发布于 ${new Date(current.published_at).toLocaleString()}`}{current.updated_at && ` · 更新于 ${new Date(current.updated_at).toLocaleString()}`}</p>}
          <section className="space-y-2"><h3 className="font-semibold">连接方式</h3>{current.remotes.length ? current.remotes.map((remote, index) => <div className="rounded-lg border p-3" key={index}><Badge variant="secondary">{String(remote.type || "remote")}</Badge><p className="mt-2 break-all font-mono text-xs">{String(remote.url || remote.deploymentUrl || "未提供 URL")}</p></div>) : <p className="text-xs text-muted-foreground">目录未声明远程连接。</p>}</section>
          {current.package_details.length > 0 && <section className="space-y-2"><h3 className="font-semibold">安装包</h3>{current.package_details.map((pkg, index) => <div className="rounded-lg border p-3" key={index}><p className="break-all font-mono text-xs">{String(pkg.registryType || "package")} · {String(pkg.identifier || "")}</p><p className="mt-1 text-xs text-muted-foreground">{pkg.version ? `版本 ${String(pkg.version)}` : "未声明版本"}{pkg.transport ? ` · ${String((pkg.transport as { type?: string }).type || pkg.transport)}` : ""}</p></div>)}</section>}
          <section className="space-y-2"><h3 className="font-semibold">配置要求</h3>{current.configuration.length ? current.configuration.map((field, index) => <div className="rounded-lg border p-3" key={index}><div className="flex flex-wrap gap-2"><code className="break-all text-xs">{field.name}</code><Badge variant="outline">{field.isRequired ? "必填" : "可选"}</Badge>{field.isSecret && <Badge variant="outline">密钥</Badge>}<span className="text-xs text-muted-foreground">{field.location}</span></div>{field.description && <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">{translated?.configuration_descriptions[index] ?? field.description}</p>}</div>) : <p className="text-xs text-muted-foreground">目录未声明额外配置，连接时仍可能需要认证。</p>}</section>
          <section className="space-y-2"><h3 className="font-semibold">目录声明的工具 {current.tools.length > 0 ? `(${current.tools.length})` : ""}</h3>{current.tools.length ? current.tools.map((tool, index) => <div className="rounded-lg border p-3" key={tool.name}><code className="break-all text-xs font-semibold">{tool.name}</code><p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">{(translated?.tool_descriptions[index] ?? tool.description) || "未提供说明"}</p>{tool.inputSchema && <details className="mt-2 text-xs"><summary className="cursor-pointer text-muted-foreground">输入参数</summary><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-3">{JSON.stringify(tool.inputSchema, null, 2)}</pre></details>}</div>) : <p className="text-xs text-muted-foreground">此目录未提供工具清单。注册并刷新能力后可查看实际工具。</p>}</section>
          <details className="rounded-lg border p-3"><summary className="cursor-pointer text-xs text-muted-foreground">查看来源原始 JSON</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(current.raw, null, 2)}</pre></details>
          {!current.item.supported && <p className="text-xs text-amber-700 dark:text-amber-300">{current.item.unsupported_reason}</p>}
          <p className="text-xs text-muted-foreground">以上信息来自目录声明，实际工具与权限以连接后的能力快照为准。</p>
          {onSelect && <Button disabled={!current.item.supported} onClick={() => { onSelect(current); setSelected(null); }}>填入注册表</Button>}
        </div>}
      </DialogContent>
    </Dialog>
  </div>;
}

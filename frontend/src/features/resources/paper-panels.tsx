import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Download, Link2, LoaderCircle, Search } from "lucide-react";
import { getPaperBibtex, readPaper, savePaper, searchPapers } from "@/api/papers";
import { SourceAssociationDialog } from "@/components/resources/source-association-dialog";
import { useCopyFeedback } from "@/components/shared/copy-feedback";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError } from "@/api/client";
import { saveBlobViaNative } from "@/lib/native-download";
import { toast } from "@/lib/notifications";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { Paper, PaperExcerpt, PaperProvider, PaperReadResponse, PaperSearchRequest } from "@/types/papers";
import type { SourceRecord } from "@/types/workflow";

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "论文服务暂时不可用";
}

function paperHost(paper: Paper) {
  try { return paper.pdf_url ? new URL(paper.pdf_url).hostname : ""; } catch { return ""; }
}

function domainChallenge(error: unknown): string | null {
  if (!(error instanceof ApiError) || !["paper_redirect_authorization_required", "paper_domain_authorization_required"].includes(error.code)) return null;
  const details = error.details as { hostname?: unknown } | undefined;
  return typeof details?.hostname === "string" ? details.hostname : null;
}

export function PaperExcerptView({ excerpt, url, title, hash }: { excerpt: PaperExcerpt; url: string; title: string; hash: string }) {
  const destination = `${url}#page=${excerpt.locator.split(":")[1]}`;
  const { copy, copied } = useCopyFeedback(`${hash}:${excerpt.locator}`);
  return <section className="space-y-2 border-t-[0.5px] py-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <a className="text-sm underline underline-offset-4" href={destination} rel="noreferrer" target="_blank">{excerpt.locator.replace("page:", "第 ")} 页</a>
      <Button type="button" size="sm" variant="ghost" onClick={() => void copy(`“${excerpt.content}”\n${title}\n${excerpt.locator}\n${destination}\nSHA-256: ${hash}`)}>{copied ? "已复制" : "复制原文与引用"}</Button>
    </div>
    <p className="whitespace-pre-wrap break-words text-sm leading-6">{excerpt.content}</p>
  </section>;
}

function PaperResultCard({ paper, workspaceId }: { paper: Paper; workspaceId: string }) {
  const queryClient = useQueryClient();
  const [reading, setReading] = useState<PaperReadResponse | null>(null);
  const [saved, setSaved] = useState<SourceRecord | null>(null);
  const [associationOpen, setAssociationOpen] = useState(false);
  const [extraDomains, setExtraDomains] = useState<string[]>([]);
  const [operation, setOperation] = useState<"read" | "save">("read");
  const host = paperHost(paper);
  const reference = { provider: paper.provider, paper_id: paper.paper_id };
  const domains = Array.from(new Set([host, ...extraDomains].filter(Boolean)));
  const read = useMutation({
    mutationFn: (approvedDomains: string[]) => readPaper({ ...reference, authorized_domains: approvedDomains }),
    onSuccess: setReading,
  });
  const save = useMutation({
    mutationFn: (approvedDomains: string[]) => savePaper({ ...reference, include_full_text: Boolean(reading), authorized_domains: approvedDomains }),
    onSuccess: async (record) => {
      setSaved(record);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "paper-library") }),
        queryClient.invalidateQueries({ queryKey: ["source-records"] }),
      ]);
      toast.success("论文已收藏到资料库");
    },
  });
  const activeError = operation === "read" ? read.error : save.error;
  const challenge = domainChallenge(activeError);
  const busy = read.isPending || save.isPending;
  return <article className="space-y-3 border-t-[0.5px] py-5" aria-label={paper.title}>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span>{paper.provider === "arxiv" ? "arXiv" : "OpenAlex"}</span>
      {paper.year ? <span>{paper.year} 年</span> : null}
      {paper.publication_type === "preprint" ? <span>预印本</span> : null}
      <span>{reading ? "已读正文片段" : paper.abstract ? "仅摘要" : "仅元数据"}</span>
    </div>
    <h3 className="text-balance text-base font-medium"><a href={paper.url} rel="noreferrer" target="_blank" className="hover:underline">{paper.title}</a></h3>
    <p className="text-sm text-muted-foreground">{paper.authors.join(", ") || "作者信息未提供"}{paper.venue ? ` — ${paper.venue}` : ""}</p>
    {paper.doi ? <p className="break-all text-xs text-muted-foreground">DOI: {paper.doi}</p> : null}
    {paper.abstract ? <details className="text-sm"><summary className="cursor-pointer">摘要</summary><p className="mt-2 whitespace-pre-wrap leading-6">{paper.abstract}</p></details> : null}
    {host ? <p className="break-all text-xs text-muted-foreground">公开原文域名：{host}</p> : null}
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" variant="outline" disabled={busy || !paper.open_access || !paper.pdf_url} onClick={() => { setOperation("read"); save.reset(); read.mutate(domains); }}>
        {read.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <BookOpen className="size-4" />}
        {read.isPending ? "读取中…" : "读取公开正文"}
      </Button>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => { setOperation("save"); read.reset(); save.mutate(domains); }}>
        {save.isPending ? "收藏中…" : reading ? "读取并收藏正文片段" : "收藏论文"}
      </Button>
      {saved ? <>
        <Button type="button" size="sm" variant="ghost" onClick={() => setAssociationOpen(true)}><Link2 className="size-4" />关联知识节点</Button>
        <PaperBibtexButton sourceId={saved.id} />
      </> : null}
    </div>
    {activeError ? <p role="alert" className="text-sm text-destructive">{errorText(activeError)}</p> : null}
    {challenge ? <div className="flex flex-wrap items-center gap-2 text-sm">
      <span>原文需要访问：{challenge}</span>
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => {
        const next = Array.from(new Set([...domains, challenge]));
        setExtraDomains(next);
        if (operation === "save") save.mutate(next); else read.mutate(next);
      }}>授权此域名并重试</Button>
    </div> : null}
    {reading ? <details open className="space-y-2">
      <summary className="cursor-pointer text-sm font-medium">可定位引用的正文片段{reading.truncated ? "（内容已截断）" : ""}</summary>
      {reading.excerpts.map((excerpt) => <PaperExcerptView key={excerpt.locator} excerpt={excerpt} url={reading.url} title={paper.title} hash={reading.content_hash} />)}
    </details> : null}
    {saved && associationOpen ? <SourceAssociationDialog open source={saved} onOpenChange={setAssociationOpen} /> : null}
  </article>;
}

export function PaperSearchPanel({ workspaceId }: { workspaceId: string }) {
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState<PaperProvider>("openalex");
  const [author, setAuthor] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [openAccess, setOpenAccess] = useState(false);
  const [sort, setSort] = useState<"relevance" | "newest">("relevance");
  const [validation, setValidation] = useState("");
  const search = useMutation({ mutationFn: (request: PaperSearchRequest) => searchPapers(request) });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim().length < 2) { setValidation("请输入至少两个字符的论文关键词"); return; }
    const from = yearFrom ? Number(yearFrom) : undefined;
    const to = yearTo ? Number(yearTo) : undefined;
    if ((from !== undefined && (!Number.isInteger(from) || from < 1500 || from > 2200)) ||
        (to !== undefined && (!Number.isInteger(to) || to < 1500 || to > 2200)) || (from && to && from > to)) {
      setValidation("请填写有效年份，起始年份不能晚于结束年份"); return;
    }
    setValidation("");
    search.mutate({ query: query.trim(), provider, author: author.trim(), year_from: from, year_to: to,
                    open_access_only: openAccess, sort, max_results: 8 });
  }
  return <section className="space-y-4 border-[0.5px] p-5" aria-label="论文专用搜索">
    <h2 className="text-base font-medium">论文专用搜索</h2>
    <form onSubmit={submit} className="space-y-3" onKeyDown={(event) => {
      if (event.key === "Enter" && (event.nativeEvent.isComposing || event.keyCode === 229)) event.preventDefault();
    }}>
      <div className="flex flex-col gap-3 sm:flex-row">
        <Input aria-label="论文关键词" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="论文标题或研究主题" className="min-w-0 flex-1" maxLength={500} />
        <Select value={provider} onValueChange={(v) => setProvider(v as PaperProvider)}>
          <SelectTrigger aria-label="论文来源" className="sm:w-auto"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="openalex">OpenAlex</SelectItem><SelectItem value="arxiv">arXiv 预印本</SelectItem></SelectContent>
        </Select>
        <Button type="submit" disabled={search.isPending}>{search.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}{search.isPending ? "检索中…" : "搜索论文"}</Button>
      </div>
      <details>
        <summary className="cursor-pointer text-sm text-muted-foreground">作者、年份与开放获取筛选</summary>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div className="space-y-1"><Label htmlFor="paper-author">作者</Label><Input id="paper-author" value={author} onChange={(e) => setAuthor(e.target.value)} maxLength={200} /></div>
          <div className="space-y-1"><Label htmlFor="paper-year-from">起始年份</Label><Input id="paper-year-from" type="number" min={1500} max={2200} value={yearFrom} onChange={(e) => setYearFrom(e.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="paper-year-to">结束年份</Label><Input id="paper-year-to" type="number" min={1500} max={2200} value={yearTo} onChange={(e) => setYearTo(e.target.value)} /></div>
          <Select value={sort} onValueChange={(v) => setSort(v as "relevance" | "newest")}>
            <SelectTrigger aria-label="论文排序" className="w-auto"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="relevance">相关性优先</SelectItem><SelectItem value="newest">最新优先</SelectItem></SelectContent>
          </Select>
          <label className="flex items-center gap-2 py-2 text-sm"><input type="checkbox" checked={openAccess} onChange={(e) => setOpenAccess(e.target.checked)} />仅开放获取</label>
        </div>
      </details>
    </form>
    {validation ? <p role="alert" className="text-sm text-destructive">{validation}</p> : null}
    {search.error ? <p role="alert" className="text-sm text-destructive">{errorText(search.error)}</p> : null}
    {search.data ? <div aria-live="polite">
      <p className="text-xs text-muted-foreground">“{search.data.query}”找到 {search.data.results.length} 条结果。{search.data.notice}</p>
      {!search.data.results.length ? <p className="py-3 text-sm text-muted-foreground">没有找到匹配的论文，可调整关键词或明确选择另一来源。</p> : null}
      {search.data.results.map((paper) => <PaperResultCard key={`${paper.provider}:${paper.paper_id}`} paper={paper} workspaceId={workspaceId} />)}
    </div> : null}
  </section>;
}

export function PaperBibtexButton({ sourceId }: { sourceId: string }) {
  const download = useMutation({
    mutationFn: async () => {
      const text = await getPaperBibtex(sourceId);
      const blob = new Blob([text], { type: "application/x-bibtex;charset=utf-8" });
      const filename = `paper-${sourceId}.bib`;
      if (await saveBlobViaNative(blob, filename)) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    onError: (error) => toast.error(errorText(error)),
  });
  return <Button type="button" variant="ghost" size="sm" disabled={download.isPending} onClick={() => download.mutate()}><Download className="size-4" />导出 BibTeX</Button>;
}

export function PaperSavedBody({ source }: { source: SourceRecord }) {
  const raw = source.metadata_json.excerpts;
  const excerpts = Array.isArray(raw) ? raw.filter((value): value is PaperExcerpt =>
    typeof value === "object" && value !== null && typeof value.locator === "string" &&
    /^page:\d+$/.test(value.locator) && typeof value.content === "string") : [];
  const hash = source.metadata_json.document_sha256;
  if (source.metadata_json.page_read === true && excerpts.length && typeof hash === "string") {
    return <>{excerpts.map((excerpt) => <PaperExcerptView key={excerpt.locator} excerpt={excerpt}
      url={source.final_url} title={source.title} hash={hash} />)}</>;
  }
  return <p className="whitespace-pre-wrap break-words text-sm leading-6">{source.content || "暂无摘要或正文"}</p>;
}

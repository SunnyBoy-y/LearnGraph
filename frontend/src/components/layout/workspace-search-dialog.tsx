import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowUpRight, FileText, LoaderCircle, MessageCircle, Search, X } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { searchWorkspace, type WorkspaceSearchHit, type WorkspaceSearchKind } from "@/api/workspace-search";
import { workspaceQueryKey } from "@/lib/query-keys";
import "./workspace-search-dialog.css";

const filters: [WorkspaceSearchKind, string][] = [["all", "全部"], ["session", "会话"], ["message", "消息"], ["file", "文件"]];
const labels = { session: "会话", message: "消息", file: "文件" };

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return text;
  const result = [];
  let start = 0;
  let index = text.toLowerCase().indexOf(query.toLowerCase());
  while (index !== -1) {
    result.push(text.slice(start, index), <mark key={index}>{text.slice(index, index + query.length)}</mark>);
    start = index + query.length;
    index = text.toLowerCase().indexOf(query.toLowerCase(), start);
  }
  result.push(text.slice(start));
  return result;
}

export function WorkspaceSearchDialog({ open, onOpenChange, workspaceId }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: string;
}) {
  // Mount a fresh search session on every open; closed dialogs do no requests.
  return <Dialog open={open} onOpenChange={onOpenChange}>
    {open && <WorkspaceSearchContent workspaceId={workspaceId} onClose={() => onOpenChange(false)} />}
  </Dialog>;
}

function WorkspaceSearchContent({ workspaceId, onClose }: { workspaceId: string; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [kind, setKind] = useState<WorkspaceSearchKind>("all");
  const [active, setActive] = useState(-1);
  const resultsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const waiting = query.trim() !== debouncedQuery;
  const search = useInfiniteQuery({
    queryKey: workspaceQueryKey(workspaceId, "workspace-search", debouncedQuery, kind),
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => searchWorkspace(debouncedQuery, kind, pageParam, signal),
    getNextPageParam: (page) => page.has_more ? page.next_offset : undefined,
    staleTime: 0,
    gcTime: 0,
    retry: 1,
  });
  const items = waiting ? [] : search.data?.pages.flatMap((page) => page.items) ?? [];
  const busy = waiting || search.isPending;
  function select(hit: WorkspaceSearchHit) {
    onClose();
    if (hit.kind === "file") {
      navigate(`/w/${workspaceId}/documents/${encodeURIComponent(hit.id)}`);
    } else {
      navigate(`/w/${workspaceId}/chat/${encodeURIComponent(hit.session_id ?? hit.id)}`, {
        state: hit.kind === "message" ? { targetMessageId: hit.id } : null,
      });
    }
  }
  function moveActive(direction: number) {
    if (!items.length) return;
    const next = active < 0 ? (direction > 0 ? 0 : items.length - 1) : (active + direction + items.length) % items.length;
    setActive(next);
    resultsRef.current?.querySelectorAll<HTMLButtonElement>("[data-search-result]")[next]?.scrollIntoView?.({ block: "nearest" });
  }
  return <DialogContent className="workspace-search-dialog" showCloseButton={false}>
    <DialogTitle className="sr-only">搜索工作区</DialogTitle>
    <DialogDescription className="sr-only">搜索文件、会话、会话内容、会话 ID 或消息 ID。使用方向键选择，回车打开。</DialogDescription>
    <div className="workspace-search-input-row">
      <Search aria-hidden="true" />
      <input autoFocus aria-label="搜索文件、会话、消息或 ID" placeholder="搜索文件、会话、消息或 ID…"
        maxLength={240} value={query} onChange={(event) => { setQuery(event.target.value); setActive(-1); }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); moveActive(event.key === "ArrowDown" ? 1 : -1);
          } else if (event.key === "Enter" && items.length && !busy) {
            event.preventDefault(); select(items[active < 0 || active >= items.length ? 0 : active]);
          }
        }} />
      {query && <button type="button" aria-label="清空搜索" onClick={() => { setQuery(""); setActive(-1); }}><X /></button>}
      <button type="button" aria-label="关闭搜索" onClick={onClose}><X /></button>
    </div>
    <div className="workspace-search-filters" aria-label="搜索范围">
      {filters.map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value}
        onClick={() => { setKind(value); setActive(-1); }}>{label}</button>)}
    </div>
    <div className="workspace-search-results" ref={resultsRef} aria-busy={busy}>
      <p className="workspace-search-heading">{query.trim() ? "搜索结果" : kind === "file" ? "搜索文件" : kind === "message" ? "搜索消息" : "最近会话"}</p>
      {busy ? <div className="workspace-search-empty" role="status"><LoaderCircle className="animate-spin" />正在搜索…</div>
        : search.isError && !search.data ? <div className="workspace-search-empty" role="alert">搜索暂时不可用<button onClick={() => void search.refetch()}>重试</button></div>
        : !items.length ? <div className="workspace-search-empty" role="status">{query.trim() ? "没有找到相关结果" : kind === "file" || kind === "message" ? "输入关键词开始搜索" : "暂无最近会话"}<span>{query.trim() ? "试试其他关键词，或粘贴完整的会话 / 消息 ID" : kind === "file" || kind === "message" ? "支持名称、正文关键词和 ID" : "开始一段对话后，可以在这里快速找到它"}</span></div>
        : <ul aria-label="搜索结果">
          {items.map((hit, index) => <li key={`${hit.kind}:${hit.id}`}>
            <button type="button" data-search-result aria-current={index === active ? "true" : undefined}
              className="workspace-search-result" onClick={() => select(hit)} onFocus={() => setActive(index)}>
              {hit.kind === "file" ? <FileText aria-hidden="true" /> : <MessageCircle aria-hidden="true" />}
              <span className="workspace-search-result-text">
                <span className="workspace-search-result-title"><Highlight text={hit.title} query={debouncedQuery} /></span>
                {hit.snippet && <span className="workspace-search-result-snippet"><Highlight text={hit.snippet} query={debouncedQuery} /></span>}
              </span>
              {query.trim() && <span className="workspace-search-result-kind">{labels[hit.kind]}</span>}
              <ArrowUpRight className="workspace-search-result-arrow" aria-hidden="true" />
            </button>
          </li>)}
        </ul>}
      {!busy && search.hasNextPage && <button type="button" className="workspace-search-more"
        disabled={search.isFetchingNextPage} onClick={() => void search.fetchNextPage()}>
        {search.isFetchingNextPage ? "加载中…" : search.isFetchNextPageError ? "加载失败，点击重试" : "加载更多"}
      </button>}
    </div>
    <div className="workspace-search-footer"><span>搜索当前工作区</span><span><kbd>↑</kbd><kbd>↓</kbd> 选择 <kbd>↵</kbd> 打开 <kbd>Esc</kbd> 关闭</span></div>
  </DialogContent>;
}

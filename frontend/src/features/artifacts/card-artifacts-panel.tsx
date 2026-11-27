import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Code2,
  Copy,
  Download,
  ExternalLink,
  Eye,
  History,
  LayoutGrid,
  Link2,
  List as ListIcon,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Scan,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Play,
  RefreshCw,
  Rocket,
  RotateCcw,
  Search,
  Share2,
  Trash2,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Pagination } from "@/components/shared/pagination";
import { Checkbox } from "@/components/ui/checkbox";
import { applyRangeSelection, useEscapeKey } from "@/lib/list-selection";
import { artifactDownload, downloadArtifacts, downloadBlob, processArtifactBatch, useGalleryScroll } from "./gallery-actions";
import { toast } from "@/lib/notifications";

import {
  cardShareUrl,
  createArtifactCardShareToken,
  deleteArtifactCard,
  deleteArtifactCardVersion,
  getArtifactCardPreview,
  listArtifactCardShareTokens,
  listArtifactCardVersions,
  listArtifactCards,
  publishArtifactCardVersion,
  revokeArtifactCardShareToken,
  updateArtifactCard,
} from "@/api/artifacts";
import { MagicCardHost } from "@/components/chat/magic-card-host";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { SectionHeading } from "@/components/shared/page-elements";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  artifactKindMeta,
  DECLARATIVE_COMPONENT_CARD_TYPE,
  extractPureSvgDataUrl,
  formatArtifactSource,
  formatRelativeUpdateTime,
  resolveArtifactGalleryKind,
  type ArtifactGalleryKind,
} from "@/features/artifacts/card-types";
import { workspaceQueryKey } from "@/lib/query-keys";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import type {
  ArtifactCard,
  ArtifactCardShareTokenCreated,
} from "@/types/artifacts";
import { ArtifactCardShell, ArtifactKindBadge, CardInlinePreview } from "./card-preview";

export { ArtifactCardShell, CardInlinePreview } from "./card-preview";

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function cardQueryKey(workspaceId: string, params: Record<string, unknown>) {
  return workspaceQueryKey(workspaceId, "cards", params);
}

function StatusBadge({ card }: { card: ArtifactCard }) {
  if (card.status === "published") {
    return (
      <Badge variant="outline">
        已发布{card.latest_version > 0 ? ` v${card.latest_version}` : ""}
      </Badge>
    );
  }
  return <Badge variant="secondary">草稿</Badge>;
}

function PrimaryActionIcon({ kind }: { kind: ArtifactGalleryKind }) {
  if (kind === "svg") return <Eye className="size-3.5" />;
  if (kind === "html") return <ExternalLink className="size-3.5" />;
  return <Play className="size-3.5" />;
}

function cardListParams(status: string, sort: string) {
  return { status: status === "all" ? undefined : status, exclude_card_type: DECLARATIVE_COMPONENT_CARD_TYPE, sort, order: "desc", limit: 0 };
}

export function useGalleryCards(workspaceId: string, status = "all", sort = "updated_at") {
  const params = cardListParams(status, sort);
  return useQuery({ queryKey: cardQueryKey(workspaceId, params), queryFn: () => listArtifactCards(params) });
}

export function CardArtifactsPanel({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [params, setParams] = useSearchParams();
  const updateBrowse = (updates: Record<string, string>, replace = true) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(updates)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    }, { replace, preventScrollReset: true });
  };
  const searchQuery = params.get("q") ?? "";
  const statusFilter = params.get("status") ?? "all";
  const typeFilter = params.get("type") ?? "all";
  const sortOrder = params.get("sort") === "created_at" ? "created_at" : "updated_at";
  const viewMode = params.get("view") === "list" ? "list" : "grid";
  const setSearchQuery = (q: string) => updateBrowse({ q, page: "" });
  const setTypeFilter = (type: string) => updateBrowse({ type, page: "" });
  const setStatusFilter = (status: string) => updateBrowse({ status, page: "" });
  const setSortOrder = (sort: string) => updateBrowse({ sort, page: "" });
  const setViewMode = (view: string) => updateBrowse({ view });
  const requestedPage = Number(params.get("page"));
  const pageSize = [12, 24, 48].includes(Number(params.get("size"))) ? Number(params.get("size")) : 12;
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchDelete, setBatchDelete] = useState<ArtifactCard[] | null>(null);

  // Snapshot-refined kinds reported by loaded previews (allows refining e.g. static SVG vs HTML)
  const [resolvedKinds, setResolvedKinds] = useState<Record<string, ArtifactGalleryKind>>({});

  // Three-tier modals:
  // 1. Runtime / Viewer layer
  const [runtimeTarget, setRuntimeTarget] = useState<{
    card: ArtifactCard;
    kind: ArtifactGalleryKind;
  } | null>(null);
  // 2. Preview / Version management layer
  const [detailTarget, setDetailTarget] = useState<ArtifactCard | null>(null);
  // 3. Direct Share layer
  const [shareTarget, setShareTarget] = useState<ArtifactCard | null>(null);
  // 4. Rename / Delete dialogs
  const [renameTarget, setRenameTarget] = useState<ArtifactCard | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ArtifactCard | null>(null);

  const galleryTop = useRef<HTMLDivElement>(null);

  const handleKindResolved = useCallback((cardId: string, kind: ArtifactGalleryKind) => {
    setResolvedKinds((prev) => (prev[cardId] === kind ? prev : { ...prev, [cardId]: kind }));
  }, []);

  const cards = useGalleryCards(workspaceId, statusFilter, sortOrder);

  const invalidateCards = () =>
    queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "cards") });

  const deleteMutation = useMutation({
    mutationFn: deleteArtifactCard,
    onSuccess: async () => {
      await invalidateCards();
      toast.success("产物已删除");
      setDeleteTarget(null);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const renameMutation = useMutation({
    mutationFn: ({ cardId, title }: { cardId: string; title: string }) =>
      updateArtifactCard(cardId, { title }),
    onSuccess: async () => {
      await invalidateCards();
      toast.success("名称已更新");
      setRenameTarget(null);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const openSession = useCallback(
    (card: ArtifactCard) => {
      if (card.chat_session_id) {
        navigate(`/w/${workspaceId}/chat/${card.chat_session_id}`, {
          state: card.message_id ? { targetMessageId: card.message_id } : undefined,
        });
      } else {
        toast.error("该产物未关联会话");
      }
    },
    [navigate, workspaceId],
  );

  const effectiveKindFor = useCallback(
    (card: ArtifactCard): ArtifactGalleryKind =>
      resolvedKinds[card.card_id] ?? resolveArtifactGalleryKind(card),
    [resolvedKinds],
  );

  const filteredCards = useMemo(() => {
    const raw = cards.data ?? [];
    const q = searchQuery.trim().toLowerCase();
    return raw.filter((card) => {
      const kind = effectiveKindFor(card);
      if (typeFilter !== "all" && kind !== typeFilter) {
        return false;
      }
      if (!q) return true;
      const source = formatArtifactSource(card).toLowerCase();
      const kindLabel = artifactKindMeta(kind).filterLabel.toLowerCase();
      return (
        card.title.toLowerCase().includes(q) ||
        source.includes(q) ||
        card.card_id.toLowerCase().includes(q) ||
        kindLabel.includes(q)
      );
    });
  }, [cards.data, effectiveKindFor, searchQuery, typeFilter]);

  const page = Math.min(Math.max(1, Number.isSafeInteger(requestedPage) ? requestedPage : 1), Math.max(1, Math.ceil(filteredCards.length / pageSize)));
  const pageCards = filteredCards.slice((page - 1) * pageSize, page * pageSize);
  const pageCardIds = useMemo(() => pageCards.map((card) => card.card_id), [pageCards]);
  const cardAnchorRef = useRef<string | null>(null);
  const cardShiftRef = useRef(false);
  const exitSelecting = useCallback(() => {
    setSelecting(false);
    setSelectedIds(new Set());
    cardAnchorRef.current = null;
  }, []);
  useEscapeKey(selecting, exitSelecting);
  // Select from the unfiltered index so sorting, paging and filtering cannot silently change the operation scope.
  const allCards = useGalleryCards(workspaceId);
  const selected = (allCards.data ?? []).filter((card) => selectedIds.has(card.card_id));
  const selectedPublishable = selected.filter((card) => card.latest_version === 0 || card.draft_dirty);
  const onPageCount = pageCards.filter((card) => selectedIds.has(card.card_id)).length;
  const toggleCards = (items: ArtifactCard[], checked: boolean) => setSelectedIds((current) => {
    const next = new Set(current);
    for (const item of items) {
      if (checked) next.add(item.card_id);
      else next.delete(item.card_id);
    }
    return next;
  });
  const toggleCard = (card: ArtifactCard, shiftKey?: boolean) => {
    if (batchBusy) return;
    const useShift = shiftKey ?? cardShiftRef.current;
    cardShiftRef.current = false;
    const nextChecked = !selectedIds.has(card.card_id);
    if (useShift && cardAnchorRef.current) {
      setSelectedIds((current) =>
        applyRangeSelection(current, pageCardIds, cardAnchorRef.current, card.card_id, nextChecked),
      );
    } else {
      toggleCards([card], nextChecked);
    }
    cardAnchorRef.current = card.card_id;
  };
  const changePage = (next: number) => {
    if (next === page) return;
    updateBrowse({ page: String(next) }, false);
    galleryTop.current?.scrollIntoView?.({ block: "start" });
  };
  useGalleryScroll(cards.isSuccess, workspaceId, galleryTop);

  const runBatch = async (mode: "download" | "delete" | "publish", items: ArtifactCard[]) => {
    setBatchBusy(true);
    try {
      if (mode === "download") {
        const { count, skipped } = await downloadArtifacts(items);
        if (skipped) toast.error(`${count} 项已导出，${skipped} 项下载失败或没有可导出的 HTML / SVG`);
        else toast.success(`已导出 ${count} 项`);
      } else if (mode === "publish") {
        const result = await processArtifactBatch(items, (item) => publishArtifactCardVersion(item.card_id, {}));
        const published = items.filter((_, index) => result[index].status === "fulfilled");
        await invalidateCards();
        if (published.length !== items.length) toast.error(`已发布 ${published.length} 项，${items.length - published.length} 项发布失败`);
        else toast.success(`已发布 ${published.length} 项`);
      } else {
        const result = await processArtifactBatch(items, (item) => deleteArtifactCard(item.card_id));
        const deleted = items.filter((_, index) => result[index].status === "fulfilled");
        toggleCards(deleted, false);
        setBatchDelete(null);
        await invalidateCards();
        if (deleted.length !== items.length) toast.error(`已删除 ${deleted.length} 项，${items.length - deleted.length} 项删除失败`);
        else toast.success(`已删除 ${deleted.length} 项`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "批量操作失败");
    } finally {
      setBatchBusy(false);
    }
  };

  const openRuntimeOrViewer = useCallback(
    (card: ArtifactCard, resolvedKind?: ArtifactGalleryKind) => {
      setRuntimeTarget({
        card,
        kind: resolvedKind ?? effectiveKindFor(card),
      });
    },
    [effectiveKindFor],
  );

  const totalCount = cards.data?.length ?? 0;
  const hasActiveFilter = Boolean(searchQuery.trim()) || typeFilter !== "all" || statusFilter !== "all";

  const renderMoreMenu = (card: ArtifactCard) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`更多操作 ${card.title}`}
          className="size-7 text-muted-foreground hover:text-foreground"
          size="icon-xs"
          title="更多操作"
          type="button"
          variant="ghost"
        >
          <MoreHorizontal className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem onSelect={() => setDetailTarget(card)}>
          <History className="size-4" />
          详情与版本
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setRenameTarget(card)}>
          <Pencil className="size-4" />
          重命名
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!card.chat_session_id}
          onSelect={() => openSession(card)}
        >
          <MessageSquare className="size-4" />
          跳转到来源会话
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => setDeleteTarget(card)}
          variant="destructive"
        >
          <Trash2 className="size-4" />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="grid min-w-0 gap-4" ref={galleryTop}>
      <div className="flex min-w-0 flex-wrap items-center gap-2.5">
        <div className="relative min-w-0 flex-[1_1_12rem]">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索产物"
            className="h-9 pr-8 pl-9"
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="搜索产物..."
            value={searchQuery}
          />
          {searchQuery ? (
            <button
              aria-label="清空搜索"
              className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={() => setSearchQuery("")}
              type="button"
            >
              <X className="size-3.5" />
            </button>
          ) : null}
        </div>

        <Select onValueChange={setTypeFilter} value={typeFilter}>
          <SelectTrigger aria-label="按产物类型筛选" className="h-9 w-[124px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            <SelectItem value="interactive">交互应用</SelectItem>
            <SelectItem value="svg">SVG 图形</SelectItem>
            <SelectItem value="html">HTML 网页</SelectItem>
            <SelectItem value="animation">动画</SelectItem>
          </SelectContent>
        </Select>

        <Select onValueChange={setStatusFilter} value={statusFilter}>
          <SelectTrigger aria-label="按发布状态筛选" className="h-9 w-[112px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="draft">草稿</SelectItem>
            <SelectItem value="published">已发布</SelectItem>
          </SelectContent>
        </Select>

        <Select onValueChange={setSortOrder} value={sortOrder}>
          <SelectTrigger aria-label="排序方式" className="h-9 w-[116px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="updated_at">最近更新</SelectItem>
            <SelectItem value="created_at">最近创建</SelectItem>
          </SelectContent>
        </Select>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Button aria-pressed={selecting} disabled={batchBusy} onClick={() => {
            setSelecting(!selecting);
            if (selecting) setSelectedIds(new Set());
          }} size="sm" type="button" variant={selecting ? "secondary" : "outline"}>
            {selecting ? "退出多选" : "多选"}
          </Button>
          <div
            aria-label="视图切换"
            className="inline-flex h-9 items-center rounded-md border bg-background p-0.5"
            role="group"
          >
            <Button
              aria-label="网格视图"
              aria-pressed={viewMode === "grid"}
              className="h-7.5 w-7.5"
              onClick={() => setViewMode("grid")}
              size="icon-xs"
              title="网格视图"
              type="button"
              variant={viewMode === "grid" ? "secondary" : "ghost"}
            >
              <LayoutGrid className="size-3.5" />
            </Button>
            <Button
              aria-label="列表视图"
              aria-pressed={viewMode === "list"}
              className="h-7.5 w-7.5"
              onClick={() => setViewMode("list")}
              size="icon-xs"
              title="列表视图"
              type="button"
              variant={viewMode === "list" ? "secondary" : "ghost"}
            >
              <ListIcon className="size-3.5" />
            </Button>
          </div>
          <Button
            aria-label="刷新"
            disabled={cards.isFetching}
            onClick={() => void cards.refetch()}
            size="icon-sm"
            title="刷新产物列表"
            type="button"
            variant="ghost"
          >
            <RefreshCw className={`size-4 ${cards.isFetching ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {hasActiveFilter && cards.isSuccess ? (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {hasActiveFilter
              ? `匹配产物 · ${filteredCards.length} / ${totalCount}`
              : `全部产物 · ${totalCount}`}
          </span>
          {hasActiveFilter ? (
            <button
              className="text-xs text-foreground/80 underline-offset-2 hover:underline"
              onClick={() => updateBrowse({ q: "", type: "", status: "", page: "" })}
              type="button"
            >
              重置筛选
            </button>
          ) : null}
        </div>
      ) : null}

      {selecting && (
        <div aria-label="批量管理" className="sticky top-2 z-10 flex min-w-0 flex-wrap items-center gap-2 rounded-xl border bg-background p-2 shadow-xs" role="group">
          <label className="flex items-center gap-2 px-2 text-sm">
            <Checkbox checked={onPageCount > 0 && (onPageCount === pageCards.length || "indeterminate")} disabled={batchBusy || !pageCards.length} onCheckedChange={(checked) => toggleCards(pageCards, checked === true)} />
            全选本页
          </label>
          <span aria-live="polite" className="text-xs text-muted-foreground">已选择 {selected.length} 项 · 本页 {onPageCount} 项</span>
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <Button disabled={batchBusy || !selectedPublishable.length} onClick={() => void runBatch("publish", selectedPublishable)} size="sm" variant="outline"><Rocket />批量发布{selectedPublishable.length ? ` (${selectedPublishable.length})` : ""}</Button>
            <Button disabled={batchBusy || !selected.length} onClick={() => void runBatch("download", selected)} size="sm" variant="outline"><Download />批量下载</Button>
            <Button disabled={batchBusy || !selected.length} onClick={() => setBatchDelete(selected)} size="sm" variant="outline"><Trash2 />批量删除</Button>
            <Button disabled={batchBusy || !selected.length} onClick={() => setSelectedIds(new Set())} size="sm" variant="ghost">清空选择</Button>
          </div>
        </div>
      )}

      <div className="min-w-0">
        {cards.isPending ? (
          <div className="artifact-gallery-grid">
            <Skeleton className="h-[270px] w-full rounded-[12px]" />
            <Skeleton className="h-[270px] w-full rounded-[12px]" />
            <Skeleton className="h-[270px] w-full rounded-[12px]" />
            <Skeleton className="h-[270px] w-full rounded-[12px]" />
          </div>
        ) : cards.isError ? (
          <p className="text-sm text-destructive">
            {cards.error instanceof Error ? cards.error.message : "加载产物失败"}
          </p>
        ) : totalCount === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed p-12 text-center">
            <LayoutGrid className="size-6 text-muted-foreground" />
            <p className="text-sm font-medium">还没有产物</p>
            <p className="text-xs text-muted-foreground">
              在会话中生成互动练习、图示或学习页面后，会自动收录到这里。
            </p>
          </div>
        ) : filteredCards.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed p-10 text-center">
            <Search className="size-5 text-muted-foreground" />
            <p className="text-sm font-medium">没有找到匹配的产物</p>
            <p className="text-xs text-muted-foreground">
              尝试更换搜索关键词或清除类型 / 状态筛选条件。
            </p>
          </div>
        ) : viewMode === "grid" ? (
          <div className="artifact-gallery-grid">
            {pageCards.map((card) => {
              const kind = effectiveKindFor(card);
              const kindMeta = artifactKindMeta(kind);
              const source = formatArtifactSource(card);
              const updatedText = formatRelativeUpdateTime(card.updated_at);

              return (
                <ArtifactCardShell
                  card={card}
                  compact
                  selection={selecting ? { checked: selectedIds.has(card.card_id), disabled: batchBusy, toggle: (shiftKey?: boolean) => toggleCard(card, shiftKey) } : undefined}
                  footer={selecting ? null : (
                    <>
                      <Button
                        className="rounded-xl"
                        onClick={() => openRuntimeOrViewer(card, kind)}
                        size="sm"
                        type="button"
                      >
                        <PrimaryActionIcon kind={kind} />
                        {kindMeta.actionLabel}
                      </Button>
                      <Button
                        aria-label={`分享 ${card.title}`}
                        className="ml-auto text-muted-foreground hover:text-foreground"
                        onClick={() => setShareTarget(card)}
                        size="icon-sm"
                        title="分享与发布"
                        type="button"
                        variant="ghost"
                      >
                        <Share2 className="size-4" />
                      </Button>
                      {renderMoreMenu(card)}
                    </>
                  )}
                  key={card.id}
                  updated={<time className="shrink-0" dateTime={card.updated_at} title={`更新于 ${formatDate(card.updated_at)}`}>{updatedText}</time>}
                  onActivate={(_id, resolved) => openRuntimeOrViewer(card, resolved ?? kind)}
                  onResolvedKind={handleKindResolved}
                  subtitle={<span className="truncate" title={source}>{source}</span>}
                  workspaceId={workspaceId}
                />
              );
            })}
          </div>
        ) : (
          <div className="grid gap-3">
            {pageCards.map((card) => {
              const kind = effectiveKindFor(card);
              const kindMeta = artifactKindMeta(kind);
              const source = formatArtifactSource(card);
              const updatedText = formatRelativeUpdateTime(card.updated_at);

              return (
                <article
                  aria-label={card.title}
                  className="artifact-gallery-row relative grid min-w-0 items-center gap-3 rounded-xl border bg-card p-3"
                  data-selected={selecting && selectedIds.has(card.card_id)}
                  key={card.id}
                  onClick={selecting ? (event) => { if (!(event.target as Element).closest('button')) toggleCard(card, event.shiftKey); } : undefined}
                >
                  {selecting && <Checkbox aria-label={`选择 ${card.title}`} checked={selectedIds.has(card.card_id)} className="absolute left-5 top-5 z-10 size-5 bg-background" disabled={batchBusy} onClickCapture={(event) => { cardShiftRef.current = event.shiftKey; }} onCheckedChange={() => toggleCard(card)} />}
                  <div className="relative min-w-0">
                    <CardInlinePreview
                      card={card}
                      onKindResolved={(resolved) => handleKindResolved(card.card_id, resolved)}
                      workspaceId={workspaceId}
                    />
                    {!selecting && <button aria-label={`${kindMeta.actionLabel} ${card.title}`} className="absolute inset-0 cursor-pointer rounded-xl focus-visible:ring-2 focus-visible:ring-ring" onClick={() => openRuntimeOrViewer(card, kind)} type="button" />}
                  </div>
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="min-w-0 line-clamp-2 break-words text-base font-semibold text-foreground" title={card.title}>
                        {card.title}
                      </h3>
                      <StatusBadge card={card} />
                      {card.draft_dirty ? (
                        <Badge className="text-[10px]" variant="secondary">
                          草稿有更新
                        </Badge>
                      ) : null}
                    </div>
                    <p className="truncate text-xs text-muted-foreground">
                      {kindMeta.filterLabel} · {source} · {updatedText}
                    </p>
                  </div>
                  {!selecting && <div className="artifact-gallery-row-actions flex min-w-0 flex-wrap items-center gap-1.5">
                    <Button
                      onClick={() => openRuntimeOrViewer(card, kind)}
                      size="sm"
                      type="button"
                      variant="outline"
                    >
                      <PrimaryActionIcon kind={kind} />
                      {kindMeta.actionLabel}
                    </Button>
                    <Button
                      aria-label={`分享 ${card.title}`}
                      onClick={() => setShareTarget(card)}
                      size="sm"
                      type="button"
                      variant="ghost"
                    >
                      <Share2 className="size-3.5" />
                      <span className="artifact-gallery-share-label">分享</span>
                    </Button>
                    {renderMoreMenu(card)}
                  </div>}
                </article>
              );
            })}
          </div>
        )}
      </div>

      {cards.isSuccess && filteredCards.length > 0 && (
        <Pagination page={page} pageSize={pageSize} total={filteredCards.length} onPageChange={changePage} onPageSizeChange={(size) => {
          updateBrowse({ size: String(size), page: "" }, false);
          galleryTop.current?.scrollIntoView?.({ block: "start" });
        }} />
      )}
      <AlertDialog open={Boolean(batchDelete)} onOpenChange={(open) => { if (!open && !batchBusy) setBatchDelete(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除选中的 {batchDelete?.length ?? 0} 项产物？</AlertDialogTitle>
            <AlertDialogDescription>
              范围包括所有已选项，不限于本页。产物将从库中移除，原始会话不受影响；若其中有产物已分享，其分享链接在产物删除期间将无法访问。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={batchBusy}>取消</AlertDialogCancel>
            <AlertDialogAction disabled={batchBusy} onClick={(event) => {
              event.preventDefault();
              if (batchDelete) void runBatch("delete", batchDelete);
            }} variant="destructive">{batchBusy ? "正在删除…" : "确认批量删除"}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Tier 3: Runtime / Static Viewer Dialog */}
      <ArtifactRuntimeDialog
        kind={runtimeTarget?.kind ?? "interactive"}
        onClose={() => setRuntimeTarget(null)}
        onOpenDetails={(card) => {
          setRuntimeTarget(null);
          setDetailTarget(card);
        }}
        onOpenSession={(card) => {
          setRuntimeTarget(null);
          openSession(card);
        }}
        target={runtimeTarget?.card ?? null}
        workspaceId={workspaceId}
      />

      {/* Tier 2: Preview & Version Management Dialog */}
      <CardPreviewDialog
        card={detailTarget}
        key={detailTarget?.card_id ?? "none"}
        onChanged={invalidateCards}
        onClose={() => setDetailTarget(null)}
        onLaunchRuntime={(card, kind) => {
          setDetailTarget(null);
          openRuntimeOrViewer(card, kind);
        }}
        workspaceId={workspaceId}
      />

      {/* Quick Share Dialog (direct from card footer) */}
      <QuickShareDialog
        card={shareTarget}
        key={`share-${shareTarget?.card_id ?? "none"}`}
        onChanged={invalidateCards}
        onClose={() => setShareTarget(null)}
        onManageVersions={(card) => {
          setShareTarget(null);
          setDetailTarget(card);
        }}
        workspaceId={workspaceId}
      />

      {/* Rename Dialog */}
      <RenameCardDialog
        busy={renameMutation.isPending}
        card={renameTarget}
        key={`rename-${renameTarget?.card_id ?? "none"}`}
        onClose={() => setRenameTarget(null)}
        onSubmit={(title) => {
          if (!renameTarget) return;
          renameMutation.mutate({ cardId: renameTarget.card_id, title });
        }}
      />

      {/* Delete Confirmation */}
      <AlertDialog
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        open={Boolean(deleteTarget)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除“{deleteTarget?.title ?? ""}”？</AlertDialogTitle>
            <AlertDialogDescription>
              产物将从产物库移除，但会话中的原始消息不会受影响。已删除产物被智能体再次生成时会恢复为草稿。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate(deleteTarget!.card_id)}
              variant="destructive"
            >
              {deleteMutation.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * Tier 3: Runtime / Full Viewer Modal
 * - SVG: zoomable static lightbox + source view + download .svg
 * - Interactive / HTML / Animation: full interactive MagicCardHost runtime
 */
function ArtifactRuntimeDialog({
  target,
  kind,
  workspaceId,
  onClose,
  onOpenDetails,
  onOpenSession,
}: {
  target: ArtifactCard | null;
  kind: ArtifactGalleryKind;
  workspaceId: string;
  onClose: () => void;
  onOpenDetails: (card: ArtifactCard) => void;
  onOpenSession: (card: ArtifactCard) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [showSource, setShowSource] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const sync = () => setFullscreen(document.fullscreenElement === dialogRef.current && Boolean(dialogRef.current));
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === dialogRef.current && dialogRef.current) await document.exitFullscreen();
      else if (dialogRef.current?.requestFullscreen) await dialogRef.current.requestFullscreen();
      else setMaximized(true);
    } catch {
      setMaximized(true);
    }
  };

  const preview = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "cards",
      target?.card_id ?? "__none__",
      "preview",
      "draft",
      target?.updated_at ?? "",
    ),
    queryFn: () => getArtifactCardPreview(target!.card_id),
    enabled: Boolean(target),
  });

  useEffect(() => {
    setZoom(1);
    setShowSource(false);
    setMaximized(false);
  }, [target?.card_id]);

  const snapshot = preview.data?.preview_snapshot ?? {};
  const previewHtml = typeof snapshot.preview_html === "string" ? snapshot.preview_html : "";
  const effectiveKind = target ? resolveArtifactGalleryKind(target, snapshot) : kind;
  const { svgMarkup, pureSvgDataUrl } = useMemo(
    () =>
      effectiveKind === "svg" && previewHtml
        ? extractPureSvgDataUrl(previewHtml)
        : { svgMarkup: undefined, pureSvgDataUrl: undefined },
    [effectiveKind, previewHtml],
  );

  const download = target && artifactDownload(target, snapshot);
  const runtimeFooter = (
    <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-t px-3 py-2 sm:px-4">
            {effectiveKind === "svg" ? (
              <>
                <Button
                  disabled={zoom <= 0.5}
                  onClick={() => setZoom((z) => Math.max(0.5, Number((z - 0.25).toFixed(2))))}
                  size="xs"
                  title="缩小"
                  type="button"
                  variant="outline"
                >
                  <ZoomOut className="size-3.5" />
                </Button>
                <span className="min-w-12 text-center text-xs tabular-nums text-muted-foreground">
                  {Math.round(zoom * 100)}%
                </span>
                <Button
                  disabled={zoom >= 3}
                  onClick={() => setZoom((z) => Math.min(3, Number((z + 0.25).toFixed(2))))}
                  size="xs"
                  title="放大"
                  type="button"
                  variant="outline"
                >
                  <ZoomIn className="size-3.5" />
                </Button>
                <Button
                  onClick={() => setZoom(1)}
                  size="xs"
                  title="重置缩放"
                  type="button"
                  variant="ghost"
                >
                  <RotateCcw className="size-3.5" />
                  重置
                </Button>
                {svgMarkup ? (
                  <Button
                    onClick={() => setShowSource((v) => !v)}
                    size="xs"
                    type="button"
                    variant={showSource ? "secondary" : "ghost"}
                  >
                    <Code2 className="size-3.5" />
                    {showSource ? "预览" : "源码"}
                  </Button>
                ) : null}
              </>
            ) : null}
            {download ? (
              <Button onClick={() => downloadBlob(new Blob([download.content], { type: download.extension === "svg" ? "image/svg+xml;charset=utf-8" : "text/html;charset=utf-8" }), `${download.name}.${download.extension}`)} size="xs" type="button" variant="ghost">
                <Download className="size-3.5" />下载 {download.extension.toUpperCase()}
              </Button>
            ) : null}
            {target ? (
              <Button
                className="ml-auto"
                onClick={() => onOpenDetails(target)}
                size="xs"
                type="button"
                variant="outline"
              >
                <History className="size-3.5" />
                版本与分享
              </Button>
            ) : null}
            {target?.chat_session_id ? (
              <Button
                onClick={() => onOpenSession(target)}
                size="xs"
                type="button"
                variant="ghost"
              >
                <MessageSquare className="size-3.5" />
                来源会话
              </Button>
            ) : null}
    </div>
  );

  return (
    <Dialog onOpenChange={(next) => { if (!next) onClose(); }} open={Boolean(target)}>
      <DialogContent
        className="artifact-runtime-dialog flex flex-col gap-0 p-0 sm:max-w-none"
        data-maximized={maximized}
        data-wide={Boolean(snapshot.bundle_id || snapshot.artifact_url || Number(snapshot.preferred_height) > 580)}
        data-kind={effectiveKind}
        ref={dialogRef}
        showCloseButton={false}
        onEscapeKeyDown={(event) => {
          if (maximized || fullscreen) {
            event.preventDefault();
            if (fullscreen) void toggleFullscreen();
            else setMaximized(false);
          }
        }}
      >
        <DialogHeader className="flex shrink-0 flex-row items-center justify-between gap-2 border-b px-3 py-2.5 text-left sm:px-4">
          <div className="min-w-0">
            <DialogTitle className="line-clamp-2 break-words text-base font-semibold">{target?.title ?? "产物运行"}</DialogTitle>
            <ArtifactKindBadge kind={effectiveKind} />
          </div>
          <DialogDescription className="sr-only">产物运行与完整预览窗口</DialogDescription>
          <div className="flex shrink-0 items-center gap-1">
            <Button aria-label={fullscreen ? "退出全屏" : "全屏运行"} title={fullscreen ? "退出全屏" : "全屏运行"} disabled={!fullscreen && !document.fullscreenEnabled} onClick={() => void toggleFullscreen()} size="icon-sm" variant="ghost"><Scan /></Button>
            <Button aria-label={maximized ? "还原窗口" : "最大化"} title={maximized ? "还原窗口" : "最大化"} aria-pressed={maximized} disabled={fullscreen} onClick={() => setMaximized((value) => !value)} size="icon-sm" variant="ghost">{maximized ? <Minimize2 /> : <Maximize2 />}</Button>
            <Button aria-label="关闭" onClick={onClose} size="icon-sm" variant="ghost"><X /></Button>
          </div>
        </DialogHeader>

        <div className="artifact-runtime-stage">
          {preview.isPending ? (
            <Skeleton className="h-48 w-full rounded-none" />
          ) : preview.isError ? (
            <div className="grid min-h-28 place-content-center justify-items-center gap-3 p-4 text-center">
              <p className="text-sm text-destructive">
                {preview.error instanceof Error ? preview.error.message : "加载产物失败"}
              </p>
              <Button onClick={() => void preview.refetch()} size="sm" type="button" variant="outline">
                重试
              </Button>
            </div>
          ) : target?.card_type === DECLARATIVE_COMPONENT_CARD_TYPE ? (
            <TrustedComponentRenderer
              data={snapshot}
              fallbackId={target.card_id}
              interactive={false}
            />
          ) : effectiveKind === "svg" && showSource && svgMarkup ? (
            <pre className="m-0 bg-background p-4 font-mono text-xs leading-relaxed text-foreground">
              <code>{svgMarkup}</code>
            </pre>
          ) : effectiveKind === "svg" && pureSvgDataUrl ? (
            <div className="artifact-runtime-svg" style={{ width: `${Math.max(1, zoom) * 100}%` }}>
              <img
                alt={target?.title ?? "SVG 预览"}
                className="mx-auto block h-auto object-contain"
                src={pureSvgDataUrl}
                style={{ maxWidth: `${Math.min(1, zoom) * 100}%`, maxHeight: `calc(var(--artifact-svg-height) * ${zoom})` }}
              />
            </div>
          ) : effectiveKind === "svg" && previewHtml ? (
            <iframe
              className="artifact-runtime-static-frame"
              referrerPolicy="no-referrer"
              sandbox=""
              srcDoc={sandboxedHtmlPreviewDocument(previewHtml, { scripts: false })}
              style={{ width: `${zoom * 100}%` }}
              title={target?.title ?? "SVG 预览"}
            />
          ) : (
            <MagicCardHost
              data={snapshot}
              fitArtifact
              expandedArtifact={maximized || fullscreen}
              hideHeader
              key={`runtime-${target?.card_id}-${target?.updated_at}`}
            />
          )}
        </div>
        {runtimeFooter}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Tier 2: Preview, Version History & Share Management Dialog
 */
function CardPreviewDialog({
  card,
  onClose,
  onLaunchRuntime,
  workspaceId,
  onChanged,
}: {
  card: ArtifactCard | null;
  onClose: () => void;
  onLaunchRuntime: (card: ArtifactCard, kind: ArtifactGalleryKind) => void;
  workspaceId: string;
  onChanged: () => Promise<void> | void;
}) {
  const navigate = useNavigate();
  const [selectedVersion, setSelectedVersion] = useState<number | "draft">(
    card?.latest_version && card.latest_version > 0 ? card.latest_version : "draft",
  );
  const [publishOpen, setPublishOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  const versions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "cards", card?.card_id ?? "__none__", "versions"),
    queryFn: () => listArtifactCardVersions(card!.card_id),
    enabled: Boolean(card),
  });

  useEffect(() => {
    if (!card) return;
    const latest =
      versions.data?.reduce<number | null>(
        (max, version) =>
          version.status !== "active"
            ? max
            : max === null || version.version > max
              ? version.version
              : max,
        null,
      ) ?? (card.latest_version > 0 ? card.latest_version : null);
    setSelectedVersion(latest ?? "draft");
  }, [card, versions.data]);

  const preview = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "cards",
      card?.card_id ?? "__none__",
      "preview",
      selectedVersion,
      card?.updated_at,
    ),
    queryFn: () =>
      getArtifactCardPreview(card!.card_id, {
        ...(selectedVersion !== "draft" ? { version: selectedVersion } : {}),
      }),
    enabled: Boolean(card),
  });

  const snapshot = preview.data?.preview_snapshot ?? {};
  const isComponent = card?.card_type === DECLARATIVE_COMPONENT_CARD_TYPE;
  const title = card?.title ?? "产物详情";
  const resolvedKind = card ? resolveArtifactGalleryKind(card, snapshot) : "html";
  const selectedVersionId =
    selectedVersion !== "draft"
      ? versions.data?.find((version) => version.version === selectedVersion)?.id
      : undefined;

  const publishMutation = useMutation({
    mutationFn: (releaseNotes: string) =>
      publishArtifactCardVersion(card!.card_id, { release_notes: releaseNotes }),
    onSuccess: async () => {
      toast.success("版本已发布");
      setPublishOpen(false);
      await versions.refetch();
      await onChanged();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const deleteVersionMutation = useMutation({
    mutationFn: deleteArtifactCardVersion,
    onSuccess: async () => {
      toast.success("版本已删除");
      if (selectedVersion !== "draft") setSelectedVersion("draft");
      await versions.refetch();
      await onChanged();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const resetState = () => {
    setSelectedVersion("draft");
    setPublishOpen(false);
    setShareOpen(false);
  };

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) {
          resetState();
          onClose();
        }
      }}
      open={Boolean(card)}
    >
      <DialogContent className="flex max-h-[90vh] max-w-3xl flex-col gap-3 overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            <History className="size-4" />
            {title}
            <ArtifactKindBadge kind={resolvedKind} />
          </DialogTitle>
          <DialogDescription>
            查看草稿与已发布版本，管理发布、分享或启动完整运行。
          </DialogDescription>
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Select
              onValueChange={(value) =>
                setSelectedVersion(value === "draft" ? "draft" : Number(value))
              }
              value={selectedVersion === "draft" ? "draft" : String(selectedVersion)}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="draft">
                  当前草稿{card?.draft_dirty ? "（有更新）" : ""}
                </SelectItem>
                {versions.data?.map((version) => (
                  <SelectItem key={version.id} value={String(version.version)}>
                    v{version.version} · {formatDate(version.created_at)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-xs text-muted-foreground">
              {isComponent
                ? "声明式组件 · 只读预览"
                : artifactKindMeta(resolvedKind).modeLabel}
            </span>
            {card ? (
              <Button
                className="ml-auto"
                onClick={() => onLaunchRuntime(card, resolvedKind)}
                size="xs"
                type="button"
                variant="outline"
              >
                <PrimaryActionIcon kind={resolvedKind} />
                {artifactKindMeta(resolvedKind).actionLabel}
              </Button>
            ) : null}
          </div>
        </DialogHeader>

        {preview.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : preview.isError ? (
          <p className="text-sm text-destructive">
            {preview.error instanceof Error ? preview.error.message : "加载预览失败"}
          </p>
        ) : isComponent ? (
          <div className="rounded-xl border bg-background p-4">
            <TrustedComponentRenderer
              data={snapshot}
              fallbackId={card?.card_id ?? "card-preview"}
              interactive={false}
            />
          </div>
        ) : (
          <MagicCardHost hideHeader key={`${card?.card_id}-${selectedVersion}`} data={snapshot} />
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
          <div className="flex items-center gap-2">
            {selectedVersion !== "draft" && selectedVersionId ? (
              <Button
                disabled={deleteVersionMutation.isPending}
                onClick={() => deleteVersionMutation.mutate(selectedVersionId)}
                size="sm"
                type="button"
                variant="outline"
              >
                <Trash2 className="size-4" />
                删除此版本
              </Button>
            ) : null}
            <Button onClick={() => setPublishOpen(true)} size="sm" type="button">
              <Rocket className="size-4" />
              发布当前草稿
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={() => setShareOpen(true)} size="sm" type="button" variant="outline">
              <Link2 className="size-4" />
              分享
            </Button>
            {card?.chat_session_id ? (
              <Button
                onClick={() =>
                  navigate(`/w/${workspaceId}/chat/${card.chat_session_id}`, {
                    state: card.message_id
                      ? { targetMessageId: card.message_id }
                      : undefined,
                  })
                }
                size="sm"
                type="button"
              >
                <MessageSquare className="size-4" />
                跳转到会话
              </Button>
            ) : null}
            <Button onClick={onClose} size="sm" type="button" variant="ghost">
              关闭
            </Button>
          </div>
        </div>
      </DialogContent>

      <PublishVersionDialog
        busy={publishMutation.isPending}
        card={card}
        onClose={() => setPublishOpen(false)}
        onSubmit={(releaseNotes) => publishMutation.mutate(releaseNotes)}
        open={publishOpen}
      />

      <CardShareDialog
        card={card}
        onClose={() => setShareOpen(false)}
        open={shareOpen}
        versionId={selectedVersionId}
        workspaceId={workspaceId}
      />
    </Dialog>
  );
}

/**
 * Direct Share Dialog opened from the card's "分享" footer action.
 * Resolves the latest published version automatically and offers 1-click publish if still draft.
 */
function QuickShareDialog({
  card,
  onClose,
  onChanged,
  onManageVersions,
  workspaceId,
}: {
  card: ArtifactCard | null;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  onManageVersions: (card: ArtifactCard) => void;
  workspaceId: string;
}) {
  const versions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "cards", card?.card_id ?? "__none__", "versions"),
    queryFn: () => listArtifactCardVersions(card!.card_id),
    enabled: Boolean(card),
  });

  const latestVersion = useMemo(
    () =>
      versions.data?.find((v) => v.status === "active") ?? null,
    [versions.data],
  );

  const publishMutation = useMutation({
    mutationFn: () => publishArtifactCardVersion(card!.card_id, { release_notes: "" }),
    onSuccess: async () => {
      toast.success("已发布为不可变版本，可生成分享链接");
      await versions.refetch();
      await onChanged();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <CardShareDialog
      card={card}
      extraHeader={
        card ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs">
            <span className="text-muted-foreground">
              {latestVersion
                ? `当前分享对象：已发布版本 v${latestVersion.version}`
                : "当前产物尚未发布版本"}
            </span>
            <div className="flex items-center gap-1.5">
              {!latestVersion && !versions.isPending ? (
                <Button
                  disabled={publishMutation.isPending}
                  onClick={() => publishMutation.mutate()}
                  size="xs"
                  type="button"
                >
                  {publishMutation.isPending ? (
                    <LoaderCircle className="size-3.5 animate-spin" />
                  ) : (
                    <Rocket className="size-3.5" />
                  )}
                  立即发布 v1
                </Button>
              ) : null}
              <Button
                onClick={() => onManageVersions(card)}
                size="xs"
                type="button"
                variant="ghost"
              >
                <History className="size-3.5" />
                版本管理
              </Button>
            </div>
          </div>
        ) : null
      }
      onClose={onClose}
      open={Boolean(card)}
      versionId={latestVersion?.id}
      workspaceId={workspaceId}
    />
  );
}

function RenameCardDialog({
  card,
  busy,
  onClose,
  onSubmit,
}: {
  card: ArtifactCard | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (title: string) => void;
}) {
  const [title, setTitle] = useState(card?.title ?? "");

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={Boolean(card)}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>重命名产物</DialogTitle>
          <DialogDescription>修改产物库中的展示名称，不影响已发布版本或会话消息。</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = title.trim();
            if (!trimmed) return;
            onSubmit(trimmed);
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="artifact-card-rename-input">产物名称</Label>
            <Input
              autoFocus
              id="artifact-card-rename-input"
              maxLength={240}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="输入产物名称"
              required
              value={title}
            />
          </div>
          <DialogFooter>
            <Button disabled={busy} onClick={onClose} type="button" variant="ghost">
              取消
            </Button>
            <Button disabled={busy || !title.trim()} type="submit">
              {busy ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PublishVersionDialog({
  card,
  open,
  onClose,
  onSubmit,
  busy,
}: {
  card: ArtifactCard | null;
  open: boolean;
  onClose: () => void;
  onSubmit: (releaseNotes: string) => void;
  busy: boolean;
}) {
  const [releaseNotes, setReleaseNotes] = useState("");
  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={open}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>发布版本 · {card?.title ?? ""}</DialogTitle>
          <DialogDescription>
            将当前草稿冻结为不可变版本（{card ? `v${card.latest_version + 1}` : ""}）。
            之后的草稿修改不会影响已发布版本。
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(releaseNotes.trim());
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="card-release-notes">版本说明（可选）</Label>
            <Textarea
              autoFocus
              id="card-release-notes"
              maxLength={4000}
              onChange={(event) => setReleaseNotes(event.target.value)}
              placeholder="这个版本包含什么变化"
              rows={3}
              value={releaseNotes}
            />
          </div>
          <DialogFooter>
            <Button disabled={busy} onClick={onClose} type="button" variant="ghost">
              取消
            </Button>
            <Button disabled={busy} type="submit">
              {busy ? <LoaderCircle className="size-4 animate-spin" /> : <Rocket className="size-4" />}
              发布
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function CardShareDialog({
  card,
  open,
  onClose,
  versionId,
  workspaceId,
  extraHeader,
}: {
  card: ArtifactCard | null;
  open: boolean;
  onClose: () => void;
  versionId: string | undefined;
  workspaceId: string;
  extraHeader?: React.ReactNode;
}) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [maxViews, setMaxViews] = useState("");
  const [createdToken, setCreatedToken] = useState<ArtifactCardShareTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const tokens = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "cards", "share-tokens", versionId ?? "__none__"),
    queryFn: () => listArtifactCardShareTokens(versionId as string),
    enabled: Boolean(versionId) && open,
  });

  const invalidateTokens = () =>
    queryClient.invalidateQueries({
      queryKey: workspaceQueryKey(workspaceId, "cards", "share-tokens", versionId ?? "__none__"),
    });

  const createMutation = useMutation({
    mutationFn: (payload: { label?: string; max_views?: number | null }) =>
      createArtifactCardShareToken(versionId as string, payload),
    onSuccess: (token) => {
      setCreatedToken(token);
      void invalidateTokens();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const revokeMutation = useMutation({
    mutationFn: revokeArtifactCardShareToken,
    onSuccess: () => {
      toast.success("分享链接已撤销");
      void invalidateTokens();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const shareUrl = useMemo(
    () => (createdToken ? window.location.origin + cardShareUrl(createdToken.token) : ""),
    [createdToken],
  );

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      toast.success("分享链接已复制");
      window.setTimeout(() => setCopied(false), 1_600);
    } catch {
      toast.error("无法复制，请手动复制链接");
    }
  };

  return (
    <Dialog onOpenChange={(next) => { if (!next) onClose(); }} open={open}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>分享产物 · {card?.title ?? ""}</DialogTitle>
          <DialogDescription>
            {versionId
              ? "生成只读预览链接；打开链接的人无需登录即可查看该版本。"
              : "分享基于不可变发布版本，发布后即可生成只读分享链接。"}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          {extraHeader}

          {!versionId ? (
            <p className="text-sm text-muted-foreground">
              当前选中内容尚未发布。请先发布版本，或在版本列表中选择一个已发布版本。
            </p>
          ) : createdToken ? (
            <div className="grid gap-3 rounded-xl border bg-muted/30 p-4">
              <p className="text-sm font-medium">分享链接已生成</p>
              <div className="flex min-w-0 items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-lg border bg-background px-3 py-2 text-xs">
                  {shareUrl}
                </code>
                <Button onClick={() => void copy()} size="icon" type="button" variant="outline">
                  {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                完整令牌只在本次生成时显示。链接可被随时撤销。
              </p>
              <div className="flex flex-wrap gap-2">
                <Button asChild size="sm" variant="outline">
                  <a href={shareUrl} rel="noreferrer noopener" target="_blank">
                    <ExternalLink className="size-4" />
                    打开链接
                  </a>
                </Button>
                <Button onClick={() => setCreatedToken(null)} size="sm" type="button">
                  完成
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="card-share-label">标签（可选）</Label>
                <Input
                  id="card-share-label"
                  maxLength={120}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="例如：给朋友的路线图"
                  value={label}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="card-share-max-views">查看次数上限（可选）</Label>
                <Input
                  id="card-share-max-views"
                  min={1}
                  onChange={(event) => setMaxViews(event.target.value)}
                  placeholder="留空表示不限次数"
                  type="number"
                  value={maxViews}
                />
              </div>
              <Button
                disabled={createMutation.isPending}
                onClick={() =>
                  createMutation.mutate({
                    label: label.trim(),
                    max_views: maxViews.trim() ? Number(maxViews) : null,
                  })
                }
                type="button"
              >
                {createMutation.isPending ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Link2 className="size-4" />
                )}
                生成分享链接
              </Button>
            </>
          )}

          {versionId ? (
            <div>
              <SectionHeading description="所有令牌只显示前缀，完整链接仅在生成时可见。" title="已有分享链接" />
              {tokens.isPending ? (
                <Skeleton className="mt-3 h-10 w-full" />
              ) : tokens.data?.length === 0 ? (
                <p className="mt-3 text-xs text-muted-foreground">暂无分享链接。</p>
              ) : (
                <div className="mt-3 flex flex-col divide-y rounded-xl border">
                  {tokens.data?.map((token) => (
                    <div
                      className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
                      key={token.id}
                    >
                      <div className="min-w-0">
                        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                          {token.label || token.token_prefix}
                          {token.revoked_at ? (
                            <Badge variant="destructive">已撤销</Badge>
                          ) : token.expires_at && new Date(token.expires_at) < new Date() ? (
                            <Badge variant="secondary">已过期</Badge>
                          ) : (
                            <Badge>有效</Badge>
                          )}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {token.token_prefix}… · 查看 {token.view_count}
                          {token.max_views ? ` / ${token.max_views}` : ""}
                          {token.expires_at ? ` · 过期 ${formatDate(token.expires_at)}` : ""}
                        </p>
                      </div>
                      <Button
                        disabled={Boolean(token.revoked_at) || revokeMutation.isPending}
                        onClick={() => revokeMutation.mutate(token.id)}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        <Trash2 className="size-4" />
                        撤销
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

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
import { useNavigate } from "react-router-dom";
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

export function CardArtifactsPanel({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [sortOrder, setSortOrder] = useState<string>("updated_at");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

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

  const [visibleCount, setVisibleCount] = useState(24);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  const handleKindResolved = useCallback((cardId: string, kind: ArtifactGalleryKind) => {
    setResolvedKinds((prev) => (prev[cardId] === kind ? prev : { ...prev, [cardId]: kind }));
  }, []);

  const params = useMemo(
    () => ({
      status: statusFilter === "all" ? undefined : statusFilter,
      // 组件卡不算页面：服务端排除，避免它挤占 limit 把页面卡挤出结果集。
      exclude_card_type: DECLARATIVE_COMPONENT_CARD_TYPE,
      sort: sortOrder,
      order: "desc",
      limit: 200,
    }),
    [statusFilter, sortOrder],
  );

  const cards = useQuery({
    queryKey: cardQueryKey(workspaceId, params),
    queryFn: () => listArtifactCards(params),
  });

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

  // Progressive reveal: render cards in batches and grow on scroll
  useEffect(() => {
    const total = filteredCards.length;
    if (visibleCount >= total) return;
    const sentinel = sentinelRef.current;
    if (!sentinel || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisibleCount((count) => Math.min(count + 24, total));
        }
      },
      { rootMargin: "320px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [filteredCards.length, visibleCount]);

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
    <div className="grid gap-4">
      {/* Gallery toolbar: Search + Type + Status + Sort + View switcher */}
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="relative min-w-[200px] flex-1 sm:max-w-xs">
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

        <div className="ml-auto flex items-center gap-1.5">
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

      {/* Count summary */}
      {!cards.isPending && !cards.isError ? (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {hasActiveFilter
              ? `匹配产物 · ${filteredCards.length} / ${totalCount}`
              : `全部产物 · ${totalCount}`}
          </span>
          {hasActiveFilter ? (
            <button
              className="text-xs text-foreground/80 underline-offset-2 hover:underline"
              onClick={() => {
                setSearchQuery("");
                setTypeFilter("all");
                setStatusFilter("all");
              }}
              type="button"
            >
              重置筛选
            </button>
          ) : null}
        </div>
      ) : null}

      <div>
        {cards.isPending ? (
          <div className="artifact-card-grid">
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
              在会话中让智能体生成 Magic Card、SVG 图示或学习页面后，会自动收录到这里。
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
          <div className="artifact-card-grid">
            {filteredCards.slice(0, visibleCount).map((card) => {
              const kind = effectiveKindFor(card);
              const kindMeta = artifactKindMeta(kind);
              const source = formatArtifactSource(card);
              const updatedText = formatRelativeUpdateTime(card.updated_at);

              return (
                <ArtifactCardShell
                  card={card}
                  footer={
                    <>
                      <Button
                        className="h-7 gap-1.5 px-2 text-xs font-medium text-foreground/85 hover:text-foreground"
                        onClick={() => openRuntimeOrViewer(card, kind)}
                        size="sm"
                        type="button"
                        variant="ghost"
                      >
                        <PrimaryActionIcon kind={kind} />
                        {kindMeta.actionLabel}
                      </Button>
                      <Button
                        aria-label={`分享 ${card.title}`}
                        className="ml-auto h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                        onClick={() => setShareTarget(card)}
                        size="sm"
                        title="分享与发布"
                        type="button"
                        variant="ghost"
                      >
                        <Share2 className="size-3.5" />
                        分享
                      </Button>
                    </>
                  }
                  headerAction={renderMoreMenu(card)}
                  key={card.id}
                  onActivate={(_id, resolved) => openRuntimeOrViewer(card, resolved ?? kind)}
                  onResolvedKind={handleKindResolved}
                  subtitle={
                    <>
                      <span className="truncate" title={source}>
                        {source}
                      </span>
                      <span aria-hidden="true">·</span>
                      <span className="shrink-0" title={`更新于 ${formatDate(card.updated_at)}`}>
                        {updatedText}
                      </span>
                      {card.status === "published" && card.latest_version > 0 ? (
                        <span className="ml-auto shrink-0 rounded bg-muted px-1.5 py-0.2 text-[10px] font-medium text-muted-foreground">
                          v{card.latest_version}
                          {card.draft_dirty ? "*" : ""}
                        </span>
                      ) : null}
                    </>
                  }
                  workspaceId={workspaceId}
                />
              );
            })}
            {visibleCount < filteredCards.length ? (
              <div
                ref={sentinelRef}
                className="flex items-center justify-center rounded-xl border border-dashed py-6 text-xs text-muted-foreground"
              >
                继续滚动加载更多…
              </div>
            ) : null}
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {filteredCards.slice(0, visibleCount).map((card) => {
              const kind = effectiveKindFor(card);
              const kindMeta = artifactKindMeta(kind);
              const source = formatArtifactSource(card);
              const updatedText = formatRelativeUpdateTime(card.updated_at);

              return (
                <article
                  aria-label={card.title}
                  className="group flex flex-col gap-3 rounded-[12px] border border-[#e5e5e5] bg-background p-2.5 transition-all duration-200 hover:border-[#c8c8cc] hover:shadow-sm sm:flex-row sm:items-center dark:border-border dark:hover:border-border/90"
                  key={card.id}
                >
                  <div
                    className="w-full shrink-0 cursor-pointer sm:w-44"
                    onClick={() => openRuntimeOrViewer(card, kind)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        openRuntimeOrViewer(card, kind);
                      }
                    }}
                  >
                    <CardInlinePreview
                      card={card}
                      kind={kind}
                      onKindResolved={(resolved) => handleKindResolved(card.card_id, resolved)}
                      workspaceId={workspaceId}
                    />
                  </div>
                  <div className="min-w-0 flex-1 space-y-1 px-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-[15px] font-semibold text-foreground">
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
                  <div className="flex shrink-0 items-center gap-1.5 px-1">
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
                      分享
                    </Button>
                    {renderMoreMenu(card)}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

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

  const handleDownload = () => {
    if (!target || !previewHtml) return;
    const isSvg = effectiveKind === "svg" && Boolean(svgMarkup);
    const content = isSvg && svgMarkup ? svgMarkup : previewHtml;
    const mime = isSvg ? "image/svg+xml;charset=utf-8" : "text/html;charset=utf-8";
    const safeName = target.title.replace(/[\\/:*?"<>|]+/g, "_").trim() || target.card_id;
    const filename = isSvg ? `${safeName}.svg` : `${safeName}.html`;
    const blobUrl = URL.createObjectURL(new Blob([content], { type: mime }));
    const anchor = document.createElement("a");
    anchor.href = blobUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 2_000);
  };

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={Boolean(target)}
    >
      <DialogContent
        className="flex h-[86vh] max-h-[880px] w-[94vw] max-w-5xl flex-col gap-3 overflow-hidden p-4 sm:p-5"
        showCloseButton
      >
        <DialogHeader className="shrink-0 pr-8">
          <div className="flex flex-wrap items-center gap-2">
            <ArtifactKindBadge kind={effectiveKind} />
            <DialogTitle className="truncate text-base font-semibold">
              {target?.title ?? "产物运行"}
            </DialogTitle>
            <span className="text-xs text-muted-foreground">
              {artifactKindMeta(effectiveKind).modeLabel}
            </span>
          </div>
          <DialogDescription className="sr-only">
            产物运行与完整预览窗口
          </DialogDescription>
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
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
            {previewHtml ? (
              <Button onClick={handleDownload} size="xs" type="button" variant="outline">
                <Download className="size-3.5" />
                {effectiveKind === "svg" ? "下载 SVG" : "下载 HTML"}
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
                会话
              </Button>
            ) : null}
          </div>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col overflow-auto rounded-lg border bg-muted/20 p-3">
          {preview.isPending ? (
            <Skeleton className="h-full min-h-[360px] w-full" />
          ) : preview.isError ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
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
            <pre className="h-full overflow-auto rounded-md bg-background p-4 font-mono text-xs leading-relaxed text-foreground">
              <code>{svgMarkup}</code>
            </pre>
          ) : effectiveKind === "svg" && pureSvgDataUrl ? (
            <div className="flex h-full w-full items-center justify-center overflow-auto">
              <img
                alt={target?.title ?? "SVG 预览"}
                className="max-h-full max-w-full object-contain transition-transform duration-150"
                src={pureSvgDataUrl}
                style={{ transform: `scale(${zoom})`, transformOrigin: "center center" }}
              />
            </div>
          ) : effectiveKind === "svg" && previewHtml ? (
            <div className="flex h-full w-full items-center justify-center overflow-auto">
              <iframe
                className="h-full w-full rounded-md border-0 bg-white transition-transform duration-150"
                referrerPolicy="no-referrer"
                sandbox=""
                srcDoc={sandboxedHtmlPreviewDocument(previewHtml, { scripts: false })}
                style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}
                title={target?.title ?? "SVG 预览"}
              />
            </div>
          ) : (
            <MagicCardHost
              data={snapshot}
              fillContainer
              hideHeader
              key={`runtime-${target?.card_id}-${target?.updated_at}`}
            />
          )}
        </div>
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

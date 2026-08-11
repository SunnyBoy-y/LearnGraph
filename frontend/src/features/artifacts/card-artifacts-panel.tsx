import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  ExternalLink,
  History,
  LayoutGrid,
  Link2,
  LoaderCircle,
  MessageSquare,
  RefreshCw,
  Rocket,
  Trash2,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

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
} from "@/api/artifacts";
import { FullscreenPreview } from "@/components/chat/fullscreen-preview";
import { MagicCardHost } from "@/components/chat/magic-card-host";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { SectionHeading, Surface } from "@/components/shared/page-elements";
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
import { workspaceQueryKey } from "@/lib/query-keys";
import type {
  ArtifactCard,
  ArtifactCardShareTokenCreated,
} from "@/types/artifacts";

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function cardQueryKey(workspaceId: string, params: Record<string, unknown>) {
  return workspaceQueryKey(workspaceId, "cards", params);
}

function CardTypeBadge({ card }: { card: ArtifactCard }) {
  return (
    <Badge variant={card.interactive ? "default" : "secondary"}>
      {card.interactive ? "双向交互" : "静态页面"}
    </Badge>
  );
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

export function CardArtifactsPanel({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [sortOrder, setSortOrder] = useState<string>("updated_at");
  const [previewCard, setPreviewCard] = useState<ArtifactCard | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ArtifactCard | null>(null);

  const params = useMemo(
    () => ({
      status: statusFilter === "all" ? undefined : statusFilter,
      card_type: typeFilter === "all" ? undefined : typeFilter,
      interactive:
        typeFilter === "interactive" ? true : typeFilter === "static" ? false : undefined,
      sort: sortOrder,
      order: "desc",
      limit: 200,
    }),
    [statusFilter, typeFilter, sortOrder],
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
      toast.success("卡片已删除");
      setDeleteTarget(null);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const openSession = (card: ArtifactCard) => {
    if (card.chat_session_id) {
      navigate(`/w/${workspaceId}/chat/${card.chat_session_id}`);
    } else {
      toast.error("该卡片未关联会话");
    }
  };

  return (
    <div className="grid gap-4">
      <Surface className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">状态</span>
            <Select onValueChange={setStatusFilter} value={statusFilter}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                <SelectItem value="draft">草稿</SelectItem>
                <SelectItem value="published">已发布</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">类型</span>
            <Select onValueChange={setTypeFilter} value={typeFilter}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                <SelectItem value="interactive">双向交互卡</SelectItem>
                <SelectItem value="static">静态页面卡</SelectItem>
                <SelectItem value="magic_card">HTML 页面</SelectItem>
                <SelectItem value="component">声明式组件</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">排序</span>
            <Select onValueChange={setSortOrder} value={sortOrder}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="updated_at">最近更新</SelectItem>
                <SelectItem value="created_at">最近创建</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button
            disabled={cards.isFetching}
            className="ml-auto"
            onClick={() => void cards.refetch()}
            size="sm"
            type="button"
            variant="ghost"
          >
            <RefreshCw className={`size-4 ${cards.isFetching ? "animate-spin" : ""}`} />
            刷新
          </Button>
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading
          description="会话中生成的交互 HTML 页面自动聚合为草稿；发布后生成不可变版本，可切换查看与分享。"
          title="会话卡片"
        />
        {cards.isPending ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : cards.isError ? (
          <p className="mt-4 text-sm text-destructive">
            {cards.error instanceof Error ? cards.error.message : "加载卡片失败"}
          </p>
        ) : cards.data?.length === 0 ? (
          <div className="mt-4 flex flex-col items-center gap-2 rounded-xl border border-dashed p-10 text-center">
            <LayoutGrid className="size-6 text-muted-foreground" />
            <p className="text-sm font-medium">还没有卡片</p>
            <p className="text-xs text-muted-foreground">
              在会话中让智能体生成交互 HTML 页面（magic card / 学习控件），会自动出现在这里。
            </p>
          </div>
        ) : (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {cards.data?.map((card) => (
              <div
                className="group flex flex-col rounded-xl border bg-background p-3 transition-colors hover:border-primary/40"
                key={card.id}
              >
                <button
                  className="flex min-w-0 flex-1 flex-col items-start gap-2 text-left"
                  onClick={() => setPreviewCard(card)}
                  type="button"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <CardTypeBadge card={card} />
                    <StatusBadge card={card} />
                    {card.draft_dirty ? (
                      <Badge variant="destructive">有未发布更新</Badge>
                    ) : null}
                  </div>
                  <span className="line-clamp-2 text-sm font-semibold">{card.title}</span>
                  <span className="text-xs text-muted-foreground">
                    更新于 {formatDate(card.updated_at)}
                  </span>
                </button>
                <div className="mt-3 flex items-center gap-1 border-t pt-2">
                  <Button
                    disabled={!card.chat_session_id}
                    onClick={() => openSession(card)}
                    size="sm"
                    title="跳转到对应会话"
                    type="button"
                    variant="ghost"
                  >
                    <MessageSquare className="size-4" />
                    会话
                  </Button>
                  <Button
                    className="ml-auto"
                    onClick={() => setPreviewCard(card)}
                    size="sm"
                    title="预览 / 版本 / 分享"
                    type="button"
                    variant="ghost"
                  >
                    <Link2 className="size-4" />
                    预览
                  </Button>
                  <Button
                    aria-label={`删除 ${card.title}`}
                    onClick={() => setDeleteTarget(card)}
                    size="icon"
                    title="删除卡片"
                    type="button"
                    variant="ghost"
                  >
                    <Trash2 className="size-4 text-destructive" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Surface>

      <CardPreviewDialog
        card={previewCard}
        onClose={() => setPreviewCard(null)}
        workspaceId={workspaceId}
        onChanged={invalidateCards}
      />

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
              卡片将从产物页移除，但会话中的原始消息不会受影响。已删除卡片被智能体再次生成时会恢复为草稿。
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

function CardPreviewDialog({
  card,
  onClose,
  workspaceId,
  onChanged,
}: {
  card: ArtifactCard | null;
  onClose: () => void;
  workspaceId: string;
  onChanged: () => Promise<void> | void;
}) {
  const navigate = useNavigate();
  // "draft" shows the live draft; a number shows a frozen published snapshot.
  const [selectedVersion, setSelectedVersion] = useState<number | "draft">("draft");
  const [publishOpen, setPublishOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  const versions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "cards", card?.card_id ?? "__none__", "versions"),
    queryFn: () => listArtifactCardVersions(card!.card_id),
    enabled: Boolean(card),
  });

  const preview = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "cards",
      card?.card_id ?? "__none__",
      "preview",
      selectedVersion,
    ),
    queryFn: () =>
      getArtifactCardPreview(card!.card_id, {
        ...(selectedVersion !== "draft" ? { version: selectedVersion } : {}),
      }),
    enabled: Boolean(card),
  });

  const snapshot = preview.data?.preview_snapshot ?? {};
  const isComponent = card?.card_type === "component";
  const title = card?.title ?? "卡片预览";
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
          </DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-3">
            <Select
              onValueChange={(value) =>
                setSelectedVersion(value === "draft" ? "draft" : Number(value))
              }
              value={selectedVersion === "draft" ? "draft" : String(selectedVersion)}
            >
              <SelectTrigger className="w-40">
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
            <span className="text-xs">
              {isComponent ? "声明式组件 · 只读预览" : "交互页面 · 沙箱预览"}
            </span>
          </DialogDescription>
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
          <FullscreenPreview label={title}>
            <MagicCardHost data={snapshot} />
          </FullscreenPreview>
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
                onClick={() => navigate(`/w/${workspaceId}/chat/${card.chat_session_id}`)}
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
        card={card}
        busy={publishMutation.isPending}
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
}: {
  card: ArtifactCard | null;
  open: boolean;
  onClose: () => void;
  versionId: string | undefined;
  workspaceId: string;
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
          <DialogTitle>分享版本 · {card?.title ?? ""}</DialogTitle>
          <DialogDescription>
            {versionId
              ? "生成只读预览链接；打开链接的人无需登录即可查看该版本。"
              : "请先在版本列表中选择一个已发布版本再分享。"}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
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
        </div>
      </DialogContent>
    </Dialog>
  );
}

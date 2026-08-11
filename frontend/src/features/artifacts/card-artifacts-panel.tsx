import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, LayoutGrid, Link2, LoaderCircle, MessageSquare, RefreshCw, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import {
  deleteArtifactCard,
  getArtifactCardPreview,
  listArtifactCards,
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { ArtifactCard } from "@/types/artifacts";

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

function StatusBadge({ status }: { status: ArtifactCard["status"] }) {
  if (status === "published") return <Badge>已发布</Badge>;
  return <Badge variant="outline">草稿</Badge>;
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
      interactive: typeFilter === "interactive" ? true : typeFilter === "static" ? false : undefined,
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
          description="会话中生成的交互 HTML 页面自动聚合为草稿；点击卡片可小窗预览并全屏。"
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
                  <div className="flex min-w-0 items-center gap-1.5">
                    <CardTypeBadge card={card} />
                    <StatusBadge status={card.status} />
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
                    disabled={!card.chat_session_id}
                    onClick={() => setPreviewCard(card)}
                    size="sm"
                    title="预览"
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
}: {
  card: ArtifactCard | null;
  onClose: () => void;
  workspaceId: string;
}) {
  const navigate = useNavigate();
  const preview = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "cards", card?.card_id ?? "__none__", "preview"),
    queryFn: () => getArtifactCardPreview(card!.card_id),
    enabled: Boolean(card),
  });

  const snapshot = preview.data?.preview_snapshot ?? {};
  const isComponent = card?.card_type === "component";
  const title = card?.title ?? "卡片预览";

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={Boolean(card)}
    >
      <DialogContent
        className="flex max-h-[90vh] max-w-3xl flex-col gap-3 overflow-y-auto"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            <History className="size-4" />
            {title}
          </DialogTitle>
          <DialogDescription>
            {isComponent ? "声明式组件 · 只读预览" : "交互页面 · 沙箱预览"}
            {card?.chat_session_id ? " · 双击右上角可全屏" : ""}
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
        {card?.chat_session_id ? (
          <div className="flex justify-end gap-2 border-t pt-3">
            <Button onClick={onClose} size="sm" type="button" variant="ghost">
              关闭
            </Button>
            <Button
              onClick={() => navigate(`/w/${workspaceId}/chat/${card.chat_session_id}`)}
              size="sm"
              type="button"
            >
              <MessageSquare className="size-4" />
              跳转到会话
            </Button>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

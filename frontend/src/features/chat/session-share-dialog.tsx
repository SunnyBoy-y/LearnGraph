import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Check, Copy, Link2, Trash2 } from "lucide-react";

import {
  createSessionShare,
  listSessionShares,
  revokeSessionShare,
  revokeSessionShareToken,
  sessionShareUrl,
  type SessionShareCreate,
  type SessionShareDetailView,
  type SessionShareTokenCreated,
} from "@/api/session-sharing";
import { listSessionMessages } from "@/api/sessions";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import type { Message } from "@/types/sessions";

const SCOPE_OPTIONS: Array<{ value: "full" | "range" | "answers"; label: string; hint: string }> = [
  { value: "full", label: "整段对话", hint: "分享全部消息" },
  { value: "range", label: "区间", hint: "只分享选定的消息范围" },
  { value: "answers", label: "仅回答", hint: "只分享 AI 回答，不含你的提问" },
];

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function messageSummary(message: Message): string {
  const text = (message.content || "").replace(/\s+/g, " ").trim();
  return text.length > 42 ? `${text.slice(0, 42)}…` : text || "(空消息)";
}

export function SessionShareDialog({
  sessionId,
  sessionTitle,
  open,
  onOpenChange,
}: {
  sessionId: string;
  sessionTitle: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [scope, setScope] = useState<"full" | "range" | "answers">("full");
  const [label, setLabel] = useState("");
  const [maxViews, setMaxViews] = useState("");
  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [shares, setShares] = useState<SessionShareDetailView[]>([]);
  const [created, setCreated] = useState<SessionShareTokenCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const reloadShares = useCallback(() => {
    listSessionShares(sessionId)
      .then(setShares)
      .catch(() => setShares([]));
  }, [sessionId]);

  useEffect(() => {
    if (!open) return;
    setCreated(null);
    setCopied(false);
    reloadShares();
  }, [open, reloadShares]);

  useEffect(() => {
    if (!open || scope !== "range") return;
    listSessionMessages(sessionId)
      .then(setMessages)
      .catch(() => setMessages([]));
  }, [open, scope, sessionId]);

  const handleCreate = async () => {
    setBusy(true);
    try {
      const payload: SessionShareCreate = { scope };
      if (scope === "range") {
        if (!fromId || !toId) {
          toast.error("请选择区间的起点和终点消息");
          return;
        }
        payload.from_message_id = fromId;
        payload.to_message_id = toId;
      }
      if (scope === "answers") payload.answers_only = true;
      if (label.trim()) payload.label = label.trim();
      const parsed = Number(maxViews);
      if (Number.isFinite(parsed) && parsed > 0) payload.max_views = parsed;
      const token = await createSessionShare(sessionId, payload);
      setCreated(token);
      toast.success("分享已创建");
      reloadShares();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建分享失败");
    } finally {
      setBusy(false);
    }
  };

  const handleRevokeShare = async (shareId: string) => {
    try {
      await revokeSessionShare(shareId);
      toast.success("分享已撤销");
      reloadShares();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "撤销分享失败");
    }
  };

  const handleRevokeToken = async (shareId: string, tokenId: string) => {
    try {
      await revokeSessionShareToken(shareId, tokenId);
      toast.success("链接已撤销");
      reloadShares();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "撤销链接失败");
    }
  };

  const copyLink = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(
        `${window.location.origin}${sessionShareUrl(created.token)}`,
      );
      setCopied(true);
      toast.success("分享链接已复制");
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("无法复制链接");
    }
  };

  const shareLink = created
    ? `${window.location.origin}${sessionShareUrl(created.token)}`
    : "";

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>分享对话 · {sessionTitle || "未命名会话"}</DialogTitle>
          <DialogDescription>
            分享会生成一个不可变的只读快照，不包含记忆、文件或其他会话内容。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label className="mb-2 block text-xs font-semibold">分享范围</Label>
            <div className="grid gap-2">
              {SCOPE_OPTIONS.map((option) => (
                <button
                  className={
                    "flex items-start gap-3 rounded-lg border p-3 text-left transition-colors " +
                    (scope === option.value
                      ? "border-primary bg-primary/5"
                      : "border-border hover:bg-muted/40")
                  }
                  key={option.value}
                  onClick={() => setScope(option.value)}
                  type="button"
                >
                  <span className="flex-1">
                    <span className="block text-sm font-medium">{option.label}</span>
                    <span className="block text-xs text-muted-foreground">
                      {option.hint}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          {scope === "range" ? (
            <div className="grid gap-3">
              <div>
                <Label htmlFor="share-from">起点消息</Label>
                <select
                  className="mt-1 h-9 w-full rounded-lg border bg-transparent px-3 text-sm"
                  id="share-from"
                  onChange={(event) => setFromId(event.target.value)}
                  value={fromId}
                >
                  <option value="">选择起点</option>
                  {messages.map((message, index) => (
                    <option key={message.id} value={message.id}>
                      #{index + 1} · {message.role === "user" ? "我" : "AI"} ·{" "}
                      {messageSummary(message)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <Label htmlFor="share-to">终点消息</Label>
                <select
                  className="mt-1 h-9 w-full rounded-lg border bg-transparent px-3 text-sm"
                  id="share-to"
                  onChange={(event) => setToId(event.target.value)}
                  value={toId}
                >
                  <option value="">选择终点</option>
                  {messages.map((message, index) => (
                    <option key={message.id} value={message.id}>
                      #{index + 1} · {message.role === "user" ? "我" : "AI"} ·{" "}
                      {messageSummary(message)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="share-label">备注（可选）</Label>
              <Input
                className="mt-1"
                id="share-label"
                maxLength={120}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="例如：给朋友看的重点"
                value={label}
              />
            </div>
            <div>
              <Label htmlFor="share-max-views">查看次数上限（可选）</Label>
              <Input
                className="mt-1"
                id="share-max-views"
                inputMode="numeric"
                onChange={(event) => setMaxViews(event.target.value)}
                placeholder="不限"
                value={maxViews}
              />
            </div>
          </div>

          {created ? (
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-3">
              <p className="text-xs font-semibold text-primary">分享链接已生成</p>
              <p className="mt-1 break-all font-mono text-xs">{shareLink}</p>
              <Button className="mt-2" onClick={() => void copyLink()} size="sm" variant="outline">
                {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                {copied ? "已复制" : "复制链接"}
              </Button>
            </div>
          ) : null}

          {shares.length ? (
            <div className="space-y-2">
              <p className="text-xs font-semibold">已有的分享</p>
              {shares.map((share) => (
                <div className="rounded-lg border p-3" key={share.id}>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">
                      {share.scope === "range"
                        ? "区间"
                        : share.scope === "answers"
                          ? "仅回答"
                          : "整段"}
                    </Badge>
                    <span className="flex-1 text-xs text-muted-foreground">
                      {share.message_count} 条消息 · {formatDate(share.created_at)}
                    </span>
                    {share.status === "revoked" ? (
                      <Badge variant="secondary">已撤销</Badge>
                    ) : (
                      <Button
                        onClick={() => void handleRevokeShare(share.id)}
                        size="xs"
                        variant="ghost"
                      >
                        <Trash2 className="size-3.5" />
                        撤销
                      </Button>
                    )}
                  </div>
                  {share.tokens.length ? (
                    <div className="mt-2 space-y-1">
                      {share.tokens.map((token) => (
                        <div
                          className="flex items-center gap-2 text-xs"
                          key={token.id}
                        >
                          <Link2 className="size-3 text-muted-foreground" />
                          <span className="font-mono text-muted-foreground">
                            {token.token_prefix}…
                          </span>
                          <span className="flex-1 text-muted-foreground">
                            {token.label || "无备注"} · {token.view_count}
                            {token.max_views ? `/${token.max_views}` : ""} 次查看
                          </span>
                          {token.revoked_at ? (
                            <Badge variant="secondary">已撤销</Badge>
                          ) : (
                            <Button
                              onClick={() =>
                                void handleRevokeToken(share.id, token.id)
                              }
                              size="xs"
                              variant="ghost"
                            >
                              撤销
                            </Button>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button disabled={busy} onClick={() => void handleCreate()} type="button">
            {busy ? "创建中…" : "创建分享"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

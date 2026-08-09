import { useMemo, useRef, useState, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  ExternalLink,
  FileText,
  Link2,
  LoaderCircle,
  Package,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import {
  artifactShareUrl,
  createArtifact,
  createArtifactShareToken,
  listArtifactShareTokens,
  listArtifactVersions,
  listArtifacts,
  publishArtifactVersion,
  revokeArtifactShareToken,
} from "@/api/artifacts";
import { listFiles, uploadFile } from "@/api/files";
import {
  PageFrame,
  PageIntro,
  SectionHeading,
  Surface,
} from "@/components/shared/page-elements";
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
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { FileRecord } from "@/types/files";
import type {
  ArtifactShareTokenCreated,
  ArtifactSummary,
  ArtifactVersion,
} from "@/types/artifacts";

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function artifactQueryKey(workspaceId: string) {
  return workspaceQueryKey(workspaceId, "artifacts");
}

function artifactVersionsQueryKey(workspaceId: string, artifactId: string) {
  return workspaceQueryKey(workspaceId, "artifacts", artifactId, "versions");
}

function shareTokensQueryKey(workspaceId: string, versionId: string) {
  return workspaceQueryKey(workspaceId, "artifacts", "versions", versionId, "share-tokens");
}

export function ArtifactsPage() {
  const { workspaceId } = useAuth();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [publishTarget, setPublishTarget] = useState<ArtifactSummary | null>(null);
  const [shareVersion, setShareVersion] = useState<ArtifactVersion | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const artifacts = useQuery({
    queryKey: artifactQueryKey(workspaceId),
    queryFn: listArtifacts,
  });

  const versions = useQuery({
    queryKey: artifactVersionsQueryKey(workspaceId, expandedId ?? "__none__"),
    queryFn: () => listArtifactVersions(expandedId as string),
    enabled: Boolean(expandedId),
  });

  const invalidateArtifacts = () =>
    queryClient.invalidateQueries({ queryKey: artifactQueryKey(workspaceId) });

  const createMutation = useMutation({
    mutationFn: createArtifact,
    onSuccess: async () => {
      await invalidateArtifacts();
      toast.success("产物已创建");
      setCreateOpen(false);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <PageFrame>
      <PageIntro
        actions={
          <Button onClick={() => setCreateOpen(true)} type="button">
            <Plus className="size-4" />
            新建产物
          </Button>
        }
        description="把学习产物发布为不可变版本，并生成可撤销的只读分享链接。"
        eyebrow="Artifacts"
        title="产物与分享"
      />

      <CreateArtifactDialog
        busy={createMutation.isPending}
        onOpenChange={setCreateOpen}
        open={createOpen}
        onSubmit={(payload) => createMutation.mutate(payload)}
      />

      <Surface className="p-5">
        <SectionHeading
          action={
            <Button
              disabled={artifacts.isFetching}
              onClick={() => void artifacts.refetch()}
              size="sm"
              type="button"
              variant="ghost"
            >
              <RefreshCw className={`size-4 ${artifacts.isFetching ? "animate-spin" : ""}`} />
              刷新
            </Button>
          }
          description="每个产物可以发布多个不可变版本，分享令牌只作用于单个版本。"
          title="工作区产物"
        />

        {artifacts.isPending ? (
          <div className="mt-4 grid gap-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : artifacts.isError ? (
          <p className="mt-4 text-sm text-destructive">
            {artifacts.error instanceof Error ? artifacts.error.message : "加载产物失败"}
          </p>
        ) : artifacts.data?.length === 0 ? (
          <div className="mt-4 flex flex-col items-center gap-2 rounded-xl border border-dashed p-8 text-center">
            <Package className="size-6 text-muted-foreground" />
            <p className="text-sm font-medium">还没有产物</p>
            <p className="text-xs text-muted-foreground">
              创建产物后，可以从工作区文件发布不可变版本。
            </p>
          </div>
        ) : (
          <div className="mt-4 flex flex-col divide-y rounded-xl border">
            {artifacts.data?.map((artifact) => (
              <ArtifactRow
                artifact={artifact}
                expanded={expandedId === artifact.id}
                key={artifact.id}
                onExpand={() =>
                  setExpandedId((current) => (current === artifact.id ? null : artifact.id))
                }
                onPublish={() => setPublishTarget(artifact)}
                versions={versions.data}
                versionsPending={versions.isPending && expandedId === artifact.id}
                onShare={setShareVersion}
              />
            ))}
          </div>
        )}
      </Surface>

      <PublishVersionDialog
        artifact={publishTarget}
        onClose={() => setPublishTarget(null)}
        onPublished={async () => {
          await invalidateArtifacts();
          if (publishTarget) {
            await queryClient.invalidateQueries({
              queryKey: artifactVersionsQueryKey(workspaceId, publishTarget.id),
            });
          }
          setPublishTarget(null);
        }}
        workspaceId={workspaceId}
      />

      {shareVersion ? (
        <ShareVersionDialog
          onClose={() => setShareVersion(null)}
          version={shareVersion}
          workspaceId={workspaceId}
        />
      ) : null}
    </PageFrame>
  );
}

function ArtifactRow({
  artifact,
  expanded,
  onExpand,
  onPublish,
  versions,
  versionsPending,
  onShare,
}: {
  artifact: ArtifactSummary;
  expanded: boolean;
  onExpand: () => void;
  onPublish: () => void;
  versions?: ArtifactVersion[];
  versionsPending: boolean;
  onShare: (version: ArtifactVersion) => void;
}) {
  return (
    <div>
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <button
          className="flex min-w-0 flex-1 items-start gap-3 text-left"
          onClick={onExpand}
          type="button"
        >
          <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <Package className="size-4" />
          </span>
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className="truncate text-sm font-semibold">{artifact.name}</span>
              <Badge variant="secondary">{artifact.version_count} 个版本</Badge>
            </span>
            <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">
              {artifact.description || "无描述"}
            </span>
            <span className="mt-1 block text-xs text-muted-foreground">
              创建于 {formatDate(artifact.created_at)}
            </span>
          </span>
        </button>
        <Button onClick={onPublish} size="sm" type="button" variant="outline">
          <Plus className="size-4" />
          发布版本
        </Button>
      </div>

      {expanded ? (
        <div className="border-t bg-muted/20 p-4">
          {versionsPending ? (
            <Skeleton className="h-12 w-full" />
          ) : versions?.length === 0 ? (
            <p className="text-xs text-muted-foreground">尚未发布版本。</p>
          ) : (
            <div className="flex flex-col divide-y rounded-xl border bg-background">
              {versions?.map((version) => (
                <div
                  className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between"
                  key={version.id}
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                      <FileText className="size-4" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium">
                        v{version.version} · {version.original_name}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {formatBytes(version.size_bytes)} · {formatDate(version.created_at)}
                      </p>
                      {version.release_notes ? (
                        <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                          {version.release_notes}
                        </p>
                      ) : null}
                    </div>
                  </div>
                  <Button onClick={() => onShare(version)} size="sm" type="button" variant="outline">
                    <Link2 className="size-4" />
                    分享
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function CreateArtifactDialog({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: { name: string; description: string }) => void;
  busy: boolean;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  return (
    <Dialog onOpenChange={(next) => (next ? undefined : onOpenChange(false))} open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建产物</DialogTitle>
          <DialogDescription>产物是工作区内可复用发布物的逻辑集合。</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) onSubmit({ name: name.trim(), description: description.trim() });
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="artifact-name">名称</Label>
            <Input
              autoFocus
              id="artifact-name"
              maxLength={240}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：FastAPI 入门路线图"
              required
              value={name}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="artifact-description">描述</Label>
            <Textarea
              id="artifact-description"
              maxLength={2000}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="这个产物包含什么，适合谁使用"
              rows={3}
              value={description}
            />
          </div>
          <DialogFooter>
            <Button
              disabled={busy || !name.trim()}
              onClick={() => onOpenChange(false)}
              type="button"
              variant="ghost"
            >
              取消
            </Button>
            <Button disabled={busy || !name.trim()} type="submit">
              {busy ? <LoaderCircle className="size-4 animate-spin" /> : <Plus className="size-4" />}
              创建
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PublishVersionDialog({
  artifact,
  onClose,
  onPublished,
  workspaceId,
}: {
  artifact: ArtifactSummary | null;
  onClose: () => void;
  onPublished: () => Promise<void>;
  workspaceId: string;
}) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedFileId, setSelectedFileId] = useState<string>("");
  const [releaseNotes, setReleaseNotes] = useState("");

  const files = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "files"),
    queryFn: () => listFiles(),
    enabled: Boolean(artifact),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadFile(file),
    onSuccess: (file) => {
      setSelectedFileId(file.id);
      queryClient.setQueryData<FileRecord[]>(
        workspaceQueryKey(workspaceId, "files"),
        (current) => [file, ...(current ?? [])],
      );
      toast.success("文件已上传");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const publishMutation = useMutation({
    mutationFn: (payload: { file_id: string; release_notes: string }) =>
      publishArtifactVersion(artifact!.id, payload),
    onSuccess: async () => {
      toast.success("版本已发布");
      await onPublished();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) uploadMutation.mutate(file);
    event.target.value = "";
  };

  const canSubmit = Boolean(artifact && selectedFileId && !publishMutation.isPending);

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={Boolean(artifact)}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>发布版本 · {artifact?.name ?? ""}</DialogTitle>
          <DialogDescription>选择一个工作区文件作为不可变版本内容。</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (artifact && selectedFileId) {
              publishMutation.mutate({
                file_id: selectedFileId,
                release_notes: releaseNotes.trim(),
              });
            }
          }}
        >
          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-3">
              <Label>来源文件</Label>
              <Button
                disabled={uploadMutation.isPending}
                onClick={() => fileInputRef.current?.click()}
                size="sm"
                type="button"
                variant="outline"
              >
                {uploadMutation.isPending ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Upload className="size-4" />
                )}
                上传新文件
              </Button>
              <input
                className="hidden"
                onChange={handleFileChange}
                ref={fileInputRef}
                type="file"
              />
            </div>
            <Select onValueChange={setSelectedFileId} value={selectedFileId || undefined}>
              <SelectTrigger>
                <SelectValue placeholder={files.isPending ? "加载文件中…" : "选择工作区文件"} />
              </SelectTrigger>
              <SelectContent>
                {files.data?.map((file) => (
                  <SelectItem key={file.id} value={file.id}>
                    {file.original_name} · {formatBytes(file.size_bytes)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {files.data?.length === 0 && !files.isPending ? (
              <p className="text-xs text-muted-foreground">
                工作区暂无文件，请先上传一个文件。
              </p>
            ) : null}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="release-notes">版本说明</Label>
            <Textarea
              id="release-notes"
              maxLength={4000}
              onChange={(event) => setReleaseNotes(event.target.value)}
              placeholder="这个版本包含什么变化"
              rows={3}
              value={releaseNotes}
            />
          </div>
          <DialogFooter>
            <Button disabled={publishMutation.isPending} onClick={onClose} type="button" variant="ghost">
              取消
            </Button>
            <Button disabled={!canSubmit} type="submit">
              {publishMutation.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Package className="size-4" />
              )}
              发布
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ShareVersionDialog({
  version,
  workspaceId,
  onClose,
}: {
  version: ArtifactVersion;
  workspaceId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [maxDownloads, setMaxDownloads] = useState("");
  const [createdToken, setCreatedToken] = useState<ArtifactShareTokenCreated | null>(null);

  const tokens = useQuery({
    queryKey: shareTokensQueryKey(workspaceId, version.id),
    queryFn: () => listArtifactShareTokens(version.id),
  });

  const invalidateTokens = () =>
    queryClient.invalidateQueries({
      queryKey: shareTokensQueryKey(workspaceId, version.id),
    });

  const createTokenMutation = useMutation({
    mutationFn: (payload: {
      label?: string;
      expires_at?: string | null;
      max_downloads?: number | null;
    }) => createArtifactShareToken(version.id, payload),
    onSuccess: (token) => {
      setCreatedToken(token);
      void invalidateTokens();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const revokeMutation = useMutation({
    mutationFn: revokeArtifactShareToken,
    onSuccess: () => {
      toast.success("分享链接已撤销");
      void invalidateTokens();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <Dialog onOpenChange={(next) => { if (!next) onClose(); }} open>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>分享版本 v{version.version}</DialogTitle>
          <DialogDescription>
            {version.original_name} · {formatBytes(version.size_bytes)} · 只读下载
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          {createdToken ? (
            <ShareLinkCreatedPanel
              onDone={() => {
                setCreatedToken(null);
                onClose();
              }}
              token={createdToken}
            />
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="share-label">标签（可选）</Label>
                <Input
                  id="share-label"
                  maxLength={120}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="例如：给朋友的路线图"
                  value={label}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="max-downloads">下载次数上限（可选）</Label>
                <Input
                  id="max-downloads"
                  min={1}
                  onChange={(event) => setMaxDownloads(event.target.value)}
                  placeholder="留空表示不限次数"
                  type="number"
                  value={maxDownloads}
                />
              </div>
              <Button
                disabled={createTokenMutation.isPending}
                onClick={() =>
                  createTokenMutation.mutate({
                    label: label.trim(),
                    max_downloads: maxDownloads.trim() ? Number(maxDownloads) : null,
                    expires_at: null,
                  })
                }
                type="button"
              >
                {createTokenMutation.isPending ? (
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
                        {token.token_prefix}… · 下载 {token.download_count}
                        {token.max_downloads ? ` / ${token.max_downloads}` : ""}
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

function ShareLinkCreatedPanel({
  token,
  onDone,
}: {
  token: ArtifactShareTokenCreated;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const shareUrl = useMemo(() => window.location.origin + artifactShareUrl(token.token), [token.token]);

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
        <Button onClick={onDone} size="sm" type="button">
          完成
        </Button>
      </div>
    </div>
  );
}

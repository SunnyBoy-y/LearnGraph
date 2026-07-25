import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitCompareArrows, History, Undo2 } from "lucide-react";
import { toast } from "sonner";

import {
  decideNodeMerge,
  listGraphRevisions,
  listNodeMerges,
  previewNodeMerge,
  undoNodeMerge,
} from "@/api";
import { StatePill } from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { Graph, NodeMergeAction, NodeMergePreview } from "@/types/graphs";

type GraphReviewDialogProps = {
  graph: Graph;
  onOpenChange: (open: boolean) => void;
  open: boolean;
};

function JsonDetails({ label, value }: { label: string; value: Record<string, unknown> }) {
  return (
    <details className="rounded-lg border bg-muted/20 px-3 py-2 text-xs">
      <summary className="cursor-pointer font-medium text-muted-foreground">
        {label}
      </summary>
      <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-foreground">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function formattedDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function GraphReviewDialog({
  graph,
  onOpenChange,
  open,
}: GraphReviewDialogProps) {
  const queryClient = useQueryClient();
  const [sourceNodeId, setSourceNodeId] = useState("");
  const [targetNodeId, setTargetNodeId] = useState("");
  const [preview, setPreview] = useState<NodeMergePreview>();
  const [rationale, setRationale] = useState("");
  const [mergeConfirmed, setMergeConfirmed] = useState(false);
  const revisions = useQuery({
    queryKey: ["graph-revisions", graph.id],
    queryFn: () => listGraphRevisions(graph.id),
    enabled: open,
  });
  const merges = useQuery({
    queryKey: ["node-merges"],
    queryFn: listNodeMerges,
    enabled: open,
  });

  useEffect(() => {
    setSourceNodeId(graph.nodes[0]?.id ?? "");
    setTargetNodeId(graph.nodes[1]?.id ?? "");
    setPreview(undefined);
    setRationale("");
    setMergeConfirmed(false);
  }, [graph.id, graph.nodes]);

  const previewMutation = useMutation({
    mutationFn: () => previewNodeMerge({ source_node_id: sourceNodeId, target_node_id: targetNodeId }),
    onSuccess: (result) => {
      setPreview(result);
      setMergeConfirmed(false);
    },
    onError: (error) => toast.error(error.message),
  });
  const decide = useMutation({
    mutationFn: (action: NodeMergeAction) =>
      decideNodeMerge({
        source_node_id: sourceNodeId,
        target_node_id: targetNodeId,
        action,
        rationale: rationale.trim(),
        user_confirmed: action === "merge" ? mergeConfirmed : true,
      }),
    onSuccess: async (record, action) => {
      toast.success(
        action === "merge"
          ? "节点合并决策已保存，可在此撤销"
          : "节点关系决策已保存，可在此撤销",
      );
      setPreview(undefined);
      setRationale("");
      setMergeConfirmed(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["node-merges"] }),
        queryClient.invalidateQueries({ queryKey: ["graph-revisions", graph.id] }),
        queryClient.invalidateQueries({ queryKey: ["graph", graph.id] }),
      ]);
      return record;
    },
    onError: (error) => toast.error(error.message),
  });
  const undo = useMutation({
    mutationFn: undoNodeMerge,
    onSuccess: async () => {
      toast.success("节点关系决策已撤销");
      await queryClient.invalidateQueries({ queryKey: ["node-merges"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const graphNodeIds = new Set(nodeById.keys());
  const relevantMerges = (merges.data ?? []).filter(
    (item) => graphNodeIds.has(item.source_node_id) || graphNodeIds.has(item.target_node_id),
  );
  const validPair = Boolean(sourceNodeId && targetNodeId && sourceNodeId !== targetNodeId);
  const matchingPreview =
    preview?.source_node_id === sourceNodeId &&
    preview.target_node_id === targetNodeId
      ? preview
      : undefined;
  const sourceLabel = nodeById.get(sourceNodeId)?.label ?? "未选择";
  const targetLabel = nodeById.get(targetNodeId)?.label ?? "未选择";

  function changeSource(nodeId: string) {
    setSourceNodeId(nodeId);
    if (nodeId === targetNodeId) {
      setTargetNodeId(graph.nodes.find((node) => node.id !== nodeId)?.id ?? "");
    }
    setPreview(undefined);
    setMergeConfirmed(false);
  }

  function changeTarget(nodeId: string) {
    setTargetNodeId(nodeId);
    if (nodeId === sourceNodeId) {
      setSourceNodeId(graph.nodes.find((node) => node.id !== nodeId)?.id ?? "");
    }
    setPreview(undefined);
    setMergeConfirmed(false);
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setPreview(undefined);
      setRationale("");
      setMergeConfirmed(false);
    }
    onOpenChange(nextOpen);
  }

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>图谱合并审核与修订历史</DialogTitle>
          <DialogDescription>
            合并在这里是可追溯、可撤销的节点关系决策，不会绕过审核静默改写目标图谱。
          </DialogDescription>
        </DialogHeader>

        <section aria-label="节点合并审核" className="space-y-4 rounded-xl border p-4">
          <div className="flex items-center gap-2">
            <GitCompareArrows className="size-4 text-primary" />
            <h3 className="font-medium">比较两个节点</h3>
          </div>
          {graph.nodes.length < 2 ? (
            <p className="text-sm text-muted-foreground">至少需要两个节点才能创建关系决策。</p>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-1.5">
                  <Label htmlFor="merge-source-node">节点 A</Label>
                  <Select onValueChange={changeSource} value={sourceNodeId}>
                    <SelectTrigger id="merge-source-node">
                      <SelectValue placeholder="选择第一个节点" />
                    </SelectTrigger>
                    <SelectContent>
                      {graph.nodes.map((node) => (
                        <SelectItem key={node.id} value={node.id}>
                          {node.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="merge-target-node">节点 B</Label>
                  <Select onValueChange={changeTarget} value={targetNodeId}>
                    <SelectTrigger id="merge-target-node">
                      <SelectValue placeholder="选择第二个节点" />
                    </SelectTrigger>
                    <SelectContent>
                      {graph.nodes.map((node) => (
                        <SelectItem disabled={node.id === sourceNodeId} key={node.id} value={node.id}>
                          {node.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <Textarea
                aria-label="节点关系决策说明"
                maxLength={2000}
                onChange={(event) => setRationale(event.target.value)}
                placeholder="可补充人工审核依据；空白时保存服务端预览说明。"
                value={rationale}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={!validPair || previewMutation.isPending || decide.isPending}
                  onClick={() => previewMutation.mutate()}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  {previewMutation.isPending ? "正在分析…" : "获取审核预览"}
                </Button>
                <Button
                  disabled={!validPair || decide.isPending}
                  onClick={() => decide.mutate("related")}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  仅标记有关联
                </Button>
                <Button
                  disabled={!validPair || decide.isPending}
                  onClick={() => decide.mutate("do_not_merge")}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  保持分开
                </Button>
              </div>
              {previewMutation.error ? (
                <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
                  无法生成审核预览：{previewMutation.error.message}
                </p>
              ) : null}
              {matchingPreview ? (
                <div className="space-y-3 rounded-xl border border-primary/20 bg-primary/[.03] p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatePill label={matchingPreview.recommendation} status={matchingPreview.recommendation === "merge" ? "approved" : "reviewing"} />
                    <Badge variant="secondary">判断：{matchingPreview.decision}</Badge>
                    <Badge variant="secondary">Provider：{matchingPreview.provider}</Badge>
                  </div>
                  <p className="text-sm leading-6">{matchingPreview.rationale}</p>
                  <JsonDetails label="查看服务端证据" value={matchingPreview.evidence} />
                  <label className="flex cursor-pointer items-start gap-2 rounded-lg border bg-background p-3 text-sm">
                    <Checkbox
                      checked={mergeConfirmed}
                      onCheckedChange={(checked) => setMergeConfirmed(checked === true)}
                    />
                    <span>
                      我已审阅「{sourceLabel}」与「{targetLabel}」的证据，并确认将它们标记为相同概念。
                    </span>
                  </label>
                  <Button
                    disabled={
                      decide.isPending ||
                      matchingPreview.recommendation === "do_not_merge" ||
                      !mergeConfirmed
                    }
                    onClick={() => decide.mutate("merge")}
                    size="sm"
                    type="button"
                  >
                    {decide.isPending ? "保存中…" : "确认相同并保存决策"}
                  </Button>
                  {matchingPreview.recommendation === "do_not_merge" ? (
                    <p className="text-xs text-muted-foreground">
                      服务端规则或现有策略阻止合并；可以保留独立节点或只标记关联。
                    </p>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </section>

        <section aria-label="当前图谱修订历史" className="space-y-3 rounded-xl border p-4">
          <div className="flex items-center gap-2">
            <History className="size-4 text-primary" />
            <h3 className="font-medium">当前图谱修订历史</h3>
          </div>
          {revisions.isPending ? <p className="text-sm text-muted-foreground">正在读取修订历史…</p> : null}
          {revisions.isError ? (
            <p className="text-sm text-destructive" role="alert">无法读取修订历史：{revisions.error.message}</p>
          ) : null}
          {!revisions.isPending && !revisions.isError && !revisions.data?.length ? (
            <p className="text-sm text-muted-foreground">服务端尚未记录此图谱的修订。</p>
          ) : null}
          <ol className="space-y-3">
            {(revisions.data ?? []).map((revision) => (
              <li className="rounded-lg border p-3" key={revision.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">修订 {revision.revision}</Badge>
                  <StatePill status={revision.change_type} />
                  <span className="ml-auto text-xs text-muted-foreground">{formattedDate(revision.created_at)}</span>
                </div>
                <p className="mt-2 font-mono text-[11px] text-muted-foreground">资源：{revision.resource_id}</p>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <JsonDetails label="变更前" value={revision.before} />
                  <JsonDetails label="变更后" value={revision.after} />
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section aria-label="当前图谱节点关系决策" className="space-y-3 rounded-xl border p-4">
          <div className="flex items-center gap-2">
            <GitCompareArrows className="size-4 text-primary" />
            <h3 className="font-medium">已保存的节点关系决策</h3>
          </div>
          {merges.isPending ? <p className="text-sm text-muted-foreground">正在读取节点关系决策…</p> : null}
          {merges.isError ? (
            <p className="text-sm text-destructive" role="alert">无法读取节点关系决策：{merges.error.message}</p>
          ) : null}
          {!merges.isPending && !merges.isError && !relevantMerges.length ? (
            <p className="text-sm text-muted-foreground">尚未为当前图谱保存节点关系决策。</p>
          ) : null}
          <ol className="space-y-3">
            {relevantMerges.map((record) => (
              <li className="rounded-lg border p-3" key={record.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <StatePill status={record.status} />
                  {record.reverted_at ? <Badge variant="secondary">已于 {formattedDate(record.reverted_at)} 撤销</Badge> : null}
                  <span className="ml-auto text-xs text-muted-foreground">{formattedDate(record.created_at)}</span>
                </div>
                <p className="mt-2 text-sm">
                  {nodeById.get(record.source_node_id)?.label ?? record.source_node_id}
                  <span className="px-2 text-muted-foreground">↔</span>
                  {nodeById.get(record.target_node_id)?.label ?? record.target_node_id}
                </p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{record.rationale}</p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">人工决策：{record.decision_source}</Badge>
                  {!record.reverted_at ? (
                    <Button
                      disabled={undo.isPending}
                      onClick={() => undo.mutate(record.id)}
                      size="xs"
                      type="button"
                      variant="outline"
                    >
                      <Undo2 className="size-3.5" />
                      {undo.isPending ? "撤销中…" : "撤销决策"}
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </section>
      </DialogContent>
    </Dialog>
  );
}

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2 } from "lucide-react";
import { toast } from "sonner";

import {
  createSourceLink,
  getGraph,
  listGoals,
  listGraphs,
  listProjects,
} from "@/api";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { SourceRecord, SourceTargetType } from "@/types/workflow";

type SourceAssociationDialogProps = {
  onLinked?: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  requireAssociation?: boolean;
  source: SourceRecord;
};

export function SourceAssociationDialog({
  onLinked,
  onOpenChange,
  open,
  requireAssociation = false,
  source,
}: SourceAssociationDialogProps) {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState("");
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => listProjects(),
    enabled: open,
  });
  const goals = useQuery({
    queryKey: ["goals"],
    queryFn: listGoals,
    enabled: open,
  });
  const graphs = useQuery({
    queryKey: ["graphs"],
    queryFn: listGraphs,
    enabled: open,
  });
  const nodes = useQuery({
    queryKey: ["source-link-nodes", graphs.data?.map((graph) => graph.id)],
    enabled: open && Boolean(graphs.data?.length),
    queryFn: async () =>
      (
        await Promise.all(
          (graphs.data ?? []).map((graph) => getGraph(graph.id)),
        )
      ).flatMap((graph) => graph.nodes),
  });
  const targetCount = useMemo(
    () =>
      (projects.data?.length ?? 0) +
      (goals.data?.length ?? 0) +
      (graphs.data?.length ?? 0) +
      (nodes.data?.length ?? 0),
    [goals.data, graphs.data, nodes.data, projects.data],
  );
  const nodesRequired = Boolean(graphs.data?.length);
  const targetsLoading =
    projects.isPending ||
    goals.isPending ||
    graphs.isPending ||
    (nodesRequired && nodes.isPending);
  const targetsError =
    projects.error ??
    goals.error ??
    graphs.error ??
    (nodesRequired ? nodes.error : null);
  const link = useMutation({
    mutationFn: () => {
      const [targetType, targetId] = target.split(":", 2) as [
        SourceTargetType,
        string,
      ];
      if (!targetType || !targetId) throw new Error("请选择关联目标");
      return createSourceLink(source.id, targetType, targetId);
    },
    onSuccess: async () => {
      toast.success("资料关联已保存");
      setTarget("");
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["source-links", source.id],
        }),
        queryClient.invalidateQueries({ queryKey: ["source-records"] }),
      ]);
      onLinked?.();
      onOpenChange(false);
    },
    onError: (error) => toast.error(error.message),
  });

  useEffect(() => {
    if (open) setTarget("");
  }, [open, source.id]);

  const preventRequiredDismissal = (event: Event) => {
    if (requireAssociation) event.preventDefault();
  };

  return (
    <Dialog
      onOpenChange={(nextOpen) => {
        if (!nextOpen && requireAssociation) return;
        onOpenChange(nextOpen);
      }}
      open={open}
    >
      <DialogContent
        onEscapeKeyDown={preventRequiredDismissal}
        onInteractOutside={preventRequiredDismissal}
        showCloseButton={!requireAssociation}
      >
        <DialogHeader>
          <DialogTitle>关联到…</DialogTitle>
          <DialogDescription>
            “{source.title || source.final_url}”已保存到资料库。请选择当前工作区内的
            Project、Goal、Graph 或 Node；关联失败不会删除已保存的来源。
          </DialogDescription>
        </DialogHeader>

        {targetsError ? (
          <p
            className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
            role="alert"
          >
            无法读取可关联目标：{targetsError.message}
          </p>
        ) : null}
        {targetsLoading ? (
          <p className="text-sm text-muted-foreground">
            正在读取当前工作区可关联目标…
          </p>
        ) : null}
        {!targetsLoading && !targetsError && targetCount === 0 ? (
          <p className="rounded-lg border border-amber-200 bg-amber-50/55 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200">
            当前没有可关联目标。来源已安全保存；请先创建 Project、Goal 或 Graph，随后可在“资料上传与解析中心”的来源详情中重试关联或删除。
          </p>
        ) : null}
        {!targetsLoading && !targetsError && targetCount > 0 ? (
          <Select onValueChange={setTarget} value={target}>
            <SelectTrigger>
              <SelectValue placeholder="选择关联目标" />
            </SelectTrigger>
            <SelectContent>
              {projects.data?.map((item) => (
                <SelectItem key={`project:${item.id}`} value={`project:${item.id}`}>
                  Project · {item.title}
                </SelectItem>
              ))}
              {goals.data?.map((item) => (
                <SelectItem key={`goal:${item.id}`} value={`goal:${item.id}`}>
                  Goal · {item.title}
                </SelectItem>
              ))}
              {graphs.data?.map((item) => (
                <SelectItem key={`graph:${item.id}`} value={`graph:${item.id}`}>
                  Graph · {item.title}
                </SelectItem>
              ))}
              {nodes.data?.map((item) => (
                <SelectItem key={`node:${item.id}`} value={`node:${item.id}`}>
                  Node · {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}

        <DialogFooter>
          {requireAssociation &&
          !targetsLoading &&
          (Boolean(targetsError) || targetCount === 0) ? (
            <Button onClick={() => onOpenChange(false)} type="button" variant="outline">
              稍后在资料库关联
            </Button>
          ) : null}
          <Button
            disabled={!target || link.isPending || targetsLoading || Boolean(targetsError)}
            onClick={() => link.mutate()}
            type="button"
          >
            <Link2 className="size-4" />
            {link.isPending ? "关联中…" : "保存关联"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

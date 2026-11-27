import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { linkFileToGraph, listFileReferences, listGraphs } from "@/api";
import { Button } from "@/components/ui/button";
import { toast } from "@/lib/notifications";
import { workspaceQueryKey } from "@/lib/query-keys";

export function FileGraphLinks({ fileId }: { fileId: string }) {
  const { workspaceId = "" } = useParams();
  const client = useQueryClient();
  const [graphId, setGraphId] = useState("");
  const [expanded, setExpanded] = useState(false);
  const graphKey = workspaceQueryKey(workspaceId, "graphs");
  const referenceKey = ["file-references", fileId];
  const graphs = useQuery({ queryKey: graphKey, queryFn: listGraphs, enabled: expanded });
  const refs = useQuery({ queryKey: referenceKey, queryFn: () => listFileReferences(fileId), enabled: expanded });
  const link = useMutation({
    mutationFn: () => linkFileToGraph(fileId, graphId),
    onSuccess: async () => {
      setGraphId("");
      toast.success("已关联学习图谱");
      await client.invalidateQueries({ queryKey: referenceKey });
    },
    onError: (error) => toast.error(error.message),
  });
  const linkedIds = new Set(refs.data?.filter((ref) => ref.target_type === "graph").map((ref) => ref.target_id));
  const available = graphs.data?.filter((graph) => !linkedIds.has(graph.id)) ?? [];
  const linked = graphs.data?.filter((graph) => linkedIds.has(graph.id)) ?? [];

  return (
    <div className="space-y-2">
      <Button aria-expanded={expanded} size="sm" variant="outline" onClick={() => setExpanded(!expanded)}>关联学习图谱</Button>
      {expanded ? (
        <div className="space-y-2 rounded-lg border p-3 text-sm">
          {graphs.isError || refs.isError ? <p role="alert">无法加载图谱关联。</p> : graphs.isPending || refs.isPending ? <p role="status">正在加载图谱…</p> : (
            <>
              {linked.map((graph) => <Link className="block break-words hover:underline" key={graph.id} to={`../graphs/${graph.id}`}>{graph.title} ›</Link>)}
              {available.length ? (
                <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); if (graphId && !link.isPending) link.mutate(); }}>
                  <select aria-label="选择学习图谱" className="h-8 min-w-32 flex-1 rounded-md border bg-background px-2" value={graphId} onChange={(event) => setGraphId(event.target.value)}>
                    <option value="">选择图谱</option>
                    {available.map((graph) => <option key={graph.id} value={graph.id}>{graph.title}</option>)}
                  </select>
                  <Button disabled={!graphId || link.isPending} size="sm" type="submit">关联</Button>
                </form>
              ) : <p className="text-muted-foreground">没有其他可关联的图谱。</p>}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

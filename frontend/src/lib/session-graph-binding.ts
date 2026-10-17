import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { getGraph, listGraphs, listSessions, updateSession } from "@/api";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { GraphSummary } from "@/types/graphs";
import type { Session, SessionUpdateRequest } from "@/types/sessions";

export type LearningNodeContext = {
  graphId: string;
  nodeId?: string;
  nodeLabel?: string;
  sessionId?: string;
  learningPackage?: boolean;
  openKey?: string;
};

export function publishLearningNodeContext(context: LearningNodeContext) {
  try {
    window.sessionStorage.setItem("learngraph:active-learning-node", JSON.stringify(context));
  } catch {
    // The event still updates the current chat when storage is unavailable.
  }
  window.dispatchEvent(new CustomEvent("learngraph:learning-node-selected", { detail: context }));
}

/** The menu and rail share persisted state and a per-session mutation lock. */
export function useSessionGraphBinding(workspaceId: string, sessionId?: string) {
  const queryClient = useQueryClient();
  const sessionsKey = workspaceQueryKey(workspaceId, "sessions");
  const mutationKey = workspaceQueryKey(workspaceId, "session-graph-binding", sessionId ?? "new");
  const sessions = useQuery({ queryKey: sessionsKey, queryFn: listSessions, staleTime: 30_000 });
  const session = sessions.data?.find((item) => item.id === sessionId);
  const graphs = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graphs"), queryFn: listGraphs, staleTime: 30_000,
  });
  const graph = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graph", session?.graph_id ?? "unbound"),
    queryFn: () => getGraph(session!.graph_id!),
    enabled: Boolean(session?.graph_id), staleTime: 30_000,
  });
  const binding = session?.graph_id ? {
    graphId: session.graph_id,
    graphTitle: graph.data?.title ?? graphs.data?.find((item) => item.id === session.graph_id)?.title ?? "当前会话图谱",
  } : undefined;
  const boundNodeId = session?.current_node_id ?? null;
  const boundNode = graph.data?.nodes.find((node) => node.id === boundNodeId);
  const bindingPending = useIsMutating({ mutationKey }) > 0;
  const mutation = useMutation({
    mutationKey,
    onMutate: () => queryClient.cancelQueries({ queryKey: sessionsKey }),
    mutationFn: (payload: SessionUpdateRequest) => updateSession(sessionId!, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData<Session[]>(sessionsKey, (current) =>
        current?.map((item) => item.id === updated.id ? updated : item),
      );
    },
  });

  function latestSession() {
    return queryClient.getQueryData<Session[]>(sessionsKey)?.find((item) => item.id === sessionId);
  }

  async function commit(payload: SessionUpdateRequest, success: string) {
    if (!sessionId || !latestSession() || queryClient.isMutating({ mutationKey })) return false;
    try {
      await mutation.mutateAsync(payload);
      toast.success(success);
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "绑定更新失败");
      return false;
    }
  }

  function clearNodeContext() {
    window.dispatchEvent(new CustomEvent("learngraph:learning-node-clear", { detail: { sessionId, persisted: true } }));
  }

  async function bindGraph(next: GraphSummary) {
    const current = latestSession();
    if (!current) return false;
    if (current.graph_id) {
      toast.message("请先解绑当前图谱，再选择其他图谱");
      return false;
    }
    if (next.status !== "published") {
      toast.error("当前会话只能绑定已发布的图谱");
      return false;
    }
    if (current.goal_id && current.goal_id !== next.goal_id) {
      toast.error("该图谱与当前会话的学习目标不一致");
      return false;
    }
    const saved = await commit({ graph_id: next.id, goal_id: current.goal_id ?? next.goal_id, current_node_id: null }, `当前会话已绑定「${next.title}」`);
    if (saved) clearNodeContext();
    return saved;
  }

  async function clearGraph() {
    if (!latestSession()?.graph_id) return false;
    const saved = await commit({ graph_id: null, current_node_id: null }, "已取消当前会话的图谱与节点绑定");
    if (saved) clearNodeContext();
    return saved;
  }

  async function bindNode(nodeId: string | null) {
    const current = latestSession();
    if (!current?.graph_id || queryClient.isMutating({ mutationKey })) return false;
    if (nodeId === (current.current_node_id ?? null)) return true;
    if (nodeId && current.current_node_id) {
      toast.message("请先在页面工具中解绑当前节点，再选择其他节点");
      return false;
    }
    const node = graph.data?.nodes.find((item) => item.id === nodeId);
    if (nodeId && (!node || graph.data?.id !== current.graph_id)) {
      toast.error("所选节点不属于当前图谱");
      return false;
    }
    const saved = await commit({ current_node_id: nodeId }, node ? `当前会话已绑定节点「${node.label}」` : "已取消当前会话的节点绑定");
    if (saved) {
      if (node) publishLearningNodeContext({ graphId: current.graph_id, nodeId: node.id, nodeLabel: node.label, sessionId });
      else clearNodeContext();
    }
    return saved;
  }

  return { session, graphs, graph, binding, boundNodeId, boundNode, bindingPending, bindGraph, clearGraph, bindNode };
}


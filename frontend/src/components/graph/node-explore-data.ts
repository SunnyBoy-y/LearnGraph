import { useQuery } from "@tanstack/react-query";

import { listNodeQuestions } from "@/api";
import { workspaceQueryKey } from "@/lib/query-keys";

export type NodeExploreRound = {
  id: string;
  content: string;
  created_at: string;
};

export function useNodeExploreRounds(
  workspaceId: string,
  graphId?: string,
  nodeId?: string,
) {
  return useQuery({
    queryKey: workspaceQueryKey(workspaceId, "node-questions", graphId, nodeId),
    queryFn: () => listNodeQuestions(graphId!, nodeId!),
    enabled: Boolean(workspaceId && graphId && nodeId),
    staleTime: 30_000,
  });
}

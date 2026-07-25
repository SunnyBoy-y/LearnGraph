import { useQuery } from "@tanstack/react-query";

import { listNodeQuestions } from "@/api";

export type NodeExploreRound = {
  id: string;
  content: string;
  created_at: string;
};

export function useNodeExploreRounds(graphId?: string, nodeId?: string) {
  return useQuery({
    queryKey: ["node-questions", graphId, nodeId],
    queryFn: () => listNodeQuestions(graphId!, nodeId!),
    enabled: Boolean(graphId && nodeId),
    staleTime: 30_000,
  });
}

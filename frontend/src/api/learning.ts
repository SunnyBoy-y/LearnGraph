import type { LearningNodeStateView } from "@/types/learning";

import { apiClient } from "./client";

/**
 * 事件溯源版学习状态读取（单节点）。
 * 取代旧版 GET /mastery 的星标视图，返回连续掌握分、置信度与误区清单。
 * 未评估的节点返回 404 + code=learning_state_not_found，调用方需据此降级。
 */
export function getLearningNodeState(nodeId: string): Promise<LearningNodeStateView> {
  return apiClient.get<LearningNodeStateView>(
    `/learning/nodes/${encodeURIComponent(nodeId)}/state`,
  );
}

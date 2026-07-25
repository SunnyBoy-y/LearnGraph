import type {
  CapabilityReport,
  MasteryAlignment,
  MasteryNode,
  MasteryReviewJob,
  MasterySchedule,
  MasterySchedulerTick,
  MasterySessionState,
} from "@/types/learning";

import { apiClient } from "./client";

export function getMastery(): Promise<MasteryNode[]> {
  return apiClient.get<MasteryNode[]>("/mastery");
}

export function getMasteryAlignment(nodeId: string): Promise<MasteryAlignment> {
  return apiClient.get<MasteryAlignment>(
    `/mastery/nodes/${encodeURIComponent(nodeId)}/alignment`,
  );
}

export function getCapabilityReport(): Promise<CapabilityReport> {
  return apiClient.get<CapabilityReport>("/mastery/capability-report");
}

export const listMasterySchedules = () =>
  apiClient.get<MasterySchedule[]>("/mastery/schedules");
export const listMasteryReviewJobs = () =>
  apiClient.get<MasteryReviewJob[]>("/mastery/review-jobs");
export const listMasterySessionStates = () =>
  apiClient.get<MasterySessionState[]>("/mastery/session-states");
export const tickMasteryScheduler = () =>
  apiClient.post<MasterySchedulerTick>("/mastery/scheduler/tick");
export const runMasteryReview = (node_ids: string[] = []) =>
  apiClient.post<MasteryReviewJob, { trigger: "manual"; node_ids: string[] }>(
    "/mastery/review-runs",
    { trigger: "manual", node_ids },
  );

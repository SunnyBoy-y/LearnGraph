import { apiClient, type ApiRequestOptions } from "./client";
import type { ScheduledTask, ScheduledTaskInput, ScheduledTaskRun } from "@/types/schedules";
export interface ScheduleSuggestion {
  id?: string;
  name: string;
  prompt: string;
  description?: string | null;
  schedule?: string | null;
  icon?: string | null;
  theme?: string | null;
}
export interface ScheduleSuggestionsResponse { suggestions: ScheduleSuggestion[]; metadata: Record<string, unknown> }
const root = "/scheduled-tasks";
export const generateScheduleSuggestions = (options?: ApiRequestOptions) => apiClient.post<ScheduleSuggestionsResponse, { count: number }>(`${root}/suggestions`, { count: 5 }, options);
const item = (id: string) => `${root}/${encodeURIComponent(id)}`;
export const listScheduledTasks = () => apiClient.get<ScheduledTask[]>(root);
export const createScheduledTask = (payload: ScheduledTaskInput) => apiClient.post<ScheduledTask, ScheduledTaskInput>(root, payload);
export const updateScheduledTask = (id: string, payload: Partial<ScheduledTaskInput>) => apiClient.patch<ScheduledTask, Partial<ScheduledTaskInput>>(item(id), payload);
export const deleteScheduledTask = (id: string) => apiClient.delete<void>(item(id));
export const runScheduledTask = (id: string) => apiClient.post<ScheduledTaskRun>(`${item(id)}/run`);
export const cancelScheduledTask = (id: string) => apiClient.post<ScheduledTaskRun>(`${item(id)}/cancel`);
export const listScheduledTaskRuns = (id: string) => apiClient.get<ScheduledTaskRun[]>(`${item(id)}/runs`);
export const getTriggerCatalog = () => apiClient.get<import("@/types/schedules").TriggerCatalog>(`${root}/trigger-catalog`);
export const rotateScheduledWebhookToken = (id:string) => apiClient.post<{token:string;header:string;path:string;note:string}>(`${item(id)}/webhook-token`);
export const emitScheduleEvent = (event_type:string,payload:Record<string,unknown>,event_id?:string) => apiClient.post(`${root}/events`,{event_type,payload,event_id});

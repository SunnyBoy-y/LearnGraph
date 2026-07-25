import type { SettingUpdateRequest, WorkspaceSetting } from "@/types/settings";

import { apiClient } from "./client";

export function listSettings(): Promise<WorkspaceSetting[]> {
  return apiClient.get<WorkspaceSetting[]>("/settings");
}

export function updateSetting(
  key: string,
  value: unknown,
): Promise<WorkspaceSetting> {
  const payload: SettingUpdateRequest = { value };
  return apiClient.put<WorkspaceSetting, SettingUpdateRequest>(
    `/settings/${encodeURIComponent(key)}`,
    payload,
  );
}

export function exportWorkspace(): Promise<Blob> {
  return apiClient.getBlob("/workspace/export");
}

import type {
  ResearchPolicy,
  SettingUpdateRequest,
  WorkspaceSetting,
} from "@/types/settings";

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

export async function getResearchPolicy(): Promise<ResearchPolicy> {
  const settings = await listSettings();
  const raw = settings.find((item) => item.key === "research.policy")?.value;
  if (!raw || typeof raw !== "object") return { allowed_domains: [] };
  const domains = (raw as Partial<ResearchPolicy>).allowed_domains;
  return {
    allowed_domains: Array.isArray(domains)
      ? domains.filter((item): item is string => typeof item === "string")
      : [],
  };
}

export async function updateResearchPolicy(
  policy: ResearchPolicy,
): Promise<ResearchPolicy> {
  const setting = await updateSetting("research.policy", policy);
  const raw = setting.value;
  if (!raw || typeof raw !== "object") return { allowed_domains: [] };
  const domains = (raw as Partial<ResearchPolicy>).allowed_domains;
  return {
    allowed_domains: Array.isArray(domains)
      ? domains.filter((item): item is string => typeof item === "string")
      : [],
  };
}

export function exportWorkspace(): Promise<Blob> {
  return apiClient.getBlob("/workspace/export");
}

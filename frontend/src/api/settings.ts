import type {
  AccessAllowlist,
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

export async function getAccessAllowlist(): Promise<AccessAllowlist> {
  const settings = await listSettings();
  const raw = settings.find((item) => item.key === "access.allowlist")?.value;
  if (!raw || typeof raw !== "object") {
    // 未保存统一白名单：并集旧的 research.policy / web_fetch.policy，
    // 首次保存时会把它们迁移进统一列表。
    const legacy = new Set<string>();
    for (const key of ["research.policy", "web_fetch.policy"]) {
      const value = settings.find((item) => item.key === key)?.value;
      if (!value || typeof value !== "object") continue;
      const domains = (value as Partial<AccessAllowlist>).allowed_domains;
      if (Array.isArray(domains)) {
        for (const item of domains) {
          if (typeof item === "string" && item.trim()) legacy.add(item.trim());
        }
      }
    }
    return { allowed_domains: [...legacy], allow_all: false };
  }
  const value = raw as Partial<AccessAllowlist>;
  const domains = value.allowed_domains;
  return {
    allowed_domains: Array.isArray(domains)
      ? domains.filter((item): item is string => typeof item === "string")
      : [],
    allow_all: value.allow_all === true,
  };
}

export async function updateAccessAllowlist(
  policy: AccessAllowlist,
): Promise<AccessAllowlist> {
  const setting = await updateSetting("access.allowlist", policy);
  const raw = setting.value;
  if (!raw || typeof raw !== "object") {
    return { allowed_domains: [], allow_all: false };
  }
  const value = raw as Partial<AccessAllowlist>;
  const domains = value.allowed_domains;
  return {
    allowed_domains: Array.isArray(domains)
      ? domains.filter((item): item is string => typeof item === "string")
      : [],
    allow_all: value.allow_all === true,
  };
}

export function exportWorkspace(): Promise<Blob> {
  return apiClient.getBlob("/workspace/export");
}

import { listSettings, updateSetting } from "@/api/settings";

/** 域名白名单策略（allowed_domains 精确 DNS 列表）。 */
export type DomainPolicy = { allowed_domains: string[] };

export const researchPolicyQueryKey = ["research-policy"] as const;

export const fetchPolicyQueryKey = ["web-fetch-policy"] as const;

export const accessAllowlistQueryKey = ["access-allowlist"] as const;

export async function getFetchPolicy(): Promise<DomainPolicy> {
  const settings = await listSettings();
  const raw = settings.find((item) => item.key === "web_fetch.policy")?.value;
  if (!raw || typeof raw !== "object") return { allowed_domains: [] };
  const domains = (raw as Partial<DomainPolicy>).allowed_domains;
  return {
    allowed_domains: Array.isArray(domains)
      ? domains.filter((item): item is string => typeof item === "string")
      : [],
  };
}

export async function updateFetchPolicy(
  policy: DomainPolicy,
): Promise<DomainPolicy> {
  const setting = await updateSetting("web_fetch.policy", policy);
  const raw = setting.value;
  if (!raw || typeof raw !== "object") return { allowed_domains: [] };
  const domains = (raw as Partial<DomainPolicy>).allowed_domains;
  return {
    allowed_domains: Array.isArray(domains)
      ? domains.filter((item): item is string => typeof item === "string")
      : [],
  };
}

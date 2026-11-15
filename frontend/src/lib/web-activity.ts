import type { MessagePart } from "@/types/sessions";

/** Labels come from real persisted tool/native events, never composer intent. */
export function webActivityLabel(part: MessagePart): string | null {
  const raw = part.data?.web_activity;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const activity = raw as Record<string, unknown>;
  const output = part.data?.output;
  const meta = output && typeof output === "object" && !Array.isArray(output)
    ? output as Record<string, unknown> : {};
  const provider = typeof meta.provider_label === "string" ? meta.provider_label
    : typeof activity.provider_label === "string" ? activity.provider_label : "网页服务";
  const operation = activity.operation === "search" ? "搜索" : "读取网页";
  const reason = typeof meta.reason === "string" ? meta.reason : "";
  const waiting = ["fetch_domain_authorization_required", "egress_authorization_required", "sandbox_auth_required"].includes(reason);
  const label = waiting ? "等待授权" : activity.completion_unconfirmed === true ? `${operation}状态未确认`
    : part.status === "failed" ? `${operation}失败`
    : part.status === "completed" ? (operation === "搜索" ? "搜索完成" : "网页读取完成")
    : part.status === "pending" ? `等待${operation}` : `正在${operation}`;
  return `${label}（${provider}）`;
}

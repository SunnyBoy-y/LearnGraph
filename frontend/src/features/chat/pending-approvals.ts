import type { SandboxAuthRequest } from "@/components/chat/sandbox-auth-dialog";
import type {
  EgressAuthorizationCardData,
  EgressAuthorizationRequest,
} from "@/types/egress";
import type {
  FetchAuthorizationData,
  FetchAuthorizationRequestView,
} from "@/types/fetch-authorization";
import type { Message } from "@/types/sessions";

export type PendingApprovalItem =
  | {
      id: string;
      kind: "sandbox_delete";
      chatSessionId: string;
      sandboxSessionId: string;
      commandIntentDigest: string;
      paths: string[];
      action: string;
      message?: string;
    }
  | {
      id: string;
      requestId: string;
      kind: "egress";
      chatSessionId: string;
      hostname: string;
      requestedUrl?: string;
      toolName?: string;
      toolLabel?: string;
      message?: string;
      expiresAt?: string;
      resumeMode?: "server";
    }
  | {
      id: string;
      requestId: string;
      kind: "fetch";
      chatSessionId: string;
      hostname: string;
      requestedUrl?: string;
      toolName?: string;
      toolLabel?: string;
      message?: string;
      resumeMode?: "server";
    };

function isPendingCardStatus(
  authorizationStatus: string | undefined,
  decision: string | undefined,
): boolean {
  if (
    authorizationStatus === "approved" ||
    authorizationStatus === "denied" ||
    authorizationStatus === "consumed" ||
    authorizationStatus === "expired"
  ) {
    return false;
  }
  if (decision === "allow_once" || decision === "allow_always" || decision === "deny") {
    return false;
  }
  return true;
}

export function collectPendingApprovals(options: {
  sessionId: string;
  messages: readonly Message[];
  sandboxAuthRequest: SandboxAuthRequest | null;
  backendEgress?: readonly EgressAuthorizationRequest[];
  backendFetch?: readonly FetchAuthorizationRequestView[];
  resolvedIds?: ReadonlySet<string>;
}): PendingApprovalItem[] {
  const {
    sessionId,
    messages,
    sandboxAuthRequest,
    backendEgress = [],
    backendFetch = [],
    resolvedIds,
  } = options;

  if (!sessionId || sessionId === "new") return [];

  const items: PendingApprovalItem[] = [];
  const seen = new Set<string>();
  const isDismissed = (id: string) => Boolean(resolvedIds?.has(id));

  if (sandboxAuthRequest && sandboxAuthRequest.chatSessionId === sessionId) {
    const id = `delete:${sandboxAuthRequest.commandIntentDigest}`;
    if (!isDismissed(id) && !seen.has(id)) {
      seen.add(id);
      items.push({
        id,
        kind: "sandbox_delete",
        chatSessionId: sandboxAuthRequest.chatSessionId,
        sandboxSessionId: sandboxAuthRequest.sandboxSessionId,
        commandIntentDigest: sandboxAuthRequest.commandIntentDigest,
        paths: sandboxAuthRequest.paths,
        action: sandboxAuthRequest.action ?? "delete_path",
        message: sandboxAuthRequest.message,
      });
    }
  }

  // Track message-level metadata so hydrated backend rows inherit resumeMode / message_zh.
  const egressCardMeta = new Map<string, EgressAuthorizationCardData>();
  const fetchCardMeta = new Map<string, FetchAuthorizationData>();

  for (const message of messages) {
    if (message.session_id !== sessionId) continue;
    for (const part of message.parts ?? []) {
      if (part.type === "egress_authorization") {
        const data = (part.data ?? {}) as EgressAuthorizationCardData;
        const requestId = data.authorization_request_id;
        if (!requestId) continue;
        egressCardMeta.set(requestId, data);
        if (!isPendingCardStatus(data.authorization_status, data.decision)) continue;
        const id = `egress:${requestId}`;
        if (isDismissed(id) || seen.has(id)) continue;
        seen.add(id);
        items.push({
          id,
          requestId,
          kind: "egress",
          chatSessionId: sessionId,
          hostname: data.hostname || "未知主机",
          requestedUrl: data.requested_url,
          toolName: data.tool_name,
          toolLabel: data.tool_label,
          message: data.message_zh,
          resumeMode: data.resume_mode,
        });
      } else if (part.type === "fetch_authorization") {
        const data = (part.data ?? {}) as FetchAuthorizationData;
        const requestId = data.authorization_request_id;
        if (!requestId) continue;
        fetchCardMeta.set(requestId, data);
        if (!isPendingCardStatus(data.authorization_status, data.decision)) continue;
        const id = `fetch:${requestId}`;
        if (isDismissed(id) || seen.has(id)) continue;
        seen.add(id);
        items.push({
          id,
          requestId,
          kind: "fetch",
          chatSessionId: sessionId,
          hostname: data.hostname || "未知域名",
          requestedUrl: data.requested_url,
          toolName: data.tool_name,
          toolLabel: data.tool_label,
          message: data.message_zh,
          resumeMode: data.resume_mode,
        });
      }
    }
  }

  // Hydrate pending server records (survives page reload even before parts arrive).
  for (const req of backendEgress) {
    if (req.status !== "pending" || req.chat_session_id !== sessionId) continue;
    const id = `egress:${req.id}`;
    if (isDismissed(id) || seen.has(id)) continue;
    seen.add(id);
    const meta = egressCardMeta.get(req.id);
    items.push({
      id,
      requestId: req.id,
      kind: "egress",
      chatSessionId: sessionId,
      hostname: req.hostname,
      requestedUrl: meta?.requested_url,
      toolName: meta?.tool_name,
      toolLabel: meta?.tool_label,
      message: meta?.message_zh,
      expiresAt: req.expires_at,
      resumeMode: meta?.resume_mode,
    });
  }

  for (const req of backendFetch) {
    if (req.status !== "pending" || req.chat_session_id !== sessionId) continue;
    const id = `fetch:${req.id}`;
    if (isDismissed(id) || seen.has(id)) continue;
    seen.add(id);
    const meta = fetchCardMeta.get(req.id);
    items.push({
      id,
      requestId: req.id,
      kind: "fetch",
      chatSessionId: sessionId,
      hostname: req.hostname,
      requestedUrl: req.requested_url || meta?.requested_url,
      toolName: req.tool_name || meta?.tool_name,
      toolLabel: meta?.tool_label,
      message: meta?.message_zh,
      resumeMode: meta?.resume_mode,
    });
  }

  return items;
}

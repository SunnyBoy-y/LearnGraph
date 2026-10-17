import { useEffect, useState } from "react";
import { apiClient } from "@/api/client";
import { groupPartsForDisplay, isArtifactPart } from "@/features/chat/chat-message-parts";
import type { Message, MessageListPage, MessagePart } from "@/types/sessions";
import type { VoiceTask } from "./voice-tasks";

/** Keep the final answer and committed outputs from earlier tool / takeover rounds. */
export function managedAnswerParts(messages: Message[]): MessagePart[] {
  const assistants = messages.filter(message => message.role === "assistant" && message.status !== "failed");
  const latest = assistants.at(-1);
  const parts = new Map<string, MessagePart>();
  for (const message of assistants) {
    const source = message.parts.length ? message.parts : message.content ? [{
      id: `${message.id}-text`, type: "text" as const, status: "completed" as const, content: message.content,
    }] : [];
    const body = groupPartsForDisplay(source, { boundary: message.finalAnswerStarted, live: message.status === "streaming" })
      .filter(segment => segment.kind === "parts").flatMap(segment => segment.parts);
    for (const part of body) {
      if (part.status === "failed" || part.type === "acknowledgement") continue;
      if (message !== latest && !isArtifactPart(part)) continue;
      const key = part.type === "ui" && part.data?.block_id ? `ui:${part.data.block_id}` : part.id;
      parts.set(key, part);
    }
  }
  return [...parts.values()];
}

/** Lazy, authorized ordinary-session reads; closing a result aborts all reads. */
export function useManagedAgentAnswer(task: VoiceTask, open: boolean) {
  const revision = `${task.taskId}:${task.requirementVersion}:${task.resultVersion ?? "pending"}`;
  const [answer, setAnswer] = useState<{ revision: string; parts: MessagePart[] }>();
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const snapshot = task.agentSessionId ? null : await apiClient.get<{ spec_json?: { agent_session_id?: string } }>(
          `/sandbox/agent-tasks/${encodeURIComponent(task.taskId)}`, { signal: abort.signal });
        const child = task.agentSessionId ?? snapshot?.spec_json?.agent_session_id;
        if (!child) return;
        let before: string | undefined;
        let messages: Message[] = [];
        do {
          const page: MessageListPage = await apiClient.get(`/sessions/${encodeURIComponent(child)}/messages`, {
            signal: abort.signal, query: { limit: 100, before_id: before, compact: "false" },
          });
          messages = [...page.items, ...messages];
          const next = page.has_more_before ? page.oldest_id : null;
          if (!next || next === before) break;
          before = next;
        } while (!abort.signal.aborted);
        if (!abort.signal.aborted) setAnswer({ revision, parts: managedAnswerParts(messages) });
      } catch { /* Keep the durable handoff visible; the next refresh retries. */ }
      finally { loading = false; }
    };
    void refresh();
    const timer = ["queued", "running", "paused", "pausing"].includes(task.status) ? window.setInterval(refresh, 5000) : undefined;
    return () => { abort.abort(); if (timer) window.clearInterval(timer); };
  }, [open, task.taskId, task.agentSessionId, revision, task.status]);
  return answer?.revision === revision ? answer.parts : undefined;
}

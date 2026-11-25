import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { QueryClientContext } from "@tanstack/react-query";

import { updateSessionPartUIState } from "@/api/sessions";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { Message, MessagePart, MessageSnapshot } from "@/types/sessions";

export interface PartUIStatePersistence {
  backendEnabled: boolean;
  persistPartUIState: (
    partId: string,
    uiState: Record<string, unknown>,
    options?: { immediate?: boolean },
  ) => void;
}

const PartUIStateContext = createContext<PartUIStatePersistence | null>(null);

const DRAFT_DEBOUNCE_MS = 250;

export function patchMessagePartsUIState(
  parts: MessagePart[],
  partId: string,
  uiState: Record<string, unknown>,
): MessagePart[] {
  let changed = false;
  const next = parts.map((part) => {
    if (part.id !== partId) return part;
    changed = true;
    return {
      ...part,
      data: {
        ...(part.data ?? {}),
        ui_state: uiState,
      },
    };
  });
  return changed ? next : parts;
}

export function patchMessagesUIState(
  messages: Message[],
  partId: string,
  uiState: Record<string, unknown>,
): Message[] {
  let changed = false;
  const next = messages.map((message) => {
    if (!message.parts?.length) return message;
    const nextParts = patchMessagePartsUIState(message.parts, partId, uiState);
    if (nextParts === message.parts) return message;
    changed = true;
    return {
      ...message,
      parts: nextParts,
    };
  });
  return changed ? next : messages;
}

export function PartUIStateProvider({
  children,
  enabled = true,
  onLocalPartUIStateChange,
  sessionId,
  workspaceId,
}: {
  children: ReactNode;
  enabled?: boolean;
  onLocalPartUIStateChange?: (
    partId: string,
    uiState: Record<string, unknown>,
  ) => void;
  sessionId?: string | null;
  workspaceId?: string | null;
}) {
  const queryClient = useContext(QueryClientContext);
  const pendingTimersRef = useRef<Map<string, number>>(new Map());
  const pendingPayloadsRef = useRef<
    Map<string, { sessionId: string; uiState: Record<string, unknown> }>
  >(new Map());
  const inFlightRef = useRef<Map<string, Promise<void>>>(new Map());
  const latestLocalCallbackRef = useRef(onLocalPartUIStateChange);
  latestLocalCallbackRef.current = onLocalPartUIStateChange;

  const backendEnabled = Boolean(enabled && sessionId && sessionId !== "new");

  const enqueueRequest = useCallback(
    (targetSessionId: string, partId: string, uiState: Record<string, unknown>) => {
      const previous = inFlightRef.current.get(partId) ?? Promise.resolve();
      const next = previous
        .catch(() => undefined)
        .then(() => updateSessionPartUIState(targetSessionId, partId, uiState))
        .then(() => undefined)
        .catch(() => {
          // Non-fatal: optimistic cache and localStorage keep interactive continuity.
        })
        .finally(() => {
          if (inFlightRef.current.get(partId) === next) {
            inFlightRef.current.delete(partId);
          }
        });
      inFlightRef.current.set(partId, next);
    },
    [],
  );

  // Flush any debounced writes when switching sessions or unmounting so rapid
  // selections before navigation are still persisted to the backend.
  useEffect(() => {
    const timers = pendingTimersRef.current;
    const payloads = pendingPayloadsRef.current;
    return () => {
      for (const timer of timers.values()) {
        window.clearTimeout(timer);
      }
      timers.clear();
      for (const [partId, entry] of payloads.entries()) {
        enqueueRequest(entry.sessionId, partId, entry.uiState);
      }
      payloads.clear();
    };
  }, [enqueueRequest, sessionId]);

  const persistPartUIState = useCallback(
    (
      partId: string,
      uiState: Record<string, unknown>,
      options?: { immediate?: boolean },
    ) => {
      if (!backendEnabled || !sessionId || !partId) return;

      if (queryClient && workspaceId) {
        queryClient.setQueryData<Message[]>(
          workspaceQueryKey(workspaceId, "messages", sessionId),
          (current) =>
            current ? patchMessagesUIState(current, partId, uiState) : current,
        );
        queryClient.setQueriesData<MessageSnapshot>(
          {
            queryKey: workspaceQueryKey(
              workspaceId,
              "message-snapshot",
              sessionId,
            ),
          },
          (current) => {
            if (!current?.parts?.length) return current;
            const nextParts = patchMessagePartsUIState(
              current.parts,
              partId,
              uiState,
            );
            return nextParts === current.parts
              ? current
              : { ...current, parts: nextParts };
          },
        );
      }
      latestLocalCallbackRef.current?.(partId, uiState);

      const existingTimer = pendingTimersRef.current.get(partId);
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
        pendingTimersRef.current.delete(partId);
      }

      if (options?.immediate) {
        pendingPayloadsRef.current.delete(partId);
        enqueueRequest(sessionId, partId, uiState);
        return;
      }

      pendingPayloadsRef.current.set(partId, { sessionId, uiState });
      const timer = window.setTimeout(() => {
        pendingTimersRef.current.delete(partId);
        const pending = pendingPayloadsRef.current.get(partId);
        if (!pending) return;
        pendingPayloadsRef.current.delete(partId);
        enqueueRequest(pending.sessionId, partId, pending.uiState);
      }, DRAFT_DEBOUNCE_MS);
      pendingTimersRef.current.set(partId, timer);
    },
    [backendEnabled, enqueueRequest, queryClient, sessionId, workspaceId],
  );

  const value = useMemo<PartUIStatePersistence>(
    () => ({ backendEnabled, persistPartUIState }),
    [backendEnabled, persistPartUIState],
  );

  return (
    <PartUIStateContext.Provider value={value}>
      {children}
    </PartUIStateContext.Provider>
  );
}

export function usePartUIStatePersistence(): PartUIStatePersistence | null {
  return useContext(PartUIStateContext);
}

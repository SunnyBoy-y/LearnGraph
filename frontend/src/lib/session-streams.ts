/**
 * Per-session AbortControllers so multiple chat sessions can stream at once.
 * Leaving a session must NOT abort its in-flight generation.
 */

export type SessionStreamHandle = {
  controller: AbortController;
  messageId: string | null;
};

const streams = new Map<string, SessionStreamHandle>();
const listeners = new Set<() => void>();

export function subscribeSessionStreams(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function emit() {
  for (const listener of listeners) listener();
}

export function getSessionStream(
  sessionId: string | null | undefined,
): SessionStreamHandle | undefined {
  if (!sessionId || sessionId === "new") return undefined;
  return streams.get(sessionId);
}

export function isSessionStreaming(
  sessionId: string | null | undefined,
): boolean {
  return Boolean(getSessionStream(sessionId));
}

/** Session ids that currently own an in-flight generation controller. */
export function listStreamingSessionIds(): string[] {
  return Array.from(streams.keys());
}

/**
 * Register a new stream for the session. Aborts any previous controller for the
 * same session only (same-session re-send), never other sessions.
 */
export function registerSessionStream(
  sessionId: string,
  controller: AbortController,
  messageId: string | null = null,
): void {
  if (!sessionId || sessionId === "new") return;
  const previous = streams.get(sessionId);
  if (previous && previous.controller !== controller) {
    previous.controller.abort();
  }
  streams.set(sessionId, { controller, messageId });
  emit();
}

export function setSessionStreamMessageId(
  sessionId: string,
  messageId: string | null,
): void {
  const current = streams.get(sessionId);
  if (!current) return;
  streams.set(sessionId, { ...current, messageId });
  emit();
}

/**
 * Drop the handle when the stream ends. Only clears if `controller` still owns
 * the slot (a newer send on the same session keeps its handle).
 */
export function clearSessionStream(
  sessionId: string,
  controller?: AbortController,
): void {
  const current = streams.get(sessionId);
  if (!current) return;
  if (controller && current.controller !== controller) return;
  streams.delete(sessionId);
  emit();
}

/** Stop generation for one session (toolbar Stop). Other sessions keep running. */
export function abortSessionStream(sessionId: string | null | undefined): void {
  if (!sessionId || sessionId === "new") return;
  const current = streams.get(sessionId);
  if (!current) return;
  current.controller.abort();
  streams.delete(sessionId);
  emit();
}

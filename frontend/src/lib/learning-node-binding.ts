import type { Session } from "@/types/sessions";

// Desktop and the preheated mobile drawer can both mount navigation listeners.
// Claim the event, not its graph/node key: a fresh click must remain retryable.
const claimedLearningNavigationEvents = new WeakSet<Event>();

export function claimLearningNodeNavigation(event: Event): boolean {
  if (claimedLearningNavigationEvents.has(event)) return false;
  claimedLearningNavigationEvents.add(event);
  return true;
}

/** Transient graph selections may not replace an existing conversation binding. */
export function matchesSessionLearningNode(
  sessionId: string,
  session: Pick<Session, "id" | "graph_id" | "current_node_id"> | undefined,
  context: { sessionId?: string; graphId: string; nodeId?: string },
): boolean {
  if (context.sessionId && context.sessionId !== sessionId) return false;
  // A new draft has no persisted binding yet. Existing sessions must wait for it.
  if (sessionId === "new") return true;
  return Boolean(
    session?.id === sessionId &&
      session.graph_id === context.graphId &&
      (session.current_node_id ?? undefined) === context.nodeId,
  );
}

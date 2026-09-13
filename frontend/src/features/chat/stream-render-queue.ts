type StreamUpdate = Record<string, unknown>;
type Part = Record<string, unknown>;

const eventType = (event: StreamUpdate) => event.type ?? event.event;
const partOf = (event: StreamUpdate): Part | undefined =>
  event.part && typeof event.part === "object" ? event.part as Part : undefined;
const isTextDelta = (event: StreamUpdate) => {
  const part = partOf(event);
  return (eventType(event) === "part.delta" || eventType(event) === "message.part.delta") &&
    part?.type === "text" && typeof part.content_delta === "string" &&
    typeof part.content !== "string";
};
const isReasoningDelta = (event: StreamUpdate) => {
  const type = eventType(event);
  const part = partOf(event);
  return (type === "part.delta" || type === "message.part.delta") &&
    (part?.type === "reasoning_summary" || part?.type === "reasoning_content");
};
const isTerminal = (event: StreamUpdate) =>
  ["message.completed", "message.failed", "message.cancelled", "message.interrupted"].includes(String(eventType(event)));

/** Only combine adjacent text deltas. Replacements, cards and lifecycle events
 * remain ordered barriers. Joining once avoids repeated growing-string copies. */
export function coalesceTextDeltas(events: StreamUpdate[]): StreamUpdate[] {
  const result: StreamUpdate[] = [];
  for (let index = 0; index < events.length;) {
    const first = events[index++];
    if (!isTextDelta(first)) {
      result.push(first);
      continue;
    }
    let event = first;
    let part = partOf(first)!;
    const chunks = [part.content_delta as string];
    while (index < events.length) {
      const next = events[index];
      const nextPart = partOf(next);
      if (!isTextDelta(next) || !nextPart || nextPart.id !== part.id ||
          nextPart.status !== part.status || eventType(next) !== eventType(first) ||
          next.message_id !== first.message_id || next.message_version_id !== first.message_version_id) break;
      chunks.push(nextPart.content_delta as string);
      part = { ...part, ...nextPart, data: { ...part.data as object, ...nextPart.data as object } };
      event = next;
      index += 1;
    }
    result.push(chunks.length === 1 ? first : { ...event, part: { ...part, content_delta: chunks.join("") } });
  }
  return result;
}

/** Publish text every 50 ms, with no artificial playback backlog. Reasoning
 * retains frame delivery; cards and lifecycle events publish immediately. A terminal event/drain flushes now,
 * including in a hidden tab where requestAnimationFrame can be suspended. */
export function createStreamRenderQueue(
  onBatch: (events: StreamUpdate[]) => void,
  options: { isViewing?: () => boolean } = {},
) {
  let pending: StreamUpdate[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  let timerDue = 0;
  let lastTextAt = -Infinity;
  const flush = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    timerDue = 0;
    if (!pending.length) return;
    const batch = pending;
    pending = [];
    if (batch.some(isTextDelta)) lastTextAt = performance.now();
    onBatch(coalesceTextDeltas(batch));
  };
  return {
    push(event: StreamUpdate) {
      const text = isTextDelta(event);
      // Cards and lifecycle transitions are never held behind a text animation.
      if (!text && !isReasoningDelta(event)) {
        // Preserve ordering: flush text that arrived before a card/lifecycle
        // event before publishing the barrier itself.
        flush();
        pending = [event];
        flush();
        return;
      }
      pending.push(event);
      if (isTerminal(event)) { flush(); return; }
      const now = performance.now();
      if (isReasoningDelta(event) && timer !== undefined && timerDue - now > 16) {
        clearTimeout(timer);
        timer = setTimeout(flush, 16);
        timerDue = now + 16;
        return;
      }
      if (timer === undefined) {
        const interval = options.isViewing?.() === false ? 100 : 50;
        const delay = text ? Math.max(0, interval - (now - lastTextAt)) : 16;
        timer = setTimeout(flush, delay);
        timerDue = now + delay;
      }
    },
    drain() { flush(); return Promise.resolve(); },
    clear() {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      timerDue = 0;
      pending = [];
    },
  };
}

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type TouchEvent as ReactTouchEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";

export type ConversationScrollMode =
  | "FOLLOW_ACTIVITY"
  | "ANSWER_ANCHORED"
  | "MANUAL_READING";

export type ConversationScrollReason =
  | "activity-follow"
  | "answer-anchor"
  | "layout-compensate"
  | "message-jump"
  | "return-latest"
  | "user-message";

export interface ConversationScrollSnapshot {
  scrollHeight: number;
  scrollTop: number;
}

interface ReadingAnchor {
  element: HTMLElement;
  viewportTop: number;
  /**
   * Re-resolve the anchor when its DOM node was replaced — a lazy message row
   * unmounting, or the optimistic → persisted id swap at the end of a turn.
   * Returning null means "give up on this anchor": a detached node reports an
   * all-zero rect, and compensating against it produced a wrong jump of up to a
   * full viewport height.
   */
  resolve: () => HTMLElement | null;
}

export interface ConversationScrollController {
  contentRef: (node: HTMLDivElement | null) => void;
  hasCommittedAnswer: boolean;
  hasNewContent: boolean;
  isAtBottom: boolean;
  mode: ConversationScrollMode;
  handleKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => void;
  handlePointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
  handleScroll: () => void;
  handleTouchEnd: (event: ReactTouchEvent<HTMLDivElement>) => void;
  handleTouchStart: (event: ReactTouchEvent<HTMLDivElement>) => void;
  handleWheel: (event: ReactWheelEvent<HTMLDivElement>) => void;
  notifyActivityUpdated: () => void;
  notifyCommittedAnswerStarted: (turnId: string) => void;
  notifyLayoutChanged: () => void;
  notifyUserTurnStarted: (messageId: string) => void;
  returnToLatest: () => void;
  scrollMessageIntoView: (messageId: string, block?: "start" | "center") => void;
  captureScrollSnapshot: () => ConversationScrollSnapshot | null;
  restoreScrollSnapshot: (snapshot: ConversationScrollSnapshot | null) => void;
  scrollRef: (node: HTMLDivElement | null) => void;
  /**
   * Drop the reserved trailing space. Hosts call this when the conversation
   * itself changes (session switch), otherwise the last session's reservation
   * would leave a blank gap at the end of the next one.
   */
  resetTailSpace: () => void;
  /**
   * Trailing space (px) reserved below the content so the active turn anchor is
   * actually reachable. Hosts must apply it to the content's bottom padding;
   * it only ever grows during a session, so it can never clamp the viewport and
   * move content the learner is reading.
   */
  tailSpace: number;
}

const NEAR_BOTTOM_THRESHOLD = 96;
/**
 * Extra tail space kept beyond the strictly required amount, so the anchored
 * turn always sits clear of the "near bottom" band. Without it the anchor would
 * land exactly on the bottom, activity-follow would consider itself at the
 * bottom, and the canvas would keep following the thinking chain downward.
 */
const ANCHOR_BOTTOM_SLACK = NEAR_BOTTOM_THRESHOLD + 32;
const ANCHOR_RETRY_FRAMES = 36;
const DESIRED_ANSWER_TOP_RATIO = 0.3;
const DESIRED_COMPACT_ANSWER_TOP_RATIO = 0.23;
const DESIRED_USER_TOP_RATIO = 0.15;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function maxScrollTop(element: HTMLElement) {
  return Math.max(0, element.scrollHeight - element.clientHeight);
}

function findAnswerAnchor(turnId: string) {
  const escapedTurnId = turnId.replaceAll("\\", "\\\\").replaceAll('"', '\\"');
  return document.querySelector<HTMLElement>(
    `[data-conversation-answer-anchor="${escapedTurnId}"]`,
  );
}

function findMessageAnchor(messageId: string) {
  return document.getElementById(`conversation-jump-${messageId}`);
}

function findVisibleReadingAnchor(element: HTMLElement) {
  const scrollerRect = element.getBoundingClientRect();
  // Message-mount wrappers come first: they stay in the DOM when a lazy row
  // unmounts its children, so they survive as a stable reading anchor.
  const messages = element.querySelectorAll<HTMLElement>(
    ".chat-messages-content [data-message-mount-id], .chat-messages-content [data-message-id], .chat-messages-content [data-conversation-anchor]",
  );
  let fallback: HTMLElement | null = null;
  for (const message of messages) {
    const rect = message.getBoundingClientRect();
    if (rect.bottom <= scrollerRect.top + 8) continue;
    if (rect.top >= scrollerRect.bottom - 8) break;
    if (!fallback) fallback = message;
    if (rect.top >= scrollerRect.top - 8) return message;
  }
  return fallback;
}

/** An anchor is only usable while its node is still inside the scroller. */
function isAnchorAttached(element: HTMLElement, scroller: HTMLElement) {
  return element.isConnected && scroller.contains(element);
}

export function useConversationScrollController(): ConversationScrollController {
  const scrollElementRef = useRef<HTMLDivElement | null>(null);
  const contentElementRef = useRef<HTMLDivElement | null>(null);
  const modeRef = useRef<ConversationScrollMode>("FOLLOW_ACTIVITY");
  const isAtBottomRef = useRef(true);
  const activeAnchorRef = useRef<ReadingAnchor | null>(null);
  const lastScrollTopRef = useRef(0);
  const lastContentHeightRef = useRef(0);
  /** Anchor's offset from the content top; grows only when content above it does. */
  const lastAnchorOffsetRef = useRef<number | null>(null);
  const tailSpaceRef = useRef(0);
  const layoutFrameRef = useRef<number | null>(null);
  const ignoreManualUntilRef = useRef(0);
  const expectedProgrammaticTopRef = useRef<number | null>(null);
  const committedAnswerTurnRef = useRef<string | null>(null);
  const processedAnswerTurnRef = useRef<string | null>(null);
  const touchStartYRef = useRef<number | null>(null);
  const contentObserverRef = useRef<ResizeObserver | null>(null);

  const [mode, setModeState] = useState<ConversationScrollMode>(
    "FOLLOW_ACTIVITY",
  );
  const [isAtBottom, setIsAtBottomState] = useState(true);
  const [hasNewContent, setHasNewContent] = useState(false);
  const [hasCommittedAnswer, setHasCommittedAnswer] = useState(false);
  const [tailSpace, setTailSpaceState] = useState(0);

  const setMode = useCallback((next: ConversationScrollMode) => {
    modeRef.current = next;
    setModeState((current) => (current === next ? current : next));
    if (next === "FOLLOW_ACTIVITY") setHasNewContent(false);
  }, []);

  const setIsAtBottom = useCallback((next: boolean) => {
    isAtBottomRef.current = next;
    setIsAtBottomState((current) => (current === next ? current : next));
  }, []);

  const updateReadingAnchor = useCallback(
    (anchor: ReadingAnchor | null) => {
      activeAnchorRef.current = anchor;
    },
    [],
  );

  const markProgrammaticScroll = useCallback((top: number) => {
    expectedProgrammaticTopRef.current = top;
    ignoreManualUntilRef.current = performance.now() + 120;
  }, []);

  const setScrollTop = useCallback(
    (
      top: number,
      _reason: ConversationScrollReason,
      options: { preserveManual?: boolean } = {},
    ) => {
      const element = scrollElementRef.current;
      if (!element) return 0;
      const next = clamp(top, 0, maxScrollTop(element));
      if (!options.preserveManual) markProgrammaticScroll(next);
      element.scrollTop = next;
      lastScrollTopRef.current = next;
      expectedProgrammaticTopRef.current = next;
      return next;
    },
    [markProgrammaticScroll],
  );

  const scrollToBottom = useCallback(
    (reason: ConversationScrollReason = "activity-follow") => {
      const element = scrollElementRef.current;
      if (!element) return;
      setScrollTop(maxScrollTop(element), reason);
      setIsAtBottom(true);
    },
    [setIsAtBottom, setScrollTop],
  );

  /** Recompute "at bottom" from the live DOM, without waiting for a scroll event. */
  const refreshAtBottom = useCallback(() => {
    const element = scrollElementRef.current;
    if (!element) return;
    setIsAtBottom(
      element.scrollHeight - element.scrollTop - element.clientHeight <=
        NEAR_BOTTOM_THRESHOLD,
    );
  }, [setIsAtBottom]);

  /**
   * Return a live anchor node, re-resolving it when the old node was detached
   * (lazy unmount / id swap). `null` means the anchor is gone for good.
   */
  const resolveLiveAnchor = useCallback((anchor: ReadingAnchor) => {
    const scroller = scrollElementRef.current;
    if (!scroller) return null;
    if (isAnchorAttached(anchor.element, scroller)) return anchor.element;
    const next = anchor.resolve();
    if (!next || !isAnchorAttached(next, scroller)) return null;
    anchor.element = next;
    return next;
  }, []);

  /**
   * Reserve enough trailing space for `element` to actually reach
   * `desiredViewportTop`. Without it every anchor request is silently clamped
   * away — the newest card can never sit higher than the composer safe area —
   * and the resting position is then decided by activity-follow instead of by
   * the anchor. The reservation is monotonic, so it never clamps the viewport.
   */
  const reserveTailFor = useCallback(
    (element: HTMLElement, desiredViewportTop: number) => {
      const scroller = scrollElementRef.current;
      const content = contentElementRef.current;
      if (!scroller || !content) return;
      const anchorRect = element.getBoundingClientRect();
      if (!anchorRect.height && !anchorRect.width) return;
      // Distance from the anchor's top edge to the end of the scrolled content,
      // including the bottom padding already in effect.
      const trailing = content.getBoundingClientRect().bottom - anchorRect.top;
      const need =
        scroller.clientHeight -
        desiredViewportTop -
        trailing +
        ANCHOR_BOTTOM_SLACK;
      if (need <= tailSpaceRef.current + 0.5) return;
      const next = Math.ceil(need);
      tailSpaceRef.current = next;
      setTailSpaceState(next);
    },
    [setTailSpaceState],
  );

  const resetTailSpace = useCallback(() => {
    if (tailSpaceRef.current === 0) return;
    tailSpaceRef.current = 0;
    setTailSpaceState(0);
  }, [setTailSpaceState]);

  const positionAnchor = useCallback(
    (
      element: HTMLElement,
      desiredViewportTop: number,
      reason: ConversationScrollReason,
      resolve?: () => HTMLElement | null,
    ) => {
      const scroller = scrollElementRef.current;
      if (!scroller) return;
      // Reserve before measuring: growing the tail only adds space *below* the
      // anchor, so nothing already on screen can move.
      reserveTailFor(element, desiredViewportTop);
      const anchor: ReadingAnchor = {
        element,
        viewportTop: desiredViewportTop,
        resolve: resolve ?? (() => null),
      };
      activeAnchorRef.current = anchor;
      const scrollerRect = scroller.getBoundingClientRect();
      const anchorRect = element.getBoundingClientRect();
      const nextTop =
        scroller.scrollTop + anchorRect.top - scrollerRect.top - desiredViewportTop;
      setScrollTop(nextTop, reason);
      // A deliberate anchor always beats activity-follow — even while a freshly
      // reserved tail space is still uncommitted and the request is clamped:
      // following now would immediately steal the turn (and the canvas would
      // slide down with the thinking chain again).
      setIsAtBottom(false);
      if (reason === "answer-anchor") setHasNewContent(false);
      let correctionFrames = 0;
      const commitCorrection = () => {
        if (activeAnchorRef.current !== anchor) return;
        const live = resolveLiveAnchor(anchor);
        if (!live) {
          updateReadingAnchor(null);
          return;
        }
        reserveTailFor(live, desiredViewportTop);
        const currentScrollerRect = scroller.getBoundingClientRect();
        const currentAnchorRect = live.getBoundingClientRect();
        const correctedTop =
          scroller.scrollTop +
          currentAnchorRect.top -
          currentScrollerRect.top -
          desiredViewportTop;
        setScrollTop(correctedTop, reason);
        refreshAtBottom();
        anchor.viewportTop = live.getBoundingClientRect().top;
        lastScrollTopRef.current = scroller.scrollTop;
        // A freshly reserved tail is committed one frame later; re-run while the
        // clamp still truncated the requested position.
        if (
          correctionFrames < 3 &&
          Math.abs(correctedTop - scroller.scrollTop) > 0.5
        ) {
          correctionFrames += 1;
          window.requestAnimationFrame(commitCorrection);
        }
      };
      window.requestAnimationFrame(commitCorrection);
    },
    [
      refreshAtBottom,
      reserveTailFor,
      resolveLiveAnchor,
      setScrollTop,
      setIsAtBottom,
      updateReadingAnchor,
    ],
  );

  const compensateLayoutChange = useCallback(() => {
    const anchor = activeAnchorRef.current;
    if (!anchor) return false;
    const element = scrollElementRef.current;
    if (!element) return false;
    const previousElement = anchor.element;
    const live = resolveLiveAnchor(anchor);
    if (!live) {
      // The node is gone for good (not just replaced): drop the anchor rather
      // than measuring a detached element, whose rect is all zeros.
      activeAnchorRef.current = null;
      return false;
    }
    if (live !== previousElement) {
      // Re-resolved after a remount: adopt where the replacement sits right
      // now, so the swap itself never moves the viewport.
      anchor.viewportTop = live.getBoundingClientRect().top;
      return false;
    }
    const nextTop = live.getBoundingClientRect().top;
    const delta = nextTop - anchor.viewportTop;
    if (Math.abs(delta) < 0.5) return false;
    const nextScrollTop = clamp(
      element.scrollTop + delta,
      0,
      maxScrollTop(element),
    );
    setScrollTop(nextScrollTop, "layout-compensate", {
      preserveManual: true,
    });
    anchor.viewportTop = nextTop - delta;
    return true;
  }, [resolveLiveAnchor, setScrollTop]);

  const scheduleLayoutDecision = useCallback(() => {
    if (layoutFrameRef.current !== null) return;
    layoutFrameRef.current = window.requestAnimationFrame(() => {
      layoutFrameRef.current = null;
      const element = scrollElementRef.current;
      if (!element) return;
      const nextHeight = element.scrollHeight;
      const previousHeight = lastContentHeightRef.current;
      lastContentHeightRef.current = nextHeight;
      // "New content" means content grew *above* the pinned anchor. Growth below
      // it — the streaming answer, the thinking chain, or the composer-driven
      // bottom padding — is not something the learner has to scroll back for and
      // must not flash the "还有新内容" button.
      const anchor = activeAnchorRef.current;
      const liveAnchor = anchor ? resolveLiveAnchor(anchor) : null;
      let grew: boolean;
      if (liveAnchor) {
        const elementRect = element.getBoundingClientRect();
        const offsetTop =
          element.scrollTop +
          liveAnchor.getBoundingClientRect().top -
          elementRect.top;
        const previousOffset = lastAnchorOffsetRef.current;
        grew = previousOffset !== null && offsetTop > previousOffset + 1;
        lastAnchorOffsetRef.current = offsetTop;
      } else {
        lastAnchorOffsetRef.current = null;
        grew = nextHeight > previousHeight + 1;
      }

      if (modeRef.current === "FOLLOW_ACTIVITY") {
        if (isAtBottomRef.current) scrollToBottom("activity-follow");
        return;
      }

      compensateLayoutChange();
      if (grew) setHasNewContent(true);
    });
  }, [compensateLayoutChange, resolveLiveAnchor, scrollToBottom]);

  const notifyLayoutChanged = useCallback(() => {
    scheduleLayoutDecision();
  }, [scheduleLayoutDecision]);

  const resolveUserTurnAnchor = useCallback(
    (messageId: string, frame = 0) => {
      if (modeRef.current === "MANUAL_READING") return;
      const element = findMessageAnchor(messageId);
      if (element) {
        const desired =
          scrollElementRef.current?.clientHeight
            ? scrollElementRef.current.clientHeight * DESIRED_USER_TOP_RATIO
            : 0;
        positionAnchor(element, desired, "user-message", () =>
          findMessageAnchor(messageId),
        );
        return;
      }
      if (frame >= ANCHOR_RETRY_FRAMES) return;
      window.requestAnimationFrame(() =>
        resolveUserTurnAnchor(messageId, frame + 1),
      );
    },
    [positionAnchor],
  );

  const resolveCommittedAnswerAnchor = useCallback(
    (turnId: string, frame = 0) => {
      if (
        processedAnswerTurnRef.current !== turnId ||
        modeRef.current !== "ANSWER_ANCHORED"
      ) {
        return;
      }
      const element = findAnswerAnchor(turnId);
      if (element) {
        const viewportHeight = scrollElementRef.current?.clientHeight ?? 0;
        const ratio =
          viewportHeight > 0 && viewportHeight < 640
            ? DESIRED_COMPACT_ANSWER_TOP_RATIO
            : DESIRED_ANSWER_TOP_RATIO;
        positionAnchor(element, viewportHeight * ratio, "answer-anchor", () =>
          findAnswerAnchor(turnId),
        );
        return;
      }
      if (frame >= ANCHOR_RETRY_FRAMES) return;
      window.requestAnimationFrame(() =>
        resolveCommittedAnswerAnchor(turnId, frame + 1),
      );
    },
    [positionAnchor],
  );

  const notifyUserTurnStarted = useCallback(
    (messageId: string) => {
      committedAnswerTurnRef.current = null;
      processedAnswerTurnRef.current = null;
      updateReadingAnchor(null);
      setHasCommittedAnswer(false);
      setMode("FOLLOW_ACTIVITY");
      resolveUserTurnAnchor(messageId);
    },
    [resolveUserTurnAnchor, setMode, updateReadingAnchor],
  );

  const notifyCommittedAnswerStarted = useCallback(
    (turnId: string) => {
      if (processedAnswerTurnRef.current === turnId) return;
      processedAnswerTurnRef.current = turnId;
      committedAnswerTurnRef.current = turnId;
      setHasCommittedAnswer(true);
      setMode("ANSWER_ANCHORED");
      setHasNewContent(false);
      resolveCommittedAnswerAnchor(turnId);
    },
    [resolveCommittedAnswerAnchor, setMode],
  );

  const notifyActivityUpdated = useCallback(() => {
    if (modeRef.current !== "FOLLOW_ACTIVITY") return;
    scheduleLayoutDecision();
  }, [scheduleLayoutDecision]);

  const adoptManualAnchor = useCallback(() => {
    const element = scrollElementRef.current;
    if (!element) return;
    const visible = findVisibleReadingAnchor(element);
    if (!visible) {
      updateReadingAnchor(null);
      return;
    }
    updateReadingAnchor({
      element: visible,
      viewportTop: visible.getBoundingClientRect().top,
      resolve: () => findVisibleReadingAnchor(element),
    });
  }, [updateReadingAnchor]);

  const enterManualReading = useCallback(() => {
    if (modeRef.current !== "MANUAL_READING") {
      setMode("MANUAL_READING");
      setHasNewContent(false);
    }
    window.requestAnimationFrame(adoptManualAnchor);
  }, [adoptManualAnchor, setMode]);

  const handleScroll = useCallback(() => {
    const element = scrollElementRef.current;
    if (!element) return;
    const nextTop = element.scrollTop;
    const previousTop = lastScrollTopRef.current;
    const distance = element.scrollHeight - nextTop - element.clientHeight;
    const nearBottom = distance <= NEAR_BOTTOM_THRESHOLD;
    const isProgrammatic =
      performance.now() < ignoreManualUntilRef.current &&
      expectedProgrammaticTopRef.current !== null &&
      Math.abs(nextTop - expectedProgrammaticTopRef.current) <= 2;

    lastScrollTopRef.current = nextTop;
    setIsAtBottom(nearBottom);
    if (isProgrammatic) {
      expectedProgrammaticTopRef.current = null;
      return;
    }
    expectedProgrammaticTopRef.current = null;

    if (nextTop < previousTop - 1 && !nearBottom) {
      enterManualReading();
      return;
    }
    if (modeRef.current === "MANUAL_READING") {
      window.requestAnimationFrame(adoptManualAnchor);
    }
  }, [adoptManualAnchor, enterManualReading, setIsAtBottom]);

  const handleWheel = useCallback(
    (event: ReactWheelEvent<HTMLDivElement>) => {
      if (event.deltaY < 0) enterManualReading();
    },
    [enterManualReading],
  );

  const handleTouchStart = useCallback(
    (event: ReactTouchEvent<HTMLDivElement>) => {
      touchStartYRef.current = event.touches[0]?.clientY ?? null;
    },
    [],
  );

  const handleTouchEnd = useCallback(
    (event: ReactTouchEvent<HTMLDivElement>) => {
      const startY = touchStartYRef.current;
      const endY = event.changedTouches[0]?.clientY;
      if (
        startY !== null &&
        typeof endY === "number" &&
        endY > startY + 12
      ) {
        enterManualReading();
      }
      touchStartYRef.current = null;
    },
    [enterManualReading],
  );

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const element = scrollElementRef.current;
      if (!element) return;
      const rect = element.getBoundingClientRect();
      if (event.clientX >= rect.right - 18) enterManualReading();
      if (event.pointerType === "touch") {
        touchStartYRef.current = event.clientY;
      }
    },
    [enterManualReading],
  );

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (
        event.key === "PageUp" ||
        event.key === "Home" ||
        event.key === "ArrowUp"
      ) {
        enterManualReading();
      }
    },
    [enterManualReading],
  );

  const returnToLatest = useCallback(() => {
    // The learner asked for the true bottom: drop the reserved tail so the last
    // message sits directly above the composer again.
    resetTailSpace();
    if (committedAnswerTurnRef.current) {
      setMode("MANUAL_READING");
      updateReadingAnchor(null);
      setHasNewContent(false);
      scrollToBottom("return-latest");
      return;
    }
    updateReadingAnchor(null);
    setMode("FOLLOW_ACTIVITY");
    setHasNewContent(false);
    scrollToBottom("return-latest");
  }, [resetTailSpace, scrollToBottom, setMode, updateReadingAnchor]);

  const scrollMessageIntoView = useCallback(
    (messageId: string, block: "start" | "center" = "center") => {
      const element = findMessageAnchor(messageId);
      const scroller = scrollElementRef.current;
      if (!element || !scroller) return;
      const desired =
        block === "start"
          ? scroller.clientHeight * DESIRED_USER_TOP_RATIO
          : (scroller.clientHeight - element.offsetHeight) / 2;
      positionAnchor(element, desired, "message-jump", () =>
        findMessageAnchor(messageId),
      );
      setMode("MANUAL_READING");
    },
    [positionAnchor, setMode],
  );

  const captureScrollSnapshot = useCallback(() => {
    const element = scrollElementRef.current;
    if (!element) return null;
    return {
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    };
  }, []);

  const restoreScrollSnapshot = useCallback(
    (snapshot: ConversationScrollSnapshot | null) => {
      const element = scrollElementRef.current;
      if (!element || !snapshot) return;
      const delta = element.scrollHeight - snapshot.scrollHeight;
      setScrollTop(snapshot.scrollTop + delta, "layout-compensate", {
        preserveManual: true,
      });
    },
    [setScrollTop],
  );

  const scrollRef = useCallback((node: HTMLDivElement | null) => {
    scrollElementRef.current = node;
    if (node) {
      lastScrollTopRef.current = node.scrollTop;
      lastContentHeightRef.current = node.scrollHeight;
    }
  }, []);

  const contentRef = useCallback(
    (node: HTMLDivElement | null) => {
      contentObserverRef.current?.disconnect();
      contentObserverRef.current = null;
      contentElementRef.current = node;
      if (!node || typeof ResizeObserver === "undefined") return;
      contentObserverRef.current = new ResizeObserver(() => {
        scheduleLayoutDecision();
      });
      contentObserverRef.current.observe(node);
      scheduleLayoutDecision();
    },
    [scheduleLayoutDecision],
  );

  useEffect(() => {
    const onConversationJump = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          block?: "start" | "center";
          messageId?: string;
        }>
      ).detail;
      if (!detail?.messageId) return;
      scrollMessageIntoView(detail.messageId, detail.block);
    };
    window.addEventListener("learngraph:conversation-jump", onConversationJump);
    return () => {
      window.removeEventListener(
        "learngraph:conversation-jump",
        onConversationJump,
      );
    };
  }, [scrollMessageIntoView]);

  useEffect(
    () => () => {
      contentObserverRef.current?.disconnect();
      if (layoutFrameRef.current !== null) {
        window.cancelAnimationFrame(layoutFrameRef.current);
      }
    },
    [],
  );

  return {
    captureScrollSnapshot,
    contentRef,
    handleKeyDown,
    handlePointerDown,
    handleScroll,
    handleTouchEnd,
    handleTouchStart,
    handleWheel,
    hasCommittedAnswer,
    hasNewContent,
    isAtBottom,
    mode,
    notifyActivityUpdated,
    notifyCommittedAnswerStarted,
    notifyLayoutChanged,
    notifyUserTurnStarted,
    returnToLatest,
    restoreScrollSnapshot,
    scrollMessageIntoView,
    scrollRef,
    resetTailSpace,
    tailSpace,
  };
}

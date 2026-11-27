/**
 * Pure, DOM-free scroll state calculations shared by conversation scrollers.
 *
 * Isolating these math/transition decisions from DOM observers makes high-DPI
 * sub-pixel rounding (125% / 150% / 175% display scaling) and native scrollbar
 * thumb dragging deterministically testable.
 */

export type ScrollFollowMode =
  | "FOLLOW_ACTIVITY"
  | "ANSWER_ANCHORED"
  | "MANUAL_READING";

export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export interface ScrollTransitionInput {
  mode: ScrollFollowMode;
  previousScrollTop: number;
  metrics: ScrollMetrics;
  nowMs: number;
  ignoreManualUntilMs: number;
  expectedProgrammaticTop: number | null;
  nearBottomThresholdPx?: number;
  devicePixelRatio?: number;
}

export interface ScrollTransitionDecision {
  nearBottom: boolean;
  isProgrammatic: boolean;
  scrollDelta: number;
  nextMode: ScrollFollowMode;
  clearExpectedProgrammaticTop: boolean;
  adoptManualAnchor: boolean;
}

export const DEFAULT_NEAR_BOTTOM_THRESHOLD_PX = 96;
export const MIN_SCROLLBAR_GUTTER_PX = 16;

/**
 * High-DPI scaling (125%, 150%, 175%) causes browsers to quantize fractional
 * scrollTop writes by up to ~2-3 CSS pixels. Scale tolerance slightly with DPR.
 */
export function programmaticScrollTolerancePx(devicePixelRatio = 1): number {
  const dpr = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  return dpr > 1 ? 3 : 2;
}

export function distanceFromBottom(metrics: ScrollMetrics): number {
  return Math.max(0, metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight);
}

export function isNearBottom(
  metrics: ScrollMetrics,
  thresholdPx = DEFAULT_NEAR_BOTTOM_THRESHOLD_PX,
  devicePixelRatio = 1,
): boolean {
  const dprSlack = devicePixelRatio > 1 ? 2 : 0.5;
  return distanceFromBottom(metrics) <= thresholdPx + dprSlack;
}

/**
 * Evaluate a scroll event into an explicit state transition without touching DOM.
 */
export function evaluateScrollEvent(
  input: ScrollTransitionInput,
): ScrollTransitionDecision {
  const {
    mode,
    previousScrollTop,
    metrics,
    nowMs,
    ignoreManualUntilMs,
    expectedProgrammaticTop,
    nearBottomThresholdPx = DEFAULT_NEAR_BOTTOM_THRESHOLD_PX,
    devicePixelRatio = 1,
  } = input;

  const tolerance = programmaticScrollTolerancePx(devicePixelRatio);
  const nearBottom = isNearBottom(metrics, nearBottomThresholdPx, devicePixelRatio);
  const scrollDelta = metrics.scrollTop - previousScrollTop;

  const isProgrammatic =
    nowMs < ignoreManualUntilMs &&
    expectedProgrammaticTop !== null &&
    Math.abs(metrics.scrollTop - expectedProgrammaticTop) <= tolerance;

  if (isProgrammatic) {
    return {
      nearBottom,
      isProgrammatic: true,
      scrollDelta,
      nextMode: mode,
      clearExpectedProgrammaticTop: true,
      adoptManualAnchor: false,
    };
  }

  // Upward scroll clear of the bottom band hands control to manual reading.
  const upwardThreshold = devicePixelRatio > 1 ? 1.5 : 1;
  if (metrics.scrollTop < previousScrollTop - upwardThreshold && !nearBottom) {
    return {
      nearBottom,
      isProgrammatic: false,
      scrollDelta,
      nextMode: "MANUAL_READING",
      clearExpectedProgrammaticTop: true,
      adoptManualAnchor: true,
    };
  }

  return {
    nearBottom,
    isProgrammatic: false,
    scrollDelta,
    nextMode: mode,
    clearExpectedProgrammaticTop: true,
    adoptManualAnchor: mode === "MANUAL_READING",
  };
}

export interface ScrollbarHitTestInput {
  clientX: number;
  rectLeft: number;
  rectRight: number;
  offsetWidth: number;
  clientWidth: number;
  direction?: "ltr" | "rtl";
}

/**
 * Detect whether a pointerdown landed on the native vertical scrollbar track
 * (which fires neither wheel nor touch events before dragging the thumb).
 */
export function isPointerOnVerticalScrollbarTrack(
  input: ScrollbarHitTestInput,
): boolean {
  const nativeTrackWidth = Math.max(0, input.offsetWidth - input.clientWidth);
  const hitWidth = Math.max(nativeTrackWidth, MIN_SCROLLBAR_GUTTER_PX);
  if (input.direction === "rtl") {
    return input.clientX <= input.rectLeft + hitWidth && input.clientX >= input.rectLeft;
  }
  return input.clientX >= input.rectRight - hitWidth && input.clientX <= input.rectRight;
}

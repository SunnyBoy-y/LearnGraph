import { describe, expect, it } from "vitest";

import {
  evaluateScrollEvent,
  isNearBottom,
  isPointerOnVerticalScrollbarTrack,
  programmaticScrollTolerancePx,
} from "@/features/chat/scroll-follow-core";

describe("scroll-follow-core pure state machine", () => {
  it("tolerates 1-3px sub-pixel drift under 125% and 150% display scaling for programmatic scroll", () => {
    expect(programmaticScrollTolerancePx(1)).toBe(2);
    expect(programmaticScrollTolerancePx(1.25)).toBe(3);
    expect(programmaticScrollTolerancePx(1.5)).toBe(3);

    const decision = evaluateScrollEvent({
      mode: "FOLLOW_ACTIVITY",
      previousScrollTop: 1000,
      metrics: { scrollTop: 1197.5, scrollHeight: 2000, clientHeight: 800 },
      nowMs: 50,
      ignoreManualUntilMs: 120,
      expectedProgrammaticTop: 1200,
      devicePixelRatio: 1.5,
    });
    expect(decision.isProgrammatic).toBe(true);
    expect(decision.nextMode).toBe("FOLLOW_ACTIVITY");
    expect(decision.nearBottom).toBe(true);
  });

  it("switches to MANUAL_READING when scrolling up away from bottom outside programmatic window", () => {
    const decision = evaluateScrollEvent({
      mode: "FOLLOW_ACTIVITY",
      previousScrollTop: 1200,
      metrics: { scrollTop: 900, scrollHeight: 2000, clientHeight: 800 },
      nowMs: 250,
      ignoreManualUntilMs: 120,
      expectedProgrammaticTop: null,
      devicePixelRatio: 1.25,
    });
    expect(decision.isProgrammatic).toBe(false);
    expect(decision.nearBottom).toBe(false);
    expect(decision.nextMode).toBe("MANUAL_READING");
    expect(decision.adoptManualAnchor).toBe(true);
  });

  it("accounts for DPR slack in isNearBottom", () => {
    expect(
      isNearBottom({ scrollTop: 1102.5, scrollHeight: 2000, clientHeight: 800 }, 96, 1.5),
    ).toBe(true);
    expect(
      isNearBottom({ scrollTop: 1000, scrollHeight: 2000, clientHeight: 800 }, 96, 1.5),
    ).toBe(false);
  });

  it("detects native vertical scrollbar track clicks in LTR and RTL", () => {
    // Classic 15px scrollbar on right edge (LTR)
    expect(
      isPointerOnVerticalScrollbarTrack({
        clientX: 792,
        rectLeft: 0,
        rectRight: 800,
        offsetWidth: 800,
        clientWidth: 785,
      }),
    ).toBe(true);

    // Click inside content area
    expect(
      isPointerOnVerticalScrollbarTrack({
        clientX: 500,
        rectLeft: 0,
        rectRight: 800,
        offsetWidth: 800,
        clientWidth: 785,
      }),
    ).toBe(false);

    // RTL scrollbar on left edge
    expect(
      isPointerOnVerticalScrollbarTrack({
        clientX: 10,
        rectLeft: 0,
        rectRight: 800,
        offsetWidth: 800,
        clientWidth: 785,
        direction: "rtl",
      }),
    ).toBe(true);
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  shouldEscapeStopRun,
  useEscapeStopRun,
} from "@/features/chat/escape-stop-run";

function Harness({
  isRunning,
  hasPendingApproval = false,
  hasLocalOverlay = false,
  onStop,
}: {
  isRunning: boolean;
  hasPendingApproval?: boolean;
  hasLocalOverlay?: boolean;
  onStop: () => void;
}) {
  useEscapeStopRun(
    {
      isRunning,
      hasPendingApproval,
      hasLocalOverlay,
      isComposerElement: (el) => Boolean(el?.closest("[data-chat-composer='true']")),
    },
    onStop,
  );
  return (
    <div>
      <input aria-label="侧栏重命名" />
      <div data-chat-composer="true">
        <textarea aria-label="主输入框" />
      </div>
    </div>
  );
}

describe("escape-stop-run layered priority", () => {
  it("never stops during IME composition (isComposing or keyCode 229)", () => {
    const allowed = shouldEscapeStopRun(
      {
        key: "Escape",
        defaultPrevented: false,
        repeat: false,
        isComposing: true,
        keyCode: 27,
        metaKey: false,
        ctrlKey: false,
        altKey: false,
        shiftKey: false,
        target: document.body,
      },
      { isRunning: true },
    );
    expect(allowed).toBe(false);

    const allowed229 = shouldEscapeStopRun(
      {
        key: "Escape",
        defaultPrevented: false,
        repeat: false,
        isComposing: false,
        keyCode: 229,
        metaKey: false,
        ctrlKey: false,
        altKey: false,
        shiftKey: false,
        target: document.body,
      },
      { isRunning: true },
    );
    expect(allowed229).toBe(false);
  });

  it("ignores Escape when focus is in a non-composer input, dialog is open, or approval is pending", () => {
    const onStop = vi.fn();
    const { rerender } = render(<Harness isRunning onStop={onStop} />);

    const sidebarInput = screen.getByLabelText("侧栏重命名");
    sidebarInput.focus();
    fireEvent.keyDown(sidebarInput, { key: "Escape" });
    expect(onStop).not.toHaveBeenCalled();

    // Main composer focus while running SHOULD stop
    const composer = screen.getByLabelText("主输入框");
    composer.focus();
    fireEvent.keyDown(composer, { key: "Escape" });
    expect(onStop).toHaveBeenCalledTimes(1);

    // Pending approval bar blocks Escape stop
    rerender(<Harness isRunning hasPendingApproval onStop={onStop} />);
    fireEvent.keyDown(composer, { key: "Escape" });
    expect(onStop).toHaveBeenCalledTimes(1);

    // Open dialog blocks Escape stop
    rerender(
      <>
        <Harness isRunning onStop={onStop} />
        <div role="dialog">Modal</div>
      </>,
    );
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("ignores Escape when user has an active text selection", () => {
    const onStop = vi.fn();
    const getSelectionSpy = vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: false,
      toString: () => "selected text",
    } as unknown as Selection);
    try {
      render(<Harness isRunning onStop={onStop} />);
      fireEvent.keyDown(document.body, { key: "Escape" });
      expect(onStop).not.toHaveBeenCalled();
    } finally {
      getSelectionSpy.mockRestore();
    }
  });
});

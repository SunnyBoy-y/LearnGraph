import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "./conversation";
import {
  useConversationScrollController,
  type ConversationScrollController,
} from "@/features/chat/use-conversation-scroll-controller";

vi.mock("@/features/chat/use-conversation-scroll-controller", () => ({
  useConversationScrollController: vi.fn(),
}));

// JSDOM cannot lay out the viewport, but can check the real positioning rules
// and portal ownership. Load only these rules, without Tailwind's CSS imports.
const css = readFileSync(resolve(import.meta.dirname, "../../index.css"), "utf8");
function rule(selector: string) {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Missing positioning rule: ${selector}`);
  return css.slice(start, css.indexOf("}", start) + 1);
}
let style: HTMLStyleElement;
let controller: ConversationScrollController;

beforeEach(() => {
  controller = {
    contentRef: vi.fn(), scrollRef: vi.fn(),
    hasCommittedAnswer: false, hasNewContent: true, isAtBottom: false,
    mode: "FOLLOW_ACTIVITY", tailSpace: 400,
    handleKeyDown: vi.fn(), handlePointerDown: vi.fn(), handleScroll: vi.fn(),
    handleTouchEnd: vi.fn(), handleTouchStart: vi.fn(), handleWheel: vi.fn(),
    notifyActivityUpdated: vi.fn(), notifyCommittedAnswerStarted: vi.fn(),
    notifyLayoutChanged: vi.fn(), notifyUserTurnStarted: vi.fn(),
    returnToLatest: vi.fn(), scrollMessageIntoView: vi.fn(),
    captureScrollSnapshot: vi.fn(), restoreScrollSnapshot: vi.fn(), resetConversation: vi.fn(),
  };
  vi.mocked(useConversationScrollController).mockReturnValue(controller);
  style = document.createElement("style");
  style.textContent = [
    rule(".chat-composer-dock"),
    rule(".chat-composer-dock > *"),
    rule(".chat-scroll-to-bottom"),
    rule(".chat-composer-dock > .chat-scroll-to-bottom"),
  ].join("\n");
  document.head.append(style);
});

afterEach(() => {
  cleanup();
  style.remove();
});

function MainChat({ dockHeight = 112, reservedHeight = 360 }) {
  const [dock, setDock] = useState<HTMLDivElement | null>(null);
  return (
    <div className="chat-canvas-page" style={{ "--composer-height": `${reservedHeight}px` } as React.CSSProperties}>
      <Conversation controller={controller}>
        <ConversationContent className="chat-messages-content">
          <p>AI 思考中</p>
        </ConversationContent>
        <ConversationScrollButton portalTarget={dock} />
      </Conversation>
      <div className="chat-composer-dock" ref={setDock} style={{ height: dockHeight }}>
        <textarea aria-label="输入消息" />
      </div>
    </div>
  );
}

describe("conversation return-to-latest button", () => {
  it("anchors the thinking-state button above the live composer, outside scrolling messages", () => {
    const view = render(<MainChat />);
    const button = screen.getByRole("button", { name: "还有新内容" });
    const dock = view.container.querySelector(".chat-composer-dock");
    expect(button.parentElement).toBe(dock);
    expect(screen.getByRole("log")).not.toContainElement(button);
    expect(view.container.querySelector("[data-conversation-scroll]")).not.toContainElement(button);
    expect(getComputedStyle(button).position).toBe("absolute");
    expect(getComputedStyle(button).bottom).toBe("calc(100% + 8px)");
    // The dock normally makes its children relative, so this override is vital.
    expect(getComputedStyle(button).pointerEvents).toBe("auto");
    expect(getComputedStyle(button).zIndex).toBe("30");
    fireEvent.click(button);
    expect(controller.returnToLatest).toHaveBeenCalledOnce();
  });

  it("does not use the locked tail reservation when the composer shrinks during streaming", () => {
    const view = render(<MainChat dockHeight={360} reservedHeight={360} />);
    const scroller = view.container.querySelector<HTMLElement>("[data-conversation-scroll]")!;
    scroller.scrollTop = 240;
    view.rerender(<MainChat dockHeight={112} reservedHeight={360} />);
    const button = screen.getByRole("button", { name: "还有新内容" });
    expect(button.parentElement).toHaveStyle({ height: "112px" });
    expect(getComputedStyle(button).bottom).toBe("calc(100% + 8px)");
    expect(scroller.scrollTop).toBe(240);
    // Growing toolbars/attachments also follows the same actual dock, not messages.
    view.rerender(<MainChat dockHeight={180} reservedHeight={500} />);
    expect(button.parentElement).toHaveStyle({ height: "180px" });
    expect(getComputedStyle(button).bottom).toBe("calc(100% + 8px)");
    expect(scroller.scrollTop).toBe(240);
  });

  it("keeps the answer-generation label and click behavior after portalling", () => {
    controller.hasCommittedAnswer = true;
    render(<MainChat />);
    fireEvent.click(screen.getByRole("button", { name: "正文仍在生成" }));
    expect(controller.returnToLatest).toHaveBeenCalledOnce();
  });

  it("still offers return-to-latest in manual reading without new content", () => {
    controller.hasNewContent = false;
    controller.mode = "MANUAL_READING";
    render(<MainChat />);
    expect(screen.getByRole("button", { name: "回到最新" })).toBeInTheDocument();
  });

  it("hides the portalled button once the reader reaches the bottom", () => {
    const view = render(<MainChat />);
    controller.hasNewContent = false;
    controller.isAtBottom = true;
    view.rerender(<MainChat />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("waits for the composer host instead of briefly floating inside the message log", () => {
    render(<Conversation controller={controller}><ConversationScrollButton portalTarget={null} /></Conversation>);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps non-portal document and rail buttons local, independent of the main composer height", () => {
    render(<div style={{ "--composer-height": "500px" } as React.CSSProperties}>
      <Conversation controller={controller}><ConversationScrollButton /></Conversation>
    </div>);
    const button = screen.getByRole("button", { name: "还有新内容" });
    expect(screen.getByRole("log")).toContainElement(button);
    expect(getComputedStyle(button).bottom).toBe("16px");
  });

  it("mirrors the scroller's symmetric scrollbar gutter onto the parent chat canvas", () => {
    const view = render(<MainChat />);
    const canvas = view.container.querySelector<HTMLElement>(".chat-canvas-page")!;
    const scroller = view.container.querySelector<HTMLElement>("[data-conversation-scroll]")!;
    Object.defineProperty(scroller, "offsetWidth", { configurable: true, value: 800 });
    Object.defineProperty(scroller, "clientWidth", { configurable: true, value: 780 });
    // Trigger ResizeObserver / initial measurement via a fresh mount with known widths.
    view.unmount();
    const origOffsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
    const origClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get() { return this.hasAttribute("data-conversation-scroll") ? 800 : 0; } });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get() { return this.hasAttribute("data-conversation-scroll") ? 780 : 0; } });
    try {
      const remounted = render(<MainChat />);
      const remountedCanvas = remounted.container.querySelector<HTMLElement>(".chat-canvas-page")!;
      expect(remountedCanvas.style.getPropertyValue("--chat-scroll-gutter")).toBe("10px");
    } finally {
      if (origOffsetWidth) Object.defineProperty(HTMLElement.prototype, "offsetWidth", origOffsetWidth);
      else delete (HTMLElement.prototype as { offsetWidth?: number }).offsetWidth;
      if (origClientWidth) Object.defineProperty(HTMLElement.prototype, "clientWidth", origClientWidth);
      else delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
    }
    expect(canvas.style.getPropertyValue("--chat-scroll-gutter")).toBe("0px");
  });
});

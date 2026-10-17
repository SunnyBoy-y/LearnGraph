import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExpandableContent } from "./expandable-content";

describe("ExpandableContent", () => {
  it("defers streaming layout measurements and cancels superseded frames", () => {
    const frames = new Map<number, FrameRequestCallback>();
    let nextId = 0;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.set(++nextId, callback);
      return nextId;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      frames.delete(id);
    });
    const readHeight = vi
      .spyOn(HTMLElement.prototype, "scrollHeight", "get")
      .mockReturnValue(1200);
    const { rerender } = render(
      <ExpandableContent lineLimit={3} streaming>
        <p>首段</p>
      </ExpandableContent>,
    );
    rerender(
      <ExpandableContent lineLimit={3} streaming>
        <p>首段与新增内容</p>
      </ExpandableContent>,
    );
    expect(readHeight).not.toHaveBeenCalled();
    expect(frames.size).toBe(1);
    act(() => {
      for (const callback of frames.values()) callback(0);
      frames.clear();
    });
    expect(
      screen.getByRole("button", { name: /展开内容/ }),
    ).toBeInTheDocument();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("folds the complete DOM tree and remembers an explicit expansion", async () => {
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(
      function scrollHeight() {
        return this.classList.contains("expandable-content__body") ? 1200 : 0;
      },
    );

    const { unmount } = render(
      <ExpandableContent lineLimit={3} storageKey="test-expandable">
        <pre>
          {Array.from({ length: 80 }, (_, index) => `line ${index}`).join("\n")}
        </pre>
      </ExpandableContent>,
    );

    const button = await screen.findByRole("button", { name: /展开内容/u });
    expect(button).toBeInTheDocument();
    expect(document.querySelector("pre")?.textContent).toContain("line 79");

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(localStorage.getItem("test-expandable")).toBe("1");

    unmount();
    render(
      <ExpandableContent lineLimit={3} storageKey="test-expandable">
        <pre>same complete source</pre>
      </ExpandableContent>,
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /收起内容/u }),
      ).toBeInTheDocument();
    });
  });
});

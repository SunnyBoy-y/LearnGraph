import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExpandableContent } from "./expandable-content";

describe("ExpandableContent", () => {
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
        <pre>{Array.from({ length: 80 }, (_, index) => `line ${index}`).join("\n")}</pre>
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
      expect(screen.getByRole("button", { name: /收起内容/u })).toBeInTheDocument();
    });
  });
});

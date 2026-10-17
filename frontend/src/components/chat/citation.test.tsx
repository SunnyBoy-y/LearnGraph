import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Citation, type CitationSource } from "./citation";

const sources: CitationSource[] = [
  { id: "5", url: "https://memory.example/paper", siteName: "Memory Papers", title: "GroupMemBench paper" },
  { id: "12", url: "https://research.example/paper", siteName: "Microsoft Research", title: "GroupMemBench: Benchmarking LLM Agent Memory", publishedAt: "2026-05-01" },
];

afterEach(() => vi.useRealTimers());

describe("Citation", () => {
  it("opens a structured group on click and dismisses with Escape", async () => {
    render(<Citation sources={sources} />);
    const trigger = screen.getByRole("button", { name: /Memory Papers/ });
    expect(trigger).toHaveTextContent("Memory Papers· +1");
    expect(trigger).not.toHaveAttribute("title");
    fireEvent.click(trigger);
    const card = await screen.findByRole("dialog", { name: "引用来源" });
    expect(card.parentElement?.closest(".message-text-block")).toBeNull();
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(screen.getByRole("link", { name: /Microsoft Research/ })).toHaveAttribute("href", sources[1].url);
    expect(screen.getByRole("link", { name: /Memory Papers/ })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("May 1, 2026")).toBeVisible();
    expect(card.querySelectorAll("time")).toHaveLength(1);
    fireEvent.keyDown(card, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("delays hover, keeps the card open while crossing the gap, then closes", async () => {
    vi.useFakeTimers();
    render(<Citation sources={sources} />);
    const trigger = screen.getByRole("button");
    fireEvent.pointerEnter(trigger, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(119));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    const card = screen.getByRole("dialog");
    fireEvent.pointerLeave(trigger, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(150));
    fireEvent.pointerEnter(card, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(250));
    expect(card).toBeVisible();
    fireEvent.pointerLeave(card, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(199));
    expect(card).toBeVisible();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("uses a domain and globe when metadata is missing and rejects unsafe URLs", async () => {
    render(<Citation sources={[
      { id: "1", url: "https://www.example.com/page", title: "A source", faviconUrl: "javascript:alert(1)" },
      { id: "2", url: "javascript:alert(1)", title: "Unavailable source" },
      { id: "3", url: "data:text/html,unsafe", title: "Unsafe data" },
      { id: "4", url: "//evil.example/path", title: "Protocol relative" },
    ]} />);
    fireEvent.click(screen.getByRole("button", { name: /example.com/ }));
    await screen.findByRole("dialog");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link")).toHaveAttribute("target", "_blank");
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("time")).toBeNull();
  });

  it("falls back after a favicon fails and refreshes structured metadata", async () => {
    const first = { ...sources[0], faviconUrl: "https://memory.example/icon.png" };
    const { rerender } = render(<Citation sources={[first]} />);
    fireEvent.error(document.querySelector("img")!);
    expect(document.querySelector("img")).toBeNull();
    rerender(<Citation sources={[{ ...first, siteName: "Updated name" }]} />);
    fireEvent.click(screen.getByRole("button", { name: /Updated name/ }));
    expect(await screen.findByRole("link")).toHaveTextContent("Updated name");
    expect(screen.getByRole("button")).not.toHaveTextContent("+1");
  });

  it("opens on touch/click and closes on an outside press", async () => {
    render(<><Citation sources={[sources[0]]} /><button>Outside</button></>);
    const trigger = screen.getByRole("button", { name: /Memory Papers/ });
    fireEvent.pointerDown(trigger, { pointerType: "touch" });
    fireEvent.click(trigger);
    const card = await screen.findByRole("dialog");
    fireEvent.pointerLeave(trigger, { pointerType: "touch" });
    expect(card).toBeVisible();
    // Radix installs the outside-pointer listener after the opening event.
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
    fireEvent.pointerDown(screen.getByRole("button", { name: "Outside" }));
    fireEvent.click(screen.getByRole("button", { name: "Outside" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

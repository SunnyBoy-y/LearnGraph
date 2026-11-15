import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThinkingSlider, supportedThinkingStops, thinkingStopIndex } from "./thinking-slider";
vi.mock("@/lib/native-bridge", () => ({ haptic: vi.fn() }));

describe("model-supported thinking stops", () => {
  it("hides unsupported levels instead of disabling fake stops", () => {
    const stops = supportedThinkingStops(["max", "high"]);
    const changed = vi.fn();
    render(<ThinkingSlider stops={stops} value={0} onChange={changed} />);
    expect(screen.queryByText("极高")).toBeNull();
    expect(screen.queryByText("低")).toBeNull();
    expect(screen.getByText("最高")).toBeDefined();
    expect(stops.map(s => s.thinking)).toEqual(["off", "high", "max"]);
    fireEvent.keyDown(screen.getByRole("slider"), { key: "End" });
    expect(changed).toHaveBeenCalledWith(2);
    expect(stops[2].thinking).toBe("max");
  });
  it("keeps max and xhigh distinct and locates the filtered index", () => {
    const stops = supportedThinkingStops(["low", "high", "xhigh", "max"], true);
    expect(stops.map(s => s.thinking)).toEqual(["low", "high", "xhigh", "max"]);
    expect(thinkingStopIndex("agentic", "max", stops)).toBe(3);
    expect(thinkingStopIndex("agentic", "xhigh", stops)).toBe(2);
  });
  it("keeps a drag active across controlled value renders", () => {
    const changed = vi.fn();
    const modes = ["high", "max"];
    const view = render(<ThinkingSlider stops={supportedThinkingStops(modes)} value={0} onChange={changed} />);
    const slider = screen.getByRole("slider");
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({ left: 0, width: 200 } as DOMRect);
    fireEvent(slider, new MouseEvent("pointerdown", { bubbles: true, clientX: 100 }));
    expect(changed).toHaveBeenLastCalledWith(1);
    view.rerender(<ThinkingSlider stops={supportedThinkingStops(modes)} value={1} onChange={changed} />);
    fireEvent(slider, new MouseEvent("pointermove", { bubbles: true, clientX: 200 }));
    expect(changed).toHaveBeenLastCalledWith(2);
  });
  it("handles a single supported stop without NaN or changes", () => {
    const changed = vi.fn();
    render(<ThinkingSlider stops={supportedThinkingStops(["high"], true)} value={0} onChange={changed} />);
    expect(screen.getByRole("slider").getAttribute("aria-valuemax")).toBe("0");
    fireEvent.keyDown(screen.getByRole("slider"), { key: "End" });
    expect(changed).not.toHaveBeenCalled();
  });
});

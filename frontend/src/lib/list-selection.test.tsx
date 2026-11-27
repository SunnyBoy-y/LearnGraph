import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Checkbox } from "@/components/ui/checkbox";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import { applyRangeSelection, useListSelection } from "./list-selection";

function SelectionHarness({
  ids,
  alwaysActive = false,
}: {
  ids: string[];
  alwaysActive?: boolean;
}) {
  const selection = useListSelection(ids, {
    initialMultiSelect: true,
    alwaysActive,
  });
  return (
    <div>
      <span data-testid="mode">{selection.multiSelect ? "on" : "off"}</span>
      <span data-testid="count">{selection.selectedCount}</span>
      <span data-testid="other">{selection.otherSelectedCount}</span>
      <button type="button" onClick={() => {
        selection.toggleItem(ids[0], { checked: true });
        selection.toggleItem(ids[2], { checked: true, shiftKey: true });
      }}>批量更新选择</button>
      <Checkbox
        aria-label="全选当前页"
        checked={selection.visibleCheckboxState}
        onCheckedChange={(checked) => selection.toggleVisible(checked === true)}
      />
      {ids.map((id) => (
        <Checkbox key={id} aria-label={`选择 ${id}`} {...selection.checkboxProps(id)} />
      ))}
    </div>
  );
}

describe("list-selection", () => {
  it("supports Shift+Click range selection across ordered items", () => {
    const next = applyRangeSelection(new Set(["a"]), ["a", "b", "c", "d"], "a", "c", true);
    expect([...next]).toEqual(["a", "b", "c"]);
    const reduced = applyRangeSelection(next, ["a", "b", "c", "d"], "b", "c", false);
    expect([...reduced]).toEqual(["a"]);
  });

  it("handles Shift+Click via checkboxProps and Esc to exit multi-select", () => {
    render(<SelectionHarness ids={["item-1", "item-2", "item-3", "item-4"]} />);

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 item-1" }));
    expect(screen.getByTestId("count")).toHaveTextContent("1");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 item-3" }), { shiftKey: true });
    expect(screen.getByTestId("count")).toHaveTextContent("3");
    expect(screen.getByRole("checkbox", { name: "选择 item-2" })).toHaveAttribute("data-state", "checked");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByTestId("mode")).toHaveTextContent("off");
    expect(screen.getByTestId("count")).toHaveTextContent("0");
  });

  it("uses the previous anchor when range selection is queued in the same event", () => {
    render(<SelectionHarness ids={["item-1", "item-2", "item-3", "item-4"]} />);
    fireEvent.click(screen.getByRole("button", { name: "批量更新选择" }));
    expect(screen.getByTestId("count")).toHaveTextContent("3");
    expect(screen.getByRole("checkbox", { name: "选择 item-2" })).toHaveAttribute("data-state", "checked");
  });

  it("reports cross-page selected count when visible page changes", () => {
    const { rerender } = render(<SelectionHarness ids={["p1-a", "p1-b"]} alwaysActive />);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 p1-a" }));
    expect(screen.getByTestId("other")).toHaveTextContent("0");

    rerender(<SelectionHarness ids={["p2-a", "p2-b"]} alwaysActive />);
    expect(screen.getByTestId("count")).toHaveTextContent("1");
    expect(screen.getByTestId("other")).toHaveTextContent("1");
  });

  it("ClearableSearchInput clears text and refocuses input", () => {
    const onChange = vi.fn();
    render(<ClearableSearchInput aria-label="搜索测试" onChange={onChange} value="hello" />);
    const clearBtn = screen.getByRole("button", { name: "清空搜索" });
    fireEvent.click(clearBtn);
    expect(onChange).toHaveBeenCalledWith("");
    expect(screen.getByRole("textbox", { name: "搜索测试" })).toHaveFocus();
  });
});

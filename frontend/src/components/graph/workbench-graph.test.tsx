import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { KnowledgeNode } from "./knowledge-graph";
import { graphLayoutFixture } from "./workbench-layout-fixture";
import { WorkbenchGraph } from "./workbench-graph";

vi.mock("@xyflow/react", () => ({
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  MiniMap: () => <div data-testid="minimap" />,
  ViewportPortal: ({ children }: { children: ReactNode }) => children,
  ReactFlow: ({ nodes, children }: { nodes: KnowledgeNode[]; children: ReactNode }) => <div data-testid="canvas">{nodes.map(node => <div key={node.id} data-node={node.id} data-position={`${node.position.x},${node.position.y}`} />)}{children}</div>,
}));

const canvasPositions = () => Array.from(screen.getByTestId("canvas").querySelectorAll<HTMLElement>("[data-node]"), el => [el.dataset.node, el.dataset.position]);

describe("workbench modes", () => {
  it.each([7, 20, 50, 100])("switches %i nodes independently and renders a canvas-free catalog", { timeout: 15000 }, size => {
    const fixture = graphLayoutFixture(size);
    const select = vi.fn();
    const { container } = render(<WorkbenchGraph {...fixture} onSelect={select} />);
    expect(screen.getByRole("button", { name: "学习路线", pressed: true })).toBeTruthy();
    const route = canvasPositions();
    expect(route).toHaveLength(size);
    fireEvent.click(screen.getByRole("button", { name: "知识关系" }));
    expect(canvasPositions()).not.toEqual(route);
    fireEvent.click(screen.getByRole("button", { name: "节点总览" }));
    expect(screen.queryByTestId("canvas")).toBeNull();
    expect(screen.queryByRole("button", { name: "放大图谱" })).toBeNull();
    expect(container.querySelectorAll("[data-card-id]")).toHaveLength(size);
    const card = container.querySelector<HTMLElement>('[data-card-id="n4"]')!;
    expect(card.classList.contains("wb-card--locked")).toBe(true);
    fireEvent.click(within(card).getByRole("button"));
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: "n4" }));
    fireEvent.click(screen.getByRole("button", { name: "学习路线" }));
    expect(canvasPositions()).toEqual(route);
  });

  it("keeps layout positions when learning status, search and selection change", () => {
    const fixture = graphLayoutFixture(7);
    const { rerender } = render(<WorkbenchGraph {...fixture} />);
    const before = canvasPositions();
    rerender(<WorkbenchGraph {...fixture} nodes={fixture.nodes.map(node => ({ ...node, data: { ...node.data, stars: 3 } }))} currentId="n4" selectedId="n4" matchIds={["n4"]} />);
    expect(canvasPositions()).toEqual(before);
  });

  it("highlights connected edges, dims unrelated ones, and reveals association edges on demand without re-layout", () => {
    const fixture = graphLayoutFixture(7);
    const edgesWithAssoc = [
      ...fixture.edges,
      { id: "assoc-1-5", source: "n1", target: "n5", label: "关联", data: { relation: "related" } },
    ];
    const { container, rerender } = render(<WorkbenchGraph nodes={fixture.nodes} edges={edgesWithAssoc} />);
    const before = canvasPositions();
    // Association edge is hidden by default in route view
    expect(container.querySelectorAll(".wb-edge--association")).toHaveLength(0);

    // Selecting n1 reveals its association edge and highlights connected edges without moving nodes
    rerender(<WorkbenchGraph nodes={fixture.nodes} edges={edgesWithAssoc} selectedId="n1" />);
    expect(canvasPositions()).toEqual(before);
    expect(container.querySelectorAll(".wb-edge--association")).toHaveLength(1);
    expect(container.querySelectorAll(".wb-edge--active").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".wb-edge--dim").length).toBeGreaterThan(0);

    // Toggling "仅显示主要关系" shows all association edges without re-layout
    rerender(<WorkbenchGraph nodes={fixture.nodes} edges={edgesWithAssoc} />);
    expect(container.querySelectorAll(".wb-edge--association")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "仅显示主要关系" }));
    expect(container.querySelectorAll(".wb-edge--association")).toHaveLength(1);
    expect(canvasPositions()).toEqual(before);
  });
});

import { describe, expect, it } from "vitest";
import {
  buildRoundedOrthogonalPath,
  CARD,
  filterWorkbenchEdges,
  frameWorkbench,
  layoutWorkbench,
  routeWorkbenchEdges,
  structuralNodes,
  workbenchIsClipped,
} from "./workbench-layout";
import { layoutWorkbenchWithElk } from "./workbench-elk";
import { graphLayoutFixture } from "./workbench-layout-fixture";

const sizes = [7, 20, 50, 100];

describe("workbench geometry", () => {
  it.each(sizes)("places, indexes and safely routes %i nodes", count => {
    const { nodes, edges } = graphLayoutFixture(count);
    const structure = structuralNodes(nodes);
    for (const mode of ["route", "relations"] as const) {
      const layout = layoutWorkbench(structure, edges, mode);
      const all = [...layout.boxes];
      expect(all).toHaveLength(count);
      expect(layout.boxes.get("n0")?.width).toBe(CARD.goalWidth);
      expect(layout.bounds?.x).toBeGreaterThan(-9999);
      for (const [index, [, a]] of all.entries()) for (const [, b] of all.slice(index + 1)) {
        expect(a.x >= b.x + b.width || b.x >= a.x + a.width || a.y >= b.y + b.height || b.y >= a.y + a.height).toBe(true);
      }
      const actual = routeWorkbenchEdges(layout.boxes, edges);
      expect(actual).toHaveLength(edges.length);
      for (const { edge, points } of actual) {
        for (let i = 1; i < points.length; i++) {
          const [a, b] = [points[i - 1], points[i]];
          for (const [id, box] of all) {
            const crosses = Math.max(a.x, b.x) > box.x && Math.min(a.x, b.x) < box.x + box.width && Math.max(a.y, b.y) > box.y && Math.min(a.y, b.y) < box.y + box.height;
            if (crosses) throw new Error(`Edge ${edge.id} enters card ${id}`);
          }
        }
      }
    }
    const catalog = layoutWorkbench(structure, edges, "catalog");
    expect(catalog.groups.flatMap(group => group.ids)).toHaveLength(count - 1);
    expect(catalog.boxes.size).toBe(0);
  });

  it("fits seven cards at desktop and resized dimensions without a minimap, and frames a readable portion of large graphs", () => {
    for (const mode of ["route", "relations"] as const) for (const count of [7, 100]) {
      const { nodes, edges } = graphLayoutFixture(count);
      const { bounds, boxes } = layoutWorkbench(structuralNodes(nodes), edges, mode);
      for (const size of [{ width: 1440, height: 790 }, { width: 1050, height: 550 }]) {
        const frame = frameWorkbench(bounds!, size, mode, boxes.get("n0"));
        expect(frame.zoom).toBeGreaterThanOrEqual(count === 7 ? .62 : .48);
        expect(workbenchIsClipped(bounds, size, frame)).toBe(count > 7);
      }
    }
  });

  it("ranks actual prerequisites and condenses cycles without ranking semantic edges", () => {
    const { nodes, edges } = graphLayoutFixture(20);
    const structure = structuralNodes(nodes);
    const route = layoutWorkbench(structure, edges, "route");
    const get = (id: string) => route.boxes.get(id)!;
    expect(get("n1").column).toBe(get("n2").column);
    expect(get("n5").column).toBeGreaterThan(get("n1").column);
    expect(get("n19")).toBeDefined();
    const filtered = layoutWorkbench(structure, edges.filter(edge => edge.data?.relation !== "prerequisite"), "route");
    expect(filtered.groups).toHaveLength(1);
    expect(layoutWorkbench(structure, edges, "relations").headings.some(h => h.label === "未建立关系")).toBe(true);
  });

  it("merges same-source same-relation forward edges into a shared trunk with unified ports", () => {
    const { nodes } = graphLayoutFixture(7);
    const edges = [1, 2, 3].map(i => ({ id: `real-n${i}`, source: "n0", target: `n${i}`, data: { relation: "contains" } }));
    const { boxes } = layoutWorkbench(structuralNodes(nodes.slice(0, 4)), edges, "route");
    const routes = routeWorkbenchEdges(boxes, edges);
    expect(new Set(routes.map(route => route.points[0].y)).size).toBe(1);
    const branched = routes.filter(route => route.points.length > 2);
    expect(branched.length).toBeGreaterThan(0);
    expect(new Set(branched.map(route => route.points[1].x)).size).toBe(1);
    expect(routes.every(route => route.sharedTrunk)).toBe(true);
    expect(branched.every(route => route.path.includes("Q"))).toBe(true);
  });

  it("keeps both prerequisite and non-prerequisite links between the same pair and drops filtered endpoints", () => {
    const { nodes, edges } = graphLayoutFixture(7);
    const layout = layoutWorkbench(structuralNodes(nodes.slice(0, 5)), edges, "route");
    const edge = edges.find(edge => edge.id === "1-4-prerequisite")!;
    const routes = routeWorkbenchEdges(layout.boxes, [...edges, { ...edge, id: "copy" }, { ...edge, id: "semantic", data: { relation: "related" } }]);
    expect(routes.map(route => route.edge.id)).toEqual(["0-1-contains", "0-2-contains", "0-3-contains", "1-4-prerequisite", "semantic"]);
    expect(routes[3].path).not.toBe(routes[4].path);
  });

  it("filters association edges in route view unless revealed by selection/hover or toggle, while keeping all in relations view", () => {
    const { edges } = graphLayoutFixture(20);
    const hasRelated = edges.some(e => e.data?.relation === "related");
    expect(hasRelated).toBe(true);

    const routeDefault = filterWorkbenchEdges(edges, "route");
    expect(routeDefault.some(e => e.data?.relation === "related")).toBe(false);
    expect(routeDefault.some(e => e.data?.relation === "prerequisite")).toBe(true);
    expect(routeDefault.some(e => e.data?.relation === "contains")).toBe(true);

    const sampleRelated = edges.find(e => e.data?.relation === "related")!;
    const routeHovered = filterWorkbenchEdges(edges, "route", { activeNodeIds: new Set([sampleRelated.source]) });
    expect(routeHovered.some(e => e.id === sampleRelated.id)).toBe(true);

    const routeShowAll = filterWorkbenchEdges(edges, "route", { showAllRelations: true });
    expect(routeShowAll.length).toBe(edges.length);

    expect(filterWorkbenchEdges(edges, "relations").length).toBe(edges.length);
    expect(filterWorkbenchEdges(edges, "catalog").length).toBe(0);
  });

  it("produces rounded orthogonal paths and obstacle-free ELK layered layout for route view", async () => {
    const rounded = buildRoundedOrthogonalPath([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }], 7);
    expect(rounded).toContain("Q100.0,0.0");

    const { nodes, edges } = graphLayoutFixture(7);
    const elk = await layoutWorkbenchWithElk(structuralNodes(nodes), edges, "route");
    expect(elk).not.toBeNull();
    const all = [...elk!.boxes];
    expect(all).toHaveLength(7);
    expect(elk!.headings.map(h => h.label)).toEqual(["学习目标", "阶段 01", "阶段 02", "阶段 03"]);
    for (const [index, [, a]] of all.entries()) for (const [, b] of all.slice(index + 1)) {
      expect(a.x >= b.x + b.width || b.x >= a.x + a.width || a.y >= b.y + b.height || b.y >= a.y + a.height).toBe(true);
    }
    const routes = routeWorkbenchEdges(elk!.boxes, edges, elk!.elkEdgePoints);
    for (const { edge, points } of routes) {
      for (let i = 1; i < points.length; i++) {
        const [a, b] = [points[i - 1], points[i]];
        for (const [id, box] of all) {
          const crosses = Math.max(a.x, b.x) > box.x && Math.min(a.x, b.x) < box.x + box.width && Math.max(a.y, b.y) > box.y && Math.min(a.y, b.y) < box.y + box.height;
          if (crosses) throw new Error(`ELK Edge ${edge.id} enters card ${id}`);
        }
      }
    }
  });
});

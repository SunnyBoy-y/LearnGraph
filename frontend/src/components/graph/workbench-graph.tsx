import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Background, BackgroundVariant, MiniMap, ReactFlow, ViewportPortal, type Edge, type NodeProps, type ReactFlowInstance, type Viewport } from "@xyflow/react";
import { Box, Download, GitFork, LayoutDashboard, Lock, RotateCcw, Sparkles, ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { toast } from "@/lib/notifications";
import { buildGraphSvg, downloadPngFile, downloadSvgFile, sanitizeFilename } from "./graph-export";
import type { KnowledgeGraphProps, KnowledgeNode } from "./knowledge-graph";
import { GraphLegend } from "./graph-legend";
import { NODE_PRIMARY_ACTIONS, nodeExecutionLabel, resolveLearningStatus, resolveNodeTypeProfile, type NodeLearningStatusId, type NodeTypeId } from "./node-presentation";
import {
  buildRoundedOrthogonalPath,
  filterWorkbenchEdges,
  frameWorkbench,
  graphBounds,
  layoutWorkbench,
  routeWorkbenchEdgesWithJunctions,
  structuralNodes,
  workbenchIsClipped,
  WORKBENCH_MIN_ZOOM,
  type EdgeRoute,
  type TrunkJunction,
  type WorkbenchLayoutResult,
  type WorkbenchView,
} from "./workbench-layout";
import { layoutWorkbenchWithElk } from "./workbench-elk";
import "./workbench-graph.css";

const MIN_ZOOM = WORKBENCH_MIN_ZOOM;
const MODES = [{ id: "route", label: "学习路线", icon: GitFork }, { id: "relations", label: "知识关系", icon: Box }, { id: "catalog", label: "节点总览", icon: LayoutDashboard }] as const;
const EMPTY_EDGES: Edge[] = [];
const INITIAL_VIEWPORT = { x: 0, y: 0, zoom: 1 };

type CardProps = { node: KnowledgeNode; active?: boolean; lod: "far" | "normal" | "near"; onSelect?: () => void; onStudy?: () => void; onExplore?: () => void };
const statusOf = (node: KnowledgeNode) => resolveLearningStatus({ stars: node.data.stars, retrievalState: node.data.state, blockedByPrerequisite: node.data.blockedByPrerequisite, attentionState: node.data.attention_state as string | undefined });

function WorkbenchCard({ node, active, lod, onSelect, onStudy, onExplore }: CardProps) {
  const { data } = node;
  const profile = resolveNodeTypeProfile(data.nodeType, { root: data.root });
  const status = statusOf(node);
  const locked = status.id === "locked";
  const Icon = profile.icon;
  const goalProgress = data.goalProgress as { completed: number; total: number } | undefined;
  const execution = nodeExecutionLabel(profile.id, typeof data.executionState === "string" ? data.executionState : null);
  const statusLabel = goalProgress ? `完成 ${goalProgress.completed}/${goalProgress.total} 个下级节点` : execution ?? status.label;
  return <article className={cn("wb-card", data.root && "wb-card--goal", locked && "wb-card--locked", active && "wb-card--active", data.matched && "wb-card--matched")} data-lod={lod} data-card-id={node.id}>
    <button type="button" className="wb-card__select nodrag nopan" aria-label={`${data.label}，${profile.label}，${status.label}`} aria-pressed={Boolean(active)} onClick={event => { event.stopPropagation(); onSelect?.(); }} title={data.label} />
    <div className="wb-card__meta">
      <span className={cn("wb-card__type", `knowledge-node__type--${profile.tone}`)}><Icon aria-hidden="true" />{lod !== "far" && profile.label}</span>
      {data.hasLearningPage && lod !== "far" && <Sparkles aria-label="已生成交互页" />}
      {locked && <Lock aria-label="未解锁" />}
      {data.planMarker && lod !== "far" && <span className="wb-card__plan">{data.planMarker.label}</span>}
    </div>
    <strong className="wb-card__label">{data.label}</strong>
    {lod === "near" && data.description && <p className="wb-card__summary" title={data.description}>{data.description}</p>}
    {lod !== "far" && <div className="wb-card__footer"><span title={statusLabel}>{statusLabel}</span>
      <div className="wb-card__actions">
        {lod === "near" && !data.root && onExplore && <button type="button" className="wb-card__study nodrag nopan" onClick={event => { event.stopPropagation(); onExplore(); }}>深挖</button>}
        {onStudy && !locked && <button type="button" className="wb-card__study nodrag nopan" onClick={event => { event.stopPropagation(); onStudy(); }}>{NODE_PRIMARY_ACTIONS[profile.id]}</button>}
      </div>
    </div>}
  </article>;
}

const FlowCard = memo(function FlowCard({ id, data, selected }: NodeProps<KnowledgeNode>) {
  return <WorkbenchCard node={{ id, data, type: "knowledge", position: { x: 0, y: 0 } }} active={selected} lod={data.lod as CardProps["lod"]} onSelect={() => (data.selectCard as (() => void) | undefined)?.()} onStudy={data.onStudy ? () => data.onStudy?.(id) : undefined} onExplore={data.onOpenExplore ? () => data.onOpenExplore?.(id) : undefined} />;
});
const nodeTypes = { knowledge: FlowCard };

type WorkbenchRoutesResult = EdgeRoute[];

const WorkbenchViewportLayer = memo(function WorkbenchViewportLayer({
  regions,
  headings,
  routes,
  junctions,
  activeFocusIds,
  planEdgeIds,
  lod,
  markerId,
  view,
}: {
  regions: WorkbenchLayoutResult["regions"];
  headings: WorkbenchLayoutResult["headings"];
  routes: WorkbenchRoutesResult;
  junctions: TrunkJunction[];
  activeFocusIds: ReadonlySet<string>;
  planEdgeIds: ReadonlySet<string>;
  lod: CardProps["lod"];
  markerId: string;
  view: WorkbenchView;
}) {
  const hasFocus = activeFocusIds.size > 0;
  const activeEdgeIds = useMemo(() => {
    const set = new Set<string>();
    if (!hasFocus) return set;
    for (const { edge } of routes) {
      if (activeFocusIds.has(edge.source) || activeFocusIds.has(edge.target)) {
        set.add(edge.id);
      }
    }
    return set;
  }, [activeFocusIds, hasFocus, routes]);

  return (
    <ViewportPortal>
      {regions.map(({ tone, ...region }) => <div key={tone} className={`wb-graph__region wb-graph__region--${tone}`} style={{ left: region.x, top: region.y, width: region.width, height: region.height }} />)}
      <svg className="wb-graph__relations" width="1" height="1" aria-hidden="true">
        <defs>
          <marker id={markerId} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="var(--wb-edge-prereq)" /></marker>
          <marker id={`${markerId}-active`} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="var(--wb-edge-active)" /></marker>
          <marker id={`${markerId}-plan`} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="var(--wb-edge-plan)" /></marker>
        </defs>
        {routes.map(({ edge, path, label, relationCategory, suppressLabel }) => {
          const strong = activeEdgeIds.has(edge.id);
          const dim = hasFocus && !strong;
          const prerequisite = relationCategory === "prerequisite";
          const plan = planEdgeIds.has(edge.id);
          const arrowId = plan ? `${markerId}-plan` : strong ? `${markerId}-active` : markerId;
          const showLabel = lod !== "far" && !suppressLabel && edge.label != null && (strong || prerequisite || view === "relations");
          return <g key={edge.id} className={cn("wb-edge", `wb-edge--${relationCategory}`, strong && "wb-edge--active", dim && "wb-edge--dim", plan && "wb-edge--plan")}>
            <path d={path} markerEnd={prerequisite || plan ? `url(#${arrowId})` : undefined} />
            {showLabel && <text x={label.x} y={label.y}>{String(edge.label)}</text>}
          </g>;
        })}
        {junctions.map((junction) => {
          const anyActive = junction.edgeIds.some((id) => activeEdgeIds.has(id));
          const allDim = hasFocus && !anyActive;
          return (
            <circle
              key={junction.id}
              cx={junction.x}
              cy={junction.y}
              r={anyActive ? 3.5 : 3}
              className={cn(
                "wb-edge-junction",
                `wb-edge-junction--${junction.relationCategory}`,
                anyActive && "wb-edge-junction--active",
                allDim && "wb-edge-junction--dim",
              )}
            />
          );
        })}
      </svg>
      {headings.map(heading => <div key={`${heading.x}:${heading.y}`} className="wb-graph__heading" style={{ left: heading.x, top: heading.y }}>{heading.label}</div>)}
    </ViewportPortal>
  );
});

const lodForZoom = (zoom: number): CardProps["lod"] => (zoom < .59 ? "far" : zoom >= 1.1 ? "near" : "normal");

type WorkbenchGraphProps = Pick<KnowledgeGraphProps, "currentId" | "edges" | "matchIds" | "multiple" | "onCanvasApi" | "onSelect" | "onSelectionChange" | "onStudy" | "onOpenExplore" | "planEdges" | "selectedId" | "selectedIds" | "title"> & {
  nodes: KnowledgeNode[];
  graphId?: string;
  legend?: React.ReactNode;
  maxDepth?: number;
  depthById?: ReadonlyMap<string, number>;
};

export function WorkbenchGraph({ nodes: sourceNodes, edges = EMPTY_EDGES, planEdges = EMPTY_EDGES, currentId, matchIds, multiple, onCanvasApi, onSelect, onSelectionChange, onStudy, onOpenExplore, selectedId, selectedIds, title, graphId, legend, maxDepth = Infinity, depthById }: WorkbenchGraphProps) {
  const nodes = useMemo(() => sourceNodes.filter(node => (depthById?.get(node.id) ?? 0) <= maxDepth), [sourceNodes, depthById, maxDepth]);
  const [view, setView] = useState<WorkbenchView>("route");
  const [flow, setFlow] = useState<ReactFlowInstance<KnowledgeNode> | null>(null);
  const viewportRef = useRef<Viewport>(INITIAL_VIEWPORT);
  const zoomOutputRef = useRef<HTMLOutputElement>(null);
  const [lod, setLod] = useState<CardProps["lod"]>(() => lodForZoom(INITIAL_VIEWPORT.zoom));
  const [clipped, setClipped] = useState(false);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const frame = useRef<HTMLDivElement>(null);
  const catalog = useRef<HTMLDivElement>(null);
  const requestedFocus = useRef<{ id: string; neighbors?: boolean } | null>(null);
  const markerId = `workbench-${useId().replace(/[^a-zA-Z0-9-]/g, "")}`;
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [showAllRelations, setShowAllRelations] = useState(false);
  const [elkState, setElkState] = useState<{ key: string; result: WorkbenchLayoutResult } | null>(null);
  const structure = useMemo(() => JSON.stringify(structuralNodes(nodes)), [nodes]);
  const structuralEdges = useMemo(
    () => JSON.stringify(edges.map(({ id, source, target, label, data }) => ({ id, source, target, label, data: { relation: data?.relation } }))),
    [edges],
  );
  const layoutKey = `${view}:${structure}:${structuralEdges}`;
  const syncLayout = useMemo(() => layoutWorkbench(JSON.parse(structure), JSON.parse(structuralEdges), view), [structure, structuralEdges, view]);
  useEffect(() => {
    if (view !== "route") return;
    let cancelled = false;
    void layoutWorkbenchWithElk(JSON.parse(structure), JSON.parse(structuralEdges), view)
      .then((result) => {
        if (!cancelled && result) {
          setElkState({ key: layoutKey, result });
        }
      })
      .catch(() => {
        // Synchronous layout remains active if ELK fails in a constrained environment.
      });
    return () => { cancelled = true; };
  }, [layoutKey, structure, structuralEdges, view]);
  const layout = elkState?.key === layoutKey ? elkState.result : syncLayout;
  const planEdgeIds = useMemo(() => new Set(planEdges.map(edge => edge.id)), [planEdges]);
  const chosen = useMemo(() => new Set(multiple ? selectedIds : selectedId ? [selectedId] : []), [multiple, selectedId, selectedIds]);
  const activeFocusIds = useMemo(() => {
    if (!hoveredId) return chosen;
    const next = new Set(chosen);
    next.add(hoveredId);
    return next;
  }, [chosen, hoveredId]);
  const allOrderedEdges = useMemo(() => {
    const links: Edge[] = JSON.parse(structuralEdges);
    return [...links, ...planEdges].sort((a, b) => Number(b.data?.relation === "prerequisite") - Number(a.data?.relation === "prerequisite"));
  }, [structuralEdges, planEdges]);
  const hasAssociationEdges = useMemo(
    () => allOrderedEdges.some(edge => {
      const rel = edge.data?.relation;
      return rel !== "prerequisite" && rel !== "contains" && !planEdgeIds.has(edge.id);
    }),
    [allOrderedEdges, planEdgeIds],
  );
  const visibleEdges = useMemo(
    () => filterWorkbenchEdges(allOrderedEdges, view, { activeNodeIds: activeFocusIds, showAllRelations, planEdgeIds }),
    [activeFocusIds, allOrderedEdges, planEdgeIds, showAllRelations, view],
  );
  const { routes, junctions } = useMemo(
    () => routeWorkbenchEdgesWithJunctions(layout.boxes, visibleEdges, layout.elkEdgePoints),
    [layout.boxes, layout.elkEdgePoints, visibleEdges],
  );
  const routeOrder = allOrderedEdges;
  const catalogItems = useMemo(() => new Map(nodes.map(node => [node.id, node])), [nodes]);
  const legendCounts = useMemo(() => {
    const typeCounts = new Map<NodeTypeId, number>();
    const statusCounts = new Map<NodeLearningStatusId, number>();
    let pageCount = 0;
    for (const node of nodes) {
      const type = resolveNodeTypeProfile(node.data.nodeType, { root: node.id === layout.rootId }).id;
      const status = statusOf(node).id;
      typeCounts.set(type, (typeCounts.get(type) ?? 0) + 1);
      statusCounts.set(status, (statusCounts.get(status) ?? 0) + 1);
      pageCount += Number(Boolean(node.data.hasLearningPage));
    }
    return { typeCounts, statusCounts, pageCount };
  }, [layout.rootId, nodes]);
  const relationIds = useMemo(() => new Set(routeOrder.flatMap(edge => activeFocusIds.has(edge.source) || activeFocusIds.has(edge.target) ? [edge.source, edge.target] : [])), [activeFocusIds, routeOrder]);
  const syncViewport = useCallback((next: Viewport) => {
    viewportRef.current = next;
    const output = zoomOutputRef.current;
    if (output) output.textContent = `${Math.round(next.zoom * 100)}%`;
    const nextLod = lodForZoom(next.zoom);
    setLod(current => (current === nextLod ? current : nextLod));
    const nextClipped = workbenchIsClipped(layout.bounds, dimensions, next);
    setClipped(current => (current === nextClipped ? current : nextClipped));
  }, [dimensions, layout.bounds]);
  useEffect(() => {
    const nextClipped = workbenchIsClipped(layout.bounds, dimensions, viewportRef.current);
    setClipped(current => (current === nextClipped ? current : nextClipped));
  }, [dimensions, layout.bounds]);
  const fit = useCallback(() => {
    const bounds = layout.bounds;
    if (view === "catalog") { catalog.current?.scrollTo?.({ top: 0 }); return; }
    if (!flow || !bounds || !dimensions.width || !dimensions.height) return;
    const first = layout.rootId ? layout.boxes.get(layout.rootId) : layout.boxes.values().next().value;
    const nextViewport = frameWorkbench(bounds, dimensions, view, first);
    void flow.setViewport(nextViewport);
    syncViewport(nextViewport);
  }, [dimensions, flow, layout, syncViewport, view]);

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    let rafId = 0;
    const resize = () => {
      const nextWidth = element.clientWidth;
      const nextHeight = element.clientHeight;
      setDimensions(previous => previous.width === nextWidth && previous.height === nextHeight ? previous : { width: nextWidth, height: nextHeight });
    };
    const scheduleResize = () => {
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        resize();
      });
    };
    resize();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(scheduleResize);
    observer.observe(element);
    return () => {
      observer.disconnect();
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);
  useEffect(() => { const id = requestAnimationFrame(fit); return () => cancelAnimationFrame(id); }, [fit, graphId]);

  const focusNode = useCallback((id: string, options?: { neighbors?: boolean }) => {
      if (view === "catalog") {
        const card = Array.from(catalog.current?.querySelectorAll<HTMLElement>("[data-card-id]") ?? []).find(element => element.dataset.cardId === id);
        if (!card) { requestedFocus.current = { id, neighbors: options?.neighbors }; return; }
        requestedFocus.current = null;
        card.scrollIntoView?.({ block: "nearest" });
        return;
      }
      const box = layout.boxes.get(id);
      if (!box || !flow) { requestedFocus.current = { id, neighbors: options?.neighbors }; return; }
      requestedFocus.current = null;
      if (!options?.neighbors) {
        void flow.setCenter(box.x + box.width / 2, box.y + box.height / 2, { zoom: Math.max(.78, flow.getZoom()) });
        return;
      }
      const ids = new Set([id]);
      for (const { edge } of routes) {
        if (edge.source === id) ids.add(edge.target);
        if (edge.target === id) ids.add(edge.source);
      }
      const bounds = graphBounds([...ids].flatMap(key => layout.boxes.get(key) ?? []));
      if (bounds) void flow.fitBounds(bounds, { padding: .20 });
  }, [flow, layout.boxes, routes, view]);
  useEffect(() => { onCanvasApi?.({ fit, focusNode }); }, [fit, focusNode, onCanvasApi]);
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const pending = requestedFocus.current;
      if (pending) focusNode(pending.id, pending);
    });
    return () => cancelAnimationFrame(id);
  }, [fit, focusNode]);

  const select = useCallback((node: KnowledgeNode) => {
    if (multiple) {
      if (node.data.nodeType === "root") return;
      const next = new Set(chosen);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      onSelectionChange?.([...next].flatMap(id => {
        const candidate = sourceNodes.find(item => item.id === id);
        return candidate ? [{ ...candidate.data, id }] : [];
      }));
    } else onSelect?.({ ...node.data, id: node.id });
  }, [chosen, multiple, sourceNodes, onSelect, onSelectionChange]);
  const present = useCallback((node: KnowledgeNode) => ({ ...node, data: { ...node.data, root: node.id === layout.rootId, current: node.id === currentId, matched: matchIds?.includes(node.id) } }), [layout.rootId, currentId, matchIds]);
  const renderedNodes = useMemo<KnowledgeNode[]>(() => nodes.flatMap(node => {
    const box = layout.boxes.get(node.id);
    if (!box) return [];
    const item = present(node);
    return [{ ...item, position: { x: box.x, y: box.y }, width: box.width, height: box.height, style: { width: box.width, height: box.height }, type: "knowledge", selected: chosen.has(node.id), draggable: false, selectable: false, focusable: false, data: { ...item.data, lod, selectCard: () => select(node), onStudy: onStudy && (() => onStudy({ ...node.data, id: node.id })), onOpenExplore: onOpenExplore && (() => onOpenExplore({ ...node.data, id: node.id })) } }];
  }), [chosen, layout.boxes, lod, nodes, onOpenExplore, onStudy, present, select]);
  const minimapNodeColor = useCallback(
    (node: KnowledgeNode) => (chosen.has(node.id) || relationIds.has(node.id) ? "var(--wb-minimap-active)" : "var(--wb-minimap-node)"),
    [chosen, relationIds],
  );
  const handleNodeClick = useCallback((_: unknown, node: KnowledgeNode) => {
    (node.data.selectCard as (() => void) | undefined)?.();
  }, []);
  const handleNodeMouseEnter = useCallback((_: unknown, node: KnowledgeNode) => {
    setHoveredId(node.id);
  }, []);
  const handleNodeMouseLeave = useCallback((_: unknown, node: KnowledgeNode) => {
    setHoveredId(current => (current === node.id ? null : current));
  }, []);
  const handleMove = useCallback((_: unknown, value: Viewport) => {
    syncViewport(value);
  }, [syncViewport]);
  const zoomBy = (difference: number) => {
    if (!flow) return;
    void flow.zoomTo(Math.min(2, Math.max(MIN_ZOOM, flow.getZoom() * difference)));
  };

  async function exportView(format: "svg" | "png") {
    const exportNodes = renderedNodes.map(node => {
      const box = layout.boxes.get(node.id)!;
      return { ...box, id: node.id, label: node.data.label, root: node.data.root, nodeType: node.data.nodeType, typeLabel: resolveNodeTypeProfile(node.data.nodeType, { root: node.data.root }).label, statusId: statusOf(node).id, statusLabel: statusOf(node).label };
    });
    const exportEdges = routes.map(({ edge, points, relationCategory }) => ({ id: edge.id, sourceX: points[0].x, sourceY: points[0].y, targetX: points.at(-1)!.x, targetY: points.at(-1)!.y, targetWidth: 0, targetHeight: 0, points, path: buildRoundedOrthogonalPath(points), directed: relationCategory === "prerequisite" || planEdgeIds.has(edge.id), active: activeFocusIds.has(edge.source) || activeFocusIds.has(edge.target) }));
    const svg = buildGraphSvg(exportNodes, exportEdges, { title });
    const filename = sanitizeFilename(title ?? "学习图谱");
    try {
      if (format === "svg") downloadSvgFile(svg, `${filename}.svg`);
      else await downloadPngFile(svg, `${filename}.png`);
    } catch { toast.error("图谱导出失败"); }
  }

  const goal = layout.rootId ? catalogItems.get(layout.rootId) : undefined;
  const completed = nodes.filter(node => node.id !== layout.rootId && statusOf(node).id === "mastered").length;
  const total = nodes.length - Number(Boolean(goal));
  return <div ref={frame} className="wb-graph" data-graph-view={view}>
    <div className="wb-graph__toolbar" aria-label="图谱视图操作">
      <div className="wb-graph__modes" aria-label="切换图谱视图" role="group">
        {MODES.map(({ id, label, icon: Icon }) => <Button key={id} variant={view === id ? "secondary" : "ghost"} aria-label={label} aria-pressed={view === id} size="sm" title={label} onClick={() => setView(id)}><Icon /><span>{label}</span></Button>)}
      </div>
      {view === "route" && hasAssociationEdges && <div className="wb-graph__tools">
        <Button
          aria-pressed={showAllRelations}
          size="sm"
          variant={showAllRelations ? "secondary" : "ghost"}
          title={showAllRelations ? "当前显示全部知识关联，点击切回主要关系" : "当前仅显示主要学习路线关系，点击展开全部知识关联"}
          onClick={() => setShowAllRelations(prev => !prev)}
        >
          {showAllRelations ? "显示全部关系" : "仅显示主要关系"}
        </Button>
      </div>}
      {view !== "catalog" && <div className="wb-graph__tools">
        <Button aria-label="缩小图谱" size="icon-sm" variant="ghost" onClick={() => zoomBy(.8)}><ZoomOut /></Button>
        <output ref={zoomOutputRef} title="缩放比例">{Math.round(viewportRef.current.zoom * 100)}%</output>
        <Button aria-label="放大图谱" size="icon-sm" variant="ghost" onClick={() => zoomBy(1.25)}><ZoomIn /></Button>
        <DropdownMenu><DropdownMenuTrigger asChild><Button aria-label="导出图谱" size="icon-sm" variant="ghost"><Download /></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => void exportView("svg")}>导出为 SVG（矢量）</DropdownMenuItem><DropdownMenuItem onSelect={() => void exportView("png")}>导出为 PNG（高清）</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
        <Button aria-label="重置图谱视图" size="icon-sm" variant="ghost" onClick={fit}><RotateCcw /></Button>
      </div>}
    </div>
    {legend ? <aside className="wb-graph__legend">{legend}</aside> : null}
    {view === "catalog" ? <div ref={catalog} className="wb-catalog">
      <header><h2>全部学习节点</h2><span>{nodes.length} 个节点</span></header>
      {goal && <div className="wb-catalog__goal"><WorkbenchCard node={present(goal)} active={chosen.has(goal.id)} lod="normal" onSelect={() => select(goal)} /><div><progress value={completed} max={total || 1} aria-label="已掌握节点" /> <span>{completed} / {total}</span></div></div>}
      {layout.groups.map(group => <section key={group.label}><h3>{group.label} · {group.ids.length} 个节点</h3><div className="wb-catalog__grid">
        {group.ids.map(id => { const node = catalogItems.get(id)!; return <WorkbenchCard key={id} node={present(node)} active={chosen.has(id)} lod="normal" onSelect={() => select(node)} onStudy={!multiple && onStudy ? () => onStudy({ ...node.data, id }) : undefined} />; })}
      </div></section>)}
    </div> : <>
      {!legend && <aside className="wb-graph__legend"><GraphLegend defaultCollapsed {...legendCounts} /></aside>}
      <ReactFlow<KnowledgeNode> className="wb-flow" nodes={renderedNodes} nodeTypes={nodeTypes} onInit={setFlow} minZoom={MIN_ZOOM} maxZoom={2} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} onNodeClick={handleNodeClick} onNodeMouseEnter={handleNodeMouseEnter} onNodeMouseLeave={handleNodeMouseLeave} onMove={handleMove} defaultViewport={INITIAL_VIEWPORT} proOptions={{ hideAttribution: true }}>
        <Background color="var(--wb-dot)" gap={30} size={1.4} variant={BackgroundVariant.Dots} />
        <WorkbenchViewportLayer activeFocusIds={activeFocusIds} headings={layout.headings} junctions={junctions} lod={lod} markerId={markerId} planEdgeIds={planEdgeIds} regions={layout.regions} routes={routes} view={view} />
        {clipped && <MiniMap pannable zoomable className="wb-graph__minimap" maskColor="var(--wb-minimap-mask)" nodeColor={minimapNodeColor} />}
      </ReactFlow>
    </>}
  </div>;
}

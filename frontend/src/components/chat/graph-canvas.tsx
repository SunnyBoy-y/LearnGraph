import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { UISnapshot } from "@/lib/stream-ui";
import { useStreamUIState } from "@/lib/stream-ui-local-state";
import { GRAPH_NODE_WIDTH, graphEdgeRoute, graphLabelLines, graphNodeHeight, layoutGraph, layoutMindMap, type Point } from "@/lib/graph-layout";

const backgrounds = { auto: "跟随主题", paper: "纸白", warm: "暖米", cool: "浅蓝", dark: "深色" };
type Background = keyof typeof backgrounds;
type Bounds = { x: number; y: number; width: number; height: number };
const clampZoom = (value: number) => Math.max(.2, Math.min(4, value));

export function GraphCanvas({ snapshot: s, selected, onSelect }: {
  snapshot: UISnapshot; selected: string | null; onSelect: (id: string | null) => void;
}) {
  const [query, setQuery] = useStreamUIState("query", "");
  const [zoom, setZoom] = useStreamUIState("zoom", 1);
  const [pan, setPan] = useStreamUIState<Point>("pan", {x: 0, y: 0});
  const [positions, setPositions] = useStreamUIState<Record<string, Point>>("positions", {});
  const [background, setBackground] = useStreamUIState<Background | null>("background", null);
  const [layoutChoice, setLayoutChoice] = useStreamUIState<"flow" | "mindmap" | null>("graph_layout", null);
  const [, setCustomized] = useStreamUIState<boolean>("canvas_customized", false);
  const mode = layoutChoice ?? s.props.graph_layout ?? "flow";
  const [frozenBounds, setFrozenBounds] = useState<Bounds | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState("");
  useEffect(() => {
    const onFullscreenChange = () => setFullscreen(document.fullscreenElement === container.current);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);
  async function toggleFullscreen() {
    const element = container.current;
    if (!element) return;
    setFullscreenError("");
    try {
      if (document.fullscreenElement === element) await document.exitFullscreen();
      else await element.requestFullscreen();
    } catch {
      setFullscreenError("无法切换全屏，请检查浏览器是否允许全屏后重试。");
    }
  }
  const [direction, setDirection] = useState<"LR" | "TB">("LR");
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      // Hidden tabs / collapsed accordions report 0 width; keep the previous
      // orientation so switching tabs never resets dragged nodes or camera.
      if (element.clientWidth <= 0) return;
      setDirection(element.clientWidth < 540 ? "TB" : "LR");
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const layout = useMemo(() => (mode === "mindmap" ? layoutMindMap : layoutGraph)(s.nodes, s.edges, direction), [s.nodes, s.edges, direction, mode]);
  const computedBounds = useMemo<Bounds>(() => {
    const baseRoutes = s.edges.map((e, i) => graphEdgeRoute(e, layout, {}, i));
    const points = s.nodes.map(n => layout.positions[n.id]);
    return {
      x: Math.min(0, ...points.map(p => p.x - 108), ...baseRoutes.map(r => r.left - 18)),
      y: Math.min(0, ...baseRoutes.map(r => r.top - 24)),
      width: Math.max(320, ...points.map(p => p.x + 116)),
      height: Math.max(220, ...s.nodes.map(n => layout.positions[n.id].y + graphNodeHeight(n.label) / 2 + 42)),
    };
  }, [layout, s.edges, s.nodes]);
  const bounds = frozenBounds ?? computedBounds;
  const svg = useRef<SVGSVGElement>(null);
  const cameraGroup = useRef<SVGGElement>(null);
  const cachedCtmRef = useRef<DOMMatrix | null>(null);
  const dragFrameRef = useRef(0);
  const pendingNodePosRef = useRef<{ id: string; point: Point } | null>(null);
  const camera = useRef({ zoom, pan });
  const previousGeometry = useRef(`${direction}:${mode}`);
  camera.current = { zoom, pan };
  const active = useRef(new Map<number, Point>());
  const drag = useRef<{ node: string | null; start: Point; position: Point; moved: boolean } | null>(null);
  const marker = useId().replace(/:/g, "");
  useEffect(() => () => {
    if (dragFrameRef.current) cancelAnimationFrame(dragFrameRef.current);
  }, []);
  const screenPoint = (x: number, y: number, useCachedCtm = false): Point => {
    const matrix = useCachedCtm && cachedCtmRef.current
      ? cachedCtmRef.current
      : typeof svg.current?.getScreenCTM === "function"
        ? svg.current.getScreenCTM()?.inverse() ?? null
        : null;
    return matrix ? new DOMPoint(x, y).matrixTransform(matrix) : {x, y};
  };
  const applyCameraDom = (nextZoom: number, nextPan: Point) => {
    camera.current = {zoom: nextZoom, pan: nextPan};
    cameraGroup.current?.setAttribute("transform", `translate(${nextPan.x} ${nextPan.y}) scale(${nextZoom})`);
  };
  const updateCamera = (nextZoom: number, nextPan: Point) => {
    camera.current = {zoom: nextZoom, pan: nextPan}; setZoom(nextZoom); setPan(nextPan);
  };
  useEffect(() => {
    const geometry = `${direction}:${mode}`;
    if (previousGeometry.current === geometry) return;
    previousGeometry.current = geometry;
    setPositions({}); setFrozenBounds(null); updateCamera(1, {x: 0, y: 0});
    // A new orientation has a new coordinate system. Reset only its geometry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [direction, mode]);
  const zoomAt = (next: number, anchor: Point) => {
    const current = camera.current, z = clampZoom(next), ratio = z / current.zoom;
    setCustomized(true);
    updateCamera(z, {x: anchor.x - (anchor.x - current.pan.x) * ratio, y: anchor.y - (anchor.y - current.pan.y) * ratio});
  };
  // Zoom on trackpad pinch / Ctrl+wheel, or any wheel in fullscreen; leave
  // ordinary inline scrolling to the surrounding conversation scroller.
  const wheelHandler = useRef<(e: WheelEvent) => void>(() => {});
  wheelHandler.current = event => {
    if (!fullscreen && !event.ctrlKey && !event.metaKey) return;
    event.preventDefault(); setFrozenBounds(bounds);
    zoomAt(camera.current.zoom * Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * .004), screenPoint(event.clientX, event.clientY));
  };
  useEffect(() => {
    const element = svg.current;
    const wheel = (event: WheelEvent) => wheelHandler.current(event);
    element?.addEventListener("wheel", wheel, {passive: false});
    return () => element?.removeEventListener("wheel", wheel);
  }, []);
  function down(event: PointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return;
    cachedCtmRef.current =
      typeof svg.current?.getScreenCTM === "function"
        ? svg.current.getScreenCTM()?.inverse() ?? null
        : null;
    const p = screenPoint(event.clientX, event.clientY, true);
    active.current.set(event.pointerId, p); setFrozenBounds(bounds);
    event.currentTarget.setPointerCapture(event.pointerId);
    const id = (event.target as Element).closest("[data-graph-node]")?.getAttribute("data-graph-node") ?? null;
    drag.current = active.current.size === 1 ? { node: id, start: p, position: id ? positions[id] ?? layout.positions[id] : camera.current.pan, moved: false } : null;
  }
  function move(event: PointerEvent<SVGSVGElement>) {
    if (!active.current.has(event.pointerId)) return;
    const old = [...active.current.values()], p = screenPoint(event.clientX, event.clientY, true);
    active.current.set(event.pointerId, p);
    const next = [...active.current.values()];
    if (next.length >= 2) {
      const center = (ps: Point[]) => ({x: (ps[0].x + ps[1].x) / 2, y: (ps[0].y + ps[1].y) / 2});
      const distance = (ps: Point[]) => Math.hypot(ps[0].x - ps[1].x, ps[0].y - ps[1].y);
      const a = center(old), b = center(next), current = camera.current;
      const z = clampZoom(current.zoom * distance(next) / Math.max(1, distance(old))), ratio = z / current.zoom;
      applyCameraDom(z, {x: b.x - (a.x - current.pan.x) * ratio, y: b.y - (a.y - current.pan.y) * ratio});
    } else if (drag.current) {
      const d = drag.current, delta = {x: p.x - d.start.x, y: p.y - d.start.y};
      if (Math.hypot(delta.x, delta.y) < 3 && !d.moved) return;
      d.moved = true;
      if (d.node) {
        pendingNodePosRef.current = {
          id: d.node,
          point: {x: d.position.x + delta.x / camera.current.zoom, y: d.position.y + delta.y / camera.current.zoom},
        };
        if (!dragFrameRef.current) {
          dragFrameRef.current = requestAnimationFrame(() => {
            dragFrameRef.current = 0;
            const pending = pendingNodePosRef.current;
            if (!pending) return;
            pendingNodePosRef.current = null;
            setCustomized(true);
            setPositions(current => ({...current, [pending.id]: pending.point}));
          });
        }
      } else {
        applyCameraDom(camera.current.zoom, {x: d.position.x + delta.x, y: d.position.y + delta.y});
      }
    }
  }
  function up(event: PointerEvent<SVGSVGElement>, cancelled = false) {
    if (!active.current.has(event.pointerId)) return;
    const d = drag.current;
    if (dragFrameRef.current) {
      cancelAnimationFrame(dragFrameRef.current);
      dragFrameRef.current = 0;
    }
    if (pendingNodePosRef.current) {
      const pending = pendingNodePosRef.current;
      pendingNodePosRef.current = null;
      setCustomized(true);
      setPositions(current => ({...current, [pending.id]: pending.point}));
    }
    if (!cancelled && d && !d.moved && active.current.size === 1) {
      if (d.node) onSelect(selected === d.node ? null : d.node);
      else onSelect(null);
    } else if (d?.moved || active.current.size >= 2) {
      setCustomized(true);
      updateCamera(camera.current.zoom, camera.current.pan);
    }
    active.current.delete(event.pointerId); drag.current = null;
    if (active.current.size === 0) cachedCtmRef.current = null;
    // After a pinch, the remaining finger continues panning without a jump.
    if (active.current.size === 1) drag.current = {node: null, start: [...active.current.values()][0], position: camera.current.pan, moved: true};
  }
  const tone = background ?? s.props.background ?? "auto";
  return <div ref={container} className="stream-ui__graph" data-background={tone} data-direction={direction} data-layout={mode} style={{"--graph-background": tone === "auto" ? "var(--background)" : undefined} as CSSProperties}>
    <div className="stream-ui__toolbar">
      <Input className="stream-ui__search" aria-label="查找节点" placeholder="查找节点…" maxLength={500} value={query} onChange={e => setQuery(e.target.value)} />
      <label>底色<select aria-label="画布底色" value={tone} onChange={e => { setCustomized(true); setBackground(e.target.value as Background); }}>{Object.entries(backgrounds).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>布局<select aria-label="画布布局" value={mode} onChange={e => { setCustomized(true); setLayoutChoice(e.target.value as "flow" | "mindmap"); }}><option value="flow">关系图</option><option value="mindmap">思维导图</option></select></label>
      <label>缩放<input aria-label="图谱缩放" type="range" min="0.2" max="4" step="0.05" value={zoom} onChange={e => {setFrozenBounds(bounds); zoomAt(Number(e.target.value), {x: (bounds.x + bounds.width) / 2, y: (bounds.y + bounds.height) / 2});}} /><output>{Math.round(zoom * 100)}%</output></label>
      <Button size="sm" variant="outline" type="button" onClick={() => {setPositions({}); updateCamera(1, {x: 0, y: 0}); setQuery(""); setFrozenBounds(null);}}>自动布局 / 适应画布</Button>
    </div>
    {fullscreenError && <p role="alert">{fullscreenError}</p>}
    <div className="stream-ui__graph-scroll">
      <Button className="stream-ui__graph-fullscreen" size="icon-sm" variant="outline" type="button"
        aria-label={fullscreen ? "退出全屏" : "全屏查看"} title={fullscreen ? "退出全屏（Esc）" : "全屏查看"}
        aria-pressed={fullscreen} onClick={() => { void toggleFullscreen(); }}>
        {fullscreen ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
      </Button>
      <svg ref={svg} viewBox={`${bounds.x} ${bounds.y} ${bounds.width - bounds.x} ${bounds.height - bounds.y}`} role="group" aria-label={s.props.title} tabIndex={0}
        onPointerDown={down} onPointerMove={move} onPointerUp={e => up(e)} onPointerCancel={e => up(e, true)} onLostPointerCapture={e => up(e, true)}
        onDoubleClick={event => {
          if ((event.target as Element).closest("[data-graph-node]")) return;
          event.preventDefault(); setFrozenBounds(bounds);
          zoomAt(camera.current.zoom * 1.4, screenPoint(event.clientX, event.clientY));
        }}
        onKeyDown={event => {
          if (event.target !== event.currentTarget) return;
          if (event.key === "Escape") { onSelect(null); return; }
          const delta = {ArrowLeft: [30, 0], ArrowRight: [-30, 0], ArrowUp: [0, 30], ArrowDown: [0, -30]}[event.key];
          if (delta) {event.preventDefault(); setFrozenBounds(bounds); setCustomized(true); updateCamera(zoom, {x: pan.x + delta[0], y: pan.y + delta[1]});}
        }}>
        <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="var(--graph-edge)" /></marker></defs>
        <g data-graph-camera="" ref={cameraGroup} transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
          {s.edges.map((edge, i) => { const route = graphEdgeRoute(edge, layout, positions, i); return <g key={edge.id} className="stream-ui__edge" data-cross-link={mode === "mindmap" && layout.back.has(edge.id)}><path d={route.d} markerEnd={mode === "mindmap" && !layout.back.has(edge.id) ? undefined : `url(#${marker})`} /><text x={route.label.x} y={route.label.y} textAnchor="middle">{edge.label}</text></g>; })}
          {s.nodes.map(node => {
            const p = positions[node.id] ?? layout.positions[node.id], lines = graphLabelLines(node.label), h = graphNodeHeight(node.label);
            return <g key={node.id} data-graph-node={node.id} data-root={layout.roots.has(node.id)} transform={`translate(${p.x} ${p.y})`} className="stream-ui__node" data-selected={selected === node.id}
              opacity={!query || `${node.label} ${node.group}`.toLowerCase().includes(query.toLowerCase()) ? 1 : .2} role="button" tabIndex={0} aria-label={node.label} aria-pressed={selected === node.id}
              onKeyDown={e => {if (e.key === "Enter" || e.key === " ") {e.preventDefault(); onSelect(selected === node.id ? null : node.id);} else if (e.key === "Escape") {onSelect(null);}}}>
              <rect x={-GRAPH_NODE_WIDTH / 2} y={-h / 2} width={GRAPH_NODE_WIDTH} height={h} rx={12} />
              <text textAnchor="middle">{lines.map((line, i) => <tspan key={i} x={0} y={(i - (lines.length - 1) / 2) * 19 + 5}>{line}</tspan>)}</text><title>{node.label}</title>
            </g>;
          })}
        </g>
        {!s.nodes.length && <text x="160" y="110" textAnchor="middle" fill="var(--graph-foreground)">{s.state === "streaming" ? "正在生成节点…" : "暂无节点"}</text>}
      </svg>
    </div>
  </div>;
}

import { Component, lazy, Suspense, useId, useMemo, useRef, useState } from "react";
import type {
  ComponentType,
  CSSProperties,
  PointerEvent,
  ReactNode,
} from "react";
import {
  CalendarDays,
  FileText,
  ChartNoAxesCombined,
  Network,
  ShieldCheck,
  Sparkles,
  Table2,
} from "lucide-react";
import type { TrustedComponentAction } from "./trusted-component-renderer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { parseUISnapshot } from "@/lib/stream-ui";
import type { UISnapshot } from "@/lib/stream-ui";
import type { MessagePart } from "@/types/sessions";
import {
  StreamUIStateScope,
  useStreamUIState,
} from "@/lib/stream-ui-local-state";
import "./stream-ui.css";

type ViewProps = {
  snapshot: UISnapshot;
  siblingParts?: MessagePart[];
  failed?: boolean;
  settled?: boolean;
  onExplain?: (nodeId?: string) => void;
};
const COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];
const format = (value: unknown) =>
  value == null
    ? "—"
    : typeof value === "number"
      ? value.toLocaleString()
      : String(value);

function ChartView({ snapshot: s }: ViewProps) {
  const [mode, setMode] = useStreamUIState<"line" | "bar" | null>("mode", null);
  const [hidden, setHidden] = useStreamUIState<string[]>("hidden", []);
  const [hovered, setHovered] = useStreamUIState<string | null>(
    "hovered",
    null,
  );
  const [limit, setLimit] = useStreamUIState("limit", 500);
  const columns = s.props.columns.filter(
    (column) => !hidden.includes(column.key),
  );
  const rows = s.rows.slice(-limit);
  const values = rows.flatMap((row) =>
    columns.flatMap((column) =>
      typeof row.values[column.key] === "number"
        ? [row.values[column.key] as number]
        : [],
    ),
  );
  const min = Math.min(0, ...values),
    max = Math.max(1, ...values);
  const x = (index: number) =>
    52 + ((index + 0.5) / Math.max(1, rows.length)) * 620;
  const y = (value: number) => 236 - ((value - min) / (max - min)) * 202;
  const active = rows.find((row) => row.id === hovered);
  const chartType = mode ?? s.props.chart_type;
  return (
    <>
      <div className="stream-ui__toolbar">
        <div className="stream-ui__segmented" aria-label="图表类型">
          {(["line", "bar"] as const).map((type) => (
            <Button
              size="xs"
              variant="ghost"
              key={type}
              type="button"
              aria-pressed={chartType === type}
              onClick={() => setMode(type)}
            >
              {type === "line" ? "折线" : "柱状"}
            </Button>
          ))}
        </div>
        <div className="stream-ui__control">
          <span>显示范围</span>
          <Select
            value={String(limit)}
            onValueChange={(value) => setLimit(Number(value))}
          >
            <SelectTrigger aria-label="显示范围" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value="500">全部</SelectItem>
              <SelectItem value="10">最近 10 项</SelectItem>
              <SelectItem value="5">最近 5 项</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="stream-ui__legend">
        {s.props.columns.map((column, index) => (
          <Button
            size="xs"
            variant="ghost"
            type="button"
            key={column.key}
            aria-pressed={!hidden.includes(column.key)}
            onClick={() =>
              setHidden((current) =>
                current.includes(column.key)
                  ? current.filter((key) => key !== column.key)
                  : [...current, column.key],
              )
            }
          >
            <i style={{ background: COLORS[index % 5] }} />
            {column.label}
          </Button>
        ))}
      </div>
      {!rows.length ? (
        <Empty />
      ) : (
        <>
          <svg
            className="stream-ui__chart"
            viewBox="0 0 700 280"
            role="img"
            aria-label={s.props.title}
          >
            {[0, 1, 2, 3, 4].map((tick) => {
              const value = min + ((max - min) * tick) / 4;
              return (
                <g key={tick}>
                  <line
                    x1={52}
                    x2={680}
                    y1={y(value)}
                    y2={y(value)}
                    className="stream-ui__grid"
                  />
                  <text x={44} y={y(value) + 4} textAnchor="end">
                    {Intl.NumberFormat("zh", { notation: "compact" }).format(
                      value,
                    )}
                  </text>
                </g>
              );
            })}
            {columns.map((column) => {
              const color =
                COLORS[
                  s.props.columns.findIndex((item) => item.key === column.key) %
                    5
                ];
              // Missing values break the line rather than inventing zeroes.
              let penDown = false;
              const path = rows
                .map((row, index) => {
                  const value = row.values[column.key];
                  if (typeof value !== "number") {
                    penDown = false;
                    return "";
                  }
                  const point = `${penDown ? "L" : "M"}${x(index)},${y(value)}`;
                  penDown = true;
                  return point;
                })
                .join(" ");
              return (
                <g key={column.key} style={{ color }}>
                  {chartType === "line" && (
                    <path
                      d={path}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2.5}
                    />
                  )}
                  {rows.map((row, index) => {
                    const value = row.values[column.key];
                    if (typeof value !== "number") return null;
                    const width = Math.min(
                      36,
                      480 / rows.length / Math.max(1, columns.length),
                    );
                    return chartType === "line" ? (
                      <circle
                        key={row.id}
                        cx={x(index)}
                        cy={y(value)}
                        r={4}
                        fill="currentColor"
                      >
                        <title>
                          {row.label}: {column.label} {format(value)}
                        </title>
                      </circle>
                    ) : (
                      <rect
                        key={row.id}
                        x={
                          x(index) +
                          (columns.indexOf(column) - columns.length / 2) * width
                        }
                        y={Math.min(y(0), y(value))}
                        width={width * 0.85}
                        height={Math.max(1, Math.abs(y(value) - y(0)))}
                        rx={2}
                        fill="currentColor"
                      >
                        <title>
                          {row.label}: {column.label} {format(value)}
                        </title>
                      </rect>
                    );
                  })}
                </g>
              );
            })}
            {rows.map((row, index) => (
              <g
                key={row.id}
                onPointerEnter={() => setHovered(row.id)}
                onFocus={() => setHovered(row.id)}
                tabIndex={0}
                role="button"
                aria-label={`${row.label}: ${columns.map((c) => `${c.label} ${format(row.values[c.key])}`).join(", ")}`}
              >
                <rect
                  x={52 + (index / rows.length) * 620}
                  y={28}
                  width={620 / rows.length}
                  height={212}
                  fill="transparent"
                />
                {(rows.length <= 12 ||
                  index % Math.ceil(rows.length / 10) === 0) && (
                  <text x={x(index)} y={263} textAnchor="middle">
                    {row.label.slice(0, 9)}
                  </text>
                )}
              </g>
            ))}
          </svg>
          <div className="stream-ui__tooltip" role="status">
            {active
              ? `${active.label} · ${columns.map((c) => `${c.label} ${format(active.values[c.key])}${s.props.unit}`).join(" · ")}`
              : "悬停或使用 Tab 查看数据；点击图例切换指标"}
          </div>
        </>
      )}
    </>
  );
}

function GraphView({ snapshot: s, onExplain }: ViewProps) {
  const [selected, setSelected] = useStreamUIState<string | null>(
    "selected",
    null,
  );
  const [query, setQuery] = useStreamUIState("query", "");
  const [zoom, setZoom] = useStreamUIState("zoom", 1);
  const [positions, setPositions] = useStreamUIState<
    Record<string, { x: number; y: number }>
  >("positions", {});
  const drag = useRef<{
    id: string;
    x: number;
    y: number;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const marker = useId().replace(/:/g, "");
  const point = (id: string) =>
    positions[id] ?? {
      x: 110 + (s.nodes.findIndex((node) => node.id === id) % 3) * 240,
      y: 70 + Math.floor(s.nodes.findIndex((node) => node.id === id) / 3) * 130,
    };
  const height = Math.max(240, Math.ceil(s.nodes.length / 3) * 130 + 40);
  const selectedNode = s.nodes.find((node) => node.id === selected);
  function move(event: PointerEvent<SVGGElement>) {
    const state = drag.current;
    if (!state || !svg.current) return;
    const transform = svg.current.getScreenCTM()?.inverse();
    if (!transform) return;
    const cursor = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      transform,
    );
    if (
      !state.moved &&
      Math.hypot(event.clientX - state.startX, event.clientY - state.startY) < 4
    )
      return;
    state.moved = true;
    setPositions((current) => ({
      ...current,
      [state.id]: {
        x: Math.max(75, Math.min(665, cursor.x / zoom - state.x)),
        y: Math.max(25, Math.min(height - 25, cursor.y / zoom - state.y)),
      },
    }));
  }
  return (
    <>
      <div className="stream-ui__toolbar">
        <Input
          className="stream-ui__search"
          aria-label="查找节点"
          placeholder="查找节点…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <label>
          缩放{" "}
          <input
            aria-label="图谱缩放"
            type="range"
            min="0.5"
            max="1.8"
            step="0.1"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          />
        </label>
        <Button
          size="sm"
          variant="outline"
          type="button"
          onClick={() => {
            setPositions({});
            setZoom(1);
            setQuery("");
          }}
        >
          重置
        </Button>
      </div>
      {!s.nodes.length ? (
        <Empty />
      ) : (
        <div className="stream-ui__graph-scroll">
          <svg
            ref={svg}
            viewBox={`0 0 760 ${height}`}
            style={{ minHeight: Math.min(height, 480) }}
            role="group"
            aria-label={s.props.title}
          >
            <defs>
              <marker
                id={marker}
                markerWidth="8"
                markerHeight="8"
                refX="7"
                refY="4"
                orient="auto"
              >
                <path d="M0,0 L8,4 L0,8" fill="var(--muted-foreground)" />
              </marker>
            </defs>
            <g transform={`scale(${zoom})`}>
              {s.edges.map((edge) => {
                const a = point(edge.source),
                  b = point(edge.target);
                const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
                const trim = Math.min(70, length * 0.35);
                return (
                  <g key={edge.id} className="stream-ui__edge">
                    <line
                      x1={a.x + ((b.x - a.x) / length) * trim}
                      y1={a.y + ((b.y - a.y) / length) * trim}
                      x2={b.x - ((b.x - a.x) / length) * trim}
                      y2={b.y - ((b.y - a.y) / length) * trim}
                      markerEnd={`url(#${marker})`}
                    />
                    <text
                      x={(a.x + b.x) / 2}
                      y={(a.y + b.y) / 2 - 7}
                      textAnchor="middle"
                    >
                      {edge.label}
                    </text>
                  </g>
                );
              })}
              {s.nodes.map((node) => {
                const p = point(node.id);
                return (
                  <g
                    key={node.id}
                    transform={`translate(${p.x} ${p.y})`}
                    className="stream-ui__node"
                    data-selected={selected === node.id}
                    opacity={
                      !query ||
                      `${node.label} ${node.group}`
                        .toLowerCase()
                        .includes(query.toLowerCase())
                        ? 1
                        : 0.2
                    }
                    role="button"
                    tabIndex={0}
                    aria-label={node.label}
                    aria-pressed={selected === node.id}
                    onPointerDown={(event) => {
                      const transform = svg.current?.getScreenCTM()?.inverse();
                      if (!transform) return;
                      const cursor = new DOMPoint(
                        event.clientX,
                        event.clientY,
                      ).matrixTransform(transform);
                      drag.current = {
                        id: node.id,
                        startX: event.clientX,
                        startY: event.clientY,
                        x: cursor.x / zoom - p.x,
                        y: cursor.y / zoom - p.y,
                        moved: false,
                      };
                      event.currentTarget.setPointerCapture(event.pointerId);
                    }}
                    onPointerMove={move}
                    onPointerUp={() => {
                      if (!drag.current?.moved) setSelected(node.id);
                      drag.current = null;
                    }}
                    onPointerCancel={() => {
                      drag.current = null;
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelected(node.id);
                      }
                    }}
                  >
                    <rect x={-70} y={-22} width={140} height={44} rx={12} />
                    <text textAnchor="middle" dy=".35em">
                      {node.label.length > 12
                        ? `${node.label.slice(0, 11)}…`
                        : node.label}
                    </text>
                    <title>{node.label}</title>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>
      )}
      {selectedNode && (
        <aside className="stream-ui__detail">
          <strong>{selectedNode.label}</strong>
          <p>{selectedNode.description || selectedNode.group}</p>
          <p>
            {s.edges
              .filter(
                (edge) => edge.source === selected || edge.target === selected,
              )
              .map(
                (edge) =>
                  `${s.nodes.find((n) => n.id === edge.source)?.label} → ${edge.label || "关联"} → ${s.nodes.find((n) => n.id === edge.target)?.label}`,
              )
              .join("；")}
          </p>
          <Button
            size="sm"
            type="button"
            disabled={!onExplain}
            onClick={() => onExplain?.(selectedNode.id)}
          >
            <Sparkles aria-hidden="true" />让 AI 解释此节点
          </Button>
        </aside>
      )}
    </>
  );
}

function TableView({ snapshot: s }: ViewProps) {
  const [query, setQuery] = useStreamUIState("query", "");
  const [sort, setSort] = useStreamUIState<{
    key: string;
    descending: boolean;
  } | null>("sort", null);
  const rows = s.rows
    .filter((row) =>
      `${row.label} ${Object.values(row.values).join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    )
    .toSorted((a, b) => {
      if (!sort) return 0;
      const left = a.values[sort.key],
        right = b.values[sort.key];
      const result =
        typeof left === "number" && typeof right === "number"
          ? left - right
          : format(left).localeCompare(format(right), "zh");
      return sort.descending ? -result : result;
    });
  return (
    <>
      <div className="stream-ui__toolbar">
        <Input
          className="stream-ui__search"
          aria-label="筛选表格"
          placeholder="筛选数据…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span>{rows.length} 项</span>
      </div>
      <div className="stream-ui__table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">项目</th>
              {s.props.columns.map((column) => (
                <th
                  scope="col"
                  key={column.key}
                  aria-sort={
                    sort?.key === column.key
                      ? sort.descending
                        ? "descending"
                        : "ascending"
                      : "none"
                  }
                >
                  <Button
                    size="xs"
                    variant="ghost"
                    type="button"
                    onClick={() =>
                      setSort({
                        key: column.key,
                        descending:
                          sort?.key === column.key && !sort.descending,
                      })
                    }
                  >
                    {column.label}{" "}
                    {sort?.key === column.key
                      ? sort.descending
                        ? "↓"
                        : "↑"
                      : "↕"}
                  </Button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <th scope="row">{row.label}</th>
                {s.props.columns.map((column) => (
                  <td key={column.key}>{format(row.values[column.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <Empty />}
    </>
  );
}

function TimelineView({ snapshot: s }: ViewProps) {
  return s.items.length ? (
    <ol className="stream-ui__timeline">
      {s.items.map((item) => (
        <li key={item.id}>
          <time>{item.date}</time>
          <details>
            <summary>{item.label}</summary>
            <p>{item.description}</p>
            {item.value != null && <span>{format(item.value)}</span>}
          </details>
        </li>
      ))}
    </ol>
  ) : (
    <Empty />
  );
}

function MetricView({ snapshot: s }: ViewProps) {
  return s.items.length ? (
    <ul
      className="metric-card__grid stream-ui__metrics"
      style={
        { "--metric-columns": Math.min(s.items.length, 3) } as CSSProperties
      }
    >
      {s.items.map((item) => (
        <li className="metric-card__item" key={item.id}>
          <span className="metric-card__label">{item.label}</span>
          <strong className="metric-card__value">
            <span>{format(item.value)}</span>
            <small>{s.props.unit}</small>
          </strong>
          {item.description && (
            <small className="metric-card__hint">{item.description}</small>
          )}
        </li>
      ))}
    </ul>
  ) : (
    <Empty />
  );
}
function Empty() {
  return (
    <div className="stream-ui__empty" role="status">
      等待数据填充…
    </div>
  );
}

/** Only this registry can instantiate components; model data cannot execute code. */
const UI_REGISTRY: Record<UISnapshot["component"], ComponentType<ViewProps>> = {
  chart: ChartView,
  graph: GraphView,
  table: TableView,
  timeline: TimelineView,
  metric: MetricView,
  document: lazy(() => import("./rich-document")),
};
const UI_ICONS = {
  chart: ChartNoAxesCombined,
  graph: Network,
  table: Table2,
  timeline: CalendarDays,
  metric: ShieldCheck,
  document: FileText,
};

class UIErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <p role="alert">图表暂时无法显示，请刷新消息重试。</p>
    ) : (
      this.props.children
    );
  }
}

function ValidatedUI({
  snapshot,
  siblingParts,
  partId,
  interactive,
  failed,
  onAction,
  embedded = false,
  settled = false,
}: {
  snapshot: UISnapshot;
  siblingParts?: MessagePart[];
  partId: string;
  interactive: boolean;
  failed: boolean;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  embedded?: boolean;
  settled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const View = UI_REGISTRY[snapshot.component];
  const Icon = UI_ICONS[snapshot.component];
  const canExplain =
    interactive &&
    !failed &&
    snapshot.state === "completed" &&
    !!onAction &&
    !busy;
  async function explain(nodeId?: string) {
    if (!canExplain) return;
    setBusy(true);
    setError("");
    try {
      await onAction?.({
        componentId: partId,
        componentType: "stream_ui",
        event: nodeId ? "graph.node.explain" : "explain",
        payload: {
          revision: snapshot.revision,
          ...(nodeId ? { node_id: nodeId } : {}),
        },
      });
    } catch (error) {
      setError(error instanceof Error ? error.message : "交互提交失败，请重试");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className={`message-component stream-ui${snapshot.component === "document" ? " stream-ui--document" : ""}${embedded ? " stream-ui--embedded" : ""}`}
      data-ui-component={snapshot.component}
      aria-label={snapshot.props.title}
      aria-busy={snapshot.state === "streaming" && !failed}
    >
      {snapshot.preview && <p className="stream-ui__status" role="status">
        {failed || settled ? "内容生成中断，显示已接收部分" : "内容正在生成…"}
      </p>}
      {snapshot.component !== "document" && <header className="stream-ui__heading">
        {snapshot.component !== "metric" && (
          <Icon className="size-4 shrink-0" aria-hidden="true" />
        )}
        <div className="stream-ui__heading-copy">
          <h3 className="stream-ui__title">{snapshot.props.title}</h3>
          {snapshot.props.description && <p>{snapshot.props.description}</p>}
        </div>
        {(snapshot.component !== "metric" ||
          snapshot.state === "streaming" ||
          failed) && (
          <Badge
            variant="secondary"
            className="stream-ui__status"
            role="status"
          >
            {failed
              ? "生成中断"
              : snapshot.state === "streaming"
                ? "正在填充"
                : "可交互"}
          </Badge>
        )}
      </header>}
      <Suspense fallback={<Empty />}><View
        snapshot={snapshot}
        siblingParts={siblingParts}
        failed={failed}
        settled={settled}
        onExplain={
          canExplain
            ? (nodeId) => {
                void explain(nodeId);
              }
            : undefined
        }
      /></Suspense>
      {failed && snapshot.component === "document" && <p className="rich-doc__caption" role="status">生成已中断，已生成内容仍可操作。</p>}
      {!embedded && snapshot.component !== "document" && <footer className="stream-ui__footer">
        <span>{snapshot.props.source_note}</span>
        <Button
          size="sm"
          variant={snapshot.component === "metric" ? "ghost" : "default"}
          type="button"
          disabled={!canExplain}
          onClick={() => {
            void explain();
          }}
        >
          <Sparkles aria-hidden="true" />
          {busy ? "正在提交…" : "让 AI 解释"}
        </Button>
      </footer>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

export function StreamUI({
  data,
  siblingParts,
  partId,
  interactive = true,
  failed = false,
  onAction,
  embedded = false,
  settled = false,
}: {
  data: unknown;
  siblingParts?: MessagePart[];
  partId: string;
  interactive?: boolean;
  failed?: boolean;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  embedded?: boolean;
  settled?: boolean;
}) {
  const snapshot = useMemo(() => parseUISnapshot(data), [data]);
  if (!snapshot)
    return (
      <p className="stream-ui__invalid" role="alert">
        交互图表数据无效，无法渲染。
      </p>
    );
  if (snapshot.state === "removed") return null;
  return (
    <UIErrorBoundary key={partId}>
      <StreamUIStateScope.Provider value={`${partId}:${snapshot.component}`}>
        <ValidatedUI
          snapshot={snapshot}
          siblingParts={siblingParts}
          partId={partId}
          interactive={interactive}
          failed={failed}
          onAction={onAction}
          embedded={embedded}
          settled={settled}
        />
      </StreamUIStateScope.Provider>
    </UIErrorBoundary>
  );
}

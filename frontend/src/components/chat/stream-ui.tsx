import { Component, lazy, Suspense, useMemo, useState } from "react";
import type {
  ComponentType,
  CSSProperties,
  ReactNode,
} from "react";
import {
  Sparkles,
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
import { committedNumericPreview, parseUISnapshot } from "@/lib/stream-ui";
import type { UISnapshot } from "@/lib/stream-ui";
import type { MessagePart } from "@/types/sessions";
import {
  StreamUIStateScope,
  useStreamUIState,
  streamUIViewState,
} from "@/lib/stream-ui-local-state";
import { GraphCanvas } from "./graph-canvas";
import { presentation } from "@/genui/presentation-simplifier";
import "./stream-ui.css";
import "@/genui/layout-tokens.css";

type ViewProps = {
  snapshot: UISnapshot;
  siblingParts?: MessagePart[];
  failed?: boolean;
  settled?: boolean;
  onExplain?: (nodeId?: string) => void;
  onBlockExplain?: (blockId: string, payload: Record<string, unknown>) => void;
};
const COLORS = [
  "var(--genui-accent)",
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
  const [focused, setFocused] = useState<string | null>(null);
  const [limit, setLimit] = useStreamUIState("limit", 500);
  const staticChart = s.props.interaction === "static";
  const columns = s.props.columns.filter(
    (column) => staticChart || !hidden.includes(column.key),
  );
  const rows = staticChart ? s.rows : s.rows.slice(-limit);
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
  const active = rows.find((row) => row.id === (focused ?? hovered));
  const chartType = staticChart ? s.props.chart_type : mode ?? s.props.chart_type;
  const first = rows[0], last = rows.at(-1), single = columns.length === 1 ? columns[0] : null;
  const start = single && first?.values[single.key], end = single && last?.values[single.key];
  const change = typeof start === "number" && typeof end === "number" && start > 0 ? (end - start) / start * 100 : null;
  const insight = single && typeof end === "number" ? `${last!.label} · ${single.label} ${format(end)}${s.props.unit}${change != null && rows.length > 1 ? `，较${first.label}${change >= 0 ? "增加" : "减少"} ${format(Math.abs(end - (start as number)))}${s.props.unit}` : ""}` : "";
  return (
    <>
      <div className="stream-ui__chart-context">
        <span>{[s.props.time_range, s.props.unit && `单位：${s.props.unit}`].filter(Boolean).join(" · ")}</span>
        {change != null && rows.length > 1 && <strong>{change > 0 ? "+" : ""}{Number(change.toFixed(1))}%</strong>}
      </div>
      {s.props.interaction !== "static" && <details className="stream-ui__explore" open={s.props.interaction === "explore" || undefined}>
      <summary>探索图表</summary>
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
              <SelectItem value="3">最近 3 项</SelectItem>
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
      </details>}
      {s.props.columns.length > 1 && <div className="stream-ui__series" aria-label="数据系列">{s.props.columns.map((column,index)=><span key={column.key} data-hidden={!staticChart && hidden.includes(column.key)}><i style={{background:COLORS[index % COLORS.length]}} />{column.label}</span>)}</div>}
      {!rows.length ? (
        <Empty settled={s.state === "completed"} />
      ) : (
        <>
          <svg
            className="stream-ui__chart"
            viewBox="0 0 700 280"
            role="img"
            aria-label={s.props.title}
          >
            {[0, 1, 2].map((tick) => {
              const value = min + ((max - min) * tick) / 2;
              return (
                <g key={tick}>
                  <line
                    x1={52}
                    x2={680}
                    y1={y(value)}
                    y2={y(value)}
                    className="stream-ui__grid"
                  />
                  <text x={48} y={y(value) + 4} textAnchor="end">
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
                      strokeWidth={3}
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
                        r={index === rows.length - 1 || row.id === (focused ?? hovered) ? 5 : 0}
                        fill="currentColor"
                        stroke="var(--background)"
                        strokeWidth={2}
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
            {columns.length === 1 && rows.length > 0 && typeof rows.at(-1)!.values[columns[0].key] === "number" && <text className="stream-ui__endpoint" x={x(rows.length - 1)} y={y(rows.at(-1)!.values[columns[0].key] as number) - 14} textAnchor="middle">{format(rows.at(-1)!.values[columns[0].key])}</text>}
            {rows.map((row, index) => (
              <g
                key={row.id}
                onPointerEnter={() => setHovered(row.id)}
                onPointerLeave={() => setHovered(null)}
                onBlur={() => setFocused(null)}
                onFocus={() => setFocused(row.id)}
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
              : insight}
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
  const selectedNode = s.nodes.find((node) => node.id === selected);
  const chain = presentation(s).chain;
  if (chain) return <div className="stream-ui__linear" data-presentation="linear">
    <ol aria-label={s.props.title}>{chain.map((node, i) => <li key={node.id}>
      {i > 0 && <span className="stream-ui__flow-arrow" aria-hidden="true">→</span>}
      <button type="button" aria-pressed={selected === node.id} onClick={() => setSelected(node.id)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") {event.preventDefault(); setSelected(node.id);} }}>{node.label}</button>
      {i > 0 && s.edges.find(e => e.target === node.id)?.label && <small>{s.edges.find(e => e.target === node.id)?.label}</small>}
    </li>)}</ol>
    {selectedNode && <div className="stream-ui__flow-detail"><strong>{selectedNode.label}</strong><p>{selectedNode.description || selectedNode.group}</p>{onExplain && <Button size="sm" variant="ghost" onClick={() => onExplain(selectedNode.id)}>让 AI 解释此节点</Button>}</div>}
  </div>;
  return (
    <>
      <GraphCanvas snapshot={s} selected={selected} onSelect={setSelected} />
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
  const explore = presentation(s).tableExplore;
  const rows = s.rows
    .filter((row) => !explore ||
      `${row.label} ${Object.values(row.values).join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    )
    .toSorted((a, b) => {
      if (!explore || !sort) return 0;
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
      {explore && <div className="stream-ui__toolbar">
        <Input
          className="stream-ui__search"
          aria-label="筛选表格"
          maxLength={500}
          placeholder="筛选数据…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span>{rows.length} 项</span>
      </div>}
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
                    explore && sort?.key === column.key
                      ? sort.descending
                        ? "descending"
                        : "ascending"
                      : "none"
                  }
                >
                  {explore ? <Button
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
                  </Button> : column.label}
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
      {!rows.length && <Empty settled={s.state === "completed"} />}
    </>
  );
}

function TimelineView({ snapshot: s }: ViewProps) {
  return s.items.length ? <ol className="stream-ui__events" aria-label="阶段安排">
    {s.items.map(item => <li key={item.id}>
      {item.date && <time>{item.date}</time>}
      <div><strong>{item.label}</strong>{item.description && <p>{item.description}</p>}{item.value != null && <small>{format(item.value)}</small>}</div>
    </li>)}
  </ol> : <Empty settled={s.state === "completed"} />;
}

function MetricView({ snapshot: s }: ViewProps) {
  return s.items.length ? (
    <ul
      className="metric-card__grid stream-ui__metrics"
      style={
        { "--metric-columns": Math.min(s.items.length, 3) } as CSSProperties
      }
    >
      {s.items.map((item, index) => {
        // Models may include a unit in a string-valued metric. Keep that unit
        // subordinate without changing the saved value or action context.
        const split = typeof item.value === "string" ? item.value.match(/^([约≈]?\s*[+−-]?\d[\d,]*(?:\.\d+)?)\s*(人(?:\/月)?|个月|月|天|年|次|个|元|万元|%|GB|MB|KB|ms|s|小时|分钟|秒)$/u) : null;
        const value = split ? split[1] + (split[2] === "%" ? "%" : "") : format(item.value);
        const unit = split ? split[2] === "%" ? "" : split[2] : s.props.unit;
        return <li className="metric-card__item" data-emphasis={index === 0 ? "primary" : index === 1 ? "secondary" : "context"} key={item.id}>
          <span className="metric-card__label">{item.label}</span>
          <strong className="metric-card__value">
            <span>{value}</span>
            {unit && <small>{unit}</small>}
          </strong>
          {item.description && (
            <small className="metric-card__hint">{item.description}</small>
          )}
        </li>;
      })}
    </ul>
  ) : (
    <Empty settled={s.state === "completed"} />
  );
}
function Empty({settled = false}: {settled?: boolean}) {
  return (
    <div className="stream-ui__empty" role="status">
      {settled ? "暂无可显示的数据" : "等待数据填充…"}
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

  const canExplain =
    interactive &&
    !failed &&
    snapshot.state === "completed" &&
    !!onAction &&
    !busy;
  async function explain(nodeId?: string, blockId?: string, viewState?: Record<string, unknown>) {
    if (!canExplain) return;
    setBusy(true);
    setError("");
    try {
      await onAction?.({
        componentId: partId,
        componentType: "stream_ui",
        event: blockId ? "document.block.explain" : nodeId ? "graph.node.explain" : "explain",
        payload: {
          revision: snapshot.revision,
          ...(nodeId ? { node_id: nodeId } : {}),
          ...(blockId ? {block_id: blockId} : {}),
          ...(viewState ? {view_state: viewState} : ["chart", "table"].includes(snapshot.component) ? {view_state: streamUIViewState(`${partId}:${snapshot.component}`)} : {}),
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
      data-presentation={presentation(snapshot).form}
      data-density={snapshot.props.density}
      data-width={snapshot.props.width}
      aria-label={snapshot.props.title}
      aria-busy={snapshot.state === "streaming" && !failed}
    >
      {snapshot.preview && <p className="stream-ui__status" role="status">
        {failed || settled ? "内容生成中断，显示已接收部分" : "内容正在生成…"}
      </p>}
      {snapshot.component !== "document" && <header className="stream-ui__heading">
        <div className="stream-ui__heading-copy">
          <h3 className="stream-ui__title">{snapshot.props.title}</h3>
          {snapshot.props.description && <p>{snapshot.props.description}</p>}
        </div>
        {(snapshot.state === "streaming" || failed) && (
          <Badge
            variant="secondary"
            className="stream-ui__status"
            role="status"
          >
            {failed
              ? "生成中断"
              : "正在填充"}
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
        onBlockExplain={canExplain ? (blockId, payload) => { void explain(
          typeof payload.node_id === "string" ? payload.node_id : undefined,
          blockId,
          payload.view_state && typeof payload.view_state === "object" ? payload.view_state as Record<string, unknown> : undefined,
        ); } : undefined}
      /></Suspense>
      {failed && snapshot.component === "document" && <p className="rich-doc__caption" role="status">生成已中断，已生成内容仍可操作。</p>}
      {snapshot.component !== "document" && (!embedded || onAction || snapshot.props.source_note || snapshot.props.data_kind === "example") && <footer className="stream-ui__footer">
        <span>{[snapshot.props.data_kind === "example" ? "示例数据" : "", snapshot.props.source_note, snapshot.component !== "chart" ? snapshot.props.time_range : ""].filter(Boolean).join(" · ")}</span>
        {onAction && <Button
          size="sm"
          variant="ghost"
          type="button"
          disabled={!canExplain}
          onClick={() => {
            void explain();
          }}
        >
          <Sparkles aria-hidden="true" />
          {busy ? "正在提交…" : "让 AI 解释"}
        </Button>}
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
  const snapshot = useMemo(() => {
    const parsed = parseUISnapshot(data);
    return parsed ? committedNumericPreview(parsed) : null;
  }, [data]);
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

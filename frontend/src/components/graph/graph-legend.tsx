import { useState } from "react";
import { ChevronDown, ChevronUp, Sparkles } from "lucide-react";

import {
  NODE_STATUS_ORDER,
  NODE_STATUS_PROFILES,
  NODE_TYPE_ORDER,
  NODE_TYPE_PROFILES,
  type NodeLearningStatusId,
  type NodeTypeId,
} from "./node-presentation";

export function GraphTypeFilter({ counts, value, onChange }: {
  counts: ReadonlyMap<NodeTypeId, number>;
  value: NodeTypeId | "all";
  onChange: (type: NodeTypeId | "all") => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return <div className="graph-legend--floating">
    <button type="button" className="graph-legend graph-legend--collapsed" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      节点筛选{value === "all" ? "" : ` · ${NODE_TYPE_PROFILES[value].label}`}
    </button>
    {expanded && <section className="graph-legend graph-legend__group" aria-label="节点类型筛选">
      <ul>{NODE_TYPE_ORDER.filter(id => (counts.get(id) ?? 0) > 0).map(id => {
        const { icon: Icon, label, tone } = NODE_TYPE_PROFILES[id];
        return <li key={id}><button type="button" className="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-muted aria-pressed:bg-muted" aria-pressed={value === id} onClick={() => onChange(value === id ? "all" : id)}>
          <span className={`graph-legend__type is-${tone}`}><Icon aria-hidden="true" /></span>{label}<em>{counts.get(id)}</em>
        </button></li>;
      })}</ul>
      {value !== "all" && <button type="button" className="graph-legend__foot" onClick={() => onChange("all")}>重置类型筛选</button>}
    </section>}
  </div>;
}

const LEGEND_COLLAPSED_KEY = "learngraph:graph-legend-collapsed";

/**
 * Canvas legend: it explains the visual language (type glyph + status colour),
 * it is not a filter. Types the current graph does not contain stay listed but
 * muted, so the vocabulary is always documented without inventing nodes.
 */
export function GraphLegend({
  typeCounts,
  statusCounts,
  pageCount = 0,
  className,
  defaultCollapsed = false,
}: {
  typeCounts: ReadonlyMap<NodeTypeId, number>;
  statusCounts: ReadonlyMap<NodeLearningStatusId, number>;
  /** Nodes whose learning page (交互页) is already generated. */
  pageCount?: number;
  className?: string;
  defaultCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const preference = window.localStorage.getItem(LEGEND_COLLAPSED_KEY);
      return preference === null ? defaultCollapsed : preference === "1";
    } catch {
      return defaultCollapsed;
    }
  });

  function toggle() {
    setCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(LEGEND_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // Best-effort preference only.
      }
      return next;
    });
  }

  if (collapsed) {
    return (
      <div className={className}>
        <button
          aria-expanded={false}
          className="graph-legend graph-legend--collapsed"
          onClick={toggle}
          title="展开图例"
          type="button"
        >
          <ChevronDown className="size-3.5" />
          图例
        </button>
      </div>
    );
  }

  return (
    <div className={className}>
      <section aria-label="图谱图例" className="graph-legend">
        <header className="graph-legend__head">
          <span>图例</span>
          <button
            aria-expanded
            aria-label="收起图例"
            onClick={toggle}
            title="收起图例"
            type="button"
          >
            <ChevronUp className="size-3.5" />
          </button>
        </header>
        <div className="graph-legend__group">
          <p>节点类型</p>
          <ul>
            {NODE_TYPE_ORDER.map((id) => {
              const profile = NODE_TYPE_PROFILES[id];
              const Icon = profile.icon;
              const count = typeCounts.get(id) ?? 0;
              return (
                <li
                  className={count ? undefined : "is-absent"}
                  key={id}
                  title={
                    count
                      ? `${profile.hint} · 当前图谱 ${count} 个`
                      : `${profile.hint} · 当前图谱暂无`
                  }
                >
                  <span className={`graph-legend__type is-${profile.tone}`}>
                    <Icon aria-hidden="true" />
                  </span>
                  {profile.label}
                  {count ? <em>{count}</em> : null}
                </li>
              );
            })}
          </ul>
        </div>
        <div className="graph-legend__group">
          <p>学习状态</p>
          <ul>
            {NODE_STATUS_ORDER.map((id) => {
              const profile = NODE_STATUS_PROFILES[id];
              const count = statusCounts.get(id) ?? 0;
              return (
                <li
                  className={count ? undefined : "is-absent"}
                  key={id}
                  title={
                    id === "locked"
                      ? "前置知识尚未完成（仍可直接学习该节点）"
                      : count
                        ? `当前图谱 ${count} 个`
                        : "当前图谱暂无"
                  }
                >
                  <span
                    className={`graph-legend__status is-${profile.tone}`}
                    aria-hidden="true"
                  />
                  {profile.label}
                  {count ? <em>{count}</em> : null}
                </li>
              );
            })}
          </ul>
        </div>
        <div className="graph-legend__group">
          <p>标记</p>
          <ul>
            <li
              className={pageCount ? undefined : "is-absent"}
              title={
                pageCount
                  ? `该节点的学习页（教材 / 互动实验 / 闯关测评）已经生成 · 当前图谱 ${pageCount} 个`
                  : "当前图谱还没有已生成学习页的节点"
              }
            >
              <span className="graph-legend__page" aria-hidden="true">
                <Sparkles />
              </span>
              已生成交互页
              {pageCount ? <em>{pageCount}</em> : null}
            </li>
          </ul>
        </div>
      </section>
    </div>
  );
}

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import {
  NODE_STATUS_ORDER,
  NODE_STATUS_PROFILES,
  NODE_TYPE_ORDER,
  NODE_TYPE_PROFILES,
  type NodeLearningStatusId,
  type NodeTypeId,
} from "./node-presentation";

const LEGEND_COLLAPSED_KEY = "learngraph:graph-legend-collapsed";

/**
 * Canvas legend: it explains the visual language (type glyph + status colour),
 * it is not a filter. Types the current graph does not contain stay listed but
 * muted, so the vocabulary is always documented without inventing nodes.
 */
export function GraphLegend({
  typeCounts,
  statusCounts,
  className,
}: {
  typeCounts: ReadonlyMap<NodeTypeId, number>;
  statusCounts: ReadonlyMap<NodeLearningStatusId, number>;
  className?: string;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(LEGEND_COLLAPSED_KEY) === "1";
    } catch {
      return false;
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
      </section>
    </div>
  );
}

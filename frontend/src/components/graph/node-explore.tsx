import { Link2, MessageCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { NodeExploreRound } from "./node-explore-data";
import {
  metricLabel,
  recommendAdvice,
  recommendFromWeight,
  weightToImportance,
  type MetricLevel,
} from "./node-metrics";

export function RecommendDots({
  weight,
  className,
  relevance,
  difficulty,
}: {
  weight: number | null | undefined;
  className?: string;
  relevance?: MetricLevel;
  difficulty?: MetricLevel;
}) {
  const importance = weightToImportance(weight);
  const score = recommendFromWeight(weight, relevance);
  const diff = difficulty ?? 2;
  const tip = `${recommendAdvice(score)} · 重要 ${metricLabel(importance)} · 相关 ${metricLabel(relevance ?? importance)} · 难度 ${metricLabel(diff)}`;
  return (
    <span
      aria-label={tip}
      className={cn("recommend-dots", `recommend-${score}`, className)}
      data-recommend-score={score}
      title={tip}
    >
      {[1, 2, 3].map((index) => (
        <span
          className={cn("recommend-dot", index <= score && "filled")}
          key={index}
        />
      ))}
    </span>
  );
}

export function NodeExploreChip({
  count,
  onOpen,
  className,
}: {
  count: number;
  onOpen?: () => void;
  className?: string;
}) {
  const active = count > 0;
  return (
    <button
      aria-label={
        active ? `查看探索链：已深入 ${count} 轮` : "还没有深入讲解记录"
      }
      className={cn(
        "node-explore-chip",
        active ? "is-active" : "is-empty",
        className,
      )}
      // Keep clickable when empty so the chip can surface the empty-state
      // panel; only jump into the chain when rounds exist.
      onClick={(event) => {
        event.stopPropagation();
        onOpen?.();
      }}
      title={
        active
          ? `已深入 ${count} 轮 · 点击查看每一轮`
          : "未深入 · 围绕此节点提问后会自动更新"
      }
      type="button"
    >
      <Link2 className="size-3" aria-hidden="true" />
      <span>{active ? `深入 ×${count}` : "未深入"}</span>
    </button>
  );
}

export function NodeExploreChain({
  rounds,
  title,
  onJump,
  onClose,
}: {
  rounds: NodeExploreRound[];
  title: string;
  onJump?: (round: NodeExploreRound, index: number) => void;
  onClose?: () => void;
}) {
  if (!rounds.length) return null;
  return (
    <div className="chain-panel visible" role="dialog" aria-label={`${title} 的探索链`}>
      <div className="chain-panel-head">
        <div>
          <strong>{title}</strong>
          <span>
            {rounds.length} 轮探索 · 点击条目查看对应问答
          </span>
        </div>
        {onClose ? (
          <button
            aria-label="关闭探索链"
            className="chain-panel-close"
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        ) : null}
      </div>
      <ol className="chain-list">
        {rounds.map((round, index) => {
          const label =
            round.content.trim().length > 40
              ? `${round.content.trim().slice(0, 40)}…`
              : round.content.trim() || "(空)";
          const when = (() => {
            const date = new Date(round.created_at);
            return Number.isNaN(date.getTime())
              ? ""
              : date.toLocaleString();
          })();
          return (
            <li className="chain-item" key={round.id}>
              <span className="chain-dot">{index + 1}</span>
              <button
                className="chain-body"
                onClick={() => onJump?.(round, index)}
                type="button"
              >
                <span className="chain-label">{label}</span>
                {when ? <span className="chain-hint">{when}</span> : null}
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function NodeExploreEmpty({
  onLearn,
}: {
  onLearn?: () => void;
}) {
  return (
    <div className="node-explore-empty">
      <p>还没有围绕这个节点的深入记录。</p>
      {onLearn ? (
        <Button onClick={onLearn} size="sm" variant="outline">
          <MessageCircle className="size-3.5" />
          开始围绕此节点学习
        </Button>
      ) : null}
    </div>
  );
}

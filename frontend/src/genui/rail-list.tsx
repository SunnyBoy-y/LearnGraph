import { memo, type ReactNode } from "react";
import {
  Zap, BrainCircuit, Search, Layers3, ShieldCheck, Lightbulb, CircleDot,
  CheckCheck, Blocks, SlidersHorizontal, FileText, Database, ChartNoAxesCombined,
  BookOpen, Target, Code2, Circle, CircleCheck, CircleAlert, LoaderCircle,
} from "lucide-react";
import type { DocumentNode } from "@/lib/stream-ui";
import type { RailIcon } from "./rail-list-schema";
import "./rail-list.css";

const ICONS = {
  zap: Zap, "brain-circuit": BrainCircuit, search: Search, layers: Layers3,
  shield: ShieldCheck, lightbulb: Lightbulb, "circle-dot": CircleDot,
  "check-check": CheckCheck, blocks: Blocks, sliders: SlidersHorizontal,
  "file-text": FileText, database: Database, chart: ChartNoAxesCombined,
  "book-open": BookOpen, target: Target, code: Code2,
} satisfies Record<RailIcon, typeof Zap>;
const STATUSES = {
  pending: {Icon: Circle, label: "待处理"}, active: {Icon: LoaderCircle, label: "进行中"},
  completed: {Icon: CircleCheck, label: "已完成"}, error: {Icon: CircleAlert, label: "未完成"},
};

const RailItem = memo(function RailItem({item, index, variant, renderText, interrupted}: {
  item: DocumentNode; index: number; variant: string; renderText: (text: string) => ReactNode; interrupted: boolean;
}) {
  const Icon = ICONS[item.icon ?? "circle-dot"];
  const status = item.item_status ?? "pending";
  const StatusIcon = STATUSES[status].Icon;
  return <li role="listitem" className="genui-rail-list__item" data-item-id={item.id} data-state={item.state} aria-busy={!interrupted && (item.state === "streaming" || item.state === "pending")}>
    <span className="genui-rail-list__marker" data-status={variant === "status_list" ? status : undefined}>
      {variant === "step_list" ? <span className="genui-rail-list__number" aria-hidden="true">{index + 1}</span> :
        variant === "status_list" ? <span role="img" aria-label={STATUSES[status].label}><StatusIcon size={20} strokeWidth={1.8} aria-hidden="true" /></span> :
          <Icon size={20} strokeWidth={1.8} aria-hidden="true" />}
    </span>
    <div className="genui-rail-list__copy">
      {item.label && <h3 className="genui-rail-list__title">{item.label}</h3>}
      {item.text && <div className="genui-rail-list__description">{renderText(item.text)}</div>}
      {item.caption && <div className="genui-rail-list__detail">{renderText(item.caption)}</div>}
      {item.state === "pending" && !item.text && !interrupted && <p className="genui-rail-list__placeholder" role="status">正在补充内容…</p>}
      {interrupted && ["streaming", "pending"].includes(item.state) && <p className="genui-rail-list__placeholder" role="status">生成已中断，保留已接收内容。</p>}
      {item.state === "error" && <p className="genui-rail-list__placeholder" role="status">此条目生成中断，保留已接收内容。</p>}
    </div>
  </li>;
});

/** Shared editorial rail, without card shells, arbitrary CSS or icon URLs. */
export function RailList({node, items, renderText, interrupted = false}: {
  node: DocumentNode; items: DocumentNode[]; renderText: (text: string) => ReactNode; interrupted?: boolean;
}) {
  const List = node.type === "step_list" ? "ol" : "ul";
  const name = node.label || {feature_list: "要点", step_list: "操作步骤", status_list: "状态进度"}[node.type as "feature_list" | "step_list" | "status_list"];
  return <section className="genui-rail-list" data-variant={node.type} data-interrupted={interrupted} aria-label={name}>
    {node.label && <h3 className="genui-rail-list__heading">{node.label}</h3>}
    {node.text && <div className="genui-rail-list__intro">{renderText(node.text)}</div>}
    {items.length ? <List role="list" className="genui-rail-list__items" aria-label={name}>
      {items.map((item, index) => <RailItem key={item.id} item={item} index={index} variant={node.type} renderText={renderText} interrupted={interrupted} />)}
    </List> : <p className="genui-rail-list__placeholder" role="status">{interrupted || node.state === "completed" ? "暂无可显示的条目" : "正在整理条目…"}</p>}
  </section>;
}

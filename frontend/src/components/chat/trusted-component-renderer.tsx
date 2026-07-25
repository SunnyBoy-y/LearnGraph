import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CloudSun,
  GitBranch,
  ImageIcon,
  Network,
  ShieldCheck,
  Thermometer,
  X,
} from "lucide-react";
import { z } from "zod";

import {
  OptionGroup,
  type OptionGroupSubmission,
} from "@/components/chat/option-group";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const optionSchema = z.object({
  id: z.string().min(1).max(160),
  label: z.string().min(1).max(500),
  description: z.string().max(2_000).nullish(),
});

const optionComponentSchema = z.object({
  component_type: z.enum(["option_group", "single_choice", "multiple_choice"]),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.string().min(1).max(32),
  props: z.object({
    title: z.string().min(1).max(500),
    description: z.string().max(2_000).nullish(),
    options: z.array(optionSchema).max(20).default([]),
    allow_custom: z.boolean().default(true),
    allow_skip: z.boolean().default(true),
    submit_label: z.string().min(1).max(80).nullish(),
  }),
  allowed_events: z.array(z.string()).max(10).default(["submit"]),
});

const textComponentSchema = z.object({
  component_type: z.enum(["fill_blank", "short_answer_table"]),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.string().min(1).max(32),
  props: z.object({
    title: z.string().min(1).max(500),
    description: z.string().max(2_000).nullish(),
    placeholder: z.string().max(500).nullish(),
    multiline: z.boolean().default(false),
    submit_label: z.string().min(1).max(80).nullish(),
  }),
  allowed_events: z.array(z.string()).max(10).default(["submit"]),
});

const imageComponentSchema = z.object({
  component_type: z.literal("image_frame"),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.string().min(1).max(32),
  props: z.object({
    title: z.string().min(1).max(500),
    alt: z.string().max(500),
    src: z.union([z.string().url(), z.literal(""), z.null()]).optional(),
    status: z.enum([
      "queued",
      "running",
      "completed",
      "failed",
      "cancelled",
      "placeholder",
      "ready",
    ]),
    aspect_ratio: z.string().max(32).nullish(),
  }),
  allowed_events: z.array(z.string()).max(10).default([]),
});

const actionSchema = z.object({
  id: z.string().min(1).max(80),
  label: z.string().min(1).max(80),
  event: z.string().min(1).max(80).optional(),
});

const weatherComponentSchema = z.object({
  component_type: z.literal("weather_card"),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.string().min(1).max(32),
  props: z.object({
    title: z.string().min(1).max(500).nullish(),
    location: z.string().min(1).max(240),
    condition: z.string().min(1).max(240),
    temperature_c: z.number().min(-100).max(100),
    high_c: z.number().min(-100).max(100).nullish(),
    low_c: z.number().min(-100).max(100).nullish(),
    summary: z.string().max(2_000).nullish(),
    unit: z.enum(["C", "F"]).default("C"),
    actions: z.array(actionSchema).max(5).default([]),
  }),
  allowed_events: z.array(z.string()).max(10).default([]),
});

const metricItemSchema = z.object({
  id: z.string().min(1).max(80).optional(),
  label: z.string().min(1).max(120),
  value: z.union([z.string().max(120), z.number()]),
  hint: z.string().max(240).nullish(),
});

const metricComponentSchema = z.object({
  component_type: z.literal("metric_card"),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.string().min(1).max(32),
  props: z.object({
    title: z.string().min(1).max(500),
    description: z.string().max(2_000).nullish(),
    metrics: z.array(metricItemSchema).min(1).max(12),
    actions: z.array(actionSchema).max(5).default([]),
  }),
  allowed_events: z.array(z.string()).max(10).default([]),
});

const graphNodeSchema = z.object({
  id: z.string().min(1).max(160),
  ref: z.string().min(1).max(160),
  node_id: z.string().max(160).nullable(),
  label: z.string().min(1).max(500),
  description: z.string().max(4_000).default(""),
  node_type: z.enum(["root", "concept", "practice", "assessment"]),
  change: z.enum(["add", "update"]),
  rationale: z.string().max(2_000),
});

const graphEdgeSchema = z.object({
  source_ref: z.string().min(1).max(160),
  target_ref: z.string().min(1).max(160),
  relation: z.enum(["contains", "prerequisite", "related", "contrast", "application"]),
  rationale: z.string().max(2_000),
});

const graphProposalSchema = z.object({
  component_type: z.literal("graph_update_proposal"),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.literal("1.0"),
  props: z.object({
    proposal_id: z.string().min(1).max(160),
    mode: z.enum(["create", "update"]),
    graph_id: z.string().max(160).nullable().optional(),
    goal_id: z.string().max(160),
    base_revision: z.number().int().nonnegative().nullable().optional(),
    confirmed_revision: z.number().int().nonnegative().nullable().optional(),
    title: z.string().min(1).max(500),
    summary: z.string().max(4_000),
    nodes: z.array(graphNodeSchema).max(100),
    edges: z.array(graphEdgeSchema).max(200),
    confirmation_required: z.boolean(),
    status: z.enum(["proposed", "confirmed", "rejected"]),
    confirmed_node_ids: z.record(z.string(), z.string()).optional(),
    rejection_reason: z.string().max(2_000).optional(),
  }),
  allowed_events: z.array(z.enum(["confirm", "reject"])).max(2),
});

const trustedComponentSchema = z.union([
  optionComponentSchema,
  textComponentSchema,
  imageComponentSchema,
  weatherComponentSchema,
  metricComponentSchema,
  graphProposalSchema,
]);

export type TrustedComponentAction = {
  componentId: string;
  componentType: string;
  event: string;
  payload: Record<string, unknown>;
};

function UnsupportedComponent({
  data,
  reason,
}: {
  data: Record<string, unknown>;
  reason: string;
}) {
  return (
    <section className="message-component message-component--invalid" role="status">
      <div className="message-component__heading">
        <AlertTriangle className="size-4" />
        <div>
          <strong>组件已安全降级</strong>
          <span>{reason}</span>
        </div>
      </div>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </section>
  );
}

export function TrustedComponentRenderer({
  data,
  fallbackId,
  onAction,
}: {
  data: Record<string, unknown>;
  fallbackId: string;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
}) {
  const parsed = useMemo(() => trustedComponentSchema.safeParse(data), [data]);
  const [textValue, setTextValue] = useState("");

  if (!parsed.success) {
    const componentType =
      typeof data.component_type === "string" ? data.component_type : "未声明类型";
    return (
      <UnsupportedComponent
        data={data}
        reason={`未注册或数据未通过 Schema：${componentType}`}
      />
    );
  }

  const component = parsed.data;
  const componentId = component.component_id ?? fallbackId;

  function emit(event: string, payload: Record<string, unknown>) {
    void onAction?.({
      componentId,
      componentType: component.component_type,
      event,
      payload,
    });
  }

  if (
    component.component_type === "option_group" ||
    component.component_type === "single_choice" ||
    component.component_type === "multiple_choice"
  ) {
    const mode =
      component.component_type === "multiple_choice" ? "multiple" : "single";
    return (
      <OptionGroup
        allowCustom={component.props.allow_custom}
        allowSkip={component.props.allow_skip}
        description={component.props.description ?? undefined}
        mode={mode}
        onSubmit={(submission: OptionGroupSubmission) =>
          emit("submit", { ...submission })
        }
        options={component.props.options.map((option) => ({
          ...option,
          description: option.description ?? undefined,
        }))}
        submitLabel={component.props.submit_label ?? undefined}
        title={component.props.title}
      />
    );
  }

  if (
    component.component_type === "fill_blank" ||
    component.component_type === "short_answer_table"
  ) {
    const multiline =
      component.component_type === "short_answer_table" || component.props.multiline;
    return (
      <section className="message-component" aria-label={component.props.title}>
        <div className="message-component__heading">
          <ShieldCheck className="size-4" />
          <div>
            <strong>{component.props.title}</strong>
            {component.props.description ? <span>{component.props.description}</span> : null}
          </div>
          <Badge variant="secondary">Schema {component.schema_version}</Badge>
        </div>
        {multiline ? (
          <Textarea
            aria-label={component.props.title}
            onChange={(event) => setTextValue(event.currentTarget.value)}
            placeholder={component.props.placeholder ?? undefined}
            value={textValue}
          />
        ) : (
          <Input
            aria-label={component.props.title}
            onChange={(event) => setTextValue(event.currentTarget.value)}
            placeholder={component.props.placeholder ?? undefined}
            value={textValue}
          />
        )}
        <div className="message-component__actions">
          <Button
            disabled={!textValue.trim() || !component.allowed_events.includes("submit")}
            onClick={() => emit("submit", { value: textValue.trim() })}
            size="sm"
          >
            {component.props.submit_label ?? "提交"}
          </Button>
        </div>
      </section>
    );
  }

  if (component.component_type === "image_frame") {
    const src = component.props.src ?? undefined;
    const ready =
      Boolean(src) &&
      (component.props.status === "completed" ||
        component.props.status === "ready");
    return (
      <figure
        className="message-component message-image-frame"
        style={{ aspectRatio: component.props.aspect_ratio ?? "16 / 9" }}
      >
        {ready ? (
          <img alt={component.props.alt} src={src} />
        ) : (
          <div className="message-image-frame__state">
            <ImageIcon className="size-6" />
            <strong>{component.props.title}</strong>
            <span>{component.props.status}</span>
          </div>
        )}
      </figure>
    );
  }

  if (component.component_type === "weather_card") {
    const unit = component.props.unit === "F" ? "°F" : "°C";
    const title = component.props.title ?? `${component.props.location} 天气`;
    return (
      <section aria-label={title} className="message-component weather-card">
        <div className="message-component__heading">
          <CloudSun className="size-4" />
          <div>
            <strong>{title}</strong>
            <span>{component.props.location}</span>
          </div>
          <Badge variant="secondary">Schema {component.schema_version}</Badge>
        </div>
        <div className="weather-card__body">
          <div className="weather-card__temp">
            <Thermometer className="size-4" />
            <strong>
              {component.props.temperature_c}
              {unit}
            </strong>
            <span>{component.props.condition}</span>
          </div>
          {(component.props.high_c != null || component.props.low_c != null) && (
            <div className="weather-card__range">
              {component.props.high_c != null ? (
                <span>
                  最高 {component.props.high_c}
                  {unit}
                </span>
              ) : null}
              {component.props.low_c != null ? (
                <span>
                  最低 {component.props.low_c}
                  {unit}
                </span>
              ) : null}
            </div>
          )}
          {component.props.summary ? (
            <p className="weather-card__summary">{component.props.summary}</p>
          ) : null}
        </div>
        {component.props.actions.length ? (
          <div className="message-component__actions">
            {component.props.actions.map((action) => {
              const eventName = action.event ?? action.id;
              return (
                <Button
                  disabled={!component.allowed_events.includes(eventName)}
                  key={action.id}
                  onClick={() =>
                    emit(eventName, {
                      action_id: action.id,
                      location: component.props.location,
                      temperature_c: component.props.temperature_c,
                      condition: component.props.condition,
                    })
                  }
                  size="sm"
                >
                  {action.label}
                </Button>
              );
            })}
          </div>
        ) : null}
      </section>
    );
  }

  if (component.component_type === "metric_card") {
    return (
      <section
        aria-label={component.props.title}
        className="message-component metric-card"
      >
        <div className="message-component__heading">
          <ShieldCheck className="size-4" />
          <div>
            <strong>{component.props.title}</strong>
            {component.props.description ? (
              <span>{component.props.description}</span>
            ) : null}
          </div>
          <Badge variant="secondary">Schema {component.schema_version}</Badge>
        </div>
        <ul className="metric-card__grid">
          {component.props.metrics.map((metric, index) => (
            <li key={metric.id ?? `${metric.label}-${index}`}>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              {metric.hint ? <small>{metric.hint}</small> : null}
            </li>
          ))}
        </ul>
        {component.props.actions.length ? (
          <div className="message-component__actions">
            {component.props.actions.map((action) => {
              const eventName = action.event ?? action.id;
              return (
                <Button
                  disabled={!component.allowed_events.includes(eventName)}
                  key={action.id}
                  onClick={() =>
                    emit(eventName, {
                      action_id: action.id,
                      metrics: component.props.metrics,
                    })
                  }
                  size="sm"
                >
                  {action.label}
                </Button>
              );
            })}
          </div>
        ) : null}
      </section>
    );
  }

  if (component.component_type !== "graph_update_proposal") {
    return (
      <UnsupportedComponent
        data={data}
        reason="组件类型没有匹配到已注册渲染器"
      />
    );
  }
  const graphComponent = graphProposalSchema.parse(data);
  const proposal = graphComponent.props;
  const canDecide =
    proposal.status === "proposed" && proposal.confirmation_required;
  return (
    <section className="graph-proposal" aria-label={`图谱变更提案：${proposal.title}`}>
      <div className="graph-proposal__heading">
        <span>
          <Network className="size-4" />
        </span>
        <div>
          <p>图谱变更提案</p>
          <strong>{proposal.title}</strong>
        </div>
        <Badge variant={proposal.status === "confirmed" ? "default" : "secondary"}>
          {proposal.status}
        </Badge>
      </div>
      <p className="graph-proposal__summary">{proposal.summary}</p>
      <div className="graph-proposal__stats">
        <span><strong>{proposal.nodes.length}</strong> 个节点</span>
        <span><strong>{proposal.edges.length}</strong> 条关系</span>
        <span><strong>{proposal.mode}</strong> 模式</span>
      </div>
      {proposal.nodes.length ? (
        <ul className="graph-proposal__nodes">
          {proposal.nodes.slice(0, 8).map((node) => (
            <li key={node.id}>
              <span data-change={node.change}>{node.change}</span>
              <div>
                <strong>{node.label}</strong>
                {node.description ? <small>{node.description}</small> : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {proposal.nodes.length > 8 ? (
        <p className="graph-proposal__more">另有 {proposal.nodes.length - 8} 个节点，确认前可在图谱工作台完整查看。</p>
      ) : null}
      {canDecide ? (
        <div className="graph-proposal__actions">
          <Button
            disabled={!graphComponent.allowed_events.includes("reject")}
            onClick={() => emit("reject", { proposal_id: proposal.proposal_id })}
            size="sm"
            variant="ghost"
          >
            <X className="size-3.5" />拒绝
          </Button>
          <Button
            disabled={!graphComponent.allowed_events.includes("confirm")}
            onClick={() => emit("confirm", { proposal_id: proposal.proposal_id })}
            size="sm"
          >
            <CheckCircle2 className="size-3.5" />确认写入图谱
          </Button>
        </div>
      ) : (
        <div className="graph-proposal__resolved">
          <GitBranch className="size-3.5" />
          {proposal.status === "confirmed" ? "已写入正式图谱修订" : "该提案已结束"}
        </div>
      )}
    </section>
  );
}

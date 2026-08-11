import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  CloudSun,
  GitBranch,
  ImageIcon,
  Network,
  Save,
  Send,
  ShieldCheck,
  Target,
  Thermometer,
  Undo2,
  X,
} from "lucide-react";
import { z } from "zod";

import {
  OptionGroup,
  type OptionGroupSubmission,
} from "@/components/chat/option-group";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { SandboxArtifact } from "@/components/chat/sandbox-artifact";
import { DateScheduleCalendar } from "@/components/chat/date-schedule-calendar";

const optionSchema = z.object({
  id: z.string().min(1).max(160),
  label: z.string().min(1).max(500),
  description: z.string().max(2_000).nullish(),
  is_correct: z.boolean().optional(),
});

const answerKeyProps = {
  correct_option_ids: z.array(z.string().min(1).max(80)).max(100).optional(),
  correct_answers: z.array(z.string().min(1).max(2_000)).max(20).optional(),
  explanation: z.string().max(5_000).nullish(),
  feedback_correct: z.string().max(2_000).nullish(),
  feedback_incorrect: z.string().max(2_000).nullish(),
};

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
    ...answerKeyProps,
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
    ...answerKeyProps,
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
    status: z.enum(["proposed", "confirmed", "rejected", "undone"]),
    confirmed_node_ids: z.record(z.string(), z.string()).optional(),
    rejection_reason: z.string().max(2_000).optional(),
    can_undo: z.boolean().optional(),
  }),
  allowed_events: z.array(z.enum(["confirm", "reject", "undo"])).max(3),
});

const goalDraftFieldSchema = z.object({
  title: z.string().min(1).max(240),
  intent: z.string().max(240).optional(),
  time_limit: z.string().max(120).optional(),
  desired_outcome: z.string().max(4_000).optional(),
});

const goalDraftEditorSchema = z.object({
  component_type: z.literal("goal_draft_editor"),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.string().min(1).max(32),
  props: z.object({
    title: z.string().min(1).max(500),
    description: z.string().max(2_000).nullish(),
    goal_id: z.string().max(160).optional(),
    goal_status: z.string().max(40).optional(),
    focus: z.enum(["title", "time", "outcome", "all"]).optional(),
    submit_label: z.string().max(80).nullish(),
    draft: goalDraftFieldSchema,
  }),
  allowed_events: z.array(z.string()).max(10).default(["submit"]),
});

const questionBatchItemSchema = z.object({
  key: z.string().min(1).max(80),
  prompt: z.string().min(1).max(500),
  input_type: z
    .enum([
      "single_choice",
      "multiple_choice",
      "fill_blank",
      "short_answer_table",
      "date",
    ])
    .default("single_choice"),
  placeholder: z.string().max(500).optional(),
  options: z.array(optionSchema).max(8).default([]),
  allow_custom: z.boolean().default(true),
  allow_skip: z.boolean().default(true),
  required: z.boolean().default(false),
});

const questionBatchSchema = z.object({
  component_type: z.literal("question_batch"),
  component_id: z.string().min(1).max(160).optional(),
  schema_version: z.string().min(1).max(32),
  props: z.object({
    title: z.string().min(1).max(500),
    description: z.string().max(2_000).nullish(),
    submit_label: z.string().max(80).nullish(),
    questions: z.array(questionBatchItemSchema).min(2).max(8),
  }),
  allowed_events: z.array(z.string()).max(10).default(["submit"]),
});

const trustedComponentSchema = z.union([
  optionComponentSchema,
  textComponentSchema,
  imageComponentSchema,
  weatherComponentSchema,
  metricComponentSchema,
  graphProposalSchema,
  goalDraftEditorSchema,
  questionBatchSchema,
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
  interactive = true,
  onAction,
}: {
  data: Record<string, unknown>;
  fallbackId: string;
  /** When false, action buttons render but stay disabled (e.g. mid-stream). */
  interactive?: boolean;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
}) {
  const parsed = useMemo(() => trustedComponentSchema.safeParse(data), [data]);
  const [textValue, setTextValue] = useState("");
  const [goalDraftValue, setGoalDraftValue] = useState<{
    title: string;
    intent: string;
    time_limit: string;
    desired_outcome: string;
  }>({ title: "", intent: "", time_limit: "", desired_outcome: "" });
  const [batchAnswers, setBatchAnswers] = useState<
    Record<string, { labels: string[]; values: string[]; text?: string }>
  >({});

  // Third-party components are delivered with delivery_mode="sandbox_artifact".
  // They never match the built-in declarative schema below; delegate them to
  // SandboxArtifact, which renders the server-owned inert preview inside the
  // opaque-origin iframe and surfaces the P2-A trusted-renderer decision. This
  // is the safe downgrade path — third-party code still never enters the main
  // DOM and the iframe boundary (no allow-same-origin, connect-src 'none') is
  // never relaxed.
  const sandboxArtifact =
    data.delivery_mode === "sandbox_artifact" &&
    typeof data.sandbox_artifact === "object" &&
    data.sandbox_artifact !== null &&
    !Array.isArray(data.sandbox_artifact)
      ? (data.sandbox_artifact as Record<string, unknown>)
      : null;
  if (sandboxArtifact) {
    const props =
      typeof data.props === "object" && data.props !== null
        ? (data.props as Record<string, unknown>)
        : undefined;
    const title = typeof props?.title === "string" ? props.title : undefined;
    return (
      <SandboxArtifact
        data={title ? { ...sandboxArtifact, title } : sandboxArtifact}
      />
    );
  }

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

  if (component.component_type === "question_batch") {
    const batch = component.props;
    // Time-keyword heuristic: open text questions that ask about a date/time
    // render the calendar too, so the model never needs to know the exact
    // input_type to give the user a calendar picker.
    const TIME_KEYWORD_RE =
      /什么时候|日期|哪天|周几|星期|几号|deadline|截止|开始时间|结束时间|安排|时段|几点|时间|每天|每周/;
    const isAnswered = (question: z.infer<typeof questionBatchItemSchema>) => {
      const answer = batchAnswers[question.key];
      if (!answer) return false;
      if (
        question.input_type === "fill_blank" ||
        question.input_type === "short_answer_table" ||
        question.input_type === "date"
      )
        return Boolean(answer.text?.trim());
      return answer.values.length > 0;
    };
    const answeredCount = batch.questions.filter(isAnswered).length;
    const missingRequired = batch.questions.filter(
      (question) => question.required && !isAnswered(question),
    );
    const canSubmit =
      interactive && missingRequired.length === 0 && answeredCount > 0;
    const typeLabel = (inputType: string) =>
      inputType === "single_choice"
        ? "单选"
        : inputType === "multiple_choice"
          ? "可多选"
          : inputType === "fill_blank"
            ? "填空"
            : inputType === "date"
              ? "日期"
              : "简答";

    const setChoice = (
      question: z.infer<typeof questionBatchItemSchema>,
      optionId: string,
      optionLabel: string,
      checked = true,
    ) => {
      setBatchAnswers((current) => {
        const existing = current[question.key];
        if (question.input_type === "multiple_choice") {
          const values = checked
            ? [...(existing?.values ?? []), optionId]
            : (existing?.values ?? []).filter((item) => item !== optionId);
          const labels = values.map((id) => {
            const option = question.options.find((item) => item.id === id);
            return option?.label ?? id;
          });
          return { ...current, [question.key]: { labels, values } };
        }
        return {
          ...current,
          [question.key]: { labels: [optionLabel], values: [optionId] },
        };
      });
    };
    const setText = (
      questionKey: string,
      text: string,
      labels: string[] = [],
    ) => {
      setBatchAnswers((current) => ({
        ...current,
        [questionKey]: {
          ...current[questionKey],
          text,
          values: [],
          labels,
        },
      }));
    };
    return (
      <section
        aria-label={batch.title}
        className="option-group question-batch"
      >
        <div className="option-group__heading">
          <div>
            <p>{batch.title}</p>
            {batch.description ? <span>{batch.description}</span> : null}
          </div>
          <span>{batch.questions.length} 题 · 聚合问答</span>
        </div>
        <div className="question-batch__list">
          {batch.questions.map((question, index) => {
            const current = batchAnswers[question.key];
            const isChoice =
              question.input_type === "single_choice" ||
              question.input_type === "multiple_choice";
            const isDate =
              question.input_type === "date" ||
              (question.input_type !== "single_choice" &&
                question.input_type !== "multiple_choice" &&
                TIME_KEYWORD_RE.test(question.prompt));
            return (
              <div className="question-batch__item" key={question.key}>
                <div className="question-batch__prompt">
                  <span className="question-batch__index">{index + 1}</span>
                  <strong>{question.prompt}</strong>
                  <span className="question-batch__type">
                    {typeLabel(isDate ? "date" : question.input_type)}
                  </span>
                  {question.required ? (
                    <span className="question-batch__required">必答</span>
                  ) : null}
                </div>
                {isChoice ? (
                  <>
                    {question.input_type === "single_choice" ? (
                      <RadioGroup
                        className="option-group__choices question-batch__choices"
                        disabled={!interactive}
                        onValueChange={(next) =>
                          setChoice(
                            question,
                            next,
                            question.options.find((item) => item.id === next)
                              ?.label ?? next,
                          )
                        }
                        value={current?.values[0] ?? ""}
                      >
                        {question.options.map((option) => (
                          <Label
                            className={cn(
                              "option-group__choice",
                              current?.values.includes(option.id) &&
                                "is-selected",
                            )}
                            htmlFor={`${componentId}-qb-${question.key}-${option.id}`}
                            key={option.id}
                          >
                            <RadioGroupItem
                              id={`${componentId}-qb-${question.key}-${option.id}`}
                              value={option.id}
                            />
                            <span>
                              <strong>{option.label}</strong>
                              {option.description ? (
                                <small>{option.description}</small>
                              ) : null}
                            </span>
                            {current?.values.includes(option.id) ? (
                              <Check aria-hidden="true" className="size-3.5" />
                            ) : null}
                          </Label>
                        ))}
                      </RadioGroup>
                    ) : (
                      <div className="option-group__choices question-batch__choices">
                        {question.options.map((option) => {
                          const selected = (current?.values ?? []).includes(
                            option.id,
                          );
                          return (
                            <Label
                              className={cn(
                                "option-group__choice",
                                selected && "is-selected",
                              )}
                              htmlFor={`${componentId}-qb-${question.key}-${option.id}`}
                              key={option.id}
                            >
                              <Checkbox
                                checked={selected}
                                disabled={!interactive}
                                id={`${componentId}-qb-${question.key}-${option.id}`}
                                onCheckedChange={(checked) =>
                                  setChoice(
                                    question,
                                    option.id,
                                    option.label,
                                    checked !== false,
                                  )
                                }
                              />
                              <span>
                                <strong>{option.label}</strong>
                                {option.description ? (
                                  <small>{option.description}</small>
                                ) : null}
                              </span>
                              {selected ? (
                                <Check aria-hidden="true" className="size-3.5" />
                              ) : null}
                            </Label>
                          );
                        })}
                      </div>
                    )}
                    {question.allow_custom ? (
                      <Input
                        aria-label={`${question.prompt} 自定义答案`}
                        className="option-group__custom"
                        disabled={!interactive}
                        onChange={(event) =>
                          setText(question.key, event.target.value)
                        }
                        placeholder="输入其他答案"
                        value={current?.text ?? ""}
                      />
                    ) : null}
                  </>
                ) : isDate ? (
                  <div className="question-batch__date">
                    <DateScheduleCalendar
                      disabled={!interactive}
                      onChange={(dateKey, label) =>
                        setText(question.key, dateKey, [label])
                      }
                      value={current?.text}
                    />
                    <Input
                      aria-label={`${question.prompt} 手动输入日期`}
                      className="option-group__custom"
                      disabled={!interactive}
                      onChange={(event) =>
                        setText(question.key, event.target.value)
                      }
                      placeholder="或手动输入，如 2026-09-01"
                      value={current?.text ?? ""}
                    />
                  </div>
                ) : (
                  <div className="question-batch__open">
                    {question.input_type === "short_answer_table" ? (
                      <Textarea
                        aria-label={question.prompt}
                        disabled={!interactive}
                        onChange={(event) =>
                          setText(question.key, event.target.value)
                        }
                        placeholder={question.placeholder ?? "请输入…"}
                        value={current?.text ?? ""}
                      />
                    ) : (
                      <Input
                        aria-label={question.prompt}
                        className="option-group__custom"
                        disabled={!interactive}
                        onChange={(event) =>
                          setText(question.key, event.target.value)
                        }
                        placeholder={question.placeholder ?? "请输入…"}
                        value={current?.text ?? ""}
                      />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="question-batch__progress">
          <span>
            已答 {answeredCount}/{batch.questions.length}
          </span>
          {missingRequired.length ? (
            <span className="question-batch__missing">
              还有 {missingRequired.length} 道必答题未作答
            </span>
          ) : (
            <span>一次提交，全部答案一起回传</span>
          )}
        </div>
        <div className="option-group__actions">
          <Button
            disabled={!canSubmit || !component.allowed_events.includes("submit")}
            onClick={() =>
              emit("submit", {
                answers: batch.questions.map((question) => {
                  const answer = batchAnswers[question.key];
                  const value = answer?.text?.trim() ?? answer?.values.join("、") ?? "";
                  const label = answer?.labels.join("、") ?? "";
                  return {
                    key: question.key,
                    prompt: question.prompt,
                    value,
                    labels: label,
                    skipped: !value && !label,
                  };
                }),
              })
            }
            size="sm"
          >
            <Send className="size-3.5" />
            {batch.submit_label ?? "一次提交全部答案"}
          </Button>
        </div>
      </section>
    );
  }

  if (component.component_type === "goal_draft_editor") {
    const editor = component.props;
    const draftValue =
      goalDraftValue.title || goalDraftValue.intent || goalDraftValue.time_limit || goalDraftValue.desired_outcome
        ? goalDraftValue
        : {
            title: editor.draft.title,
            intent: editor.draft.intent ?? "",
            time_limit: editor.draft.time_limit ?? "",
            desired_outcome: editor.draft.desired_outcome ?? "",
          };
    const updateField = (field: "title" | "intent" | "time_limit" | "desired_outcome", value: string) =>
      setGoalDraftValue((current) => ({ ...current, [field]: value }));
    const canSubmit = interactive && draftValue.title.trim().length > 0;
    return (
      <section
        aria-label={editor.title}
        className="message-component goal-draft-editor"
      >
        <div className="message-component__heading">
          <Target className="size-4" />
          <div>
            <strong>{editor.title}</strong>
            {editor.description ? <span>{editor.description}</span> : null}
          </div>
          {editor.goal_status ? (
            <Badge variant="secondary">{editor.goal_status}</Badge>
          ) : null}
        </div>
        <div className="goal-draft-editor__form">
          <label>
            <span>目标名称</span>
            <Input
              aria-label="目标名称"
              disabled={!interactive}
              onChange={(event) => updateField("title", event.target.value)}
              value={draftValue.title}
            />
          </label>
          <label>
            <span>学习意图</span>
            <Input
              aria-label="学习意图"
              disabled={!interactive}
              onChange={(event) => updateField("intent", event.target.value)}
              value={draftValue.intent}
            />
          </label>
          <label>
            <span>时间约束</span>
            <Input
              aria-label="时间约束"
              disabled={!interactive}
              onChange={(event) => updateField("time_limit", event.target.value)}
              placeholder="例如：每天 2 小时，共 3 周"
              value={draftValue.time_limit}
            />
          </label>
          <label className="goal-draft-editor__wide">
            <span>期望结果</span>
            <Textarea
              aria-label="期望结果"
              disabled={!interactive}
              onChange={(event) => updateField("desired_outcome", event.target.value)}
              placeholder="希望通过什么方式验收学习成果？"
              value={draftValue.desired_outcome}
            />
          </label>
        </div>
        <div className="message-component__actions">
          <Button
            disabled={!canSubmit || !component.allowed_events.includes("submit")}
            onClick={() =>
              emit("submit", {
                draft: {
                  title: draftValue.title.trim(),
                  intent: draftValue.intent.trim(),
                  time_limit: draftValue.time_limit.trim(),
                  desired_outcome: draftValue.desired_outcome.trim(),
                },
              })
            }
            size="sm"
          >
            <Save className="size-3.5" />
            {editor.submit_label ?? "提交"}
          </Button>
        </div>
        <p className="goal-draft-editor__hint">
          修改会以结构化消息发回给智能体，由智能体确认目标草稿后再生成图谱。
        </p>
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
    interactive &&
    proposal.status === "proposed" &&
    proposal.confirmation_required;
  const canUndo =
    interactive &&
    proposal.status === "confirmed" &&
    (proposal.can_undo !== false) &&
    graphComponent.allowed_events.includes("undo");
  const statusLabel =
    proposal.status === "confirmed"
      ? "已采纳"
      : proposal.status === "rejected"
        ? "已拒绝"
        : proposal.status === "undone"
          ? "已撤销"
          : "待审核";
  const statusVariant =
    proposal.status === "confirmed"
      ? "default"
      : proposal.status === "rejected" || proposal.status === "undone"
        ? "secondary"
        : "outline";
  return (
    <section
      className={
        interactive
          ? "graph-proposal"
          : "graph-proposal graph-proposal--locked"
      }
      aria-label={`图谱变更提案：${proposal.title}`}
      aria-disabled={!interactive || undefined}
    >
      <div className="graph-proposal__heading">
        <span>
          <Network className="size-4" />
        </span>
        <div>
          <p>图谱变更提案</p>
          <strong>{proposal.title}</strong>
        </div>
        <Badge variant={statusVariant}>{statusLabel}</Badge>
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
      {!interactive && proposal.status === "proposed" ? (
        <div className="graph-proposal__resolved graph-proposal__resolved--pending">
          <GitBranch className="size-3.5" />
          回答生成中，完成后可审核此提案
        </div>
      ) : canDecide ? (
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
      ) : proposal.status === "confirmed" ? (
        <div className="graph-proposal__resolved graph-proposal__resolved--confirmed">
          <div className="graph-proposal__resolved-copy">
            <CheckCircle2 className="size-3.5" />
            <span>
              已写入正式图谱
              {proposal.confirmed_revision != null
                ? ` 修订 v${proposal.confirmed_revision}`
                : ""}
            </span>
          </div>
          {canUndo ? (
            <Button
              onClick={() => emit("undo", { proposal_id: proposal.proposal_id })}
              size="sm"
              variant="ghost"
            >
              <Undo2 className="size-3.5" />撤销
            </Button>
          ) : null}
        </div>
      ) : (
        <div className="graph-proposal__resolved">
          <GitBranch className="size-3.5" />
          {proposal.status === "rejected"
            ? proposal.rejection_reason
              ? `已拒绝：${proposal.rejection_reason}`
              : "该提案已拒绝，正式图谱未被修改"
            : proposal.status === "undone"
              ? "该提案写入已撤销，图谱已恢复"
              : "该提案已结束"}
        </div>
      )}
    </section>
  );
}

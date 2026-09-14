import { CheckCircle2, CircleHelp, ListChecks, ToggleLeft } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { StatePill, Surface } from "@/components/shared/page-elements";
import type { AnswerResult, Exercise } from "@/types/learning";
import { cn } from "@/lib/utils";
import { questionTypeLabel } from "./exercise-labels";

export function QuestionTypeBadge({ type }: { type: string }) {
  const label = questionTypeLabel(type);
  const Icon =
    type === "true_false"
      ? ToggleLeft
      : type === "multiple_choice" || type === "single_choice"
        ? ListChecks
        : type === "short_answer" || type === "fill_blank"
          ? CircleHelp
          : CircleHelp;
  return (
    <Badge className="gap-1" variant="secondary">
      <Icon className="size-3.5" />
      {label}
    </Badge>
  );
}

export type AnswerValue = string | string[];

/**
 * 所有题型共用的作答控件（单选 / 多选 / 判断 / 填空 / 简答）。
 * 题库卡片与练习 Session 运行器都渲染这一个实现，避免为不同页面重做一套题型 UI。
 */
export function ExerciseResponseInput({
  questionType,
  options,
  answer,
  onAnswerChange,
  disabled,
  idPrefix,
  className,
}: {
  questionType: string;
  options: string[];
  answer: AnswerValue;
  onAnswerChange: (value: AnswerValue) => void;
  disabled?: boolean;
  idPrefix: string;
  className?: string;
}) {
  const isMultiple = questionType === "multiple_choice";
  const isChoice =
    questionType === "single_choice" ||
    questionType === "true_false" ||
    (Boolean(options?.length) && !isMultiple && questionType !== "short_answer");
  const isShort = questionType === "short_answer";
  const isFill = questionType === "fill_blank" || (!isChoice && !isMultiple && !isShort);
  const choiceOptions = options?.length
    ? options
    : questionType === "true_false"
      ? ["正确", "错误"]
      : [];

  if (isMultiple) {
    const selected = Array.isArray(answer) ? answer : [];
    return (
      <div className={cn("space-y-2", className)}>
        {options.map((option, index) => {
          const checked = selected.includes(option);
          return (
            <Label
              className="flex items-center gap-3 rounded-xl border p-3 text-sm"
              htmlFor={`${idPrefix}-mc-${index}`}
              key={option}
            >
              <Checkbox
                checked={checked}
                disabled={disabled}
                id={`${idPrefix}-mc-${index}`}
                onCheckedChange={(next) =>
                  onAnswerChange(
                    next
                      ? [...selected, option]
                      : selected.filter((item) => item !== option),
                  )
                }
              />
              <span className="flex-1 font-normal">
                {String.fromCharCode(65 + index)}. {option}
              </span>
            </Label>
          );
        })}
      </div>
    );
  }

  if (isChoice) {
    return (
      <RadioGroup
        className={cn("space-y-2", className)}
        disabled={disabled}
        onValueChange={onAnswerChange}
        value={typeof answer === "string" ? answer : ""}
      >
        {choiceOptions.map((option, index) => (
          <div
            className="flex items-center gap-3 rounded-xl border p-3 text-sm focus-within:border-primary"
            key={option}
          >
            <RadioGroupItem id={`${idPrefix}-sc-${index}`} value={option} />
            <Label
              className="flex-1 cursor-pointer font-normal"
              htmlFor={`${idPrefix}-sc-${index}`}
            >
              {questionType === "true_false"
                ? option
                : `${String.fromCharCode(65 + index)}. ${option}`}
            </Label>
          </div>
        ))}
        {!choiceOptions.length ? (
          <p className="text-xs text-muted-foreground">该题缺少选项，无法作答。</p>
        ) : null}
      </RadioGroup>
    );
  }

  if (isShort) {
    return (
      <Textarea
        className={cn("min-h-28", className)}
        disabled={disabled}
        onChange={(event) => onAnswerChange(event.currentTarget.value)}
        placeholder="用自己的话组织答案，覆盖关键要点"
        value={typeof answer === "string" ? answer : ""}
      />
    );
  }

  if (isFill) {
    return (
      <Input
        className={className}
        disabled={disabled}
        onChange={(event) => onAnswerChange(event.currentTarget.value)}
        placeholder="填写答案"
        value={typeof answer === "string" ? answer : ""}
      />
    );
  }

  return (
    <Textarea
      className={cn("min-h-24", className)}
      disabled={disabled}
      onChange={(event) => onAnswerChange(event.currentTarget.value)}
      placeholder="输入你的回答"
      value={typeof answer === "string" ? answer : ""}
    />
  );
}

export function ExerciseAnswerCard({
  exercise,
  answer,
  onAnswerChange,
  result,
  disabled,
  className,
}: {
  exercise: Exercise;
  answer: AnswerValue;
  onAnswerChange: (value: AnswerValue) => void;
  result?: AnswerResult | null;
  disabled?: boolean;
  className?: string;
}) {
  const qtype = exercise.question_type;
  const isMultiple = qtype === "multiple_choice";
  const isChoice =
    qtype === "single_choice" ||
    qtype === "true_false" ||
    (Boolean(exercise.options?.length) && !isMultiple && qtype !== "short_answer");

  return (
    <Surface className={cn("p-5", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <QuestionTypeBadge type={qtype} />
            {exercise.difficulty ? (
              <Badge variant="outline">{exercise.difficulty}</Badge>
            ) : null}
            {typeof exercise.attempt_count === "number" && exercise.attempt_count > 0 ? (
              <Badge variant="outline">
                作答 {exercise.correct_count ?? 0}/{exercise.attempt_count}
              </Badge>
            ) : null}
          </div>
          <p className="text-sm font-semibold leading-6">{exercise.prompt}</p>
        </div>
        {result ? (
          <StatePill
            label={result.is_correct ? "回答正确" : "需要复习"}
            status={result.is_correct ? "approved" : "conflicted"}
          />
        ) : null}
      </div>

      <div className="mt-4">
        <ExerciseResponseInput
          answer={answer}
          disabled={disabled}
          idPrefix={exercise.id}
          onAnswerChange={onAnswerChange}
          options={exercise.options ?? []}
          questionType={qtype}
        />
        {isChoice && result?.is_correct && typeof answer === "string" ? (
          <p className="mt-2 flex items-center gap-1 text-xs text-primary">
            <CheckCircle2 className="size-3.5" />
            已选择：{answer}
          </p>
        ) : null}
      </div>

      {result ? (
        <div className="mt-4 rounded-xl border bg-muted/25 p-4">
          <p className="text-sm font-semibold">批改结果</p>
          <p className="mt-2 text-sm leading-6">{result.feedback}</p>
          {result.covered_points?.length ? (
            <p className="mt-2 text-xs leading-5 text-primary">
              已覆盖：{result.covered_points.join("、")}
            </p>
          ) : null}
          {result.missing_points?.length ? (
            <p className="mt-1 text-xs leading-5 text-amber-600 dark:text-amber-400">
              尚未覆盖：{result.missing_points.join("、")}
            </p>
          ) : null}
          {typeof result.score_ratio === "number" ? (
            <p className="mt-1 text-xs text-muted-foreground">
              得分比例 {Math.round(result.score_ratio * 100)}%
            </p>
          ) : null}
          {exercise.explanation ? (
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              {exercise.explanation}
            </p>
          ) : null}
          <p className="mt-2 text-xs text-muted-foreground">
            Evidence · {result.evidence_signal_id}
            {result.mastery_star_awarded ? " · 成长星 +1" : ""}
            {result.schedule_reason ? ` · ${result.schedule_reason}` : ""}
          </p>
        </div>
      ) : null}

      {exercise.source_refs?.length ? (
        <p className="mt-3 text-xs text-muted-foreground">
          来源片段 {exercise.source_refs.length} 条
          {exercise.source_refs[0]?.filename
            ? ` · 如 ${exercise.source_refs[0].filename}`
            : ""}
        </p>
      ) : null}
    </Surface>
  );
}

export function ExerciseBankCard({
  exercise,
  href,
  nodeLabel,
}: {
  exercise: Exercise;
  href: string;
  nodeLabel?: string;
}) {
  return (
    <a
      className="block rounded-xl border p-4 transition-colors hover:border-primary hover:bg-primary/[.025]"
      href={href}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 space-y-2">
          <QuestionTypeBadge type={exercise.question_type} />
          <p className="text-sm font-semibold leading-6">{exercise.prompt}</p>
          <p className="text-xs text-muted-foreground">
            {nodeLabel ?? exercise.node_id}
            {typeof exercise.attempt_count === "number" && exercise.attempt_count > 0
              ? ` · 正确 ${exercise.correct_count ?? 0}/${exercise.attempt_count}`
              : " · 尚未作答"}
          </p>
        </div>
      </div>
    </a>
  );
}

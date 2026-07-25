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

type AnswerValue = string | string[];

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
  const isShort = qtype === "short_answer";
  const isFill = qtype === "fill_blank" || (!isChoice && !isMultiple && !isShort);

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
        {isMultiple ? (
          <div className="space-y-2">
            {exercise.options.map((option, index) => {
              const selected = Array.isArray(answer) ? answer : [];
              const checked = selected.includes(option);
              return (
                <Label
                  className="flex items-center gap-3 rounded-xl border p-3 text-sm"
                  htmlFor={`${exercise.id}-mc-${index}`}
                  key={option}
                >
                  <Checkbox
                    checked={checked}
                    disabled={disabled}
                    id={`${exercise.id}-mc-${index}`}
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
        ) : isChoice ? (
          <RadioGroup
            className="space-y-2"
            disabled={disabled}
            onValueChange={onAnswerChange}
            value={typeof answer === "string" ? answer : ""}
          >
            {(exercise.options?.length
              ? exercise.options
              : qtype === "true_false"
                ? ["正确", "错误"]
                : []
            ).map((option, index) => {
              const selected =
                typeof answer === "string" && answer === option && result?.is_correct;
              return (
                <div
                  className={
                    selected
                      ? "flex items-center gap-3 rounded-xl border border-primary bg-primary/5 p-3 text-sm"
                      : "flex items-center gap-3 rounded-xl border p-3 text-sm"
                  }
                  key={option}
                >
                  <RadioGroupItem
                    id={`${exercise.id}-sc-${index}`}
                    value={option}
                  />
                  <Label
                    className="flex-1 cursor-pointer font-normal"
                    htmlFor={`${exercise.id}-sc-${index}`}
                  >
                    {qtype === "true_false"
                      ? option
                      : `${String.fromCharCode(65 + index)}. ${option}`}
                  </Label>
                  {selected ? (
                    <CheckCircle2 className="size-4 text-primary" />
                  ) : null}
                </div>
              );
            })}
          </RadioGroup>
        ) : isShort ? (
          <Textarea
            className="min-h-28"
            disabled={disabled}
            onChange={(event) => onAnswerChange(event.currentTarget.value)}
            placeholder="用自己的话组织答案，覆盖关键要点"
            value={typeof answer === "string" ? answer : ""}
          />
        ) : isFill ? (
          <Input
            disabled={disabled}
            onChange={(event) => onAnswerChange(event.currentTarget.value)}
            placeholder="填写答案"
            value={typeof answer === "string" ? answer : ""}
          />
        ) : (
          <Textarea
            className="min-h-24"
            disabled={disabled}
            onChange={(event) => onAnswerChange(event.currentTarget.value)}
            placeholder="输入你的回答"
            value={typeof answer === "string" ? answer : ""}
          />
        )}
      </div>

      {result ? (
        <div className="mt-4 rounded-xl border bg-muted/25 p-4">
          <p className="text-sm font-semibold">批改结果</p>
          <p className="mt-2 text-sm leading-6">{result.feedback}</p>
          {exercise.explanation ? (
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              {exercise.explanation}
            </p>
          ) : null}
          <p className="mt-2 text-xs text-muted-foreground">
            Evidence · {result.evidence_signal_id}
            {result.mastery_star_awarded ? " · 成长星 +1" : ""}
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

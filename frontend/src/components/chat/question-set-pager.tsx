import { useEffect, useMemo, useState } from "react";
import {
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  XCircle,
} from "lucide-react";

import {
  OptionGroup,
  type OptionGroupChoice,
  type OptionGroupSubmission,
} from "@/components/chat/option-group";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { MessagePart } from "@/types/sessions";

export type QuestionAnswerKey = {
  correctOptionIds: string[];
  correctAnswers: string[];
  explanation?: string;
  feedbackCorrect?: string;
  feedbackIncorrect?: string;
};

export type QuestionItem = {
  componentId: string;
  componentType: string;
  title: string;
  description?: string;
  mode: "single" | "multiple" | "text";
  options: OptionGroupChoice[];
  allowCustom: boolean;
  allowSkip: boolean;
  submitLabel?: string;
  placeholder?: string;
  multiline: boolean;
  answerKey: QuestionAnswerKey;
};

export type QuestionResult = {
  componentId: string;
  componentType: string;
  title: string;
  values: string[];
  labels: string[];
  skipped: boolean;
  isCorrect: boolean | null;
  feedback: string;
  explanation?: string;
};

export type QuestionSetSubmission = {
  results: QuestionResult[];
  summaryText: string;
  gradedCount: number;
  correctCount: number;
};

const QUESTION_COMPONENT_TYPES = new Set([
  "option_group",
  "single_choice",
  "multiple_choice",
  "fill_blank",
  "short_answer_table",
  "quiz",
]);

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeText(value: string) {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}

export function isQuestionComponentPart(part: MessagePart): boolean {
  if (part.type === "quiz") return true;
  if (part.type !== "component") return false;
  const componentType =
    typeof part.data?.component_type === "string"
      ? part.data.component_type
      : "";
  return QUESTION_COMPONENT_TYPES.has(componentType);
}

function readProps(data: Record<string, unknown> | undefined) {
  if (!data) return {} as Record<string, unknown>;
  const props = data.props;
  if (props && typeof props === "object" && !Array.isArray(props)) {
    return props as Record<string, unknown>;
  }
  return data;
}

export function questionItemFromPart(part: MessagePart): QuestionItem | null {
  if (part.type === "quiz") {
    const data = part.data ?? {};
    const prompt =
      typeof data.prompt === "string" && data.prompt.trim()
        ? data.prompt.trim()
        : "即时验收题";
    const rawOptions = Array.isArray(data.options) ? data.options : [];
    const options = rawOptions.flatMap((item, index) => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      const id =
        typeof record.id === "string" && record.id.trim()
          ? record.id.trim()
          : `opt_${index + 1}`;
      const label =
        typeof record.label === "string" && record.label.trim()
          ? record.label.trim()
          : typeof record.text === "string" && record.text.trim()
            ? record.text.trim()
            : "";
      if (!label) return [];
      return [
        {
          id,
          label,
          description:
            typeof record.description === "string"
              ? record.description
              : undefined,
          isCorrect: record.is_correct === true,
        },
      ];
    });
    if (!options.length) return null;
    return {
      componentId: part.id,
      componentType: "quiz",
      title: prompt,
      description: undefined,
      mode: "single",
      options,
      allowCustom: false,
      allowSkip: false,
      submitLabel: "提交答案",
      multiline: false,
      answerKey: extractAnswerKey(data, options),
    };
  }

  if (part.type !== "component") return null;
  const data = part.data ?? {};
  const componentType =
    typeof data.component_type === "string" ? data.component_type : "";
  if (!QUESTION_COMPONENT_TYPES.has(componentType)) return null;

  const props = readProps(data);
  const componentId =
    (typeof data.component_id === "string" && data.component_id) || part.id;
  const title =
    (typeof props.title === "string" && props.title.trim()) ||
    (typeof props.prompt === "string" && props.prompt.trim()) ||
    "请作答";
  const description =
    typeof props.description === "string" && props.description.trim()
      ? props.description.trim()
      : undefined;

  if (
    componentType === "fill_blank" ||
    componentType === "short_answer_table"
  ) {
    return {
      componentId,
      componentType,
      title,
      description,
      mode: "text",
      options: [],
      allowCustom: false,
      allowSkip: false,
      submitLabel:
        typeof props.submit_label === "string"
          ? props.submit_label
          : undefined,
      placeholder:
        typeof props.placeholder === "string"
          ? props.placeholder
          : undefined,
      multiline:
        componentType === "short_answer_table" || props.multiline === true,
      answerKey: extractAnswerKey(props, []),
    };
  }

  const rawOptions = Array.isArray(props.options) ? props.options : [];
  const options = rawOptions.flatMap((item, index) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    const id =
      typeof record.id === "string" && record.id.trim()
        ? record.id.trim()
        : `opt_${index + 1}`;
    const label =
      typeof record.label === "string" && record.label.trim()
        ? record.label.trim()
        : "";
    if (!label) return [];
    return [
      {
        id,
        label,
        description:
          typeof record.description === "string"
            ? record.description
            : undefined,
        isCorrect: record.is_correct === true,
      },
    ];
  });

  return {
    componentId,
    componentType,
    title,
    description,
    mode: componentType === "multiple_choice" ? "multiple" : "single",
    options,
    allowCustom: props.allow_custom !== false,
    allowSkip: props.allow_skip !== false,
    submitLabel:
      typeof props.submit_label === "string" ? props.submit_label : undefined,
    multiline: false,
    answerKey: extractAnswerKey(props, options),
  };
}

function extractAnswerKey(
  props: Record<string, unknown>,
  options: Array<OptionGroupChoice & { isCorrect?: boolean }>,
): QuestionAnswerKey {
  const fromProps = asStringArray(props.correct_option_ids);
  const fromOptions = options
    .filter((option) => option.isCorrect)
    .map((option) => option.id);
  const correctAnswers = asStringArray(props.correct_answers);
  if (
    !correctAnswers.length &&
    typeof props.correct_answer === "string" &&
    props.correct_answer.trim()
  ) {
    correctAnswers.push(props.correct_answer.trim());
  }
  return {
    correctOptionIds: fromProps.length ? fromProps : fromOptions,
    correctAnswers,
    explanation:
      typeof props.explanation === "string" && props.explanation.trim()
        ? props.explanation.trim()
        : undefined,
    feedbackCorrect:
      typeof props.feedback_correct === "string" &&
      props.feedback_correct.trim()
        ? props.feedback_correct.trim()
        : undefined,
    feedbackIncorrect:
      typeof props.feedback_incorrect === "string" &&
      props.feedback_incorrect.trim()
        ? props.feedback_incorrect.trim()
        : undefined,
  };
}

function gradeQuestion(
  question: QuestionItem,
  values: string[],
  labels: string[],
  skipped: boolean,
): QuestionResult {
  if (skipped) {
    return {
      componentId: question.componentId,
      componentType: question.componentType,
      title: question.title,
      values: [],
      labels: [],
      skipped: true,
      isCorrect: null,
      feedback: "已跳过",
      explanation: question.answerKey.explanation,
    };
  }

  const key = question.answerKey;
  let isCorrect: boolean | null = null;
  let feedback = "已提交";

  if (question.mode === "text") {
    if (key.correctAnswers.length) {
      const answer = normalizeText(labels[0] ?? values[0] ?? "");
      isCorrect = key.correctAnswers.some(
        (candidate) => normalizeText(candidate) === answer,
      );
    }
  } else if (key.correctOptionIds.length) {
    const selected = [...values].sort();
    const expected = [...key.correctOptionIds].sort();
    isCorrect =
      selected.length === expected.length &&
      selected.every((item, index) => item === expected[index]);
  }

  if (isCorrect === true) {
    feedback = key.feedbackCorrect ?? "回答正确";
  } else if (isCorrect === false) {
    feedback = key.feedbackIncorrect ?? "需要复习";
  } else {
    feedback = "已提交，等待模型批改";
  }

  return {
    componentId: question.componentId,
    componentType: question.componentType,
    title: question.title,
    values,
    labels,
    skipped: false,
    isCorrect,
    feedback,
    explanation: key.explanation,
  };
}

function formatSubmissionText(results: QuestionResult[]): string {
  if (results.length === 1) {
    const only = results[0];
    if (only.skipped) return "跳过该问题";
    if (!only.labels.length) return "跳过该问题";
    return `我的回答：${only.labels.join("、")}`;
  }
  const lines = results.map((result, index) => {
    const answer = result.skipped
      ? "（跳过）"
      : result.labels.join("、") || "（未作答）";
    const grade =
      result.isCorrect === true
        ? "正确"
        : result.isCorrect === false
          ? "错误"
          : result.skipped
            ? "跳过"
            : "待批改";
    return `${index + 1}. ${result.title}\n回答：${answer}\n判定：${grade}`;
  });
  const graded = results.filter((item) => item.isCorrect !== null);
  const correct = graded.filter((item) => item.isCorrect).length;
  const header =
    graded.length > 0
      ? `练习作答（${correct}/${graded.length} 正确）：`
      : `练习作答（共 ${results.length} 题）：`;
  return `${header}\n${lines.join("\n")}`;
}

type DraftAnswer = {
  values: string[];
  labels: string[];
  customValue: string;
  textValue: string;
  skipped: boolean;
  answered: boolean;
};

function emptyDraft(): DraftAnswer {
  return {
    values: [],
    labels: [],
    customValue: "",
    textValue: "",
    skipped: false,
    answered: false,
  };
}

export function QuestionSetPager({
  questions,
  onSubmit,
}: {
  questions: QuestionItem[];
  onSubmit?: (submission: QuestionSetSubmission) => void | Promise<void>;
}) {
  const [page, setPage] = useState(0);
  const [drafts, setDrafts] = useState<DraftAnswer[]>(() =>
    questions.map(() => emptyDraft()),
  );
  const [results, setResults] = useState<QuestionResult[] | null>(null);
  const [resultPage, setResultPage] = useState(0);

  // Grow draft slots if the model streams more questions into the same set.
  useEffect(() => {
    setDrafts((prev) => {
      if (prev.length === questions.length) return prev;
      if (prev.length > questions.length) return prev.slice(0, questions.length);
      return [
        ...prev,
        ...Array.from({ length: questions.length - prev.length }, () =>
          emptyDraft(),
        ),
      ];
    });
    setPage((value) => Math.min(value, Math.max(0, questions.length - 1)));
  }, [questions.length]);

  const total = questions.length;
  const current = questions[Math.min(page, total - 1)];
  const draft = drafts[Math.min(page, total - 1)] ?? emptyDraft();
  const answeredCount = drafts.filter((item) => item.answered || item.skipped)
    .length;

  function currentPageReady(source: DraftAnswer = draft) {
    if (source.answered || source.skipped) return true;
    if (current?.mode === "text") return Boolean(source.textValue.trim());
    return source.labels.length > 0 || source.values.length > 0;
  }

  const canPrimary = currentPageReady();
  // Last page: confirm only when every question is answered/skipped (current counts live).
  const primaryDisabled =
    page < total - 1
      ? !canPrimary
      : !drafts.every((item, index) =>
          index === page
            ? canPrimary
            : item.answered || item.skipped,
        );

  function primaryLabel() {
    if (page < total - 1) return "下一题";
    return "确认并查看批改";
  }

  const resultSummary = useMemo(() => {
    if (!results) return null;
    const graded = results.filter((item) => item.isCorrect !== null);
    const correct = graded.filter((item) => item.isCorrect).length;
    return { graded: graded.length, correct, total: results.length };
  }, [results]);

  function updateDraft(index: number, patch: Partial<DraftAnswer>) {
    setDrafts((prev) =>
      prev.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );
  }

  function applyDraft(
    index: number,
    next: DraftAnswer,
    options?: { advance?: boolean },
  ) {
    setDrafts((prev) =>
      prev.map((item, itemIndex) =>
        itemIndex === index ? next : item,
      ),
    );
    if (options?.advance && index < total - 1) {
      setPage(index + 1);
    }
  }

  function confirmWithDrafts(source: DraftAnswer[]) {
    const graded = questions.map((question, index) => {
      const item = source[index] ?? emptyDraft();
      if (item.skipped || !item.answered) {
        return gradeQuestion(question, [], [], true);
      }
      return gradeQuestion(question, item.values, item.labels, false);
    });
    setResults(graded);
    setResultPage(0);
    const summaryText = formatSubmissionText(graded);
    const gradedCount = graded.filter((item) => item.isCorrect !== null).length;
    const correctCount = graded.filter((item) => item.isCorrect === true)
      .length;
    void onSubmit?.({
      results: graded,
      summaryText,
      gradedCount,
      correctCount,
    });
  }

  function handleOptionChange(submission: OptionGroupSubmission) {
    const labels = submission.labels.length
      ? submission.labels
      : submission.values;
    const hasAnswer = labels.length > 0;
    updateDraft(page, {
      values: submission.values,
      labels,
      customValue: draft.customValue,
      textValue: draft.textValue,
      skipped: false,
      answered: hasAnswer,
    });
  }

  function handleSkip() {
    const next: DraftAnswer = {
      values: [],
      labels: [],
      customValue: "",
      textValue: "",
      skipped: true,
      answered: true,
    };
    // Skip advances when more questions remain; last skip only marks answered.
    applyDraft(page, next, { advance: page < total - 1 });
  }

  function markCurrentAnswered(source: DraftAnswer[]): DraftAnswer[] {
    return source.map((item, index) => {
      if (index !== page) return item;
      if (item.answered || item.skipped) return item;
      if (item.labels.length || item.values.length || item.textValue.trim()) {
        const labels =
          item.labels.length > 0
            ? item.labels
            : item.textValue.trim()
              ? [item.textValue.trim()]
              : item.values;
        return {
          ...item,
          labels,
          values: item.values.length ? item.values : labels,
          answered: true,
          skipped: false,
        };
      }
      return item;
    });
  }

  function handlePrimaryAction() {
    if (!canPrimary) return;

    if (page < total - 1) {
      setDrafts((prev) => markCurrentAnswered(prev));
      setPage(page + 1);
      return;
    }

    setDrafts((prev) => {
      const updated = markCurrentAnswered(prev);
      const complete = updated.every((item) => item.answered || item.skipped);
      if (complete) {
        queueMicrotask(() => confirmWithDrafts(updated));
      }
      return updated;
    });
  }

  if (!current) return null;

  if (results) {
    const currentResult = results[Math.min(resultPage, results.length - 1)];
    return (
      <section
        aria-label="练习批改结果"
        className="question-set-pager question-set-pager--result"
      >
        <div className="question-set-pager__chrome">
          <div className="question-set-pager__meta">
            <strong>批改结果</strong>
            <span>
              {resultSummary
                ? resultSummary.graded > 0
                  ? `${resultSummary.correct}/${resultSummary.graded} 正确 · 共 ${resultSummary.total} 题`
                  : `共 ${resultSummary.total} 题已提交`
                : null}
            </span>
          </div>
          {total > 1 ? (
            <div className="question-set-pager__dots" aria-hidden="true">
              {results.map((item, index) => (
                <button
                  className={cn(
                    "question-set-pager__dot",
                    index === resultPage && "is-active",
                    item.isCorrect === true && "is-correct",
                    item.isCorrect === false && "is-wrong",
                  )}
                  key={item.componentId}
                  onClick={() => setResultPage(index)}
                  type="button"
                />
              ))}
            </div>
          ) : null}
        </div>

        {currentResult ? (
          <article className="question-set-pager__result-card">
            <div className="question-set-pager__result-heading">
              <div>
                <p>
                  {total > 1 ? `第 ${resultPage + 1} 题 · ` : null}
                  {currentResult.title}
                </p>
                <span>
                  {currentResult.skipped
                    ? "已跳过"
                    : currentResult.labels.join("、") || "未作答"}
                </span>
              </div>
              <span
                className={cn(
                  "question-set-pager__verdict",
                  currentResult.isCorrect === true && "is-correct",
                  currentResult.isCorrect === false && "is-wrong",
                  currentResult.isCorrect == null && "is-pending",
                )}
              >
                {currentResult.isCorrect === true ? (
                  <>
                    <CheckCircle2 className="size-3.5" />
                    正确
                  </>
                ) : currentResult.isCorrect === false ? (
                  <>
                    <XCircle className="size-3.5" />
                    需复习
                  </>
                ) : (
                  <>
                    <CircleHelp className="size-3.5" />
                    {currentResult.skipped ? "已跳过" : "待批改"}
                  </>
                )}
              </span>
            </div>
            <p className="question-set-pager__feedback">
              {currentResult.feedback}
            </p>
            {currentResult.explanation ? (
              <p className="question-set-pager__explanation">
                {currentResult.explanation}
              </p>
            ) : null}
          </article>
        ) : null}

        {total > 1 ? (
          <div className="question-set-pager__nav">
            <Button
              disabled={resultPage <= 0}
              onClick={() => setResultPage((value) => Math.max(0, value - 1))}
              size="sm"
              type="button"
              variant="ghost"
            >
              <ChevronLeft className="size-3.5" />
              上一题
            </Button>
            <span>
              {resultPage + 1} / {total}
            </span>
            <Button
              disabled={resultPage >= total - 1}
              onClick={() =>
                setResultPage((value) => Math.min(total - 1, value + 1))
              }
              size="sm"
              type="button"
              variant="ghost"
            >
              下一题
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <section aria-label="交互练习" className="question-set-pager">
      <div className="question-set-pager__chrome">
        <div className="question-set-pager__meta">
          <strong>交互练习</strong>
          <span>
            {total > 1
              ? `第 ${page + 1} / ${total} 题 · 已答 ${answeredCount}`
              : "作答后确认提交"}
          </span>
        </div>
        {total > 1 ? (
          <div className="question-set-pager__dots" aria-hidden="true">
            {questions.map((question, index) => {
              const item = drafts[index];
              return (
                <button
                  className={cn(
                    "question-set-pager__dot",
                    index === page && "is-active",
                    (item?.answered || item?.skipped) && "is-done",
                  )}
                  key={question.componentId}
                  onClick={() => setPage(index)}
                  type="button"
                />
              );
            })}
          </div>
        ) : null}
      </div>

      <div className="question-set-pager__stage">
        {current.mode === "text" ? (
          <section className="option-group" aria-label={current.title}>
            <div className="option-group__heading">
              <div>
                <p>{current.title}</p>
                {current.description ? <span>{current.description}</span> : null}
              </div>
              <span>简答</span>
            </div>
            {current.multiline ? (
              <Textarea
                aria-label={current.title}
                className="min-h-24"
                onChange={(event) =>
                  updateDraft(page, {
                    textValue: event.currentTarget.value,
                    values: event.currentTarget.value.trim()
                      ? [event.currentTarget.value.trim()]
                      : [],
                    labels: event.currentTarget.value.trim()
                      ? [event.currentTarget.value.trim()]
                      : [],
                    answered: Boolean(event.currentTarget.value.trim()),
                    skipped: false,
                  })
                }
                placeholder={current.placeholder ?? "输入你的回答"}
                value={draft.textValue}
              />
            ) : (
              <Input
                aria-label={current.title}
                onChange={(event) =>
                  updateDraft(page, {
                    textValue: event.currentTarget.value,
                    values: event.currentTarget.value.trim()
                      ? [event.currentTarget.value.trim()]
                      : [],
                    labels: event.currentTarget.value.trim()
                      ? [event.currentTarget.value.trim()]
                      : [],
                    answered: Boolean(event.currentTarget.value.trim()),
                    skipped: false,
                  })
                }
                placeholder={current.placeholder ?? "填写答案"}
                value={draft.textValue}
              />
            )}
          </section>
        ) : (
          <OptionGroup
            key={current.componentId}
            allowCustom={current.allowCustom}
            allowSkip={false}
            description={current.description}
            hideActions
            mode={current.mode}
            onChange={handleOptionChange}
            options={current.options}
            title={current.title}
            value={draft.values}
          />
        )}
      </div>

      <div className="question-set-pager__footer">
        <div className="question-set-pager__nav">
          {total > 1 ? (
            <>
              <Button
                disabled={page <= 0}
                onClick={() => setPage((value) => Math.max(0, value - 1))}
                size="sm"
                type="button"
                variant="ghost"
              >
                <ChevronLeft className="size-3.5" />
                上一题
              </Button>
              <span>
                {page + 1} / {total}
              </span>
            </>
          ) : (
            <span />
          )}
        </div>
        <div className="question-set-pager__actions">
          {current.allowSkip ? (
            <Button onClick={handleSkip} size="sm" type="button" variant="ghost">
              跳过
            </Button>
          ) : null}
          <Button
            disabled={primaryDisabled}
            onClick={handlePrimaryAction}
            size="sm"
            type="button"
          >
            {page >= total - 1 ? <Check className="size-3.5" /> : null}
            {primaryLabel()}
            {page < total - 1 ? <ChevronRight className="size-3.5" /> : null}
          </Button>
        </div>
      </div>
    </section>
  );
}

/**
 * Collapse interactive question parts in one message into a single pager.
 * Intervening text/cards stay in stream order; all questions are pulled into
 * one set at the first question's position so multi-item exercises share one
 * footer (上一题 / 下一题 / 确认并查看批改).
 */
export function groupQuestionParts(parts: MessagePart[]): Array<
  | { kind: "part"; part: MessagePart }
  | { kind: "question_set"; parts: MessagePart[]; questions: QuestionItem[] }
> {
  const questionEntries: Array<{ part: MessagePart; question: QuestionItem }> =
    [];
  for (const part of parts) {
    if (!isQuestionComponentPart(part)) continue;
    const question = questionItemFromPart(part);
    if (question) questionEntries.push({ part, question });
  }

  if (!questionEntries.length) {
    return parts.map((part) => ({ kind: "part" as const, part }));
  }

  const questionIds = new Set(questionEntries.map((item) => item.part.id));
  const firstQuestionId = questionEntries[0].part.id;
  const groups: Array<
    | { kind: "part"; part: MessagePart }
    | { kind: "question_set"; parts: MessagePart[]; questions: QuestionItem[] }
  > = [];

  for (const part of parts) {
    if (questionIds.has(part.id)) {
      if (part.id === firstQuestionId) {
        groups.push({
          kind: "question_set",
          parts: questionEntries.map((item) => item.part),
          questions: questionEntries.map((item) => item.question),
        });
      }
      continue;
    }
    groups.push({ kind: "part", part });
  }
  return groups;
}

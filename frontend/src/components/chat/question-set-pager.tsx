import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  RotateCcw,
  XCircle,
} from "lucide-react";

import { isSandboxImageArtifactPart } from "@/components/chat/sandbox-image-artifact";
import { isSideLayoutArtifactPart } from "@/features/chat/chat-message-parts";
import { reconcileArtifacts } from "@/features/chat/response-composer";
import {
  OptionGroup,
  type OptionChoiceVerdict,
  type OptionGroupChoice,
  type OptionGroupSubmission,
} from "@/components/chat/option-group";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  readQuestionChoiceState,
  writeQuestionChoiceState,
} from "@/lib/question-choice-state";
import { usePartUIStatePersistence } from "@/lib/part-ui-state";
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
  partId?: string;
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
  uiState?: Record<string, unknown>;
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

function readUiStateObject(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
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
      partId: part.id,
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
      uiState: readUiStateObject(data.ui_state),
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
      partId: part.id,
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
      uiState: readUiStateObject(data.ui_state),
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
    partId: part.id,
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
    uiState: readUiStateObject(data.ui_state),
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
    feedback = key.feedbackIncorrect ?? "回答错误";
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

function correctAnswerDisplay(question: QuestionItem): string {
  if (question.mode === "text") {
    return question.answerKey.correctAnswers.join("、");
  }
  const optionMap = new Map(
    question.options.map((option) => [option.id, option.label]),
  );
  return question.answerKey.correctOptionIds
    .map((id) => optionMap.get(id) ?? id)
    .filter(Boolean)
    .join("、");
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

type PersistedQuestionSetState = {
  storageKey?: string;
  page: number;
  drafts: DraftAnswer[];
  results: QuestionResult[] | null;
  resultPage: number;
};

function parsePersistedQuestionSetState(
  raw: unknown,
  expectedStorageKey: string,
  questionCount: number,
): {
  hasSaved: boolean;
  page: number;
  drafts: DraftAnswer[];
  results: QuestionResult[] | null;
  resultPage: number;
} {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {
      hasSaved: false,
      page: 0,
      drafts: normalizeDrafts(undefined, questionCount),
      results: null,
      resultPage: 0,
    };
  }
  const saved = raw as Partial<PersistedQuestionSetState>;
  if (
    typeof saved.storageKey === "string" &&
    expectedStorageKey &&
    saved.storageKey !== expectedStorageKey
  ) {
    return {
      hasSaved: false,
      page: 0,
      drafts: normalizeDrafts(undefined, questionCount),
      results: null,
      resultPage: 0,
    };
  }
  const clampedPage = Math.max(
    0,
    Math.min(
      typeof saved.page === "number" ? saved.page : 0,
      Math.max(0, questionCount - 1),
    ),
  );
  const validResults =
    Array.isArray(saved.results) && saved.results.length === questionCount
      ? saved.results
      : null;
  const clampedResultPage = Math.max(
    0,
    Math.min(
      typeof saved.resultPage === "number" ? saved.resultPage : 0,
      Math.max(0, questionCount - 1),
    ),
  );
  return {
    hasSaved: true,
    page: clampedPage,
    drafts: normalizeDrafts(saved.drafts, questionCount),
    results: validResults,
    resultPage: clampedResultPage,
  };
}

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

function normalizeDrafts(
  saved: DraftAnswer[] | undefined,
  count: number,
): DraftAnswer[] {
  return Array.from({ length: count }, (_, index) => {
    const item = saved?.[index];
    if (!item || typeof item !== "object") return emptyDraft();
    return {
      values: Array.isArray(item.values) ? item.values : [],
      labels: Array.isArray(item.labels) ? item.labels : [],
      customValue: typeof item.customValue === "string" ? item.customValue : "",
      textValue: typeof item.textValue === "string" ? item.textValue : "",
      skipped: Boolean(item.skipped),
      answered: Boolean(item.answered),
    };
  });
}

export function QuestionSetPager({
  interactive = true,
  onSubmit,
  questions,
  storageKey,
}: {
  interactive?: boolean;
  onSubmit?: (submission: QuestionSetSubmission) => void | Promise<void>;
  questions: QuestionItem[];
  storageKey?: string;
}) {
  const persistence = usePartUIStatePersistence();
  const anchorPartId = questions[0]?.partId;
  const effectiveStorageKey = useMemo(() => {
    const raw =
      storageKey?.trim() ||
      questions.map((item) => item.partId ?? item.componentId).join(":");
    return raw ? `pager:${raw}` : "";
  }, [questions, storageKey]);

  const backendUiState = questions[0]?.uiState;
  const backendStateKey = useMemo(
    () => (backendUiState ? JSON.stringify(backendUiState) : ""),
    [backendUiState],
  );

  const initialPersisted = useMemo(() => {
    const backendParsed = parsePersistedQuestionSetState(
      backendUiState,
      effectiveStorageKey,
      questions.length,
    );
    if (backendParsed.hasSaved) {
      return backendParsed;
    }
    const localSaved = effectiveStorageKey
      ? readQuestionChoiceState<PersistedQuestionSetState>(effectiveStorageKey)
      : null;
    return parsePersistedQuestionSetState(
      localSaved,
      effectiveStorageKey,
      questions.length,
    );
    // Hydrate on mount / storage key change only; in-progress edits live in state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveStorageKey]);

  const [page, setPage] = useState(initialPersisted.page);
  const [drafts, setDrafts] = useState<DraftAnswer[]>(initialPersisted.drafts);
  const [results, setResults] = useState<QuestionResult[] | null>(
    initialPersisted.results,
  );
  const [resultPage, setResultPage] = useState(initialPersisted.resultPage);
  const shouldPersistRef = useRef(initialPersisted.hasSaved);
  const lastPersistedJsonRef = useRef<string>("");
  const lastBackendKeyRef = useRef(backendStateKey);

  // Sync persisted state when storageKey changes without remount.
  const prevKeyRef = useRef(effectiveStorageKey);
  useEffect(() => {
    if (prevKeyRef.current === effectiveStorageKey) return;
    prevKeyRef.current = effectiveStorageKey;
    lastBackendKeyRef.current = backendStateKey;
    lastPersistedJsonRef.current = "";
    shouldPersistRef.current = initialPersisted.hasSaved;
    setPage(initialPersisted.page);
    setDrafts(initialPersisted.drafts);
    setResults(initialPersisted.results);
    setResultPage(initialPersisted.resultPage);
  }, [backendStateKey, effectiveStorageKey, initialPersisted]);

  // Sync if backend ui_state arrives/updates externally (e.g. query refetch).
  useEffect(() => {
    if (backendStateKey === lastBackendKeyRef.current) return;
    lastBackendKeyRef.current = backendStateKey;
    if (!backendUiState) return;
    const parsed = parsePersistedQuestionSetState(
      backendUiState,
      effectiveStorageKey,
      questions.length,
    );
    if (!parsed.hasSaved) return;
    const canonical = JSON.stringify({
      ...(effectiveStorageKey ? { storageKey: effectiveStorageKey } : {}),
      page: parsed.page,
      drafts: parsed.drafts,
      results: parsed.results,
      resultPage: parsed.resultPage,
    });
    if (canonical === lastPersistedJsonRef.current) return;
    lastPersistedJsonRef.current = canonical;
    shouldPersistRef.current = true;
    setPage(parsed.page);
    setDrafts(parsed.drafts);
    setResults(parsed.results);
    setResultPage(parsed.resultPage);
  }, [backendStateKey, backendUiState, effectiveStorageKey, questions.length]);

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

  function persistState(
    nextState: PersistedQuestionSetState,
    options?: { immediate?: boolean },
  ) {
    const payload: PersistedQuestionSetState = {
      ...(effectiveStorageKey ? { storageKey: effectiveStorageKey } : {}),
      page: nextState.page,
      drafts: nextState.drafts,
      results: nextState.results,
      resultPage: nextState.resultPage,
    };
    const serialized = JSON.stringify(payload);
    lastPersistedJsonRef.current = serialized;
    if (effectiveStorageKey) {
      writeQuestionChoiceState<PersistedQuestionSetState>(
        effectiveStorageKey,
        payload,
      );
    }
    if (anchorPartId) {
      persistence?.persistPartUIState(anchorPartId, payload, options);
    }
  }

  useEffect(() => {
    if (!shouldPersistRef.current) return;
    const payload: PersistedQuestionSetState = {
      ...(effectiveStorageKey ? { storageKey: effectiveStorageKey } : {}),
      page,
      drafts,
      results,
      resultPage,
    };
    const serialized = JSON.stringify(payload);
    if (serialized === lastPersistedJsonRef.current) return;
    lastPersistedJsonRef.current = serialized;
    if (effectiveStorageKey) {
      writeQuestionChoiceState<PersistedQuestionSetState>(
        effectiveStorageKey,
        payload,
      );
    }
    if (anchorPartId) {
      persistence?.persistPartUIState(anchorPartId, payload, {
        immediate: false,
      });
    }
  }, [
    anchorPartId,
    drafts,
    effectiveStorageKey,
    page,
    persistence,
    resultPage,
    results,
  ]);

  const total = questions.length;
  const current = questions[Math.min(page, total - 1)];
  const draft = drafts[Math.min(page, total - 1)] ?? emptyDraft();
  function currentPageReady(source: DraftAnswer = draft) {
    if (source.answered || source.skipped) return true;
    if (current?.mode === "text") return Boolean(source.textValue.trim());
    return source.labels.length > 0 || source.values.length > 0;
  }

  const canPrimary = currentPageReady();
  // Last page: confirm only when every question is answered/skipped (current counts live).
  const primaryDisabled =
    !interactive ||
    (page < total - 1
      ? !canPrimary
      : !drafts.every((item, index) =>
          index === page ? canPrimary : item.answered || item.skipped,
        ));

  function primaryLabel() {
    if (page < total - 1) return "下一题";
    return current?.submitLabel ?? "提交答案";
  }

  const resultSummary = useMemo(() => {
    if (!results) return null;
    const graded = results.filter((item) => item.isCorrect !== null);
    const correct = graded.filter((item) => item.isCorrect).length;
    return { graded: graded.length, correct, total: results.length };
  }, [results]);

  function updateDraft(index: number, patch: Partial<DraftAnswer>) {
    shouldPersistRef.current = true;
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
    shouldPersistRef.current = true;
    setDrafts((prev) =>
      prev.map((item, itemIndex) => (itemIndex === index ? next : item)),
    );
    if (options?.advance && index < total - 1) {
      setPage(index + 1);
    }
  }

  function confirmWithDrafts(source: DraftAnswer[]) {
    shouldPersistRef.current = true;
    const graded = questions.map((question, index) => {
      const item = source[index] ?? emptyDraft();
      if (item.skipped || !item.answered) {
        return gradeQuestion(question, [], [], true);
      }
      return gradeQuestion(question, item.values, item.labels, false);
    });
    setResults(graded);
    setResultPage(0);
    persistState(
      {
        page,
        drafts: source,
        results: graded,
        resultPage: 0,
      },
      { immediate: true },
    );
    const summaryText = formatSubmissionText(graded);
    const gradedCount = graded.filter((item) => item.isCorrect !== null).length;
    const correctCount = graded.filter((item) => item.isCorrect === true).length;
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
    if (!canPrimary || !interactive) return;

    if (page < total - 1) {
      shouldPersistRef.current = true;
      setDrafts((prev) => markCurrentAnswered(prev));
      setPage(page + 1);
      return;
    }

    shouldPersistRef.current = true;
    setDrafts((prev) => {
      const updated = markCurrentAnswered(prev);
      const complete = updated.every((item) => item.answered || item.skipped);
      if (complete) {
        queueMicrotask(() => confirmWithDrafts(updated));
      }
      return updated;
    });
  }

  function handleRetry() {
    shouldPersistRef.current = true;
    setPage(resultPage);
    setResults(null);
    persistState(
      {
        page: resultPage,
        drafts,
        results: null,
        resultPage,
      },
      { immediate: true },
    );
  }

  if (!current) return null;

  if (results) {
    const safeResultPage = Math.min(resultPage, Math.max(0, results.length - 1));
    const currentResult = results[safeResultPage];
    const currentQuestion = questions[safeResultPage] ?? current;
    const currentDraft = drafts[safeResultPage] ?? emptyDraft();
    const correctDisplay = correctAnswerDisplay(currentQuestion);

    const optionStates: Record<string, OptionChoiceVerdict> | undefined =
      currentResult &&
      currentResult.isCorrect !== null &&
      currentQuestion.mode !== "text"
        ? (() => {
            const states: Record<string, OptionChoiceVerdict> = {};
            const expected = new Set(currentQuestion.answerKey.correctOptionIds);
            for (const id of expected) {
              states[id] = "correct";
            }
            for (const id of currentResult.values) {
              if (!expected.has(id)) {
                states[id] = "wrong";
              }
            }
            return states;
          })()
        : undefined;

    const showCustomFeedback =
      currentResult &&
      currentResult.feedback &&
      !["回答正确", "回答错误", "已跳过", "已提交，等待模型批改"].includes(
        currentResult.feedback,
      );

    return (
      <section
        aria-label="练习批改结果"
        className="question-set-pager question-set-pager--result"
      >
        {total > 1 ? (
          <div className="question-set-pager__chrome">
            <div className="question-set-pager__meta">
              <strong>
                第 {safeResultPage + 1} / {total} 题
              </strong>
              <span>
                {resultSummary
                  ? resultSummary.graded > 0
                    ? `${resultSummary.correct}/${resultSummary.graded} 正确`
                    : `共 ${resultSummary.total} 题已提交`
                  : null}
              </span>
            </div>
          </div>
        ) : null}

        <div className="question-set-pager__stage">
          {currentQuestion.mode === "text" ? (
            <section className="option-group" aria-label={currentQuestion.title}>
              <div className="option-group__heading">
                <div>
                  <p>{currentQuestion.title}</p>
                  {currentQuestion.description ? (
                    <span>{currentQuestion.description}</span>
                  ) : null}
                </div>
                <span className="option-group__badge">简答</span>
              </div>
              <div className="question-set-pager__submitted-text">
                {currentResult?.skipped
                  ? "（已跳过）"
                  : currentResult?.labels.join("、") ||
                    currentDraft.textValue ||
                    "（未作答）"}
              </div>
            </section>
          ) : (
            <OptionGroup
              key={`result-${currentQuestion.partId ?? currentQuestion.componentId}-${safeResultPage}`}
              allowCustom={false}
              allowSkip={false}
              description={currentQuestion.description}
              disabled
              hideActions
              mode={currentQuestion.mode}
              optionStates={optionStates}
              options={currentQuestion.options}
              title={currentQuestion.title}
              value={
                currentResult?.values.length
                  ? currentResult.values
                  : currentDraft.values
              }
            />
          )}
        </div>

        {currentResult ? (
          <div
            className={cn(
              "question-set-pager__verdict-banner",
              currentResult.isCorrect === true && "is-correct",
              currentResult.isCorrect === false && "is-wrong",
              currentResult.isCorrect == null &&
                (currentResult.skipped ? "is-pending" : "is-submitted"),
            )}
            role="status"
          >
            <div className="question-set-pager__verdict-row">
              {currentResult.isCorrect === true ? (
                <>
                  <CheckCircle2
                    aria-hidden="true"
                    className="question-set-pager__verdict-icon is-correct size-5"
                  />
                  <strong>回答正确</strong>
                </>
              ) : currentResult.isCorrect === false ? (
                <>
                  <XCircle
                    aria-hidden="true"
                    className="question-set-pager__verdict-icon is-wrong size-5"
                  />
                  <strong>
                    回答错误
                    {correctDisplay ? `，正确答案是 ${correctDisplay}` : ""}
                  </strong>
                </>
              ) : currentResult.skipped ? (
                <>
                  <CircleHelp
                    aria-hidden="true"
                    className="question-set-pager__verdict-icon is-pending size-5"
                  />
                  <strong>已跳过该题</strong>
                </>
              ) : (
                <>
                  <CheckCircle2
                    aria-hidden="true"
                    className="question-set-pager__verdict-icon is-submitted size-5"
                  />
                  <strong>
                    已提交选择：
                    {currentResult.labels.join("、") || "已选择"}
                  </strong>
                </>
              )}
            </div>
            {showCustomFeedback ? (
              <p className="question-set-pager__feedback">
                {currentResult.feedback}
              </p>
            ) : null}
            {currentResult.explanation ? (
              <p className="question-set-pager__explanation">
                {currentResult.explanation}
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="question-set-pager__footer">
          {total > 1 ? (
            <div className="question-set-pager__nav">
              <Button
                disabled={safeResultPage <= 0}
                onClick={() => setResultPage((value) => Math.max(0, value - 1))}
                size="sm"
                type="button"
                variant="ghost"
              >
                <ChevronLeft className="size-3.5" />
                上一题
              </Button>
              <span>
                {safeResultPage + 1} / {total}
              </span>
              <Button
                disabled={safeResultPage >= total - 1}
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
          ) : (
            <span />
          )}
          <div className="question-set-pager__actions">
            <Button
              disabled={!interactive}
              onClick={handleRetry}
              size="sm"
              type="button"
              variant="ghost"
            >
              <RotateCcw className="size-3.5" />
              重新作答
            </Button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section aria-label="交互练习" className="question-set-pager">
      <div className="question-set-pager__stage">
        {current.mode === "text" ? (
          <section className="option-group" aria-label={current.title}>
            <div className="option-group__heading">
              <div>
                <p>{current.title}</p>
                {current.description ? <span>{current.description}</span> : null}
              </div>
              <span className="option-group__badge">简答</span>
            </div>
            {current.multiline ? (
              <Textarea
                aria-label={current.title}
                className="min-h-24"
                disabled={!interactive}
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
                disabled={!interactive}
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
            key={`${current.partId ?? current.componentId}-${page}`}
            allowCustom={current.allowCustom}
            allowSkip={false}
            description={current.description}
            disabled={!interactive}
            hideActions
            mode={current.mode}
            onChange={handleOptionChange}
            options={current.options}
            title={current.title}
            value={draft.values}
          />
        )}
      </div>

      <div
        className={cn(
          "question-set-pager__footer",
          total === 1 && "question-set-pager__footer--single",
        )}
      >
        {total > 1 ? (
          <div className="question-set-pager__nav">
            <Button
              disabled={page <= 0}
              onClick={() => {
                shouldPersistRef.current = true;
                setPage((value) => Math.max(0, value - 1));
              }}
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
            <Button
              aria-label="下一题"
              disabled={page >= total - 1}
              onClick={() => {
                shouldPersistRef.current = true;
                setPage((value) => Math.min(total - 1, value + 1));
              }}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
        ) : null}
        <div
          className={cn(
            "question-set-pager__actions",
            total === 1 && "question-set-pager__actions--single",
          )}
        >
          {current.allowSkip ? (
            <Button
              disabled={!interactive}
              onClick={handleSkip}
              size="sm"
              type="button"
              variant="ghost"
            >
              跳过
            </Button>
          ) : null}
          <Button
            className={cn(
              "question-set-pager__submit-pill",
              total === 1 && !current.allowSkip && "is-full",
            )}
            disabled={primaryDisabled}
            onClick={handlePrimaryAction}
            size="sm"
            type="button"
          >
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

/**
 * Final answer-body grouping used by every chat renderer:
 * question pager groups from {@link groupQuestionParts}, plus adjacent sandbox
 * image artifacts merged into a single inline image strip. Only strictly
 * adjacent image parts are merged — text/cards in between split the stream so
 * the horizontal-scroll strip never swallows images separated by prose.
 */
export type AnswerGroup =
  | { kind: "part"; part: MessagePart }
  | { kind: "question_set"; parts: MessagePart[]; questions: QuestionItem[] }
  | { kind: "image_strip"; parts: MessagePart[] }
  | {
      kind: "side_pair";
      /** Introducing text segment (the reference tag's preceding prose). */
      text: MessagePart;
      /** Artifact explicitly placed next to the text via |layout=side. */
      artifact: MessagePart;
    };

export function groupAnswerParts(parts: MessagePart[]): AnswerGroup[] {
  const { parts: reconciled } = reconcileArtifacts(parts);
  const groups = groupQuestionParts(reconciled);
  const result: AnswerGroup[] = [];
  for (const group of groups) {
    if (group.kind === "part" && isSandboxImageArtifactPart(group.part)) {
      const last = result[result.length - 1];
      if (last && last.kind === "image_strip") {
        last.parts.push(group.part);
      } else {
        result.push({ kind: "image_strip", parts: [group.part] });
      }
      continue;
    }
    result.push(group);
  }
  // Model-authored |layout=side references: pair the artifact with the text
  // segment that immediately precedes it so they render side by side.
  const paired: AnswerGroup[] = [];
  for (let index = 0; index < result.length; index += 1) {
    const current = result[index];
    const next = result[index + 1];
    if (
      current.kind === "part" &&
      current.part.type === "text" &&
      next?.kind === "part" &&
      isSideLayoutArtifactPart(next.part)
    ) {
      paired.push({
        kind: "side_pair",
        text: current.part,
        artifact: next.part,
      });
      index += 1;
      continue;
    }
    paired.push(current);
  }
  return paired;
}

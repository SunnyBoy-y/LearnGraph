import { useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  readQuestionChoiceState,
  writeQuestionChoiceState,
} from "@/lib/question-choice-state";
import { cn } from "@/lib/utils";

export type OptionGroupChoice = {
  id: string;
  label: string;
  description?: string;
  isCorrect?: boolean;
};

export type OptionGroupSubmission = {
  values: string[];
  labels: string[];
};

export type OptionChoiceVerdict = "correct" | "wrong";

export type PersistedOptionGroupState = {
  values: string[];
  customValue: string;
  submitted?: boolean;
};

export function OptionGroup({
  allowCustom = true,
  allowSkip = true,
  className,
  description,
  disabled = false,
  hideActions = false,
  initialState,
  mode = "single",
  onChange,
  onStateChange,
  onSubmit,
  optionStates,
  options,
  storageKey,
  submitLabel = "提交答案",
  title,
  value,
}: {
  allowCustom?: boolean;
  allowSkip?: boolean;
  className?: string;
  description?: string;
  disabled?: boolean;
  /** When true, only render the prompt/choices; parent owns submit/skip/nav. */
  hideActions?: boolean;
  /** Optional backend-hydrated state for standalone OptionGroup renders. */
  initialState?: PersistedOptionGroupState | null;
  mode?: "single" | "multiple";
  onChange?: (submission: OptionGroupSubmission) => void;
  onStateChange?: (state: PersistedOptionGroupState) => void;
  onSubmit?: (submission: OptionGroupSubmission) => void;
  optionStates?: Record<string, OptionChoiceVerdict>;
  options: OptionGroupChoice[];
  /** Optional persistence key for standalone OptionGroup renders. */
  storageKey?: string;
  submitLabel?: string;
  title: string;
  /** Initial selection only. Remount (key) to restore a different draft. */
  value?: string[];
}) {
  const instanceId = useId();
  const optionMap = useMemo(
    () => new Map(options.map((option) => [option.id, option])),
    [options],
  );
  const backendStateKey = useMemo(
    () => (initialState ? JSON.stringify(initialState) : ""),
    [initialState],
  );
  const initial = useMemo(() => {
    const persisted =
      value === undefined
        ? (initialState ??
          (storageKey
            ? readQuestionChoiceState<PersistedOptionGroupState>(storageKey)
            : null))
        : null;
    const seed = value ?? persisted?.values ?? [];
    const known = seed.filter((item) => optionMap.has(item));
    const customFromSeed = seed.find((item) => !optionMap.has(item)) ?? "";
    const custom = persisted?.customValue ?? customFromSeed;
    return { known, custom, submitted: Boolean(persisted?.submitted) };
    // Hydrate on mount / storageKey / value changes; backend updates sync below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [optionMap, storageKey, value]);

  const [internalValues, setInternalValues] = useState<string[]>(initial.known);
  const [customValue, setCustomValue] = useState(initial.custom);
  const [submitted, setSubmitted] = useState(initial.submitted);
  const lastPersistedKeyRef = useRef("");
  const lastBackendKeyRef = useRef(backendStateKey);

  useEffect(() => {
    if (value !== undefined) return;
    if (backendStateKey === lastBackendKeyRef.current) return;
    lastBackendKeyRef.current = backendStateKey;
    if (!initialState) return;
    const known = (initialState.values ?? []).filter((item) => optionMap.has(item));
    const custom =
      typeof initialState.customValue === "string"
        ? initialState.customValue
        : (initialState.values ?? []).find((item) => !optionMap.has(item)) ?? "";
    const nextState: PersistedOptionGroupState = {
      values: known,
      customValue: custom,
      submitted: Boolean(initialState.submitted),
    };
    const canonical = JSON.stringify(nextState);
    if (canonical === lastPersistedKeyRef.current) return;
    lastPersistedKeyRef.current = canonical;
    setInternalValues(known);
    setCustomValue(custom);
    setSubmitted(Boolean(initialState.submitted));
  }, [backendStateKey, initialState, optionMap, value]);

  const selectedValues = internalValues;

  function persist(nextValues: string[], nextCustom: string, nextSubmitted: boolean) {
    const nextState: PersistedOptionGroupState = {
      values: nextValues,
      customValue: nextCustom,
      submitted: nextSubmitted,
    };
    lastPersistedKeyRef.current = JSON.stringify(nextState);
    if (storageKey) {
      writeQuestionChoiceState<PersistedOptionGroupState>(storageKey, nextState);
    }
    onStateChange?.(nextState);
  }

  function buildSubmission(
    values: string[],
    custom = customValue,
  ): OptionGroupSubmission {
    const customTrimmed = custom.trim();
    const normalized = [
      ...values,
      ...(customTrimmed && !values.includes(customTrimmed) ? [customTrimmed] : []),
    ];
    return {
      values: normalized,
      labels: normalized.map((item) => optionMap.get(item)?.label ?? item),
    };
  }

  function update(next: string[]) {
    setInternalValues(next);
    if (submitted) setSubmitted(false);
    persist(next, customValue, false);
    onChange?.(buildSubmission(next));
  }

  function submit(values = selectedValues) {
    setSubmitted(true);
    persist(values, customValue, true);
    onSubmit?.(buildSubmission(values));
  }

  function skip() {
    setInternalValues([]);
    setCustomValue("");
    setSubmitted(true);
    persist([], "", true);
    onSubmit?.({ values: [], labels: [] });
  }

  return (
    <section className={cn("option-group", className)} aria-label={title}>
      <div className="option-group__heading">
        <div>
          <p>{title}</p>
          {description ? <span>{description}</span> : null}
        </div>
        <span className="option-group__badge">
          {mode === "multiple" ? "可多选" : "单选"}
        </span>
      </div>

      {options.length ? (
        mode === "single" ? (
          <RadioGroup
            className="option-group__choices"
            disabled={disabled}
            onValueChange={(next) => update([next])}
            value={selectedValues[0] ?? ""}
          >
            {options.map((option) => {
              const selected = selectedValues.includes(option.id);
              const verdict = optionStates?.[option.id];
              return (
                <Label
                  className={cn(
                    "option-group__choice",
                    selected && "is-selected",
                    verdict === "correct" && "is-correct",
                    verdict === "wrong" && "is-wrong",
                    disabled && "is-disabled",
                  )}
                  htmlFor={`${instanceId}-choice-${option.id}`}
                  key={option.id}
                >
                  <RadioGroupItem
                    disabled={disabled}
                    id={`${instanceId}-choice-${option.id}`}
                    value={option.id}
                  />
                  <span>
                    <strong>{option.label}</strong>
                    {option.description ? (
                      <small>{option.description}</small>
                    ) : null}
                  </span>
                  {verdict === "correct" ? (
                    <CheckCircle2
                      aria-hidden="true"
                      className="option-group__status-icon is-correct size-4"
                    />
                  ) : verdict === "wrong" ? (
                    <XCircle
                      aria-hidden="true"
                      className="option-group__status-icon is-wrong size-4"
                    />
                  ) : null}
                </Label>
              );
            })}
          </RadioGroup>
        ) : (
          <div className="option-group__choices">
            {options.map((option) => {
              const selected = selectedValues.includes(option.id);
              const verdict = optionStates?.[option.id];
              return (
                <Label
                  className={cn(
                    "option-group__choice",
                    selected && "is-selected",
                    verdict === "correct" && "is-correct",
                    verdict === "wrong" && "is-wrong",
                    disabled && "is-disabled",
                  )}
                  htmlFor={`${instanceId}-choice-${option.id}`}
                  key={option.id}
                >
                  <Checkbox
                    checked={selected}
                    disabled={disabled}
                    id={`${instanceId}-choice-${option.id}`}
                    onCheckedChange={(checked) =>
                      update(
                        checked
                          ? [...selectedValues, option.id]
                          : selectedValues.filter((item) => item !== option.id),
                      )
                    }
                  />
                  <span>
                    <strong>{option.label}</strong>
                    {option.description ? (
                      <small>{option.description}</small>
                    ) : null}
                  </span>
                  {verdict === "correct" ? (
                    <CheckCircle2
                      aria-hidden="true"
                      className="option-group__status-icon is-correct size-4"
                    />
                  ) : verdict === "wrong" ? (
                    <XCircle
                      aria-hidden="true"
                      className="option-group__status-icon is-wrong size-4"
                    />
                  ) : null}
                </Label>
              );
            })}
          </div>
        )
      ) : (
        <p className="option-group__empty">
          服务端没有返回可选项，可填写自定义答案或跳过。
        </p>
      )}

      {allowCustom ? (
        <Input
          aria-label="自定义答案"
          className="option-group__custom"
          disabled={disabled}
          onChange={(event) => {
            const nextCustom = event.currentTarget.value;
            setCustomValue(nextCustom);
            if (submitted) setSubmitted(false);
            persist(selectedValues, nextCustom, false);
            onChange?.(buildSubmission(selectedValues, nextCustom));
          }}
          placeholder="输入其他答案"
          value={customValue}
        />
      ) : null}

      {!hideActions ? (
        <div
          className={cn(
            "option-group__actions",
            !allowSkip && "option-group__actions--single",
          )}
        >
          {allowSkip ? (
            <Button
              disabled={disabled}
              onClick={skip}
              size="sm"
              type="button"
              variant="ghost"
            >
              跳过
            </Button>
          ) : null}
          <Button
            className="option-group__submit-pill"
            disabled={disabled || (!selectedValues.length && !customValue.trim())}
            onClick={() => submit()}
            size="sm"
            type="button"
          >
            {submitLabel}
          </Button>
        </div>
      ) : null}

      {!hideActions && storageKey && submitted ? (
        <p className="option-group__submitted-note" role="status">
          <CheckCircle2 aria-hidden="true" className="size-4" />
          <span>
            {selectedValues.length || customValue.trim()
              ? `已提交选择：${buildSubmission(selectedValues).labels.join("、")}`
              : "已跳过该问题"}
          </span>
        </p>
      ) : null}
    </section>
  );
}

import { useId, useMemo, useState } from "react";
import { Check, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { cn } from "@/lib/utils";

export type OptionGroupChoice = {
  id: string;
  label: string;
  description?: string;
};

export type OptionGroupSubmission = {
  values: string[];
  labels: string[];
};

export function OptionGroup({
  allowCustom = true,
  allowSkip = true,
  description,
  mode = "single",
  onSubmit,
  options,
  submitLabel = "确认并继续",
  title,
  value,
}: {
  allowCustom?: boolean;
  allowSkip?: boolean;
  description?: string;
  mode?: "single" | "multiple";
  onSubmit: (submission: OptionGroupSubmission) => void;
  options: OptionGroupChoice[];
  submitLabel?: string;
  title: string;
  value?: string[];
}) {
  const instanceId = useId();
  const [internalValues, setInternalValues] = useState<string[]>(value ?? []);
  const [customValue, setCustomValue] = useState("");
  const selectedValues = internalValues;
  const optionMap = useMemo(
    () => new Map(options.map((option) => [option.id, option])),
    [options],
  );

  function update(next: string[]) {
    setInternalValues(next);
  }

  function submit(values = selectedValues) {
    const custom = customValue.trim();
    const normalized = [
      ...values,
      ...(custom && !values.includes(custom) ? [custom] : []),
    ];
    onSubmit({
      values: normalized,
      labels: normalized.map((item) => optionMap.get(item)?.label ?? item),
    });
  }

  return (
    <section className="option-group" aria-label={title}>
      <div className="option-group__heading">
        <div>
          <p>{title}</p>
          {description ? <span>{description}</span> : null}
        </div>
        <span>{mode === "multiple" ? "可多选" : "单选"}</span>
      </div>

      {options.length ? (
        mode === "single" ? (
          <RadioGroup
            className="option-group__choices"
            onValueChange={(next) => update([next])}
            value={selectedValues[0] ?? ""}
          >
            {options.map((option) => (
              <Label
                className={cn(
                  "option-group__choice",
                  selectedValues.includes(option.id) && "is-selected",
                )}
                htmlFor={`${instanceId}-choice-${option.id}`}
                key={option.id}
              >
                <RadioGroupItem
                  id={`${instanceId}-choice-${option.id}`}
                  value={option.id}
                />
                <span>
                  <strong>{option.label}</strong>
                  {option.description ? (
                    <small>{option.description}</small>
                  ) : null}
                </span>
                {selectedValues.includes(option.id) ? (
                  <Check aria-hidden="true" className="size-3.5" />
                ) : null}
              </Label>
            ))}
          </RadioGroup>
        ) : (
          <div className="option-group__choices">
            {options.map((option) => {
              const selected = selectedValues.includes(option.id);
              return (
                <Label
                  className={cn(
                    "option-group__choice",
                    selected && "is-selected",
                  )}
                  htmlFor={`${instanceId}-choice-${option.id}`}
                  key={option.id}
                >
                  <Checkbox
                    checked={selected}
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
          onChange={(event) => setCustomValue(event.currentTarget.value)}
          placeholder="输入其他答案"
          value={customValue}
        />
      ) : null}

      <div className="option-group__actions">
        {allowSkip ? (
          <Button
            onClick={() => onSubmit({ values: [], labels: [] })}
            size="sm"
            type="button"
            variant="ghost"
          >
            跳过
          </Button>
        ) : null}
        <Button
          disabled={!selectedValues.length && !customValue.trim()}
          onClick={() => submit()}
          size="sm"
          type="button"
        >
          {submitLabel}
          <ChevronRight className="size-3.5" />
        </Button>
      </div>
    </section>
  );
}

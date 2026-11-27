import {
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "@/components/ui/dropdown-menu";
import { responseModeLabel } from "@/lib/generation-config";
import type { ResponseMode } from "@/lib/session-composer-prefs";
import { cn } from "@/lib/utils";
import { CONFIG_MENU_ROW, keepMenuOpen } from "./floating-menu";

export interface ResponseModeSelectorProps {
  value: ResponseMode;
  onChange: (mode: ResponseMode) => void;
  supportsAgent: boolean;
  hasThinkingModes: boolean;
  thinkingRequired?: boolean;
  keepOpenOnSelect?: boolean;
  /** "menu" renders DropdownMenuRadioItems; "inline" renders compact segmented buttons. */
  variant?: "menu" | "inline";
}

export function ResponseModeSelector({
  value,
  onChange,
  supportsAgent,
  hasThinkingModes,
  thinkingRequired = false,
  keepOpenOnSelect = true,
  variant = "menu",
}: ResponseModeSelectorProps) {
  const showThinkingOnlyOption = !supportsAgent && hasThinkingModes;

  if (variant === "inline") {
    const options: Array<{ mode: ResponseMode; disabled: boolean }> = [
      { mode: "fast", disabled: thinkingRequired },
      ...(showThinkingOnlyOption
        ? [{ mode: "thinking" as const, disabled: false }]
        : []),
      { mode: "agentic", disabled: !supportsAgent },
    ];
    return (
      <div className="space-y-1.5" data-slot="response-mode-selector">
        <div
          role="radiogroup"
          aria-label="响应模式"
          className="grid gap-1 rounded-lg bg-muted/60 p-1"
          style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
        >
          {options.map(({ mode, disabled }) => {
            const active = value === mode;
            return (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={disabled}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => onChange(mode)}
                className={cn(
                  "rounded-md px-2 py-1 text-xs font-medium transition-colors",
                  active
                    ? "bg-background text-foreground shadow-xs"
                    : "text-muted-foreground hover:text-foreground",
                  disabled && "cursor-not-allowed opacity-45",
                )}
              >
                {responseModeLabel(mode)}
              </button>
            );
          })}
        </div>
        {!supportsAgent ? (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            当前模型不支持智能体工具
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div data-slot="response-mode-selector">
      <DropdownMenuRadioGroup
        value={value}
        onValueChange={(next) => onChange(next as ResponseMode)}
      >
        <DropdownMenuRadioItem
          className={CONFIG_MENU_ROW}
          value="fast"
          disabled={thinkingRequired}
          onSelect={keepOpenOnSelect ? keepMenuOpen : undefined}
        >
          极速
        </DropdownMenuRadioItem>
        {showThinkingOnlyOption ? (
          <DropdownMenuRadioItem
            className={CONFIG_MENU_ROW}
            value="thinking"
            onSelect={keepOpenOnSelect ? keepMenuOpen : undefined}
          >
            思考
          </DropdownMenuRadioItem>
        ) : null}
        <DropdownMenuRadioItem
          className={CONFIG_MENU_ROW}
          value="agentic"
          disabled={!supportsAgent}
          onSelect={keepOpenOnSelect ? keepMenuOpen : undefined}
        >
          智能体
        </DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
      {!supportsAgent ? (
        <p className="px-2.5 py-1 text-[11px] leading-relaxed text-muted-foreground">
          当前模型不支持智能体工具
        </p>
      ) : null}
      {thinkingRequired ? (
        <p className="px-2.5 py-1 text-[11px] leading-relaxed text-muted-foreground">
          当前模型要求开启思考，不支持极速模式
        </p>
      ) : null}
    </div>
  );
}

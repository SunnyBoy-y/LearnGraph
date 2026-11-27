import { useRef, useState, type ReactNode } from "react";
import { Bot, Brain, Layers } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  responseModeLabel,
  type ModelSelectorGroup,
} from "@/lib/generation-config";
import { thinkingLabels } from "@/lib/model-choices";
import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import { cn } from "@/lib/utils";
import {
  CONFIG_MENU_SUMMARY,
  FloatingMenuContent,
  FloatingSubmenu,
  FloatingSubmenuContent,
  FloatingSubmenuTrigger,
} from "./floating-menu";
import { ModelSelector } from "./model-selector";
import { ReasoningSelector } from "./reasoning-selector";
import { ResponseModeSelector } from "./response-mode-selector";

export interface GenerationConfigRowsProps {
  groups: readonly ModelSelectorGroup[];
  providerId: string;
  modelId: string;
  responseMode: ResponseMode;
  thinkingMode: ThinkingMode;
  /** Model's declared supported reasoning efforts (excluding "off"). */
  thinkingModes: readonly ThinkingMode[];
  supportsAgent: boolean;
  thinkingRequired?: boolean;
  modelError?: boolean;
  onModelChange: (providerId: string, modelId: string) => void;
  onModeChange: (mode: ResponseMode) => void;
  onThinkingChange: (mode: ThinkingMode) => void;
  onManageModels?: () => void;
  onRefreshModels?: () => void;
  /** Keep menu open when selecting a model or mode (true for retry drafts). */
  keepOpenOnModelSelect?: boolean;
  keepOpenOnModeSelect?: boolean;
  modelSearch?: string;
  onModelSearchChange?: (value: string) => void;
}

/**
 * Shared configuration rows (模型 / 响应模式 / 思考强度) and submenus reused
 * identically by both the composer generation menu and the retry answer menu.
 */
export function GenerationConfigRows({
  groups,
  providerId,
  modelId,
  responseMode,
  thinkingMode,
  thinkingModes,
  supportsAgent,
  thinkingRequired = false,
  modelError = false,
  onModelChange,
  onModeChange,
  onThinkingChange,
  onManageModels,
  onRefreshModels,
  keepOpenOnModelSelect = false,
  keepOpenOnModeSelect = true,
  modelSearch,
  onModelSearchChange,
}: GenerationConfigRowsProps) {
  const [modelOpen, setModelOpen] = useState(false);
  const modelTriggerRef = useRef<HTMLDivElement>(null);
  const nonOffThinkingModes = thinkingModes.filter((mode) => mode !== "off");
  const hasThinkingModes = nonOffThinkingModes.length > 0;
  const reasoningDisabled =
    (responseMode === "fast" && !thinkingRequired) || !hasThinkingModes;
  const reasoningSummary = !hasThinkingModes
    ? "默认"
    : responseMode === "fast" && !thinkingRequired
      ? thinkingLabels.off
      : thinkingLabels[thinkingMode];

  return (
    <>
      <FloatingSubmenu open={modelOpen} onOpenChange={setModelOpen}>
        <FloatingSubmenuTrigger ref={modelTriggerRef}>
          <Layers aria-hidden="true" className="size-4" />
          <span>模型</span>
          <span className={CONFIG_MENU_SUMMARY}>{modelId || "选择模型"}</span>
        </FloatingSubmenuTrigger>
        <FloatingSubmenuContent className="w-72">
          <ModelSelector
            groups={groups}
            providerId={providerId}
            modelId={modelId}
            onSelect={(nextProviderId, nextModelId) => {
              onModelChange(nextProviderId, nextModelId);
              // On narrow screens the submenu can cover the retry button.
              if (keepOpenOnModelSelect && window.matchMedia("(max-width: 640px)").matches) {
                modelTriggerRef.current?.focus();
                setModelOpen(false);
              }
            }}
            onManageModels={onManageModels}
            onRefreshModels={onRefreshModels}
            modelError={modelError}
            keepOpenOnSelect={keepOpenOnModelSelect}
            search={modelSearch}
            onSearchChange={onModelSearchChange}
          />
        </FloatingSubmenuContent>
      </FloatingSubmenu>

      <FloatingSubmenu>
        <FloatingSubmenuTrigger>
          <Bot aria-hidden="true" className="size-4" />
          <span>响应模式</span>
          <span className={CONFIG_MENU_SUMMARY}>
            {responseModeLabel(responseMode)}
          </span>
        </FloatingSubmenuTrigger>
        <FloatingSubmenuContent className="w-52">
          <ResponseModeSelector
            value={responseMode}
            onChange={onModeChange}
            supportsAgent={supportsAgent}
            hasThinkingModes={hasThinkingModes}
            thinkingRequired={thinkingRequired}
            keepOpenOnSelect={keepOpenOnModeSelect}
          />
        </FloatingSubmenuContent>
      </FloatingSubmenu>

      <FloatingSubmenu>
        <FloatingSubmenuTrigger disabled={reasoningDisabled}>
          <Brain aria-hidden="true" className="size-4" />
          <span>思考强度</span>
          <span className={CONFIG_MENU_SUMMARY}>{reasoningSummary}</span>
        </FloatingSubmenuTrigger>
        <FloatingSubmenuContent
          className="w-64 p-3"
          onFocus={(event) => {
            if (event.target === event.currentTarget) {
              event.currentTarget
                .querySelector<HTMLElement>('[role="slider"]')
                ?.focus();
            }
          }}
        >
          <ReasoningSelector
            responseMode={responseMode}
            thinkingMode={thinkingMode}
            thinkingModes={nonOffThinkingModes}
            thinkingRequired={thinkingRequired}
            onChange={onThinkingChange}
          />
        </FloatingSubmenuContent>
      </FloatingSubmenu>
    </>
  );
}

export interface GenerationConfigMenuProps extends GenerationConfigRowsProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactNode;
  align?: "start" | "center" | "end";
  side?: "top" | "bottom" | "left" | "right";
  /**
   * "submenu" renders the compact 3-row menu with floating submenus (desktop).
   * "inline" renders the exact same shared selectors stacked inline (phone / narrow rail).
   */
  layout?: "submenu" | "inline";
  title?: string;
  footerText?: string;
  extraContent?: ReactNode;
  contentClassName?: string;
}

export function GenerationConfigMenu({
  open,
  onOpenChange,
  trigger,
  align = "end",
  side = "top",
  layout = "submenu",
  title = "生成配置",
  footerText = "应用于后续消息",
  extraContent,
  contentClassName,
  groups,
  providerId,
  modelId,
  responseMode,
  thinkingMode,
  thinkingModes,
  supportsAgent,
  thinkingRequired = false,
  modelError = false,
  onModelChange,
  onModeChange,
  onThinkingChange,
  onManageModels,
  onRefreshModels,
  keepOpenOnModelSelect = false,
  keepOpenOnModeSelect = true,
  modelSearch,
  onModelSearchChange,
}: GenerationConfigMenuProps) {
  const nonOffThinkingModes = thinkingModes.filter((mode) => mode !== "off");
  const hasThinkingModes = nonOffThinkingModes.length > 0;

  return (
    <DropdownMenu
      modal={false}
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) onModelSearchChange?.("");
      }}
    >
      <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
      <FloatingMenuContent
        align={align}
        side={side}
        aria-label={title}
        className={cn(
          layout === "inline"
            ? "flex max-h-[min(72dvh,540px)] w-[min(320px,calc(100vw-24px))] flex-col overflow-hidden"
            : "w-[280px]",
          contentClassName,
        )}
      >
        <DropdownMenuLabel className="px-2.5 pb-1.5 pt-2 text-[13px] font-semibold text-foreground">
          {title}
        </DropdownMenuLabel>

        {layout === "inline" ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="shrink-0 space-y-2.5 px-2 pb-2 pt-1">
              <div className="space-y-1">
                <p className="text-[11px] font-medium text-muted-foreground">
                  响应模式
                </p>
                <ResponseModeSelector
                  variant="inline"
                  value={responseMode}
                  onChange={onModeChange}
                  supportsAgent={supportsAgent}
                  hasThinkingModes={hasThinkingModes}
                  thinkingRequired={thinkingRequired}
                />
              </div>
              <ReasoningSelector
                responseMode={responseMode}
                thinkingMode={thinkingMode}
                thinkingModes={nonOffThinkingModes}
                thinkingRequired={thinkingRequired}
                onChange={onThinkingChange}
              />
            </div>
            <DropdownMenuSeparator className="mx-1 my-1" />
            <DropdownMenuLabel className="px-2.5 pb-1 pt-1 text-[11px] font-medium text-muted-foreground">
              模型
            </DropdownMenuLabel>
            <ModelSelector
              groups={groups}
              providerId={providerId}
              modelId={modelId}
              onSelect={onModelChange}
              onManageModels={onManageModels}
              onRefreshModels={onRefreshModels}
              modelError={modelError}
              keepOpenOnSelect={keepOpenOnModelSelect}
              search={modelSearch}
              onSearchChange={onModelSearchChange}
              listClassName="max-h-[min(36vh,240px)]"
            />
          </div>
        ) : (
          <GenerationConfigRows
            groups={groups}
            providerId={providerId}
            modelId={modelId}
            responseMode={responseMode}
            thinkingMode={thinkingMode}
            thinkingModes={nonOffThinkingModes}
            supportsAgent={supportsAgent}
            thinkingRequired={thinkingRequired}
            modelError={modelError}
            onModelChange={onModelChange}
            onModeChange={onModeChange}
            onThinkingChange={onThinkingChange}
            onManageModels={onManageModels}
            onRefreshModels={onRefreshModels}
            keepOpenOnModelSelect={keepOpenOnModelSelect}
            keepOpenOnModeSelect={keepOpenOnModeSelect}
            modelSearch={modelSearch}
            onModelSearchChange={onModelSearchChange}
          />
        )}

        {extraContent ? (
          <>
            <DropdownMenuSeparator className="mx-1 my-1.5" />
            {extraContent}
          </>
        ) : null}

        {footerText ? (
          <>
            <DropdownMenuSeparator className="mx-1 my-1.5" />
            <p className="px-2.5 pb-1.5 pt-0.5 text-[11px] leading-relaxed text-muted-foreground">
              {footerText}
            </p>
          </>
        ) : null}
      </FloatingMenuContent>
    </DropdownMenu>
  );
}

import type { ReactNode } from "react";
import { Globe, RefreshCcw } from "lucide-react";

import {
  CONFIG_MENU_ROW,
  CONFIG_MENU_SUMMARY,
  FloatingMenuContent,
  FloatingSubmenu,
  FloatingSubmenuContent,
  FloatingSubmenuTrigger,
  GenerationConfigRows,
  keepMenuOpen,
} from "@/components/chat/generation-config";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  summarizeAnswerTrace,
  type ModelSelectorGroup,
} from "@/lib/generation-config";
import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import type { UnknownRecord } from "@/types/common";
import "./retry-answer-menu.css";

export function RetryAnswerMenu({
  children,
  content,
  open,
  onOpenChange,
}: {
  children: ReactNode;
  content: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <DropdownMenu modal={false} open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      {content}
    </DropdownMenu>
  );
}

export type RetryModelGroup = ModelSelectorGroup;

export function RetryMenuSettings({
  groups,
  providerId,
  modelId,
  responseMode,
  thinkingMode,
  thinkingModes,
  supportsAgent,
  thinkingRequired = false,
  webSearch,
  canSearch,
  canRetry,
  canDirectRetry,
  modelError,
  onModelChange,
  onModeChange,
  onThinkingChange,
  onSearchChange,
  onDirectRetry,
  onRetry,
  onRefreshModels,
  onConfigure,
  allowedDomains = "",
  onDomainsChange,
  previousAnswerTrace,
}: {
  previousAnswerTrace: UnknownRecord;
  groups: RetryModelGroup[];
  providerId: string;
  modelId: string;
  responseMode: ResponseMode;
  thinkingMode: ThinkingMode;
  thinkingModes: ThinkingMode[];
  supportsAgent: boolean;
  thinkingRequired?: boolean;
  webSearch: boolean;
  canSearch: boolean;
  canRetry: boolean;
  /** Override whether direct retry with historical config is enabled. */
  canDirectRetry?: boolean;
  modelError: boolean;
  onModelChange: (providerId: string, modelId: string) => void;
  onModeChange: (mode: ResponseMode) => void;
  onThinkingChange: (mode: ThinkingMode) => void;
  onSearchChange: (enabled: boolean) => void;
  /** Executes retry using the historical answer configuration snapshot. */
  onDirectRetry?: () => void;
  /** Executes retry using the current temporary draft configuration. */
  onRetry: () => void;
  onRefreshModels: () => void;
  onConfigure: () => void;
  allowedDomains?: string;
  onDomainsChange?: (value: string) => void;
}) {
  const effectiveSearch = webSearch && canSearch;
  const summary = summarizeAnswerTrace(previousAnswerTrace, groups);
  const directRetryEnabled =
    canDirectRetry ?? (canRetry && !summary.recordedModelMissing);
  const previousModelLabel = summary.recordedModelId || "模型未知";

  return (
    <FloatingMenuContent
      aria-label="重新生成"
      aria-labelledby={undefined}
      align="start"
      side="top"
    >
      <DropdownMenuItem
        className={`${CONFIG_MENU_ROW} items-start py-2`}
        disabled={!directRetryEnabled}
        onSelect={onDirectRetry ?? onRetry}
      >
        <RefreshCcw className="mt-0.5 size-4" />
        <span className="grid min-w-0 gap-0.5">
          <span className="font-semibold text-foreground">重新生成</span>
          <span className="text-[11px] leading-relaxed text-muted-foreground">
            沿用上次回答配置
          </span>
          <span className="break-all text-[11px] leading-relaxed text-muted-foreground">
            上次回答：{previousModelLabel}
            {summary.recordedModelMissing ? "（当前不可用）" : ""} ·{" "}
            {summary.searchLabel}
          </span>
        </span>
      </DropdownMenuItem>

      <DropdownMenuSeparator className="mx-1 my-1.5" />

      <DropdownMenuLabel className="px-2.5 pb-1 pt-0.5 text-[11px] font-medium text-muted-foreground">
        本次生成配置
      </DropdownMenuLabel>

      <GenerationConfigRows
        groups={groups}
        providerId={providerId}
        modelId={modelId}
        responseMode={responseMode}
        thinkingMode={thinkingMode}
        thinkingModes={thinkingModes}
        supportsAgent={supportsAgent}
        thinkingRequired={thinkingRequired}
        modelError={modelError}
        onModelChange={onModelChange}
        onModeChange={onModeChange}
        onThinkingChange={onThinkingChange}
        onManageModels={onConfigure}
        onRefreshModels={onRefreshModels}
        keepOpenOnModelSelect
        keepOpenOnModeSelect
      />

      <DropdownMenuCheckboxItem
        className={`${CONFIG_MENU_ROW} pr-2.5 [&>[data-slot=dropdown-menu-checkbox-item-indicator]]:hidden`}
        checked={effectiveSearch}
        disabled={!canSearch}
        onCheckedChange={(checked) => onSearchChange(checked === true)}
        onSelect={keepMenuOpen}
        aria-describedby={!canSearch ? "retry-search-unavailable" : undefined}
      >
        <Globe className="size-4" />
        <span>联网搜索</span>
        <span
          aria-hidden="true"
          className="ml-auto flex items-center gap-2 text-xs text-muted-foreground"
        >
          {effectiveSearch ? "开启" : "关闭"}
          <span
            className={`flex h-4 w-7 items-center rounded-full p-0.5 transition-colors ${
              effectiveSearch ? "bg-foreground" : "bg-muted-foreground/25"
            }`}
          >
            <span
              className={`size-3 rounded-full bg-background transition-transform ${
                effectiveSearch ? "translate-x-3" : "translate-x-0"
              }`}
            />
          </span>
        </span>
      </DropdownMenuCheckboxItem>

      {effectiveSearch && onDomainsChange ? (
        <FloatingSubmenu>
          <FloatingSubmenuTrigger>
            <span className="pl-[26px]">搜索来源</span>
            <span className={CONFIG_MENU_SUMMARY}>
              {allowedDomains.trim() ? "已限定" : "全部"}
            </span>
          </FloatingSubmenuTrigger>
          <FloatingSubmenuContent
            className="w-64 p-3"
            onFocus={(event) => {
              if (event.target === event.currentTarget) {
                event.currentTarget.querySelector("input")?.focus();
              }
            }}
          >
            <label htmlFor="retry-allowed-domains" className="text-xs font-medium">
              限定来源域名（可选）
            </label>
            <Input
              id="retry-allowed-domains"
              className="my-2 h-8 text-xs"
              placeholder="例如：react.dev, python.org"
              value={allowedDomains}
              onChange={(event) => onDomainsChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Escape" && event.key !== "Tab") {
                  event.stopPropagation();
                }
              }}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              用逗号或空格分隔，留空则不限定来源。
            </p>
          </FloatingSubmenuContent>
        </FloatingSubmenu>
      ) : null}

      {!canSearch ? (
        <p
          id="retry-search-unavailable"
          className="px-2.5 pb-1 text-[11px] leading-relaxed text-muted-foreground"
        >
          当前模型与工作区暂无可用搜索能力
        </p>
      ) : null}

      {modelError ? (
        <DropdownMenuItem
          className={`${CONFIG_MENU_ROW} text-destructive`}
          onSelect={(event) => {
            event.preventDefault();
            onRefreshModels();
          }}
        >
          模型载入失败，点击重新载入
        </DropdownMenuItem>
      ) : null}

      <div className="px-1 pt-2">
        <DropdownMenuItem
          data-slot="retry-custom-submit"
          disabled={!canRetry}
          onSelect={onRetry}
          className="retry-custom-submit min-h-9 w-full justify-center rounded-lg bg-foreground px-3 py-2 text-[13px] font-medium text-background"
        >
          使用当前配置重新生成
        </DropdownMenuItem>
      </div>

      <p className="px-2.5 pb-1.5 pt-2 text-[11px] leading-relaxed text-muted-foreground">
        仅本次生效，保留历史回答。
      </p>
    </FloatingMenuContent>
  );
}

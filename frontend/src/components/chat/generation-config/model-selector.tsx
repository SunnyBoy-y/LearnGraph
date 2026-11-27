import { useMemo, useRef, useState } from "react";
import { Image as ImageIcon, Search, Settings } from "lucide-react";

import {
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { ModelSelectorGroup } from "@/lib/generation-config";
import {
  fuzzyModelMatch,
  modelChoiceValue,
  parseModelChoiceValue,
} from "@/lib/model-choices";
import { cn } from "@/lib/utils";
import { CONFIG_MENU_ROW, keepMenuOpen } from "./floating-menu";

export interface ModelSelectorProps {
  groups: readonly ModelSelectorGroup[];
  providerId: string;
  modelId: string;
  onSelect: (providerId: string, modelId: string) => void;
  onManageModels?: () => void;
  onRefreshModels?: () => void;
  modelError?: boolean;
  /** Keep the enclosing menu open after picking a model (e.g. retry draft). */
  keepOpenOnSelect?: boolean;
  searchLabel?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  manageLabel?: string;
  /** Optional controlled search query. */
  search?: string;
  onSearchChange?: (value: string) => void;
  /** Optional max-height class when rendered inline inside a parent menu. */
  listClassName?: string;
  className?: string;
}

export function ModelSelector({
  groups,
  providerId,
  modelId,
  onSelect,
  onManageModels,
  onRefreshModels,
  modelError = false,
  keepOpenOnSelect = false,
  searchLabel = "搜索模型",
  searchPlaceholder = "搜索模型",
  emptyText = "暂无可用模型",
  manageLabel = "管理模型",
  search: controlledSearch,
  onSearchChange,
  listClassName,
  className,
}: ModelSelectorProps) {
  const [internalSearch, setInternalSearch] = useState("");
  const query = controlledSearch ?? internalSearch;
  const setQuery = onSearchChange ?? setInternalSearch;
  const listRef = useRef<HTMLDivElement>(null);

  const trimmedQuery = query.trim();

  const filteredGroups = useMemo(() => {
    if (!trimmedQuery) return groups;
    return groups
      .map((group) => ({
        ...group,
        models: group.models.filter((model) =>
          fuzzyModelMatch(`${model.id} ${group.label}`, trimmedQuery),
        ),
      }))
      .filter((group) => group.models.length > 0);
  }, [groups, trimmedQuery]);

  const selectedValue =
    providerId && modelId ? modelChoiceValue(providerId, modelId) : "";

  const anyLoading = groups.some((group) => group.loading);

  return (
    <div
      className={cn("flex min-h-0 flex-1 flex-col", className)}
      data-slot="model-selector"
    >
      {/* Fixed search header */}
      <div className="shrink-0 px-1 pb-1.5 pt-0.5">
        <div className="relative">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            aria-label={searchLabel}
            className="h-8 rounded-lg bg-background pl-8 text-xs shadow-none"
            placeholder={searchPlaceholder}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onPointerDown={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                const firstItem = listRef.current?.querySelector<HTMLElement>(
                  '[role="menuitemradio"]:not([data-disabled])',
                );
                firstItem?.focus();
                return;
              }
              if (event.key !== "Escape" && event.key !== "Tab") {
                event.stopPropagation();
              }
            }}
          />
        </div>
      </div>

      {/* Independent scrollable grouped model list */}
      <div
        ref={listRef}
        className={cn(
          "min-h-0 flex-1 overflow-y-auto overscroll-contain py-0.5",
          listClassName,
        )}
        data-slot="model-selector-list"
      >
        {filteredGroups.length > 0 ? (
          <DropdownMenuRadioGroup
            value={selectedValue}
            onValueChange={(value) => {
              const choice = parseModelChoiceValue(value);
              if (choice) onSelect(choice.providerId, choice.modelId);
            }}
          >
            {filteredGroups.map((group, groupIndex) => (
              <div
                key={group.id}
                className={
                  groupIndex > 0
                    ? "mt-1.5 border-t border-border/60 pt-1.5"
                    : undefined
                }
              >
                <DropdownMenuLabel className="px-2.5 pb-1 pt-1 text-[11px] font-medium text-muted-foreground">
                  {group.label}
                </DropdownMenuLabel>
                {group.models.length > 0 ? (
                  group.models.map((model) => (
                    <DropdownMenuRadioItem
                      key={`${group.id}:${model.id}`}
                      value={modelChoiceValue(group.id, model.id)}
                      disabled={model.remote === false}
                      onSelect={keepOpenOnSelect ? keepMenuOpen : undefined}
                      className={cn(CONFIG_MENU_ROW, "pr-8")}
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {model.id}
                      </span>
                      {model.capabilities?.supports_image_input ? (
                        <span
                          className="model-capability-badge shrink-0"
                          data-tooltip="支持图片理解"
                          title="支持图片理解"
                          aria-label="支持图片理解"
                        >
                          <ImageIcon aria-hidden="true" className="size-3.5" />
                        </span>
                      ) : null}
                    </DropdownMenuRadioItem>
                  ))
                ) : (
                  <p className="px-2.5 py-1.5 text-xs text-muted-foreground">
                    {group.loading
                      ? "正在载入模型…"
                      : group.failed
                        ? "模型载入失败"
                        : "暂无可用模型"}
                  </p>
                )}
              </div>
            ))}
          </DropdownMenuRadioGroup>
        ) : (
          <p className="px-2.5 py-3 text-center text-xs text-muted-foreground">
            {trimmedQuery
              ? "未找到匹配的模型"
              : anyLoading
                ? "正在载入模型…"
                : groups.length === 0
                  ? "尚未配置可用模型"
                  : emptyText}
          </p>
        )}
      </div>

      {/* Fixed error refresh + manage footer */}
      {(modelError && onRefreshModels) || onManageModels ? (
        <div
          className="shrink-0 pt-1"
          data-slot="model-selector-footer"
        >
          <DropdownMenuSeparator className="mx-1 mb-1 mt-0" />
          {modelError && onRefreshModels ? (
            <DropdownMenuItem
              className={cn(CONFIG_MENU_ROW, "text-destructive")}
              onSelect={(event) => {
                event.preventDefault();
                onRefreshModels();
              }}
            >
              模型载入失败，点击重新载入
            </DropdownMenuItem>
          ) : null}
          {onManageModels ? (
            <DropdownMenuItem
              className={CONFIG_MENU_ROW}
              onSelect={onManageModels}
            >
              <Settings aria-hidden="true" className="size-4" />
              <span>{manageLabel}</span>
            </DropdownMenuItem>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

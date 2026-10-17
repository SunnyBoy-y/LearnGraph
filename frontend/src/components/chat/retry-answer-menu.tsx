import { useCallback, useEffect, useRef, type ComponentProps, type ReactNode } from "react";
import { Bot, Brain, Globe, Layers, RefreshCcw } from "lucide-react";

import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent,
  DropdownMenuItem, DropdownMenuLabel, DropdownMenuPortal,
  DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator,
  DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { modelChoiceValue, parseModelChoiceValue, thinkingLabels } from "@/lib/model-choices";
import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import type { UnknownRecord } from "@/types/common";
import "./retry-answer-menu.css";
import { Input } from "@/components/ui/input";

const surface = "retry-answer-menu rounded-[12px] border border-border bg-popover p-1.5 text-popover-foreground shadow-[0_8px_28px_rgba(0,0,0,0.09)] ring-0";
const row = "min-h-9 gap-2.5 rounded-md px-2.5 text-[13px] focus:bg-muted data-[highlighted]:bg-muted data-[state=open]:bg-muted data-disabled:opacity-50 [&_svg]:text-muted-foreground";
const summary = "ml-auto max-w-28 truncate text-xs text-muted-foreground";
const keepOpen = (event: Event) => event.preventDefault();

function RetrySubTrigger(props: ComponentProps<typeof DropdownMenuSubTrigger>) {
  return <DropdownMenuSubTrigger {...props} onPointerLeave={(event) => {
    props.onPointerLeave?.(event);
    const submenuId = event.currentTarget.getAttribute("aria-controls");
    const submenu = submenuId ? document.getElementById(submenuId) : null;
    // An overlapping/flipped submenu can sit under the pointer immediately.
    // Keep focus there instead of letting Radix refocus and close its parent.
    if (event.relatedTarget instanceof Node && submenu?.contains(event.relatedTarget)) event.preventDefault();
  }} />;
}

// Radix flips when one side has room. If neither side fits (e.g. a phone),
// let the submenu overlap the parent just enough to stay inside the viewport.
function RetrySubContent(props: ComponentProps<typeof DropdownMenuSubContent>) {
  const contentRef = useRef<HTMLDivElement>(null);
  const constrain = useCallback(() => {
    const content = contentRef.current;
    if (!content) return;
    content.style.translate = "none";
    const rect = content.getBoundingClientRect();
    const shift = Math.max(12 - rect.left, Math.min(0, document.documentElement.clientWidth - 12 - rect.right));
    content.style.translate = `${shift}px 0`;
  }, []);
  const setContent = useCallback((content: HTMLDivElement | null) => {
    contentRef.current = content;
    if (!content?.parentElement) return;
    // Re-clamp after Floating UI places or flips its wrapper, including resize.
    const observer = new MutationObserver(constrain);
    observer.observe(content.parentElement, { attributes: true, attributeFilter: ["style"] });
    const frame = requestAnimationFrame(constrain);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); contentRef.current = null; };
  }, [constrain]);
  useEffect(() => {
    let frame = 0;
    const onResize = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(constrain); };
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); cancelAnimationFrame(frame); };
  }, [constrain]);
  return <DropdownMenuSubContent {...props} ref={setContent} className={`${props.className ?? ""} data-open:animate-none data-closed:animate-none`} />;
}

export function RetryAnswerMenu({ children, content, open, onOpenChange }: {
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

export type RetryModelGroup = {
  id: string;
  label: string;
  models: { id: string; remote: boolean }[];
  loading?: boolean;
  failed?: boolean;
};

export function RetryMenuSettings({
  groups, providerId, modelId, responseMode, thinkingMode, thinkingModes,
  supportsAgent, webSearch, canSearch, canRetry, modelError, onModelChange,
  onModeChange, onThinkingChange, onSearchChange, onRetry, onRefreshModels, onConfigure,
  allowedDomains = "", onDomainsChange,
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
  webSearch: boolean;
  canSearch: boolean;
  canRetry: boolean;
  modelError: boolean;
  onModelChange: (providerId: string, modelId: string) => void;
  onModeChange: (mode: ResponseMode) => void;
  onThinkingChange: (mode: ThinkingMode) => void;
  onSearchChange: (enabled: boolean) => void;
  onRetry: () => void;
  onRefreshModels: () => void;
  onConfigure: () => void;
  allowedDomains?: string;
  onDomainsChange?: (value: string) => void;
}) {
  // Compare effective request settings, so reverting a choice restores the
  // historical summary and inactive search/thinking options do not mark edits.
  const effectiveSearch = webSearch && canSearch;
  const effectiveThinking = responseMode === "fast" ? "off" : thinkingMode;
  const domains = effectiveSearch
    ? [...new Set(allowedDomains.split(/[\s,，]+/u).map((domain) => domain.trim()).filter(Boolean))].sort()
    : [];
  const settingsKey = JSON.stringify([providerId, modelId, responseMode, effectiveThinking, effectiveSearch, domains]);
  const initialSettingsKey = useRef(settingsKey);
  const settingsChanged = settingsKey !== initialSettingsKey.current;
  const previousModel = typeof previousAnswerTrace.model_id === "string"
    ? previousAnswerTrace.model_id.trim() : "";
  // Older answers may only record a search route or an external provider.
  const route = previousAnswerTrace.search_route;
  const previousSearch = typeof previousAnswerTrace.web_search === "boolean"
    ? previousAnswerTrace.web_search ? "联网已开启" : "联网已关闭"
    : ["model_native", "external", "local"].includes(String(route)) ||
      (typeof previousAnswerTrace.search_provider_id === "string" && Boolean(previousAnswerTrace.search_provider_id.trim()))
      ? "联网已开启"
      : route === "disabled" ? "联网已关闭" : "联网状态未知";
  return (
    <DropdownMenuContent
      aria-label="重新生成回答" aria-labelledby={undefined} align="start" side="top" sideOffset={8}
      collisionPadding={12} className={`${surface} w-[280px] max-w-[calc(100vw-24px)]`}
    >
      <DropdownMenuLabel className="px-2.5 pb-1.5 pt-2 text-[11px]">重新生成回答</DropdownMenuLabel>
      <DropdownMenuItem className={`${row} py-2`} disabled={!canRetry} onSelect={onRetry}>
        <RefreshCcw className="size-4" />
        <span className="grid min-w-0 gap-0.5">
          <span className="font-medium">{settingsChanged ? "更改后重试" : "直接重试"}</span>
          <span className="break-all text-[11px] leading-relaxed text-muted-foreground">
            {settingsChanged
              ? `本次重试：${modelId || "未选择模型"}`
              : `上次回答：${previousModel && previousModel !== "unknown" ? previousModel : "模型未知"}`}
          </span>
          <span className="text-[11px] text-muted-foreground">
            {settingsChanged
              ? `${responseMode === "fast" ? "极速" : "智能体"} · 思考${thinkingLabels[effectiveThinking]} · ${effectiveSearch ? "联网已开启" : "联网已关闭"}`
              : previousSearch}
          </span>
          {settingsChanged && effectiveSearch && <span className="break-all text-[11px] leading-relaxed text-muted-foreground">
            搜索来源：{domains.length ? domains.join("、") : "全部"}
          </span>}
        </span>
      </DropdownMenuItem>
      <DropdownMenuSeparator className="mx-1 my-1.5" />
      <DropdownMenuLabel className="px-2.5 text-[11px]">重试设置 · 仅本次生效</DropdownMenuLabel>
      <DropdownMenuSub>
        <RetrySubTrigger className={row}>
          <Layers className="size-4" /><span>使用模型</span><span className={summary}>{modelId || "选择模型"}</span>
        </RetrySubTrigger>
        <DropdownMenuPortal>
          <RetrySubContent collisionPadding={12} sideOffset={6} className={`${surface} max-h-[min(60vh,var(--radix-dropdown-menu-content-available-height))] w-64 max-w-[calc(100vw-24px)] overflow-y-auto`}>
            <DropdownMenuRadioGroup value={modelChoiceValue(providerId, modelId)} onValueChange={(value) => {
              const choice = parseModelChoiceValue(value);
              if (choice) onModelChange(choice.providerId, choice.modelId);
            }}>
              {groups.map((group) => (
                <div key={group.id}>
                  <DropdownMenuLabel className="px-2.5 pt-2">{group.label}</DropdownMenuLabel>
                  {group.models.map((model) => (
                    <DropdownMenuRadioItem className={`${row} pr-8`} key={model.id} value={modelChoiceValue(group.id, model.id)} disabled={!model.remote} onSelect={keepOpen}>
                      <span className="truncate">{model.id}</span>
                    </DropdownMenuRadioItem>
                  ))}
                  {!group.models.length && <DropdownMenuLabel className="px-2.5 font-normal">{group.loading ? "正在载入模型…" : group.failed ? "模型载入失败" : "暂无可用模型"}</DropdownMenuLabel>}
                </div>
              ))}
            </DropdownMenuRadioGroup>
            {!groups.length && <DropdownMenuLabel className="px-2.5">尚未配置可用模型</DropdownMenuLabel>}
            <DropdownMenuSeparator className="mx-1" />
            <DropdownMenuItem className={row} onSelect={onConfigure}>管理已配置模型…</DropdownMenuItem>
          </RetrySubContent>
        </DropdownMenuPortal>
      </DropdownMenuSub>
      <DropdownMenuSub>
        <RetrySubTrigger className={row}>
          <Bot className="size-4" /><span>响应模式</span><span className={summary}>{responseMode === "fast" ? "极速" : "智能体"}</span>
        </RetrySubTrigger>
        <DropdownMenuPortal>
          <RetrySubContent collisionPadding={12} sideOffset={6} className={`${surface} w-52`}>
            <DropdownMenuRadioGroup value={responseMode} onValueChange={(value) => onModeChange(value as ResponseMode)}>
              <DropdownMenuRadioItem className={row} value="fast" onSelect={keepOpen}>极速</DropdownMenuRadioItem>
              <DropdownMenuRadioItem className={row} value="agentic" disabled={!supportsAgent} onSelect={keepOpen}>智能体</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
            {!supportsAgent && <p className="px-2.5 py-1 text-[11px] text-muted-foreground">当前模型不支持智能体工具</p>}
          </RetrySubContent>
        </DropdownMenuPortal>
      </DropdownMenuSub>
      <DropdownMenuSub>
        <RetrySubTrigger className={row} disabled={responseMode === "fast" || !thinkingModes.length}>
          <Brain className="size-4" /><span>思考强度</span><span className={summary}>{thinkingLabels[thinkingMode]}</span>
        </RetrySubTrigger>
        <DropdownMenuPortal>
          <RetrySubContent collisionPadding={12} sideOffset={6} className={`${surface} w-40`}>
            <DropdownMenuRadioGroup value={thinkingMode} onValueChange={(value) => onThinkingChange(value as ThinkingMode)}>
              {thinkingModes.map((mode) => <DropdownMenuRadioItem key={mode} className={row} value={mode} onSelect={keepOpen}>{thinkingLabels[mode]}</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
          </RetrySubContent>
        </DropdownMenuPortal>
      </DropdownMenuSub>
      <DropdownMenuSeparator className="mx-1 my-1.5" />
      <DropdownMenuCheckboxItem
        className={`${row} pr-2.5 [&>[data-slot=dropdown-menu-checkbox-item-indicator]]:hidden`}
        checked={webSearch && canSearch} disabled={!canSearch} onCheckedChange={(checked) => onSearchChange(checked === true)} onSelect={keepOpen}
        aria-describedby={!canSearch ? "retry-search-unavailable" : undefined}
      >
        <Globe className="size-4" /><span>联网搜索</span>
        <span aria-hidden="true" className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          {webSearch && canSearch ? "开启" : "关闭"}
          <span className={`flex h-4 w-7 items-center rounded-full p-0.5 ${webSearch && canSearch ? "bg-foreground" : "bg-muted-foreground/25"}`}>
            <span className={`size-3 rounded-full bg-background transition-transform ${webSearch && canSearch ? "translate-x-3" : "translate-x-0"}`} />
          </span>
        </span>
      </DropdownMenuCheckboxItem>
      {webSearch && canSearch && onDomainsChange && <DropdownMenuSub>
        <RetrySubTrigger className={row}>
          <span className="pl-[26px]">搜索来源</span><span className={summary}>{allowedDomains.trim() ? "已限定" : "全部"}</span>
        </RetrySubTrigger>
        <DropdownMenuPortal>
          <RetrySubContent
            collisionPadding={12} sideOffset={6} className={`${surface} w-64 max-w-[calc(100vw-24px)] p-3`}
            onFocus={(event) => { if (event.target === event.currentTarget) event.currentTarget.querySelector("input")?.focus(); }}
          >
            <label htmlFor="retry-allowed-domains" className="text-xs font-medium">限定来源域名（可选）</label>
            <Input id="retry-allowed-domains" className="my-2 h-8 text-xs" placeholder="例如：react.dev, python.org" value={allowedDomains} onChange={(event) => onDomainsChange(event.target.value)}
              onKeyDown={(event) => { if (event.key !== "Escape" && event.key !== "Tab") event.stopPropagation(); }} />
            <p className="text-[11px] leading-relaxed text-muted-foreground">用逗号或空格分隔，留空则不限定来源。</p>
          </RetrySubContent>
        </DropdownMenuPortal>
      </DropdownMenuSub>}
      {!canSearch && <p id="retry-search-unavailable" className="px-2.5 pb-1 text-[11px] leading-relaxed text-muted-foreground">当前模型与工作区暂无可用搜索能力</p>}
      {modelError && <DropdownMenuItem className={`${row} text-destructive`} onSelect={(event) => { event.preventDefault(); onRefreshModels(); }}>模型载入失败，点击重新载入</DropdownMenuItem>}
      <p className="px-2.5 pb-2 pt-2.5 text-[11px] leading-relaxed text-muted-foreground">重试后保留原回答及历史版本</p>
    </DropdownMenuContent>
  );
}

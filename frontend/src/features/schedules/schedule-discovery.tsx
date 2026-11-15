import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowUp, ChevronDown, Plus, RefreshCw, Sparkles } from "lucide-react";
import { DynamicIcon, iconNames, type IconName } from "lucide-react/dynamic";
import { useQuery } from "@tanstack/react-query";
import { generateScheduleSuggestions } from "@/api/schedules";
import { workspaceQueryKey } from "@/lib/query-keys";
import { toast } from "@/lib/notifications";
import { createSession } from "@/api/sessions";
import { PromptInput, PromptInputSubmit, PromptInputTextarea } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { setSessionComposerPrefs } from "@/lib/session-composer-prefs";

const availableIcons = new Set<string>(iconNames);
function SuggestionIcon({ name }: { name: string }) {
  return availableIcons.has(name)
    ? <DynamicIcon name={name as IconName} size={24} fallback={() => <Sparkles className="size-6" />} />
    : <Sparkles className="size-6" />;
}

export function ScheduleDiscovery({ workspaceId, onManualCreate, children }: { workspaceId: string; onManualCreate: () => void; children?: ReactNode }) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const [expanded, setExpanded] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); }, []);
  const suggestions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "schedules", "suggestions"),
    queryFn: ({ signal }) => generateScheduleSuggestions({ signal, headers: { "X-Workspace-ID": workspaceId } }),
    staleTime: 5 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const notifiedAt = useRef(0);
  const { isError, errorUpdatedAt, refetch } = suggestions;
  useEffect(() => {
    if (!isError || notifiedAt.current === errorUpdatedAt) return;
    notifiedAt.current = errorUpdatedAt;
    toast.error("任务推荐生成失败", { action: { label: "重试", onClick: () => { void refetch(); } } });
  }, [isError, errorUpdatedAt, refetch]);

  const submit = async (text: string) => {
    const prompt = text.trim();
    if (!prompt || request.current) return false;
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    setError("");
    try {
      // Use the normal conversation runtime, including its permissions, model
      // selection, one-shot pendingPrompt handoff and persisted task tools.
      const session = await createSession({ title: "安排定时任务" }, {
        signal: controller.signal, headers: { "X-Workspace-ID": workspaceId },
      });
      if (controller.signal.aborted) return false;
      setSessionComposerPrefs(session.id, { responseMode: "agentic", generationMode: "text" });
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      void navigate(`/w/${workspaceId}/chat/${session.id}`, {
        state: { pendingPrompt: `请创建并保存以下定时任务，缺少必要信息时先向我确认。时区：${timezone}。\n\n${prompt}` },
      });
      return true;
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法开始安排任务，请重试");
      return false;
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setPending(false);
      }
    }
  };

  return <>
    <div className="schedule-discovery">
      <PromptInput className="schedule-composer" maxFiles={0}
        onError={() => setError("请在任务对话中添加附件")}
        onSubmit={({ text }) => submit(text)}>
        <Button type="button" variant="ghost" size="icon" className="schedule-composer__add" aria-label="新建任务" title="手动设置任务" disabled={pending} onClick={onManualCreate}>
          <Plus className="size-5" />
        </Button>
        <PromptInputTextarea ref={textarea} aria-label="安排任务" placeholder="安排任务" rows={1} maxLength={32000}
          value={draft} disabled={pending} onChange={event => { setDraft(event.target.value); setError(""); }} />
        <PromptInputSubmit className="schedule-composer__send" aria-label="发送任务" disabled={pending || !draft.trim()} status={pending ? "submitted" : "ready"}>
          {pending ? undefined : <ArrowUp className="size-5" />}
        </PromptInputSubmit>
      </PromptInput>
      {error ? <p role="alert" className="mt-3 text-sm text-destructive">{error}</p> : null}
    </div>
    {children}
    <section className="schedule-suggestions" aria-label="推荐任务">
      <div className="flex items-center justify-between">
        <Button type="button" variant="ghost" className="schedule-section-toggle" aria-expanded={expanded} aria-controls="schedule-suggestions" onClick={() => setExpanded(value => !value)}>
          推荐<ChevronDown className={`size-4 transition-transform ${expanded ? "" : "-rotate-90"}`} />
        </Button>
        <Button type="button" variant="ghost" size="icon" aria-label="刷新任务推荐" disabled={suggestions.isFetching} onClick={() => { void suggestions.refetch(); }}>
          <RefreshCw className={`size-4 ${suggestions.isFetching ? "animate-spin motion-reduce:animate-none" : ""}`} />
        </Button>
      </div>
      {expanded ? <ul id="schedule-suggestions" className="schedule-suggestions__list" aria-busy={suggestions.isFetching}>
        {suggestions.isPending ? Array.from({ length: 3 }, (_, index) => <li key={index} aria-hidden="true" className="schedule-suggestion animate-pulse motion-reduce:animate-none">
          <span className="size-6 rounded-md bg-muted" /><span className="space-y-3"><span className="block h-4 w-1/3 rounded bg-muted" /><span className="block h-4 w-4/5 rounded bg-muted" /></span>
        </li>) : null}
        {(suggestions.data?.suggestions ?? []).map(suggestion => <li key={suggestion.name}>
          <button type="button" className="schedule-suggestion" disabled={pending} aria-label={`安排${suggestion.name}`} onClick={() => {
            setDraft(suggestion.prompt);
            setError("");
            textarea.current?.focus();
          }}>
            <span className="schedule-suggestion__icon" aria-hidden="true"><SuggestionIcon name={suggestion.icon} /></span>
            <span className="min-w-0"><span className="schedule-suggestion__name">{suggestion.name}</span><span className="schedule-suggestion__description">{suggestion.prompt}</span></span>
            <Plus className="schedule-suggestion__add size-5" aria-hidden="true" />
          </button>
        </li>)}
      </ul> : null}
    </section>
  </>;
}

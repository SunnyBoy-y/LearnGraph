import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowUp, ChevronDown, Plus, RefreshCw } from "lucide-react";
import { TaskRecommendationCard } from "./task-recommendation-card";
import { useQuery } from "@tanstack/react-query";
import { generateScheduleSuggestions } from "@/api/schedules";
import { workspaceQueryKey } from "@/lib/query-keys";
import { toast } from "@/lib/notifications";
import { createSession } from "@/api/sessions";
import { PromptInput, PromptInputSubmit, PromptInputTextarea } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { setSessionComposerPrefs } from "@/lib/session-composer-prefs";

export function ScheduleDiscovery({ workspaceId, onManualCreate, children }: { workspaceId: string; onManualCreate: (opener: HTMLButtonElement) => void; children?: ReactNode }) {
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
        <PromptInputTextarea ref={textarea} aria-label="安排任务" placeholder="你希望我定期做什么？" rows={1} maxLength={32000}
          value={draft} disabled={pending} onChange={event => { setDraft(event.target.value); setError(""); }} />
        <div className="schedule-composer__toolbar">
          <Button type="button" variant="ghost" size="icon" className="schedule-composer__add" aria-label="新建任务" title="手动设置任务" disabled={pending} onClick={event => onManualCreate(event.currentTarget)}>
            <Plus className="size-5" />
          </Button>
          <PromptInputSubmit className="schedule-composer__send" aria-label="发送任务" disabled={pending || !draft.trim()} status={pending ? "submitted" : "ready"}>
            {pending ? undefined : <ArrowUp className="size-5" />}
          </PromptInputSubmit>
        </div>
      </PromptInput>
      {error ? <p role="alert" className="mt-3 text-sm text-destructive">{error}</p> : null}
    </div>
    {children}
    <section className="schedule-suggestions" aria-label="推荐任务">
      <div className="schedule-suggestions__header">
        <h2><button type="button" className="schedule-section-toggle" aria-expanded={expanded} aria-controls="schedule-suggestions" onClick={() => setExpanded(value => !value)}>
          试试这些任务<ChevronDown aria-hidden="true" className={`size-4 transition-transform ${expanded ? "" : "-rotate-90"}`} />
        </button></h2>
        {expanded ? <Button type="button" variant="ghost" size="sm" className="schedule-suggestions__refresh" title="刷新任务推荐" disabled={suggestions.isFetching} onClick={() => { void suggestions.refetch(); }}>
          换一组<RefreshCw aria-hidden="true" className={`size-4 ${suggestions.isFetching ? "animate-spin motion-reduce:animate-none" : ""}`} />
        </Button> : null}
      </div>
      <ul id="schedule-suggestions" hidden={!expanded} className="schedule-suggestions__list" aria-busy={suggestions.isFetching}>
        {suggestions.isPending ? Array.from({ length: 3 }, (_, index) => <li key={index} aria-hidden="true" className="task-recommendation-card task-recommendation-card--loading animate-pulse motion-reduce:animate-none">
          <span className="block size-5 rounded bg-muted" /><span className="block h-4 w-2/3 rounded bg-muted" /><span className="block h-4 w-1/3 rounded bg-muted" />
        </li>) : null}
        {(suggestions.data?.suggestions ?? []).map(suggestion => <TaskRecommendationCard key={suggestion.id ?? suggestion.name} suggestion={suggestion} disabled={pending} onAdd={() => {
          setDraft(suggestion.prompt);
          setError("");
          textarea.current?.focus();
        }} />)}
        {suggestions.isSuccess && !suggestions.data.suggestions.length ? <li className="schedule-empty">暂无推荐</li> : null}
      </ul>
    </section>
  </>;
}

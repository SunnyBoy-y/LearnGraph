import { useEffect, useState } from "react";
import { apiClient } from "@/api/client";
import { authStore } from "@/api/auth-store";
import { Button } from "@/components/ui/button";

type Snapshot = { status: string; spec_json: { agent_session_id?: string } };
type Control = { status: string; agent_session_id: string; controller: string };

type ChildTask = Snapshot & { task_id: string };
export function ManagedChildSessionControls({ sessionId, parentId }: { sessionId: string; parentId: string }) {
  const [task, setTask] = useState<ChildTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    const refresh = () => apiClient.get<{ tasks: ChildTask[] }>(`/sandbox/agent-tasks?chat_session_id=${encodeURIComponent(parentId)}&limit=100`, { signal: abort.signal })
      .then(result => setTask(result.tasks.find(item => item.spec_json.agent_session_id === sessionId) ?? null)).catch(() => {});
    void refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [sessionId, parentId]);
  const control = async (operation: "pause" | "resume" | "takeover") => {
    if (!task) return;
    setBusy(true);
    try {
      const result = await apiClient.post<Control>(`/sandbox/agent-tasks/${encodeURIComponent(task.task_id)}/execution/${operation}`, {});
      setTask({ ...task, status: result.status.toUpperCase() });
      setNotice(operation === "resume" ? "已交回 Agent，继续原任务。" : result.controller === "user" ? "已交给你，可在下方继续对话。" : "已请求暂停，正在保存检查点。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "操作失败，请重试。"); }
    finally { setBusy(false); }
  };
  if (!task) return null;
  const active = ["RUNNING", "STARTING", "QUEUED"].includes(task.status);
  const labels: Record<string, string> = { RUNNING: "执行中", STARTING: "准备中", QUEUED: "排队中", PAUSED: "已暂停", SUCCEEDED: "已完成", PARTIAL: "部分完成", FAILED: "执行失败" };
  return <section aria-label="子会话执行控制" className="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs">
    <span>Agent 子会话 · {labels[task.status] ?? task.status}</span>
    {active ? <Button size="sm" variant="outline" disabled={busy} onClick={() => void control("pause")}>暂停执行</Button> : null}
    {task.status === "PAUSED" ? <Button size="sm" variant="outline" disabled={busy} onClick={() => void control("resume")}>交回 Agent 继续</Button> : null}
    <Button size="sm" variant="outline" disabled={busy} onClick={() => void control("takeover")}>接管对话</Button>
    {notice ? <span role="status">{notice}</span> : null}
  </section>;
}

export function ManagedAgentControls({ taskId, active }: { taskId: string; active: boolean }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [takingOver, setTakingOver] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    const refresh = () => apiClient.get<Snapshot>(`/sandbox/agent-tasks/${encodeURIComponent(taskId)}`, { signal: abort.signal })
      .then(setSnapshot).catch(() => {});
    void refresh();
    const timer = active || takingOver ? window.setInterval(refresh, 2000) : undefined;
    return () => { abort.abort(); if (timer) window.clearInterval(timer); };
  }, [taskId, active, takingOver]);
  const child = snapshot?.spec_json.agent_session_id;
  const workspace = authStore.getWorkspaceId();
  const url = child && workspace ? `/w/${encodeURIComponent(workspace)}/chat/${encodeURIComponent(child)}` : undefined;
  const takeover = async () => {
    setBusy(true);
    setNotice("");
    try {
      const result = await apiClient.post<Control>(`/sandbox/agent-tasks/${encodeURIComponent(taskId)}/execution/takeover`, {});
      setTakingOver(result.controller !== "user");
      setNotice(result.controller === "user" ? "已交给你接管，可打开子会话继续操作。" : "正在保存检查点，暂停后可打开子会话接管。");
      setSnapshot({ status: result.status, spec_json: { agent_session_id: result.agent_session_id } });
    } catch (error) { setNotice(error instanceof Error ? error.message : "接管失败，请重试。"); }
    finally { setBusy(false); }
  };
  return <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="子会话管理">
    {url ? <Button asChild size="sm" variant="outline"><a href={url} target="_blank" rel="noopener noreferrer">查看执行过程</a></Button> : null}
    {child ? <Button type="button" size="sm" variant="outline" disabled={busy || (takingOver && snapshot?.status !== "PAUSED")} onClick={() => void takeover()}>
      {takingOver && snapshot?.status !== "PAUSED" ? "正在保存检查点…" : "接管子会话"}
    </Button> : null}
    {notice ? <p role="status" className="w-full text-xs text-muted-foreground">{takingOver && snapshot?.status === "PAUSED" ? "检查点已保存，可打开子会话继续操作。" : notice}</p> : null}
  </div>;
}

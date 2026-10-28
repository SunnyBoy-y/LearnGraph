import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "@/api/client";
import { acknowledgeVoiceScene } from "@/api/voice";
import { VoiceTaskCanvas } from "./voice-task-canvas";
import { emptyVoiceTaskState, mergeVoiceTaskResults, mergeVoiceTaskSnapshots, mergeVoiceScenePresentation, type VoiceTask } from "./voice-tasks";

type Wire = Record<string, unknown>;
const record = (value: unknown): Wire => value && typeof value === "object" ? value as Wire : {};

/** Durable parent tasks remain visible when the audio peer lives in another client. */
export function useManagedSessionTasks(sessionId: string | null) {
  const [tasks, setTasks] = useState<VoiceTask[]>([]);
  const [owners, setOwners] = useState<Record<string, { voice: string; epoch: number }>>({});
  const [loadedSessionId, setLoadedSessionId] = useState<string | null>(null);
  const receipts = useRef(new Map<string, { signature: string; at: number }>());
  const pendingReceipts = useRef(new Map<string, Promise<unknown>>());
  const current = useRef({ tasks, owners });
  current.current = loadedSessionId === sessionId ? { tasks, owners } : { tasks: [], owners: {} };
  useEffect(() => {
    if (!sessionId) return;
    const abort = new AbortController();
    const turnCache = new Map<string, { at:number; turns:Map<string, {turn_id:string; user_text?:string; started_at?:string}> }>();
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await apiClient.get<{ tasks: Wire[] }>(`/sandbox/agent-tasks?chat_session_id=${encodeURIComponent(sessionId)}&limit=50`, { signal: abort.signal });
        let state = mergeVoiceTaskSnapshots(emptyVoiceTaskState(), result.tasks);
        const nextOwners: typeof owners = {};
        const voiceIds = new Set(result.tasks.map(task => {
          const spec = record(task.spec_json), input = record(spec.input_snapshot);
          return record(spec.association).voice_session_id ?? input.voice_session_id ?? record(input.context).voice_session_id;
        }).filter((id): id is string => typeof id === "string" && Boolean(id)));
        for (const voice of voiceIds) {
          const snapshot = await apiClient.get<{ session_epoch: number; tasks: Wire[]; results: Wire[]; scene_presentation?: Wire }>(`/voice/sessions/${encodeURIComponent(voice)}`, { signal: abort.signal });
          state = mergeVoiceTaskResults(mergeVoiceTaskSnapshots(state, snapshot.tasks), snapshot.results);
          const owned = new Set(snapshot.tasks.map(task => String(task.task_id ?? task.subagent_id)));
          const focused = new Map(mergeVoiceScenePresentation(state, snapshot.scene_presentation).tasks.map(task => [task.taskId, task]));
          state = { ...state, tasks: state.tasks.map(task => owned.has(task.taskId) ? focused.get(task.taskId)! : task) };
          for (const task of snapshot.tasks) nextOwners[String(task.task_id ?? task.subagent_id)] = { voice, epoch: snapshot.session_epoch };
          // Legacy voice turns may exist in the journal even when their chat
          // message is absent. Recover their real question/time for placement.
          let cached = turnCache.get(voice);
          if ((!cached || state.tasks.some(task => owned.has(task.taskId) && task.triggerTurnId && !cached!.turns.has(task.triggerTurnId))) && (!cached || Date.now()-cached.at>10_000)) {
            const transcript = await apiClient.get<{ turns?: Array<{turn_id:string; user_text?:string; started_at?:string}> }>(`/voice/sessions/${encodeURIComponent(voice)}/transcript?limit=500`, { signal: abort.signal }).catch(() => null);
            cached = { at:Date.now(), turns:new Map((transcript?.turns ?? []).map(turn => [turn.turn_id, turn])) };
            turnCache.set(voice,cached);
          }
          const turns = cached?.turns ?? new Map();
          state = { ...state, tasks: state.tasks.map(task => {
            const turn = owned.has(task.taskId) && task.triggerTurnId ? turns.get(task.triggerTurnId) : undefined;
            return turn ? { ...task, triggerUserText: turn.user_text, triggerCreatedAt: turn.started_at } : task;
          }) };
        }
        if (!abort.signal.aborted) { setTasks(state.tasks); setOwners(nextOwners); setLoadedSessionId(sessionId); }
      } catch { /* The ordinary conversation remains usable while details retry. */ }
      finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 1000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [sessionId]);
  const rendered = useCallback(async (taskId: string, anchors: string[], status: "rendered" | "failed" = "rendered") => {
    const task = current.current.tasks.find(item => item.taskId === taskId), owner = current.current.owners[taskId];
    if (!task?.resultId || !task.resultVersion || !owner) return;
    const key = `${owner.voice}:${owner.epoch}:${task.resultId}:${task.resultVersion}`;
    const signature = `${status}:${anchors.join(",")}`;
    const previous = receipts.current.get(key);
    if (previous?.signature === signature && Date.now() - previous.at < 8000) return;
    receipts.current.set(key, { signature, at: Date.now() });
    const request = (pendingReceipts.current.get(key) ?? Promise.resolve()).catch(() => {}).then(() =>
      acknowledgeVoiceScene(owner.voice, task.resultId!, { requirement_version: task.requirementVersion, result_version: task.resultVersion!, session_epoch: owner.epoch, status, anchors }));
    pendingReceipts.current.set(key, request);
    try { await request; }
    catch { if (receipts.current.get(key)?.signature === signature) receipts.current.delete(key); }
  }, []);
  return { tasks: loadedSessionId === sessionId ? tasks : [], rendered };
}

export function ManagedSessionTasks({ sessionId }: { sessionId: string }) {
  const { tasks, rendered } = useManagedSessionTasks(sessionId);
  if (!tasks.length) return null;
  return <VoiceTaskCanvas tasks={tasks} onRender={rendered} label="会话后台任务" />;
}

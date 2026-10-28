import type { Message } from "@/types/sessions";
import { emptyVoiceTaskState, mergeVoiceTaskSnapshots, type VoiceTask } from "./voice-tasks";

export type ConversationTaskRow =
  | { kind: "message"; key: string; message: Message; tasks: VoiceTask[] }
  | { kind: "task-round"; key: string; tasks: VoiceTask[]; sourceText?: string };

const strings = (...values: unknown[]): string[] => values.flatMap(value => Array.isArray(value) ? strings(...value) : typeof value === "string" && value ? [value] : []);
const time = (value?: string) => {
  const utc = value && /^\d{4}-\d\d-\d\dT/.test(value) && !/(Z|[+-]\d\d:\d\d)$/i.test(value) ? `${value}Z` : value;
  const parsed = Date.parse(utc ?? ""); return Number.isFinite(parsed) ? parsed : null;
};

export function mergeConversationTasks(durable: VoiceTask[], live: VoiceTask[]): VoiceTask[] {
  return mergeVoiceTaskSnapshots({ ...emptyVoiceTaskState(), tasks: durable }, live.map(task => ({...task}))).tasks;
}

/** Placement uses creation identity, never completion, focus or playback order. */
export function conversationTaskRows(messages: Message[], tasks: VoiceTask[]): ConversationTaskRow[] {
  const rows: ConversationTaskRow[] = messages.map(message => ({kind:"message", key:message.id, message, tasks:[]}));
  const detached = new Map<string, { tasks:VoiceTask[]; sourceText?:string; at:number | null }>();
  const seen = new Set<string>();
  for (const task of tasks) {
    if (seen.has(task.taskId)) continue;
    seen.add(task.taskId);
    let index = task.parentMessageId ? messages.findIndex(m => strings(m.id, m.provider_trace?.optimistic_persisted_message_id, m.provider_trace?.voice_merged_message_ids).includes(task.parentMessageId!)) : -1;
    if (index < 0 && task.triggerTurnId) index = messages.findIndex(m => strings(m.provider_trace?.turn_id, m.provider_trace?.voice_turn_id, m.provider_trace?.voice_merged_turn_ids).includes(task.triggerTurnId!));
    // Old chat tasks may predate explicit associations. Their creation time
    // places them in the then-current turn; late completion never moves them.
    const created = time(task.triggerCreatedAt ?? task.createdAt);
    if (index < 0 && !task.triggerTurnId && !task.parentMessageId && created !== null) {
      for (let i=0; i<messages.length; i++) {
        const at = time(messages[i].created_at);
        if (messages[i].role === "user" && at !== null && at <= created) index=i;
      }
    }
    if (index >= 0) {
      // Keep output with the answer to that question, including streaming and
      // interrupted answers, before the next user starts another turn.
      while (index+1<messages.length && messages[index+1].role !== "user") index++;
      const row=rows[index];
      if (row.kind === "message") row.tasks.push(task);
    } else {
      const key=task.triggerTurnId ?? task.parentMessageId ?? task.taskId;
      const group=detached.get(key) ?? {tasks:[],sourceText:task.triggerUserText,at:created};
      group.tasks.push(task);detached.set(key,group);
    }
  }
  // A missing/unloaded parent is its own dated turn, not a permanent footer.
  for (const [key,group] of [...detached].sort((a,b)=>(a[1].at ?? -Infinity)-(b[1].at ?? -Infinity))) {
    const row:ConversationTaskRow={kind:"task-round",key:`task-round:${key}`,tasks:group.tasks,sourceText:group.sourceText};
    const index=rows.findIndex(candidate=>candidate.kind === "message" && (group.at === null || (time(candidate.message.created_at) ?? Infinity)>group.at));
    if (index<0) rows.push(row); else rows.splice(index,0,row);
  }
  return rows;
}

import { describe, expect, it } from "vitest";
import type { Message } from "@/types/sessions";
import { conversationTaskRows, mergeConversationTasks } from "./task-rounds";
import { emptyVoiceTaskState, mergeVoiceTaskSnapshots, reduceVoiceTaskEvent, type VoiceTask } from "./voice-tasks";
import { mergeAdjacentVoiceUserMessages } from "./voice-rows";

const at=(minute:number)=>`2026-10-21T10:${String(minute).padStart(2,"0")}:00Z`;
const message=(id:string,role:"user"|"assistant",minute:number,turn?:string):Message=>({
 id,role,workspace_id:"ws",session_id:"chat",parent_message_id:null,version:1,status:"completed",content:id,parts:[],
 provider_trace:turn?{voice:true,voice_turn_id:turn}:{},created_at:at(minute),
});
const task=(patch:Record<string,unknown>={}):VoiceTask=>mergeVoiceTaskSnapshots(emptyVoiceTaskState(),[{
 task_id:"task",status:"SUCCEEDED",title:"表格",created_at:at(1),updated_at:at(30),trigger_turn_id:"turn-1",...patch,
}]).tasks[0];
const conversation=[message("u1","user",0,"turn-1"),message("a1","assistant",2,"turn-1"),message("u2","user",5,"turn-2"),message("a2","assistant",6,"turn-2")];

describe("task output ownership in the conversation",()=>{
 it("keeps a late result inside its initiating round when new questions arrive",()=>{
  const tasks=[task()];
  const rows=conversationTaskRows([...conversation,message("u3","user",40)],tasks);
  expect(rows.map(row=>row.key)).toEqual(["u1","a1","u2","a2","u3"]);
  expect(rows[1].tasks.map(t=>t.taskId)).toEqual(["task"]);
  expect(rows.at(-1)?.tasks).toEqual([]);
 });
 it("shows running work by its question, then by its answer as that answer appears",()=>{
  const running=task({status:"RUNNING"});
  expect(conversationTaskRows([conversation[0]],[running])[0].tasks).toHaveLength(1);
  expect(conversationTaskRows(conversation.slice(0,2),[running])[1].tasks).toHaveLength(1);
 });
 it("supports ordinary task message associations",()=>{
  const rows=conversationTaskRows(conversation,[task({trigger_turn_id:undefined,spec_json:{association:{parent_message_id:"u2"}}})]);
  expect(rows[3].tasks).toHaveLength(1);
  expect(rows[1].tasks).toHaveLength(0);
 });
 it("retains all original identities when adjacent voice user rows coalesce",()=>{
  const combined=mergeAdjacentVoiceUserMessages([conversation[0],message("pause","user",1,"turn-pause"),conversation[1],...conversation.slice(2)]);
  const rows=conversationTaskRows(combined,[task({trigger_turn_id:"turn-pause"})]);
  expect(combined[0].provider_trace?.voice_merged_turn_ids).toEqual(["turn-1","turn-pause"]);
  expect(rows.find(row=>row.key==="a1")?.tasks).toHaveLength(1);
 });
 it("recovers a missing historical turn before later messages, without assigning it to the latest question",()=>{
  const rows=conversationTaskRows(conversation.slice(2),[task({triggerUserText:"请做一张表",triggerCreatedAt:at(0)})]);
  expect(rows[0]).toMatchObject({kind:"task-round",sourceText:"请做一张表"});
  expect(rows[0].tasks).toHaveLength(1);
  expect(rows[1].key).toBe("u2");
 });
 it("places legacy unassociated tasks by creation, not their late update time",()=>{
  const rows=conversationTaskRows(conversation,[task({trigger_turn_id:undefined})]);
  expect(rows[1].tasks).toHaveLength(1);
  expect(rows[3].tasks).toHaveLength(0);
 });
 it("treats server naive timestamps as UTC when restoring a missing round",()=>{
  const rows=conversationTaskRows(conversation.slice(2),[task({triggerCreatedAt:"2026-10-21T10:01:00"})]);
  expect(rows[0].kind).toBe("task-round");
 });
 it("deduplicates live and durable copies without losing the owner",()=>{
  const merged=mergeConversationTasks([task()],[task({trigger_turn_id:undefined,status:"RUNNING"})]);
  expect(merged).toHaveLength(1);
  expect(merged[0].triggerTurnId).toBe("turn-1");
  expect(conversationTaskRows(conversation,merged)[1].tasks).toHaveLength(1);
 });
 it("does not move ownership to the later playback/focus turn",()=>{
  let state=mergeVoiceTaskSnapshots(emptyVoiceTaskState(),[{...task()}]);
  state=reduceVoiceTaskEvent(state,{type:"scene.focus",task_id:"task",turn_id:"delivery-turn",anchor_id:"summary",event_id:"focus"}).state;
  expect(state.tasks[0].triggerTurnId).toBe("turn-1");
  expect(state.tasks[0].focusTurnId).toBe("delivery-turn");
  expect(conversationTaskRows(conversation,state.tasks)[1].tasks).toHaveLength(1);
 });
});

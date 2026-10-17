import { renderWithProviders as render } from "@/test/render";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VoiceTaskScene } from "./voice-task-scene";
import { emptyVoiceTaskState, mergeVoiceTaskResults, reduceVoiceTaskEvent } from "./voice-tasks";
import { voiceSessionController } from "./voice-session-controller";

function state() {
  return mergeVoiceTaskResults(emptyVoiceTaskState(), [{
    id: "result-1", subagent_id: "task-1", status: "ready", requirement_version: 1, result_version: 2,
    summary: "Result summary", payload: {agent_result: {findings: ["First finding"], limitations: ["Not independently verified"]}, visuals: [{
      protocol: "suip/0.1", block_id: "compare", component: "table", revision: 1, state: "completed", props: {title: "Comparison", columns: [{key: "method", label: "Method"}]}, rows: [{id: "a", label: "A", values: {method: "One"}}],
    }]},
  }]);
}

describe("shared voice task scene", () => {
  it("scrolls to and highlights the component until presentation ends", async () => {
    const old = Object.getOwnPropertyDescriptor(HTMLElement.prototype,"scrollIntoView");
    const scroll = vi.fn();
    Object.defineProperty(HTMLElement.prototype,"scrollIntoView",{value:scroll,configurable:true});
    try {
      const task = state().tasks[0];
      const view = render(<VoiceTaskScene task={{...task,focusId:"focus",focusAnchor:"visual-compare"}} open />);
      await waitFor(()=>expect(scroll).toHaveBeenCalledWith(expect.objectContaining({block:"center"})));
      expect(view.container.querySelector('[data-voice-anchor="visual-compare"]')).toHaveAttribute("data-voice-focused","true");
      view.rerender(<VoiceTaskScene task={task} open />);
      expect(view.container.querySelector('[data-voice-anchor="visual-compare"]')).not.toHaveAttribute("data-voice-focused");
    } finally {
      if(old) Object.defineProperty(HTMLElement.prototype,"scrollIntoView",old);
      else Reflect.deleteProperty(HTMLElement.prototype,"scrollIntoView");
    }
  });
  it("accepts completion at the same resource revision without reviving a late streaming copy", () => {
    const complete = state().tasks[0].visuals![0];
    const result = (scene: typeof complete) => [{id:"r",subagent_id:"t",status:"ready",requirement_version:1,result_version:1,payload:{visuals:[scene]}}];
    const streaming = mergeVoiceTaskResults(emptyVoiceTaskState(), result({...complete,state:"streaming"}));
    const finalized = mergeVoiceTaskResults(streaming, result(complete));
    expect(finalized.tasks[0].visuals![0].state).toBe("completed");
    expect(mergeVoiceTaskResults(finalized,result({...complete,state:"streaming"})).tasks[0].visuals![0].state).toBe("completed");
  });
  it("sends the selected finding through the real voice controller and focuses the same anchor", async () => {
    voiceSessionController.open("scene-workspace", `scene-${crypto.randomUUID()}`);
    voiceSessionController.upsertTask(state().tasks[0]);
    const send = vi.spyOn(voiceSessionController, "sendText").mockReturnValue(true);
    try {
      expect(await voiceSessionController.explainTask("task-1", "finding-0")).toBe(true);
      expect(send).toHaveBeenCalledWith(expect.stringContaining("First finding"));
      expect(voiceSessionController.getSnapshot().tasks[0].focusAnchor).toBe("finding-0");
    } finally { send.mockRestore(); }
  });
  it("acknowledges mounted anchors, renders a real table, and routes a conclusion click", async () => {
    const onRender = vi.fn(), onExplain = vi.fn();
    const task = state().tasks[0];
    render(<VoiceTaskScene task={task} open onRender={onRender} onExplain={onExplain} />);
    await waitFor(() => expect(onRender).toHaveBeenCalledWith("task-1", ["summary", "visual-compare", "finding-0"]));
    expect(screen.getByRole("table")).toHaveTextContent("One");
    expect(screen.getByText("Not independently verified")).toBeInTheDocument();
    fireEvent.click(screen.getByText("解释第 1 项"));
    expect(onExplain).toHaveBeenCalledWith("task-1", "finding-0");
    expect(task.resultVersion).toBe(2);
  });
  it("cannot acknowledge a collapsed scene", () => {
    const onRender = vi.fn();
    render(<VoiceTaskScene task={state().tasks[0]} open={false} onRender={onRender} />);
    expect(onRender).not.toHaveBeenCalled();
  });
  it("revokes scene availability when the user collapses the result", async () => {
    const onRender = vi.fn();
    const task = state().tasks[0];
    const view = render(<VoiceTaskScene task={task} open onRender={onRender} />);
    await waitFor(() => expect(onRender).toHaveBeenCalledWith("task-1", ["summary", "visual-compare", "finding-0"]));
    view.rerender(<VoiceTaskScene task={task} open={false} onRender={onRender} />);
    expect(onRender).toHaveBeenLastCalledWith("task-1", [], "failed");
  });
  it("uses the task endpoint identity instead of an internal worker row id", () => {
    const current = reduceVoiceTaskEvent(state(), {event_seq: 1, type: "tool_started", payload: {task_id: "internal-row-id", tool_name: "search_web"}}, "task-1").state;
    expect(current.tasks.map((task) => task.taskId)).toEqual(["task-1"]);
  });
  it("does not let an older result replace the latest result after reconnect", () => {
    const current = mergeVoiceTaskResults(state(), [{id: "old-result", subagent_id: "task-1", requirement_version: 1, result_version: 1, status: "ready", summary: "Older summary"}]);
    expect(current.tasks[0].resultId).toBe("result-1");
    expect(current.tasks[0].resultVersion).toBe(2);
    expect(current.tasks[0].summary).toBe("Result summary");
  });
  it("replays visual revisions monotonically and ignores obsolete requirements", () => {
    let current = state();
    const scene = current.tasks[0].visuals![0];
    current = reduceVoiceTaskEvent(current, {task_id: "task-1", event_seq: 1, type: "artifact.upsert", payload: {requirement_version: 1, snapshot: {...scene, revision: 0, props: {...scene.props, title: "Old"}}}}).state;
    expect(current.tasks[0].visuals![0].props.title).toBe("Comparison");
    current = reduceVoiceTaskEvent(current, {task_id: "task-1", event_seq: 2, type: "task.requirement_revised", payload: {requirement_version: 2}}).state;
    expect(current.tasks[0].visuals).toEqual([]);
    expect(current.tasks[0].findings).toEqual([]);
    expect(current.tasks[0].resultId).toBeUndefined();
    current = reduceVoiceTaskEvent(current, {task_id: "task-1", event_seq: 3, type: "artifact.upsert", payload: {requirement_version: 1, snapshot: {...scene, revision: 99}}}).state;
    expect(current.tasks[0].visuals).toEqual([]);
  });
});

vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => ({ workspaceId: "ws" }) }));

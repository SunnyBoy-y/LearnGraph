import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "./client";
import { generateScheduleSuggestions, createScheduledTask, deleteScheduledTask, listScheduledTaskRuns, listScheduledTasks, runScheduledTask, updateScheduledTask } from "./schedules";
import type { ScheduledTaskInput } from "@/types/schedules";
afterEach(() => vi.restoreAllMocks());
const payload: ScheduledTaskInput = { name:"Daily",description:"",task_type:"skill",skill_id:"s",mcp_server_id:null,tool_name:null,cron_expression:"0 9 * * *",timezone:"Asia/Shanghai",enabled:true,payload_json:{script_path:"scripts/run.py"},timeout_seconds:60 };
describe("scheduled task API", () => {
  it("generates recommendations without creating a task and preserves request scope", async () => {
    const response = { suggestions: [{ name: "研究回顾", prompt: "每周五总结研究进展。", icon: "microscope" }], metadata: {} };
    vi.spyOn(apiClient, "post").mockResolvedValue(response);
    const options = { signal: new AbortController().signal, headers: { "X-Workspace-ID": "workspace-a" } };
    expect(await generateScheduleSuggestions(options)).toEqual(response);
    expect(apiClient.post).toHaveBeenCalledExactlyOnceWith("/scheduled-tasks/suggestions", { count: 5 }, options);
  });
  it("uses the authenticated workspace client for CRUD", async () => {
    vi.spyOn(apiClient,"get").mockResolvedValue([]);
    vi.spyOn(apiClient,"post").mockResolvedValue({id:"task"});
    vi.spyOn(apiClient,"patch").mockResolvedValue({id:"task"});
    vi.spyOn(apiClient,"delete").mockResolvedValue(undefined);
    await listScheduledTasks(); await createScheduledTask(payload); await updateScheduledTask("task/1",{enabled:false}); await deleteScheduledTask("task/1");
    expect(apiClient.get).toHaveBeenCalledWith("/scheduled-tasks");
    expect(apiClient.post).toHaveBeenCalledWith("/scheduled-tasks",payload);
    expect(apiClient.patch).toHaveBeenCalledWith("/scheduled-tasks/task%2F1",{enabled:false});
    expect(apiClient.delete).toHaveBeenCalledWith("/scheduled-tasks/task%2F1");
  });
  it("keeps execution separate from definition updates", async () => {
    vi.spyOn(apiClient,"post").mockResolvedValue({status:"running"}); vi.spyOn(apiClient,"get").mockResolvedValue([]);
    await runScheduledTask("t"); await listScheduledTaskRuns("t");
    expect(apiClient.post).toHaveBeenCalledWith("/scheduled-tasks/t/run");
    expect(apiClient.get).toHaveBeenCalledWith("/scheduled-tasks/t/runs");
  });
});

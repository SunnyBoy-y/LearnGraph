import { renderWithProviders as render } from "@/test/render";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ManagedSessionTasks } from "./managed-session-tasks";

const api=vi.hoisted(() => ({ get:vi.fn(), post:vi.fn(), ack:vi.fn() }));
vi.mock("@/api/client",()=>({apiClient:api}));
vi.mock("@/api/auth-store",()=>({authStore:{getWorkspaceId:()=>"ws",getSession:()=>null}}));
vi.mock("@/api/voice",()=>({acknowledgeVoiceScene:api.ack}));

it("reopens durable task output and acknowledges only the actual rendered scene",async()=>{
  const visual={protocol:"suip/0.1",block_id:"table",component:"table",state:"completed",revision:1,resource_revision:0,
    props:{title:"两组比较",columns:[{key:"value",label:"数值"}]},rows:[{id:"a",label:"甲组",values:{value:14}},{id:"b",label:"乙组",values:{value:77}}],nodes:[],edges:[],items:[],blocks:[]};
  const task={task_id:"task",status:"SUCCEEDED",title:"比较",requirement_version:1,spec_json:{agent_session_id:"child",association:{voice_session_id:"voice"}},deliverables:{visuals:[visual]}};
  api.get.mockImplementation((url:string)=>Promise.resolve(url.startsWith("/sandbox/agent-tasks?") ? {tasks:[task]} :
    url.startsWith("/sessions/child/messages") ? {items:[{id:"answer",role:"assistant",status:"completed",parts:[
      {id:"markdown",type:"text",status:"completed",content:"**完整子会话内容**"},
      {id:"table",type:"ui",status:"completed",data:visual},
    ]}],has_more_before:false} :
    url.startsWith("/voice/") ? {session_epoch:2,tasks:[task],results:[{id:"result",task_id:"task",subagent_id:"task",status:"ready",requirement_version:1,result_version:1,summary:"比较完成",payload:{visuals:[visual]}}]} : task));
  api.ack.mockResolvedValue({});
  render(<ManagedSessionTasks sessionId="parent"/>);
  const title = await screen.findByRole("heading", {name:"比较"});
  expect(title.closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByText("14")).not.toBeInTheDocument();
  expect(api.ack).not.toHaveBeenCalled();
  expect(api.get.mock.calls.some(([url])=>String(url).startsWith("/sessions/child/messages"))).toBe(false);
  fireEvent.click(title.closest("summary")!);
  expect(await screen.findByText("完整子会话内容", {}, {timeout:5000})).toBeInTheDocument();
  expect(screen.getByText("14")).toBeInTheDocument();
  expect(screen.getByText("77")).toBeInTheDocument();
  expect(await screen.findByRole("link",{name:"查看执行过程"})).toHaveAttribute("href","/w/ws/chat/child");
  await waitFor(()=>expect(api.ack).toHaveBeenCalledWith("voice","result",expect.objectContaining({session_epoch:2,status:"rendered",anchors:expect.arrayContaining(["visual-table"])})));
  fireEvent.click(title.closest("summary")!);
  await waitFor(()=>expect(api.ack).toHaveBeenLastCalledWith("voice","result",expect.objectContaining({status:"failed",anchors:[]})));
  expect(screen.queryByText("完整子会话内容")).not.toBeInTheDocument();
});

vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => ({ workspaceId: "ws" }) }));

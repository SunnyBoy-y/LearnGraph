import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { apiClient } from "@/api/client";
import { renderWithProviders } from "@/test/render";
import type { MCPServer } from "@/types/extensions";
import { McpContentControls, McpStdioControls } from "./mcp-advanced-controls";
const server = {id:"mcp",display_name:"Notes",transport:"streamable_http",manifest_json:{requested_resources:["notes://{id}"],requested_prompts:["summary"]}} as unknown as MCPServer;
afterEach(()=>{cleanup();vi.restoreAllMocks();});
describe("MCP advanced execution",()=>{
  it("reads reviewed resources and passes template variables",async()=>{
    const post=vi.spyOn(apiClient,"post").mockResolvedValue({status:"succeeded",result:{contents:[{text:"real result"}]}});
    renderWithProviders(<McpContentControls server={server}/>);
    fireEvent.click(screen.getByText("资源与提示"));
    fireEvent.change(screen.getByLabelText("参数 / URI 模板变量（JSON）"),{target:{value:'{"id":"graph"}'}});
    fireEvent.click(screen.getByRole("button",{name:"调用"}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith("/mcp/servers/mcp/resources/read",{uri:"notes://{id}",arguments:{id:"graph"}}));
    expect(await screen.findByText(/real result/)).toBeInTheDocument();
  });
  it("rejects invalid arguments instead of issuing a request",async()=>{
    const post=vi.spyOn(apiClient,"post");
    renderWithProviders(<McpContentControls server={server}/>);
    fireEvent.click(screen.getByText("资源与提示"));
    fireEvent.change(screen.getByLabelText("参数 / URI 模板变量（JSON）"),{target:{value:"[]"}});
    fireEvent.click(screen.getByRole("button",{name:"调用"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("JSON 对象");
    expect(post).not.toHaveBeenCalled();
  });
  it("requires explicit review and binds approval to the displayed launch hash",async()=>{
    const hash="a".repeat(64);
    const stdio={...server,transport:"stdio",runner_image_digest:"sha256:"+"b".repeat(64),launch_command:["python","/opt/main.py"],launch_spec_hash:hash};
    const post=vi.spyOn(apiClient,"post").mockResolvedValue({launch_status:"approved"});
    renderWithProviders(<McpStdioControls server={stdio} onChanged={vi.fn()}/>);
    fireEvent.click(screen.getByText("隔离启动"));
    expect(screen.getByText("审批已登记规范")).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText("审批已登记规范"));
    await waitFor(()=>expect(post).toHaveBeenCalledWith(`/mcp/servers/mcp/stdio-launch-spec/approve?expected_launch_spec_hash=${hash}`));
  });
});

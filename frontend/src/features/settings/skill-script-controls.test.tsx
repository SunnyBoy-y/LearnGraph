import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { apiClient } from "@/api/client";
import { renderWithProviders } from "@/test/render";
import type { Skill } from "@/types/extensions";
import { SkillScriptControls } from "./skill-script-controls";
vi.mock("@/api/extensions",()=>({listSkillFiles:vi.fn(async()=>({files:[{relative_path:"scripts/original.py",is_directory:false},{relative_path:"assets/data.bin",is_directory:false}]}))}));
afterEach(()=>{cleanup();vi.restoreAllMocks();});
const skill={id:"skill",name:"Original",has_scripts:true} as Skill;
describe("Skill code execution controls",()=>{
  it("executes an existing package script without sending rewritten code",async()=>{
    const post=vi.spyOn(apiClient,"post").mockResolvedValue({status:"running",available:true,invocation_id:"inv",chat_session_id:"chat"});
    vi.spyOn(apiClient,"get").mockResolvedValue({status:"succeeded",result_json:{stdout_summary:"ORIGINAL-CODE"}});
    renderWithProviders(<SkillScriptControls skill={skill} workspaceId="ws"/>);
    fireEvent.click(screen.getByText("运行包内脚本"));
    await screen.findByText("scripts/original.py");
    fireEvent.click(screen.getByText("运行原始脚本"));
    await waitFor(()=>expect(post).toHaveBeenCalledWith("/skills/skill/sandbox-run",{script_path:"scripts/original.py",argv_extra:[],cwd_mode:"package",yield_time_ms:0}));
    expect(await screen.findByText(/ORIGINAL-CODE/)).toBeInTheDocument();
    expect(screen.getByText("状态：成功")).toBeInTheDocument();
  });
  it("does not report sandbox unavailability as success",async()=>{
    vi.spyOn(apiClient,"post").mockResolvedValue({status:"unavailable",available:false,invocation_id:null,error_message:"Docker is not ready"});
    renderWithProviders(<SkillScriptControls skill={skill} workspaceId="ws"/>);
    fireEvent.click(screen.getByText("运行包内脚本"));
    await screen.findByText("scripts/original.py");fireEvent.click(screen.getByText("运行原始脚本"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Docker is not ready");
    expect(screen.getByText("状态：沙箱不可用")).toBeInTheDocument();
  });
});

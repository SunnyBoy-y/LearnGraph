import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from '@/lib/notifications';
import { apiClient } from "@/api/client";
import type { MCPServer } from "@/types/extensions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

export function McpContentControls({ server }: { server: MCPServer }) {
  const manifest = server.manifest_json as { requested_resources?: string[]; requested_prompts?: string[] };
  const choices = [...(manifest.requested_resources ?? []).map(id => ({ kind:"resource",id })), ...(manifest.requested_prompts ?? []).map(id => ({ kind:"prompt",id }))];
  const [index,setIndex] = useState(0);
  const [args,setArgs] = useState("{}");
  const [error,setError] = useState("");
  const run = useMutation({mutationFn: async () => {
    const selected = choices[index];
    if (!selected) throw new Error("请选择已审核的资源或提示");
    const parsed: unknown = JSON.parse(args);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object" || Object.values(parsed).some(value => typeof value !== "string")) throw new Error("参数必须为字符串值的 JSON 对象");
    return apiClient.post<Record<string,unknown>,Record<string,unknown>>(`/mcp/servers/${encodeURIComponent(server.id)}/${selected.kind === "resource" ? "resources/read" : "prompts/get"}`,
      selected.kind === "resource" ? {uri:selected.id,arguments:parsed} : {name:selected.id,arguments:parsed});
  },onError:(e:Error)=>setError(e.message)});
  if (!choices.length) return null;
  return <Dialog><DialogTrigger asChild><Button size="xs" variant="outline">资源与提示</Button></DialogTrigger><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>{server.display_name}：资源与提示</DialogTitle><DialogDescription>仅访问已审核范围；调用结果进入审计记录。</DialogDescription></DialogHeader>
    <Label>能力<select className="mt-2 h-9 w-full rounded-md border bg-background px-3 text-sm" value={index} onChange={e=>{setIndex(Number(e.target.value));run.reset();setError("");}}>{choices.map((c,i)=><option key={c.kind+c.id} value={i}>{c.kind === "resource" ? "资源" : "提示"}：{c.id}</option>)}</select></Label>
    <Label>参数 / URI 模板变量（JSON）<Textarea className="mt-2 font-mono" dir="ltr" value={args} onChange={e=>setArgs(e.target.value)} /></Label>
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    <Button disabled={run.isPending} onClick={()=>{setError("");run.mutate();}}>{run.isPending ? "调用中…" : "调用"}</Button>
    {run.data ? <pre dir="ltr" className="max-h-72 overflow-auto bg-muted p-3 text-xs">{JSON.stringify(run.data,null,2)}</pre> : null}
  </DialogContent></Dialog>;
}

export function McpStdioControls({ server, onChanged }: { server: MCPServer; onChanged:()=>void }) {
  const extended = server as MCPServer & { runner_image_digest?: string; launch_command?: string[]; launch_status?: string };
  const [digest,setDigest] = useState(extended.runner_image_digest ?? "");
  const [argv,setArgv] = useState(JSON.stringify(extended.launch_command ?? ["python","/opt/server/main.py"]));
  const [error,setError] = useState("");
  const [review,setReview] = useState(false);
  const change = useMutation({mutationFn:async (approve:boolean)=>{
    const root=`/mcp/servers/${encodeURIComponent(server.id)}/stdio-launch-spec`;
    if (approve) return apiClient.post(`${root}/approve?expected_launch_spec_hash=${encodeURIComponent(server.launch_spec_hash ?? "")}`);
    const command:unknown=JSON.parse(argv);
    if (!Array.isArray(command) || command.length===0 || command.some(part=>typeof part!=="string")) throw new Error("启动命令必须为非空字符串数组");
    return apiClient.post(root,{image_digest:digest,command});
  },onSuccess:()=>{setReview(false);onChanged();toast.success("启动规范已更新，请重新探测并授权");},onError:(e:Error)=>setError(e.message)});
  let matchesRegistered = false;
  try { matchesRegistered = digest === extended.runner_image_digest && JSON.stringify(JSON.parse(argv)) === JSON.stringify(extended.launch_command); } catch { /* Keep approval disabled for malformed drafts. */ }
  if (server.transport!=="stdio") return null;
  return <Dialog><DialogTrigger asChild><Button size="xs" variant="outline">隔离启动</Button></DialogTrigger><DialogContent><DialogHeader><DialogTitle>stdio 启动规范</DialogTitle><DialogDescription>仅在隔离沙箱内启动已审核的固定镜像与命令，不执行宿主命令。</DialogDescription></DialogHeader>
    <p className="text-sm">审批状态：{extended.launch_status === "approved" ? "已审批" : "未审批"}</p>
    <Label>镜像 SHA256<Input className="mt-2 font-mono" dir="ltr" value={digest} placeholder="sha256:…" onChange={e=>{setDigest(e.target.value);setReview(false);}} /></Label>
    <Label>启动 argv（JSON）<Textarea className="mt-2 font-mono" dir="ltr" value={argv} onChange={e=>{setArgv(e.target.value);setReview(false);}} /></Label>
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    <Button disabled={change.isPending} variant="outline" onClick={()=>{setError("");change.mutate(false);}}>登记启动规范</Button>
    <label className="flex items-start gap-2 text-sm"><input className="mt-1" type="checkbox" checked={review} onChange={e=>setReview(e.target.checked)} />我已审核服务端登记的镜像和命令，确认允许其在沙箱中执行。</label>
    <Button disabled={change.isPending || !review || !matchesRegistered || !server.launch_spec_hash} onClick={()=>{setError("");change.mutate(true);}}>审批已登记规范</Button>
  </DialogContent></Dialog>;
}

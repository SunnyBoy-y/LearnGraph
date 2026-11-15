import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { listSkillFiles } from "@/api/extensions";
import { workspaceQueryKey } from "@/lib/query-keys";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import type { Skill } from "@/types/extensions";
interface ScriptResult { status:string; available:boolean; invocation_id:string|null; job_id?:string; chat_session_id?:string; stdout_summary:string; stderr_summary:string; error_message?:string|null; }
interface Invocation { status:string; result_json:Record<string,unknown>; error_message:string|null; }
export function SkillScriptControls({ skill, workspaceId }: { skill:Skill; workspaceId:string }) {
  const [open,setOpen] = useState(false);
  const [path,setPath] = useState("");
  const [args,setArgs] = useState("[]");
  const [error,setError] = useState("");
  const files = useQuery({queryKey:workspaceQueryKey(workspaceId,"skills",skill.id,"files"),queryFn:()=>listSkillFiles(skill.id),enabled:open && skill.has_scripts});
  const scripts = (files.data?.files ?? []).filter(file=>!file.is_directory && /^scripts\/.*\.(py|js|mjs|cjs)$/i.test(file.relative_path));
  const start = useMutation({mutationFn:async()=>{
    const argv:unknown=JSON.parse(args);
    if (!Array.isArray(argv) || argv.some(value=>typeof value!=="string")) throw new Error("脚本参数必须为字符串数组");
    const selected=path || scripts[0]?.relative_path;
    if (!selected) throw new Error("请选择脚本");
    return apiClient.post<ScriptResult,Record<string,unknown>>(`/skills/${encodeURIComponent(skill.id)}/sandbox-run`,{script_path:selected,argv_extra:argv,cwd_mode:"package",yield_time_ms:0});
  },onError:(e:Error)=>setError(e.message)});
  const invocationId=start.data?.invocation_id;
  const status=useQuery({queryKey:workspaceQueryKey(workspaceId,"skills",skill.id,"sandbox-runs",invocationId ?? "none"),
    queryFn:()=>apiClient.get<Invocation>(`/skills/${encodeURIComponent(skill.id)}/sandbox-runs/${encodeURIComponent(invocationId!)}`),enabled:Boolean(invocationId),
    refetchInterval:query=>query.state.data?.status === "running" || !query.state.data ? 1000 : false});
  const cancel=useMutation({mutationFn:()=>apiClient.post<Invocation>(`/skills/${encodeURIComponent(skill.id)}/sandbox-runs/${encodeURIComponent(invocationId!)}/cancel`),onSuccess:()=>void status.refetch(),onError:(e:Error)=>setError(e.message)});
  if (!skill.has_scripts) return null;
  const current=status.data?.status ?? start.data?.status;
  const running=start.isPending || current === "running" || current === "pending";
  const result=status.data?.result_json ?? start.data;
  return <Dialog open={open} onOpenChange={setOpen}><DialogTrigger asChild><Button size="xs" variant="outline">运行包内脚本</Button></DialogTrigger><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>{skill.name}：包内脚本</DialogTitle><DialogDescription>执行存储的原始代码和资源，仅在隔离沙箱中运行。</DialogDescription></DialogHeader>
    <Label>脚本<select className="mt-2 h-9 w-full rounded-md border bg-background px-3 text-sm" disabled={running} value={path || scripts[0]?.relative_path || ""} onChange={e=>setPath(e.target.value)}>{scripts.length ? scripts.map(file=><option key={file.relative_path} value={file.relative_path}>{file.relative_path}</option>) : <option value="">{files.isPending ? "读取脚本…" : "没有可运行脚本"}</option>}</select></Label>
    <Label>argv 参数（JSON）<Textarea className="mt-2 font-mono" dir="ltr" disabled={running} value={args} onChange={e=>setArgs(e.target.value)}/></Label>
    {error || files.error || status.error ? <p role="alert" className="text-sm text-destructive">{error || files.error?.message || status.error?.message}</p> : null}
    {start.data?.error_message || status.data?.error_message ? <p role="alert" className="text-sm text-destructive">{status.data?.error_message || start.data?.error_message}</p> : null}
    <div className="flex gap-2"><Button disabled={running || !scripts.length} onClick={()=>{setError("");start.mutate();}}>运行原始脚本</Button>{running && invocationId ? <Button variant="outline" disabled={cancel.isPending} onClick={()=>cancel.mutate()}>请求停止</Button> : null}</div>
    {current ? <p className="text-sm">状态：{current === "running" ? "执行中" : current === "succeeded" ? "成功" : current === "unavailable" ? "沙箱不可用" : current === "failed" ? "失败" : current}</p> : null}
    {start.data?.chat_session_id ? <Link className="text-sm underline" to={`/w/${workspaceId}/chat/${start.data.chat_session_id}`}>查看执行会话</Link> : null}
    {result ? <pre className="max-h-64 overflow-auto bg-muted p-3 text-xs" dir="ltr">{JSON.stringify(result,null,2)}</pre> : null}
  </DialogContent></Dialog>;
}

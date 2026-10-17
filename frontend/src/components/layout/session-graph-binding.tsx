import { GitBranch, LoaderCircle, Network, X } from "lucide-react";
import {
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { useSessionGraphBinding } from "@/lib/session-graph-binding";

export function SessionGraphBindingMenu({ workspaceId, sessionId }: { workspaceId: string; sessionId?: string }) {
  const { session, graphs, graph, binding, boundNodeId, boundNode, bindingPending, bindGraph, clearGraph, bindNode } = useSessionGraphBinding(workspaceId, sessionId);
  return (
    <section aria-label="当前会话图谱绑定" className="session-graph-binding">
      <div className="session-graph-binding__summary">
        <span>当前会话</span>
        <strong>{binding?.graphTitle ?? "未绑定图谱"}</strong>
        <small>{binding ? boundNodeId ? `绑定节点 · ${boundNode?.label ?? "读取中…"}` : "未绑定具体节点" : "选择图谱后可绑定节点"}</small>
      </div>
      {bindingPending ? <DropdownMenuItem disabled><LoaderCircle className="size-3.5 animate-spin" />正在更新绑定…</DropdownMenuItem> : null}
      {binding ? (
        <DropdownMenuItem disabled={bindingPending} onSelect={(event) => { event.preventDefault(); void clearGraph(); }}>
          <X className="size-3.5" />解绑图谱
        </DropdownMenuItem>
      ) : (
        <DropdownMenuSub>
          <DropdownMenuSubTrigger disabled={!session || bindingPending}><Network className="size-3.5" />绑定图谱</DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="max-h-80 w-60 overflow-y-auto">
            <DropdownMenuLabel>绑定到当前会话</DropdownMenuLabel>
            {graphs.isPending ? <DropdownMenuItem disabled>正在读取图谱…</DropdownMenuItem> : graphs.isError ? <DropdownMenuItem onSelect={(event) => { event.preventDefault(); void graphs.refetch(); }}>重新读取图谱</DropdownMenuItem> : !graphs.data?.length ? <DropdownMenuItem disabled>暂无可绑定图谱</DropdownMenuItem> : graphs.data.map((item) => (
              <DropdownMenuItem key={item.id} disabled={bindingPending || item.status !== "published" || Boolean(session?.goal_id && session.goal_id !== item.goal_id)} onSelect={(event) => { event.preventDefault(); void bindGraph(item); }}>
                <Network className="size-3.5" /><span className="min-w-0 truncate">{item.title}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      )}
      {binding ? <>
        <DropdownMenuSeparator />
        {boundNodeId ? (
          <DropdownMenuItem disabled={bindingPending} onSelect={(event) => { event.preventDefault(); void bindNode(null); }}><X className="size-3.5" />解绑节点</DropdownMenuItem>
        ) : (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger disabled={bindingPending}><GitBranch className="size-3.5" />绑定节点</DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="max-h-80 w-60 overflow-y-auto">
              {graph.isPending ? <DropdownMenuItem disabled>正在读取节点…</DropdownMenuItem> : graph.isError ? <DropdownMenuItem onSelect={(event) => { event.preventDefault(); void graph.refetch(); }}>重新读取节点</DropdownMenuItem> : !graph.data?.nodes.length ? <DropdownMenuItem disabled>暂无可绑定节点</DropdownMenuItem> : graph.data.nodes.map((node) => (
                <DropdownMenuItem key={node.id} disabled={bindingPending} onSelect={(event) => { event.preventDefault(); void bindNode(node.id); }}><span className="min-w-0 truncate">{node.label}</span></DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}
        <small className="text-muted-foreground">切换图谱或节点前，请先解绑对应绑定。</small>
      </> : null}
    </section>
  );
}

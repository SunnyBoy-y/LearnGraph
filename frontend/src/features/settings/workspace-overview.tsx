import { LoadingState, ErrorState } from '@/components/shared/page-elements';
import { SettingsSection, SettingRow } from './settings-primitives';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Copy } from 'lucide-react';
import { toast } from 'sonner';
import { listWorkspaces } from "@/api";
import { authStore } from "@/api/auth-store";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/features/auth/auth-context-value";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
function useWorkspaceOverview() {
    const auth = useAuth();
    const session = authStore.getSession();
    const workspaces = useQuery({
        queryKey: ["workspaces"],
        queryFn: listWorkspaces,
        staleTime: 30000,
    });
    const activeWorkspace = useMemo(() => (workspaces.data ?? []).find((item) => item.id === auth.workspaceId) ??
        null, [workspaces.data, auth.workspaceId]);
    const userId = session?.userId;
    const workspaceKindLabel = activeWorkspace?.workspace_kind === "organization" ? "组织工作区" : "个人学习区";
    const isOwner = Boolean(userId) &&
        Boolean(activeWorkspace?.owner_user_id) &&
        userId === activeWorkspace?.owner_user_id;
    return { auth, workspaceKindLabel, isOwner, workspaces, activeWorkspace };
}
export function WorkspaceOverview() {
  const { auth, workspaceKindLabel, isOwner, workspaces, activeWorkspace } = useWorkspaceOverview();
  if (workspaces.isPending) return <LoadingState/>;
  if (workspaces.error) return <ErrorState message={workspaces.error.message}/>;
  return <SettingsSection title="工作区概览" description="当前工作区的资料与访问身份。">
    <SettingRow title="工作区名称"><span className="text-sm">{auth.workspaceName || activeWorkspace?.name || '—'}</span></SettingRow>
    <SettingRow title="工作区类型"><span className="text-sm">{activeWorkspace ? workspaceKindLabel : '—'}</span></SettingRow>
    <SettingRow title="当前角色"><Badge variant="outline">{!activeWorkspace ? '—' : isOwner ? '所有者' : activeWorkspace.effective_permissions.includes('workspace.manage') ? '管理者' : '成员'}</Badge></SettingRow>
    <SettingRow title="工作区 ID"><div className="flex min-w-0 items-center gap-2"><Input className="w-72 font-mono text-xs" aria-label="工作区 ID" readOnly value={auth.workspaceId}/><Button variant="outline" size="sm" aria-label="复制工作区 ID" onClick={() => { void navigator.clipboard.writeText(auth.workspaceId).then(() => toast.success('工作区 ID 已复制'), () => toast.error('复制失败，请手动复制')); }}><Copy className="size-4"/></Button></div></SettingRow>
  </SettingsSection>;
}

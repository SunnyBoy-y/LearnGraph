import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  CircleAlert,
  FileCog,
  Network,
  PackageCheck,
  Play,
  RefreshCcw,
  ServerCog,
  TerminalSquare,
} from "lucide-react";
import { toast } from "sonner";

import {
  addMembership,
  authorizeComponent,
  cancelSandboxTask,
  cleanupSandboxSession,
  createManagedUser,
  createOrganization,
  createRole,
  createSandboxTask,
  getCurrentUser,
  getMcpServer,
  getSandboxBootstrapStatus,
  getSandboxTask,
  getSkill,
  listAuthSessions,
  listComponentAuthorizations,
  listComponentChecks,
  listComponentManifests,
  listFiles,
  listMcpServers,
  listMcpSnapshots,
  listMcpTransportCapabilities,
  listManagedUsers,
  listMemberships,
  listOrganizations,
  listPermissions,
  listPlugins,
  listRoles,
  listSandboxExecutions,
  listSandboxProfiles,
  listSandboxSessions,
  listSandboxTasks,
  listSessions,
  listSkills,
  prepareComponentArtifact,
  registerComponent,
  revokeAuthSession,
  revokeComponentAuthorization,
  runComponentCheck,
  startSandboxBootstrap,
  updateManagedUserStatus,
  updateMcpServer,
  updateMembership,
  updateRole,
  updateSkill,
  validateComponentEvent,
} from "@/api";
import {
  EmptyState,
  ErrorState,
  KeyValueGrid,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import type { MCPServer, Skill } from "@/types/extensions";

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function parseObject(value: FormDataEntryValue | null, label: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(String(value ?? "{}"));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("not an object");
    }
    return parsed as Record<string, unknown>;
  } catch {
    toast.error(`${label} 必须是有效的 JSON 对象`);
    return null;
  }
}

function QueryFailure({ message }: { message: string }) {
  return (
    <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100">
      <CircleAlert className="mt-0.5 size-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function AccessManagementPage() {
  const queryClient = useQueryClient();
  const [organizationChoice, setOrganizationChoice] = useState("");
  const currentUser = useQuery({ queryKey: ["auth-me"], queryFn: getCurrentUser });
  const authSessions = useQuery({ queryKey: ["auth-sessions"], queryFn: listAuthSessions });
  const organizations = useQuery({
    queryKey: ["organizations"],
    queryFn: listOrganizations,
    enabled: Boolean(currentUser.data),
  });
  const permissions = useQuery({
    queryKey: ["permissions"],
    queryFn: listPermissions,
    enabled: Boolean(currentUser.data?.is_system_admin),
  });
  const users = useQuery({
    queryKey: ["managed-users"],
    queryFn: listManagedUsers,
    enabled: Boolean(currentUser.data?.is_system_admin),
  });
  const selectedOrganizationId =
    organizationChoice || organizations.data?.[0]?.id || "";
  const roles = useQuery({
    queryKey: ["organization-roles", selectedOrganizationId],
    queryFn: () => listRoles(selectedOrganizationId),
    enabled: Boolean(selectedOrganizationId),
  });
  const memberships = useQuery({
    queryKey: ["organization-memberships", selectedOrganizationId],
    queryFn: () => listMemberships(selectedOrganizationId),
    enabled: Boolean(selectedOrganizationId),
  });
  const revokeSession = useMutation({
    mutationFn: revokeAuthSession,
    onSuccess: () => {
      toast.success("会话已下线");
      void queryClient.invalidateQueries({ queryKey: ["auth-sessions"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const createOrg = useMutation({
    mutationFn: createOrganization,
    onSuccess: (organization) => {
      setOrganizationChoice(organization.id);
      toast.success("组织和初始工作区已创建");
      void queryClient.invalidateQueries({ queryKey: ["organizations"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const createUser = useMutation({
    mutationFn: createManagedUser,
    onSuccess: () => {
      toast.success("用户已创建");
      void queryClient.invalidateQueries({ queryKey: ["managed-users"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const changeUserStatus = useMutation({
    mutationFn: ({ userId, status }: { userId: string; status: "active" | "disabled" }) =>
      updateManagedUserStatus(userId, status),
    onSuccess: () => {
      toast.success("用户状态已更新");
      void queryClient.invalidateQueries({ queryKey: ["managed-users"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const createRoleMutation = useMutation({
    mutationFn: (payload: { name: string; description: string; permission_keys: string[] }) =>
      createRole(selectedOrganizationId, payload),
    onSuccess: () => {
      toast.success("角色已创建");
      void queryClient.invalidateQueries({ queryKey: ["organization-roles", selectedOrganizationId] });
    },
    onError: (error) => toast.error(error.message),
  });
  const updateRoleMutation = useMutation({
    mutationFn: ({ roleId, payload }: { roleId: string; payload: { description?: string; permission_keys?: string[] } }) =>
      updateRole(selectedOrganizationId, roleId, payload),
    onSuccess: () => {
      toast.success("角色权限已更新");
      void queryClient.invalidateQueries({ queryKey: ["organization-roles", selectedOrganizationId] });
      void queryClient.invalidateQueries({ queryKey: ["organization-memberships", selectedOrganizationId] });
    },
    onError: (error) => toast.error(error.message),
  });
  const addMembershipMutation = useMutation({
    mutationFn: (payload: { user_id: string; role_id: string }) =>
      addMembership(selectedOrganizationId, payload),
    onSuccess: () => {
      toast.success("成员已加入组织");
      void queryClient.invalidateQueries({ queryKey: ["organization-memberships", selectedOrganizationId] });
    },
    onError: (error) => toast.error(error.message),
  });
  const updateMembershipMutation = useMutation({
    mutationFn: ({ membershipId, payload }: { membershipId: string; payload: { role_id?: string; status?: "active" | "revoked" } }) =>
      updateMembership(selectedOrganizationId, membershipId, payload),
    onSuccess: () => {
      toast.success("成员关系已更新");
      void queryClient.invalidateQueries({ queryKey: ["organization-memberships", selectedOrganizationId] });
    },
    onError: (error) => toast.error(error.message),
  });
  if (currentUser.isPending || authSessions.isPending) {
    return <PageFrame><LoadingState /></PageFrame>;
  }
  if (currentUser.isError || authSessions.isError) {
    return <PageFrame><ErrorState message={(currentUser.error ?? authSessions.error)?.message ?? "身份信息读取失败"} /></PageFrame>;
  }
  const isSystemAdmin = currentUser.data.is_system_admin;
  const activeSessions = authSessions.data.filter((session) => !session.revoked_at);
  return (
    <PageFrame>
      <PageIntro
        eyebrow="Identity and access"
        title="账户、组织与访问控制"
        description="身份、会话、组织角色和工作区权限始终由服务端重新校验；工作区 Header 只用于定位范围，不能替代授权。"
      />

      <Surface className="p-5">
        <SectionHeading
          action={isSystemAdmin ? (
            <details>
              <summary className="cursor-pointer text-sm font-medium text-primary">新建组织</summary>
              <OrganizationForm busy={createOrg.isPending} onSubmit={(payload) => createOrg.mutate(payload)} />
            </details>
          ) : null}
          description="组织、角色和 Membership 都是持久化 RBAC 事实，不使用前端临时开关。"
          title="组织与成员"
        />
        {organizations.isError ? <QueryFailure message={organizations.error.message} /> : null}
        {organizations.data?.length ? (
          <div className="mt-4">
            <Label htmlFor="managed-organization">当前组织</Label>
            <select
              className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm sm:max-w-md"
              id="managed-organization"
              onChange={(event) => setOrganizationChoice(event.target.value)}
              value={selectedOrganizationId}
            >
              {organizations.data.map((organization) => (
                <option key={organization.id} value={organization.id}>
                  {organization.name}
                </option>
              ))}
            </select>
          </div>
        ) : organizations.isSuccess ? (
          <EmptyState description="创建组织后，才能为其定义角色与成员关系。" title="还没有可管理的组织" />
        ) : null}
        {selectedOrganizationId ? (
          <div className="mt-5 grid gap-5 xl:grid-cols-2">
            <div className="rounded-xl border bg-card/40 p-4">
              <SectionHeading
                action={
                  isSystemAdmin ? (
                    <details>
                      <summary className="cursor-pointer text-xs text-primary">新增角色</summary>
                      <RoleForm
                        busy={createRoleMutation.isPending}
                        onSubmit={(payload) => createRoleMutation.mutate(payload)}
                        permissions={permissions.data ?? []}
                      />
                    </details>
                  ) : null
                }
                title="角色与权限"
              />
              {roles.isError ? <QueryFailure message={roles.error.message} /> : null}
              <div className="mt-4 space-y-2">
                {roles.data?.map((role) => (
                  <details className="rounded-lg border bg-background/60 p-3" key={role.id}>
                    <summary className="flex cursor-pointer list-none items-center gap-2">
                      <span className="min-w-0 flex-1 font-medium">{role.name}</span>
                      <Badge variant="secondary">{role.permission_keys.length} 权限</Badge>
                      {role.is_system ? <Badge variant="outline">系统</Badge> : null}
                    </summary>
                    <p className="mt-2 text-xs text-muted-foreground">{role.description || "无角色说明"}</p>
                    <div className="mt-3 flex flex-wrap gap-1">
                      {role.permission_keys.map((permission) => (
                        <Badge className="font-mono text-[10px]" key={permission} variant="outline">
                          {permission}
                        </Badge>
                      ))}
                    </div>
                    {isSystemAdmin ? (
                      <RoleEditor
                        busy={updateRoleMutation.isPending}
                        onSubmit={(payload) => updateRoleMutation.mutate({ roleId: role.id, payload })}
                        permissions={permissions.data ?? []}
                        role={role}
                      />
                    ) : null}
                  </details>
                ))}
                {roles.isSuccess && !roles.data.length ? (
                  <p className="py-5 text-sm text-muted-foreground">该组织尚未定义角色。</p>
                ) : null}
              </div>
            </div>
            <div className="rounded-xl border bg-card/40 p-4">
              <SectionHeading
                action={
                  isSystemAdmin ? (
                    <details>
                      <summary className="cursor-pointer text-xs text-primary">添加成员</summary>
                      <MembershipForm
                        busy={addMembershipMutation.isPending}
                        onSubmit={(payload) => addMembershipMutation.mutate(payload)}
                        roles={roles.data ?? []}
                        users={users.data ?? []}
                      />
                    </details>
                  ) : null
                }
                title="成员关系"
              />
              {memberships.isError ? <QueryFailure message={memberships.error.message} /> : null}
              <div className="mt-4 space-y-2">
                {memberships.data?.map((membership) => (
                  <div
                    className="flex flex-col gap-2 rounded-lg border bg-background/60 p-3 sm:flex-row sm:items-center"
                    key={membership.id}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{membership.display_name}</p>
                      <p className="text-xs text-muted-foreground">
                        {membership.username} · {membership.role_name}
                      </p>
                    </div>
                    <StatePill status={membership.status} />
                    {isSystemAdmin ? (
                      <div className="flex gap-1">
                        <Button
                          disabled={updateMembershipMutation.isPending || membership.status === "active"}
                          onClick={() =>
                            updateMembershipMutation.mutate({
                              membershipId: membership.id,
                              payload: { status: "active" },
                            })
                          }
                          size="xs"
                          variant="outline"
                        >
                          恢复
                        </Button>
                        <Button
                          disabled={updateMembershipMutation.isPending || membership.status === "revoked"}
                          onClick={() =>
                            updateMembershipMutation.mutate({
                              membershipId: membership.id,
                              payload: { status: "revoked" },
                            })
                          }
                          size="xs"
                          variant="ghost"
                        >
                          撤销
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ))}
                {memberships.isSuccess && !memberships.data.length ? (
                  <p className="py-5 text-sm text-muted-foreground">该组织尚无成员。</p>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </Surface>

      {isSystemAdmin ? (
        <Surface className="p-5">
          <SectionHeading
            action={
              <details>
                <summary className="cursor-pointer text-sm font-medium text-primary">创建用户</summary>
                <UserForm busy={createUser.isPending} onSubmit={(payload) => createUser.mutate(payload)} />
              </details>
            }
            description="管理系统用户账号，可启用/停用并授予系统管理员身份。"
            title="用户目录"
          />
          <div className="mt-4 divide-y overflow-hidden rounded-xl border">
            {users.data?.map((user) => (
              <div className="flex flex-col gap-3 bg-card/30 p-4 sm:flex-row sm:items-center" key={user.id}>
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{user.display_name}</p>
                  <p className="text-xs text-muted-foreground">
                    {user.username}
                    {user.email ? ` · ${user.email}` : ""}
                  </p>
                </div>
                <StatePill status={user.status} />
                <Badge variant="outline">{user.is_system_admin ? "系统管理员" : "普通用户"}</Badge>
                <Button
                  disabled={changeUserStatus.isPending || user.id === currentUser.data.id}
                  onClick={() =>
                    changeUserStatus.mutate({
                      userId: user.id,
                      status: user.status === "active" ? "disabled" : "active",
                    })
                  }
                  size="xs"
                  variant="outline"
                >
                  {user.status === "active" ? "停用" : "启用"}
                </Button>
              </div>
            ))}
            {users.isError ? <QueryFailure message={users.error.message} /> : null}
            {users.isSuccess && !users.data?.length ? (
              <p className="p-5 text-sm text-muted-foreground">暂无用户记录。</p>
            ) : null}
          </div>
        </Surface>
      ) : null}

      {!isSystemAdmin ? (
        <QueryFailure message="当前身份不是系统管理员。会话管理仍可使用；组织、用户与全局权限管理会按后端授权策略显示可访问内容或明确拒绝。" />
      ) : null}

      <div className="grid gap-5">
        <Surface className="p-5">
          <SectionHeading
            description="可单独下线其他登录设备；当前会话通过退出登录处理。已下线的会话不再显示。"
            title="会话安全"
          />
          <div className="mt-4 space-y-2">
            {activeSessions.map((session) => (
              <div
                className="flex flex-col gap-3 rounded-xl border bg-card/30 p-3 sm:flex-row sm:items-center"
                key={session.id}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-mono text-xs">{session.id.slice(0, 12)}</p>
                    <StatePill
                      status={session.current ? "approved" : "active"}
                      label={session.current ? "当前会话" : "有效"}
                    />
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {session.user_agent || "未提供 User-Agent"} · 最近活动 {formatDate(session.last_seen_at)}
                  </p>
                </div>
                <Button
                  disabled={session.current || revokeSession.isPending}
                  onClick={() => revokeSession.mutate(session.id)}
                  size="xs"
                  variant="outline"
                >
                  下线
                </Button>
              </div>
            ))}
            {!activeSessions.length ? (
              <p className="py-5 text-sm text-muted-foreground">当前没有有效登录会话。</p>
            ) : null}
          </div>
        </Surface>
      </div>

    </PageFrame>
  );
}

function OrganizationForm({ busy, onSubmit }: { busy: boolean; onSubmit: (payload: { name: string; workspace_name?: string }) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ name: String(data.get("name") ?? "").trim(), workspace_name: String(data.get("workspace_name") ?? "").trim() || undefined }); event.currentTarget.reset(); }
  return <form className="mt-3 grid gap-2 sm:grid-cols-3" onSubmit={submit}><Input name="name" placeholder="组织名称" required /><Input name="workspace_name" placeholder="初始工作区（可选）" /><Button disabled={busy} size="sm" type="submit">创建</Button></form>;
}

function UserForm({ busy, onSubmit }: { busy: boolean; onSubmit: (payload: { username: string; email?: string; display_name: string; password: string; is_system_admin: boolean }) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ username: String(data.get("username") ?? "").trim(), email: String(data.get("email") ?? "").trim() || undefined, display_name: String(data.get("display_name") ?? "").trim(), password: String(data.get("password") ?? ""), is_system_admin: data.get("is_system_admin") === "on" }); event.currentTarget.reset(); }
  return <form className="mt-3 grid gap-2 sm:grid-cols-2" onSubmit={submit}><Input name="username" placeholder="用户名" required /><Input name="display_name" placeholder="显示名称" required /><Input name="email" placeholder="邮箱（可选）" type="email" /><Input name="password" minLength={12} placeholder="至少 12 位密码" required type="password" /><Label className="flex items-center gap-2 text-xs"><input name="is_system_admin" type="checkbox" />系统管理员</Label><Button disabled={busy} size="sm" type="submit">创建用户</Button></form>;
}

function RoleForm({ busy, onSubmit, permissions }: { busy: boolean; onSubmit: (payload: { name: string; description: string; permission_keys: string[] }) => void; permissions: Array<{ key: string; description: string }> }) {
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ name: String(data.get("name") ?? "").trim(), description: String(data.get("description") ?? "").trim(), permission_keys: data.getAll("permission_key").map(String) }); event.currentTarget.reset(); }
  return <form className="mt-3 space-y-3" onSubmit={submit}><Input name="name" placeholder="角色名称" required /><Input name="description" placeholder="角色说明" /><PermissionChooser permissions={permissions} /><Button disabled={busy} size="sm" type="submit">保存角色</Button></form>;
}

function RoleEditor({ busy, onSubmit, permissions, role }: { busy: boolean; onSubmit: (payload: { description?: string; permission_keys?: string[] }) => void; permissions: Array<{ key: string; description: string }>; role: { description: string; permission_keys: string[] } }) {
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ description: String(data.get("description") ?? "").trim(), permission_keys: data.getAll("permission_key").map(String) }); }
  return <form className="mt-4 space-y-3 border-t pt-3" onSubmit={submit}><Input defaultValue={role.description} name="description" placeholder="角色说明" /><PermissionChooser initial={role.permission_keys} permissions={permissions} /><Button disabled={busy} size="xs" type="submit">更新角色</Button></form>;
}

function PermissionChooser({ initial = [], permissions }: { initial?: string[]; permissions: Array<{ key: string; description: string }> }) {
  return <div className="grid gap-2 sm:grid-cols-2">{permissions.map((permission) => <Label className="flex items-start gap-2 rounded-lg border p-2 text-xs" key={permission.key}><input defaultChecked={initial.includes(permission.key)} name="permission_key" type="checkbox" value={permission.key} /><span><span className="font-mono">{permission.key}</span><span className="mt-0.5 block text-muted-foreground">{permission.description}</span></span></Label>)}{!permissions.length ? <p className="text-xs text-muted-foreground">权限目录需要系统管理员读取后才会显示。</p> : null}</div>;
}

function MembershipForm({ busy, onSubmit, roles, users }: { busy: boolean; onSubmit: (payload: { user_id: string; role_id: string }) => void; roles: Array<{ id: string; name: string }>; users: Array<{ id: string; display_name: string; username: string }> }) {
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ user_id: String(data.get("user_id") ?? ""), role_id: String(data.get("role_id") ?? "") }); event.currentTarget.reset(); }
  return <form className="mt-3 grid gap-2 sm:grid-cols-3" onSubmit={submit}><select className="h-9 rounded-lg border bg-transparent px-3 text-sm" name="user_id" required><option value="">选择用户</option>{users.map((user) => <option key={user.id} value={user.id}>{user.display_name} · {user.username}</option>)}</select><select className="h-9 rounded-lg border bg-transparent px-3 text-sm" name="role_id" required><option value="">选择角色</option>{roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}</select><Button disabled={busy || !users.length || !roles.length} size="sm" type="submit">添加</Button></form>;
}

/** Runtime controls embedded by the unified Extensions Hub (D-076). */
export function RuntimeControlsBody({
  defaultTab = "mcp",
}: {
  defaultTab?: "mcp" | "components" | "sandbox";
}) {
  return (
    <Tabs defaultValue={defaultTab}>
      <TabsList aria-label="运行时控制分类" className="w-full justify-start">
        <TabsTrigger value="mcp">
          <Network />
          MCP 与 Skills 修订
        </TabsTrigger>
        <TabsTrigger value="components">
          <PackageCheck />
          可信组件
        </TabsTrigger>
        <TabsTrigger value="sandbox">
          <TerminalSquare />
          Sandbox
        </TabsTrigger>
      </TabsList>
      <TabsContent className="mt-5" value="mcp">
        <McpAdministration />
      </TabsContent>
      <TabsContent className="mt-5" value="components">
        <ComponentAdministration />
      </TabsContent>
      <TabsContent className="mt-5" value="sandbox">
        <SandboxAdministration />
      </TabsContent>
    </Tabs>
  );
}

function McpAdministration() {
  const queryClient = useQueryClient();
  const [serverChoice, setServerChoice] = useState("");
  const [skillChoice, setSkillChoice] = useState("");
  const transports = useQuery({ queryKey: ["mcp-transport-capabilities"], queryFn: listMcpTransportCapabilities });
  const servers = useQuery({ queryKey: ["mcp-servers"], queryFn: listMcpServers });
  const skills = useQuery({ queryKey: ["skills"], queryFn: listSkills });
  const selectedServerId = serverChoice || servers.data?.[0]?.id || "";
  const selectedSkillId = skillChoice || skills.data?.[0]?.id || "";
  const server = useQuery({ queryKey: ["mcp-server", selectedServerId], queryFn: () => getMcpServer(selectedServerId), enabled: Boolean(selectedServerId) });
  const snapshots = useQuery({ queryKey: ["mcp-snapshots", selectedServerId], queryFn: () => listMcpSnapshots(selectedServerId), enabled: Boolean(selectedServerId) });
  const skill = useQuery({ queryKey: ["skill", selectedSkillId], queryFn: () => getSkill(selectedSkillId), enabled: Boolean(selectedSkillId) });
  const updateServerMutation = useMutation({ mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof updateMcpServer>[1] }) => updateMcpServer(id, payload), onSuccess: () => { toast.success("MCP Server 已更新"); void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] }); void queryClient.invalidateQueries({ queryKey: ["mcp-server", selectedServerId] }); }, onError: (error) => toast.error(error.message) });
  const updateSkillMutation = useMutation({ mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof updateSkill>[1] }) => updateSkill(id, payload), onSuccess: () => { toast.success("Skill 已更新"); void queryClient.invalidateQueries({ queryKey: ["skills"] }); void queryClient.invalidateQueries({ queryKey: ["skill", selectedSkillId] }); }, onError: (error) => toast.error(error.message) });
  return <div className="space-y-5"><Surface className="p-5"><SectionHeading description="能力声明来自服务端实现，而不是前端的 transport 下拉框。" title="Transport 能力" />{transports.isError ? <QueryFailure message={transports.error.message} /> : null}<div className="mt-4 grid gap-3 sm:grid-cols-2">{transports.data?.map((item) => <div className="rounded-xl border p-4" key={item.transport}><div className="flex items-center justify-between gap-3"><p className="font-mono text-sm">{item.transport}</p><StatePill status={item.available ? "approved" : "failed"} label={item.available ? "可用" : "不可用"} /></div><p className="mt-2 text-xs text-muted-foreground">协议 {item.protocol_version ?? "—"} · 真实执行 {item.supports_real_execution ? "支持" : "未支持"}</p><p className="mt-1 text-xs text-muted-foreground">{item.reason}</p></div>)}</div></Surface><div className="grid gap-5 xl:grid-cols-2"><Surface className="p-5"><SectionHeading title="MCP Server 详情与快照" />{servers.isError ? <QueryFailure message={servers.error.message} /> : null}<ServerChooser onChange={setServerChoice} servers={servers.data ?? []} value={selectedServerId} />{server.data ? <McpServerEditor busy={updateServerMutation.isPending} onSubmit={(payload) => updateServerMutation.mutate({ id: server.data!.id, payload })} server={server.data} /> : server.isError ? <QueryFailure message={server.error.message} /> : null}{snapshots.data?.length ? <div className="mt-4 space-y-2"><p className="text-xs font-semibold">能力快照</p>{snapshots.data.map((snapshot) => <details className="rounded-lg border p-3" key={snapshot.id}><summary className="flex cursor-pointer list-none items-center gap-2 text-xs"><span className="min-w-0 flex-1 font-mono">#{snapshot.sequence} · {snapshot.snapshot_hash.slice(0, 12)}</span><StatePill status={snapshot.changed ? "pending" : "approved"} label={snapshot.changed ? "已变化" : "一致"} /></summary><pre className="mt-3 max-h-48 overflow-auto rounded bg-muted p-3 text-[10px]">{JSON.stringify({ capabilities: snapshot.capabilities, tools: snapshot.tools, resources: snapshot.resources, prompts: snapshot.prompts }, null, 2)}</pre></details>)}</div> : snapshots.isSuccess && selectedServerId ? <p className="mt-4 text-sm text-muted-foreground">尚无能力快照。请先在基础工具页刷新 Server。</p> : null}</Surface><Surface className="p-5"><SectionHeading title="Skill 详情与修订" />{skills.isError ? <QueryFailure message={skills.error.message} /> : null}<SkillChooser onChange={setSkillChoice} skills={skills.data ?? []} value={selectedSkillId} />{skill.data ? <SkillEditor busy={updateSkillMutation.isPending} onSubmit={(payload) => updateSkillMutation.mutate({ id: skill.data!.id, payload })} skill={skill.data} /> : skill.isError ? <QueryFailure message={skill.error.message} /> : null}</Surface></div></div>;
}

function ServerChooser({ onChange, servers, value }: { onChange: (value: string) => void; servers: MCPServer[]; value: string }) { return <div className="mt-4"><Label htmlFor="runtime-mcp-server">Server</Label><select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" id="runtime-mcp-server" onChange={(event) => onChange(event.target.value)} value={value}>{servers.map((server) => <option key={server.id} value={server.id}>{server.display_name} · {server.transport}</option>)}</select>{!servers.length ? <p className="mt-3 text-sm text-muted-foreground">尚未注册 MCP Server。请先在基础工具页注册。</p> : null}</div>; }

function McpServerEditor({ busy, onSubmit, server }: { busy: boolean; onSubmit: (payload: Parameters<typeof updateMcpServer>[1]) => void; server: MCPServer }) { function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); const manifest = parseObject(data.get("manifest"), "MCP Manifest"); if (!manifest) return; onSubmit({ display_name: String(data.get("display_name") ?? "").trim() || undefined, source: String(data.get("source") ?? "").trim(), version: String(data.get("version") ?? "").trim(), endpoint_url: String(data.get("endpoint_url") ?? "").trim() || null, bearer_token: String(data.get("bearer_token") ?? "").trim() || undefined, clear_bearer_token: data.get("clear_bearer_token") === "on", manifest, agent_auto_invoke: data.get("agent_auto_invoke") === "on", timeout_ms: Number(data.get("timeout_ms")) || undefined, max_input_bytes: Number(data.get("max_input_bytes")) || undefined, max_result_bytes: Number(data.get("max_result_bytes")) || undefined, max_concurrency: Number(data.get("max_concurrency")) || undefined }); } return <form className="mt-4 space-y-3" key={server.id} onSubmit={submit}><div className="grid gap-3 sm:grid-cols-2"><Label>显示名称<Input defaultValue={server.display_name} name="display_name" /></Label><Label>来源<Input defaultValue={server.source} name="source" required /></Label><Label>版本<Input defaultValue={server.version} name="version" required /></Label><Label>Endpoint URL<Input defaultValue={server.endpoint_url ?? ""} name="endpoint_url" type="url" /></Label><Label>超时（ms）<Input defaultValue={server.timeout_ms} min="100" name="timeout_ms" type="number" /></Label><Label>最大并发<Input defaultValue={server.max_concurrency} min="1" name="max_concurrency" type="number" /></Label></div><Label>Manifest JSON<Textarea className="mt-2 font-mono text-xs" defaultValue={JSON.stringify(server.manifest_json, null, 2)} name="manifest" rows={9} required /></Label><details className="rounded-lg border p-3"><summary className="cursor-pointer text-xs">轮换或移除 Bearer Secret</summary><div className="mt-3 grid gap-2 sm:grid-cols-2"><Label>新 Secret<Input autoComplete="off" name="bearer_token" type="password" /></Label><Label className="flex items-center gap-2 pt-5 text-xs"><input name="clear_bearer_token" type="checkbox" />移除现有 Secret</Label></div></details><Label className="flex items-center gap-2 text-xs"><input defaultChecked={server.agent_auto_invoke} name="agent_auto_invoke" type="checkbox" />允许 Agent 自动调用（仍受授权快照约束）</Label><Button disabled={busy} size="sm" type="submit"><ServerCog className="size-4" />保存 MCP 配置</Button></form>; }

function SkillChooser({ onChange, skills, value }: { onChange: (value: string) => void; skills: Skill[]; value: string }) { return <div className="mt-4"><Label htmlFor="runtime-skill">Skill</Label><select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" id="runtime-skill" onChange={(event) => onChange(event.target.value)} value={value}>{skills.map((skill) => <option key={skill.id} value={skill.id}>{skill.name} · v{skill.version}</option>)}</select>{!skills.length ? <p className="mt-3 text-sm text-muted-foreground">尚未安装 Skill。请先在基础工具页安装。</p> : null}</div>; }

function SkillEditor({ busy, onSubmit, skill }: { busy: boolean; onSubmit: (payload: Parameters<typeof updateSkill>[1]) => void; skill: Skill }) { function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); const manifest = parseObject(data.get("manifest"), "Skill Manifest"); if (!manifest) return; onSubmit({ name: String(data.get("name") ?? "").trim() || undefined, source: String(data.get("source") ?? "").trim(), version: String(data.get("version") ?? "").trim(), manifest }); } return <form className="mt-4 space-y-3" key={skill.id} onSubmit={submit}><div className="grid gap-3 sm:grid-cols-2"><Label>名称<Input defaultValue={skill.name} name="name" /></Label><Label>来源<Input defaultValue={skill.source} name="source" required /></Label><Label>版本<Input defaultValue={skill.version} name="version" required /></Label><div className="rounded-lg border p-2 text-xs">状态：<StatePill status={skill.status} /></div></div><Label>声明式 Manifest JSON<Textarea className="mt-2 font-mono text-xs" defaultValue={JSON.stringify(skill.manifest_json, null, 2)} name="manifest" rows={10} required /></Label><Button disabled={busy} size="sm" type="submit"><FileCog className="size-4" />保存 Skill 修订</Button></form>; }

function ComponentAdministration() {
  const queryClient = useQueryClient();
  const [pluginChoice, setPluginChoice] = useState("");
  const [artifact, setArtifact] = useState<unknown>(null);
  const [eventResult, setEventResult] = useState<unknown>(null);
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: listPlugins });
  const selectedPluginId = pluginChoice || plugins.data?.[0]?.id || "";
  const manifests = useQuery({ queryKey: ["component-manifests", selectedPluginId], queryFn: () => listComponentManifests(selectedPluginId), enabled: Boolean(selectedPluginId) });
  const authorizations = useQuery({ queryKey: ["component-authorizations", selectedPluginId], queryFn: () => listComponentAuthorizations(selectedPluginId), enabled: Boolean(selectedPluginId) });
  const checks = useQuery({ queryKey: ["component-checks", selectedPluginId], queryFn: () => listComponentChecks(selectedPluginId), enabled: Boolean(selectedPluginId) });
  const register = useMutation({ mutationFn: registerComponent, onSuccess: (result) => { toast.success(`组件 ${result.manifest.display_name} 已登记`); setPluginChoice(result.plugin.id); void queryClient.invalidateQueries({ queryKey: ["plugins"] }); void queryClient.invalidateQueries({ queryKey: ["component-manifests", result.plugin.id] }); }, onError: (error) => toast.error(error.message) });
  const authorize = useMutation({ mutationFn: ({ pluginId, manifestId }: { pluginId: string; manifestId: string }) => authorizeComponent(pluginId, manifestId), onSuccess: () => { toast.success("组件版本已授权"); void queryClient.invalidateQueries({ queryKey: ["component-authorizations", selectedPluginId] }); }, onError: (error) => toast.error(error.message) });
  const revoke = useMutation({ mutationFn: ({ pluginId, reason }: { pluginId: string; reason: string }) => revokeComponentAuthorization(pluginId, reason), onSuccess: () => { toast.success("组件授权已撤销"); void queryClient.invalidateQueries({ queryKey: ["component-authorizations", selectedPluginId] }); }, onError: (error) => toast.error(error.message) });
  const check = useMutation({ mutationFn: ({ pluginId, manifestId, checkType, sampleData }: { pluginId: string; manifestId: string; checkType: "health" | "render"; sampleData?: Record<string, unknown> }) => runComponentCheck(pluginId, { manifest_version_id: manifestId, check_type: checkType, sample_data: sampleData }), onSuccess: () => { toast.success("组件检查已记录"); void queryClient.invalidateQueries({ queryKey: ["component-checks", selectedPluginId] }); }, onError: (error) => toast.error(error.message) });
  const artifactMutation = useMutation({ mutationFn: ({ pluginId, manifestId, data }: { pluginId: string; manifestId: string; data: Record<string, unknown> }) => prepareComponentArtifact(pluginId, { manifest_version_id: manifestId, data }), onSuccess: (result) => { setArtifact(result); toast.success("Artifact 已按授权边界生成"); }, onError: (error) => toast.error(error.message) });
  const validateEvent = useMutation({ mutationFn: ({ pluginId, manifestId, event }: { pluginId: string; manifestId: string; event: Record<string, unknown> }) => validateComponentEvent(pluginId, { manifest_version_id: manifestId, event }), onSuccess: (result) => { setEventResult(result); toast.success(result.accepted ? "组件事件通过 Schema 校验" : "组件事件未被接受"); }, onError: (error) => toast.error(error.message) });
  return <div className="space-y-5"><Surface className="p-5"><SectionHeading description="导入内容会先经服务端 Manifest、签名和 Schema 检查；未知组件不会直接注入 React 或任意 HTML。" title="登记可信组件" /><ComponentRegistrationForm busy={register.isPending} onSubmit={(payload) => register.mutate(payload)} /></Surface><Surface className="p-5"><SectionHeading title="组件版本、授权与运行检查" />{plugins.isError ? <QueryFailure message={plugins.error.message} /> : null}<div className="mt-4"><Label htmlFor="component-plugin">Plugin</Label><select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" id="component-plugin" onChange={(event) => setPluginChoice(event.target.value)} value={selectedPluginId}>{plugins.data?.map((plugin) => <option key={plugin.id} value={plugin.id}>{plugin.name} · {plugin.plugin_type}</option>)}</select></div>{manifests.isError ? <QueryFailure message={manifests.error.message} /> : null}<div className="mt-4 space-y-3">{manifests.data?.map((manifest) => <ComponentManifestCard artifactBusy={artifactMutation.isPending} artifactResult={artifact} authorization={authorizations.data?.find((item) => item.manifest_version_id === manifest.id && item.status !== "revoked")} authorizeBusy={authorize.isPending} checkBusy={check.isPending} checks={checks.data?.filter((item) => item.manifest_version_id === manifest.id) ?? []} eventBusy={validateEvent.isPending} eventResult={eventResult} key={manifest.id} manifest={manifest} onAuthorize={() => authorize.mutate({ pluginId: selectedPluginId, manifestId: manifest.id })} onCheck={(checkType, sampleData) => check.mutate({ pluginId: selectedPluginId, manifestId: manifest.id, checkType, sampleData })} onPrepareArtifact={(data) => artifactMutation.mutate({ pluginId: selectedPluginId, manifestId: manifest.id, data })} onValidateEvent={(event) => validateEvent.mutate({ pluginId: selectedPluginId, manifestId: manifest.id, event })} revokeBusy={revoke.isPending} onRevoke={(reason) => revoke.mutate({ pluginId: selectedPluginId, reason })} />)}{manifests.isSuccess && !manifests.data.length ? <EmptyState description="先导入一个经过校验的组件 Manifest，才能进行授权、检查或 Artifact 交付。" title="该 Plugin 没有组件版本" /> : null}</div></Surface></div>;
}

function ComponentRegistrationForm({ busy, onSubmit }: { busy: boolean; onSubmit: (payload: Record<string, unknown>) => void }) { const [manifestText, setManifestText] = useState(""); function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const payload = parseObject(manifestText, "组件 Manifest"); if (!payload) return; onSubmit(payload); } return <form className="mt-4 space-y-3" onSubmit={submit}><Label htmlFor="component-manifest-import">Component Manifest JSON</Label><Textarea id="component-manifest-import" onChange={(event) => setManifestText(event.target.value)} placeholder='{"component_id":"...","version":"1.0.0", ...}' rows={10} value={manifestText} /><p className="text-xs leading-5 text-muted-foreground">请提供完整、已签名（如适用）的 Manifest。服务端会核验版本、哈希、权限、数据 Schema 和事件 Schema。</p><Button disabled={busy || !manifestText.trim()} size="sm" type="submit"><PackageCheck className="size-4" />导入并检查</Button></form>; }

function ComponentManifestCard({ artifactBusy, artifactResult, authorization, authorizeBusy, checkBusy, checks, eventBusy, eventResult, manifest, onAuthorize, onCheck, onPrepareArtifact, onRevoke, onValidateEvent, revokeBusy }: { artifactBusy: boolean; artifactResult: unknown; authorization: { status: string } | undefined; authorizeBusy: boolean; checkBusy: boolean; checks: Array<{ id: string; check_type: string; status: string; runtime_executed: boolean; checked_at: string; details: Record<string, unknown> }>; eventBusy: boolean; eventResult: unknown; manifest: { component_id: string; version: string; display_name: string; renderer: string; package_hash_status: string; signature_status: string; id: string; example_data: Record<string, unknown> }; onAuthorize: () => void; onCheck: (type: "health" | "render", data?: Record<string, unknown>) => void; onPrepareArtifact: (data: Record<string, unknown>) => void; onRevoke: (reason: string) => void; onValidateEvent: (event: Record<string, unknown>) => void; revokeBusy: boolean }) { function parsedAction(event: FormEvent<HTMLFormElement>, target: (value: Record<string, unknown>) => void, label: string) { event.preventDefault(); const data = new FormData(event.currentTarget); const parsed = parseObject(data.get("json"), label); if (parsed) target(parsed); } return <details className="rounded-xl border p-4"><summary className="flex cursor-pointer list-none items-center gap-3"><Boxes className="size-4 text-primary" /><span className="min-w-0 flex-1"><span className="block font-medium">{manifest.display_name}</span><span className="font-mono text-xs text-muted-foreground">{manifest.component_id} · v{manifest.version}</span></span><StatePill status={authorization ? authorization.status : "pending"} label={authorization ? authorization.status : "未授权"} /></summary><div className="mt-4 grid gap-3 md:grid-cols-2"><KeyValueGrid items={[{ label: "Renderer", value: manifest.renderer }, { label: "包哈希", value: manifest.package_hash_status }, { label: "签名", value: manifest.signature_status }, { label: "已记录检查", value: checks.length }]} /><div className="flex flex-wrap content-start gap-2"><Button disabled={authorizeBusy || Boolean(authorization)} onClick={onAuthorize} size="xs">授权此版本</Button><Button disabled={revokeBusy || !authorization} onClick={() => onRevoke("workspace_user_revoked")} size="xs" variant="outline">撤销授权</Button><Button disabled={checkBusy} onClick={() => onCheck("health")} size="xs" variant="outline">健康检查</Button><Button disabled={checkBusy} onClick={() => onCheck("render", manifest.example_data)} size="xs" variant="outline">渲染检查</Button></div></div><div className="mt-4 grid gap-3 lg:grid-cols-2"><form className="rounded-lg border p-3" onSubmit={(event) => parsedAction(event, onPrepareArtifact, "Artifact 数据")}><p className="text-xs font-semibold">生成受控 Artifact</p><Textarea className="mt-2 font-mono text-xs" defaultValue={JSON.stringify(manifest.example_data, null, 2)} name="json" rows={5} /><Button className="mt-2" disabled={artifactBusy || !authorization} size="xs" type="submit">准备 Artifact</Button></form><form className="rounded-lg border p-3" onSubmit={(event) => parsedAction(event, onValidateEvent, "组件事件")}><p className="text-xs font-semibold">验证组件事件</p><Textarea className="mt-2 font-mono text-xs" name="json" placeholder='{"action":"..."}' rows={5} /><Button className="mt-2" disabled={eventBusy || !authorization} size="xs" type="submit" variant="outline">验证事件</Button></form></div>{checks.length ? <div className="mt-3 space-y-2">{checks.map((check) => <details className="rounded-lg border p-3" key={check.id}><summary className="flex cursor-pointer list-none items-center gap-2 text-xs"><span className="min-w-0 flex-1">{check.check_type} · {formatDate(check.checked_at)}</span><StatePill status={check.status} /><Badge variant="outline">{check.runtime_executed ? "已执行" : "未执行 runtime"}</Badge></summary><pre className="mt-2 max-h-36 overflow-auto rounded bg-muted p-2 text-[10px]">{JSON.stringify(check.details, null, 2)}</pre></details>)}</div> : null}{artifactResult ? <pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-muted p-3 text-[10px]">{JSON.stringify(artifactResult, null, 2)}</pre> : null}{eventResult ? <pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-muted p-3 text-[10px]">{JSON.stringify(eventResult, null, 2)}</pre> : null}</details>; }

function SandboxAdministration() {
  const queryClient = useQueryClient();
  const [selectedTaskChoice, setSelectedTaskChoice] = useState("");
  const bootstrap = useQuery({
    queryKey: ["sandbox-bootstrap-status"],
    queryFn: getSandboxBootstrapStatus,
    refetchInterval: (query) => {
      const job = query.state.data?.active_job;
      return job && job.status === "running" ? 1500 : false;
    },
  });
  const profiles = useQuery({ queryKey: ["sandbox-profiles"], queryFn: listSandboxProfiles });
  const sandboxSessions = useQuery({ queryKey: ["sandbox-sessions"], queryFn: () => listSandboxSessions() });
  const tasks = useQuery({ queryKey: ["sandbox-tasks"], queryFn: () => listSandboxTasks() });
  const chatSessions = useQuery({ queryKey: ["sessions"], queryFn: listSessions });
  const files = useQuery({ queryKey: ["files"], queryFn: () => listFiles() });
  const selectedTaskId = selectedTaskChoice || tasks.data?.[0]?.id || "";
  const task = useQuery({ queryKey: ["sandbox-task", selectedTaskId], queryFn: () => getSandboxTask(selectedTaskId), enabled: Boolean(selectedTaskId) });
  const executions = useQuery({ queryKey: ["sandbox-executions", selectedTaskId], queryFn: () => listSandboxExecutions(selectedTaskId), enabled: Boolean(selectedTaskId) });
  const create = useMutation({ mutationFn: createSandboxTask, onSuccess: (result) => { setSelectedTaskChoice(result.id); toast.success(result.status === "completed" ? "Sandbox 任务已完成" : `Sandbox 任务状态：${result.status}`); void queryClient.invalidateQueries({ queryKey: ["sandbox-tasks"] }); void queryClient.invalidateQueries({ queryKey: ["sandbox-sessions"] }); }, onError: (error) => toast.error(error.message) });
  const cancel = useMutation({ mutationFn: cancelSandboxTask, onSuccess: () => { toast.success("Sandbox 任务已取消"); void queryClient.invalidateQueries({ queryKey: ["sandbox-tasks"] }); void queryClient.invalidateQueries({ queryKey: ["sandbox-task", selectedTaskId] }); }, onError: (error) => toast.error(error.message) });
  const cleanup = useMutation({ mutationFn: cleanupSandboxSession, onSuccess: () => { toast.success("Sandbox Session 清理已完成"); void queryClient.invalidateQueries({ queryKey: ["sandbox-sessions"] }); }, onError: (error) => toast.error(error.message) });
  const initBootstrap = useMutation({
    mutationFn: startSandboxBootstrap,
    onSuccess: (result) => {
      if (!result.accepted) {
        toast.error(result.error_message || "无法启动沙箱初始化");
      } else {
        toast.success(result.joined_existing ? "已加入进行中的初始化任务" : "沙箱初始化已开始");
      }
      void queryClient.invalidateQueries({ queryKey: ["sandbox-bootstrap-status"] });
      void queryClient.invalidateQueries({ queryKey: ["sandbox-profiles"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const bootstrapStatus = bootstrap.data;
  const activeJob = bootstrapStatus?.active_job;
  const progress = activeJob?.progress_percent ?? bootstrapStatus?.progress_percent ?? 0;
  return <div className="space-y-5"><Surface className="p-5"><SectionHeading description="在任意已安装 Docker 的设备上点击初始化即可构建 Runner（Python + Node）。digest 由后端自动保存，无需手改 .env。" title="沙箱一键初始化" />{bootstrap.isError ? <QueryFailure message={bootstrap.error.message} /> : null}{bootstrapStatus ? <div className="mt-4 space-y-3"><div className="flex flex-wrap items-center gap-3"><StatePill status={bootstrapStatus.image_ready ? "approved" : activeJob ? "pending" : "failed"} label={bootstrapStatus.image_ready ? "已就绪" : activeJob ? "初始化中" : "未就绪"} /><p className="text-sm text-muted-foreground">{bootstrapStatus.message}</p></div><div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div><KeyValueGrid items={[{ label: "Docker", value: bootstrapStatus.docker_reachable ? "可达" : "不可达" }, { label: "镜像 digest", value: <span className="font-mono text-[10px]">{bootstrapStatus.image_digest ?? "—"}</span> }, { label: "配置来源", value: bootstrapStatus.image_source ?? "—" }, { label: "阶段", value: bootstrapStatus.phase }]} /><ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{bootstrapStatus.remediation_steps.map((step) => <li key={step}>{step}</li>)}</ul>{activeJob?.log_tail?.length ? <pre className="max-h-40 overflow-auto rounded-lg bg-muted p-3 text-[10px]">{activeJob.log_tail.join("\n")}</pre> : null}<div className="flex flex-wrap gap-2"><Button disabled={initBootstrap.isPending || Boolean(activeJob) || !bootstrapStatus.can_initialize} onClick={() => initBootstrap.mutate()} size="sm"><Play className="size-4" />{activeJob ? "初始化进行中…" : bootstrapStatus.image_ready ? "重新构建沙箱" : "初始化沙箱"}</Button><Button onClick={() => { void bootstrap.refetch(); void profiles.refetch(); }} size="sm" variant="outline"><RefreshCcw className="size-4" />刷新状态</Button></div>{bootstrapStatus.last_failed_job && !activeJob ? <p className="text-xs text-amber-800 dark:text-amber-200">上次失败：{bootstrapStatus.last_failed_job.error_code} · {bootstrapStatus.last_failed_job.error_message}</p> : null}</div> : bootstrap.isLoading ? <LoadingState label="正在读取沙箱初始化状态…" /> : null}</Surface><Surface className="p-5"><SectionHeading description="Profile 来自当前运行时探测；Docker 或镜像不可用时会明确标为 unavailable。" title="Sandbox Profile" />{profiles.isError ? <QueryFailure message={profiles.error.message} /> : null}<div className="mt-4 grid gap-3 sm:grid-cols-2">{profiles.data?.map((profile) => <div className="rounded-xl border p-4" key={profile.backend_id}><div className="flex items-center justify-between gap-3"><p className="font-medium">{profile.backend_id}</p><StatePill status={profile.available ? "approved" : "failed"} label={profile.available ? "可用" : "不可用"} /></div><p className="mt-2 text-xs text-muted-foreground">{profile.platform} · 固定镜像 {profile.image_pinned ? "是" : "否"}</p><p className="mt-1 text-xs text-muted-foreground">{profile.reason ?? profile.capabilities.join(" / ")}</p></div>)}</div></Surface><div className="grid gap-5 xl:grid-cols-[.85fr_1.15fr]"><Surface className="p-5"><SectionHeading description="文件和会话均从当前工作区真实记录选择；任务会使用隔离 Runner，而不是 API 进程直接执行。" title="创建 Sandbox 任务" /><SandboxTaskForm busy={create.isPending} chatSessions={chatSessions.data ?? []} files={files.data ?? []} onSubmit={(payload) => create.mutate(payload)} sandboxSessions={sandboxSessions.data ?? []} /></Surface><Surface className="p-5"><SectionHeading title="持久化任务与执行历史" />{tasks.isError ? <QueryFailure message={tasks.error.message} /> : null}<div className="mt-4"><Label htmlFor="sandbox-task-select">任务</Label><select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" id="sandbox-task-select" onChange={(event) => setSelectedTaskChoice(event.target.value)} value={selectedTaskId}>{tasks.data?.map((item) => <option key={item.id} value={item.id}>{item.task_type} · {item.status} · {formatDate(item.created_at)}</option>)}</select></div>{task.data ? <div className="mt-4 space-y-3"><KeyValueGrid items={[{ label: "任务状态", value: <StatePill status={task.data.status} /> }, { label: "Sandbox Session", value: <span className="font-mono text-xs">{task.data.sandbox_session_id}</span> }, { label: "文件", value: <span className="font-mono text-xs">{task.data.file_id}</span> }, { label: "错误", value: task.data.error_class ?? "无" }]} /><div className="flex flex-wrap gap-2"><Button disabled={cancel.isPending || ["completed", "failed", "cancelled"].includes(task.data.status)} onClick={() => cancel.mutate(task.data!.id)} size="xs" variant="outline">取消任务</Button><Button onClick={() => { void task.refetch(); void executions.refetch(); }} size="xs" variant="ghost"><RefreshCcw className="size-3" />刷新</Button></div><pre className="max-h-52 overflow-auto rounded-lg bg-muted p-3 text-[10px]">{JSON.stringify(task.data.artifact_json, null, 2)}</pre>{executions.data?.map((execution) => <details className="rounded-lg border p-3" key={execution.id}><summary className="flex cursor-pointer list-none items-center gap-2 text-xs"><span className="min-w-0 flex-1">尝试 #{execution.attempt_no} · {execution.latency_ms} ms</span><StatePill status={execution.status} /></summary><pre className="mt-2 max-h-40 overflow-auto rounded bg-muted p-2 text-[10px]">{JSON.stringify(execution, null, 2)}</pre></details>)}</div> : tasks.isSuccess && !tasks.data.length ? <EmptyState description="创建任务后，这里会保留执行、错误与 Artifact 的可追溯历史。" title="尚无 Sandbox 任务" /> : task.isError ? <QueryFailure message={task.error.message} /> : null}</Surface></div><Surface className="p-5"><SectionHeading title="Sandbox Session 清理" />{sandboxSessions.isError ? <QueryFailure message={sandboxSessions.error.message} /> : null}<div className="mt-4 space-y-2">{sandboxSessions.data?.map((session) => <div className="flex flex-col gap-3 rounded-xl border p-3 sm:flex-row sm:items-center" key={session.id}><div className="min-w-0 flex-1"><p className="font-mono text-xs">{session.id}</p><p className="mt-1 text-xs text-muted-foreground">{session.backend_id} · 过期 {formatDate(session.expires_at)} · 清理 {session.cleanup_status}</p></div><StatePill status={session.status} /><Button disabled={cleanup.isPending || session.cleanup_status === "cleaned"} onClick={() => cleanup.mutate(session.id)} size="xs" variant="outline">清理</Button></div>)}{sandboxSessions.isSuccess && !sandboxSessions.data.length ? <p className="py-5 text-sm text-muted-foreground">当前身份还没有 Sandbox Session。</p> : null}</div></Surface></div>;
}

function SandboxTaskForm({ busy, chatSessions, files, onSubmit, sandboxSessions }: { busy: boolean; chatSessions: Array<{ id: string; title: string }>; files: Array<{ id: string; original_name: string }>; onSubmit: (payload: { chat_session_id: string; file_id: string; task_type: "file_inspect" | "extract_inert_text"; output_format: "metadata_json" | "text_bundle"; sandbox_session_id?: string }) => void; sandboxSessions: Array<{ id: string; chat_session_id: string; status: string }> }) { function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ chat_session_id: String(data.get("chat_session_id") ?? ""), file_id: String(data.get("file_id") ?? ""), task_type: String(data.get("task_type") ?? "file_inspect") as "file_inspect" | "extract_inert_text", output_format: String(data.get("output_format") ?? "metadata_json") as "metadata_json" | "text_bundle", sandbox_session_id: String(data.get("sandbox_session_id") ?? "") || undefined }); } return <form className="mt-4 space-y-3" onSubmit={submit}><Label>学习会话<select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" name="chat_session_id" required><option value="">选择会话</option>{chatSessions.map((session) => <option key={session.id} value={session.id}>{session.title}</option>)}</select></Label><Label>文件<select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" name="file_id" required><option value="">选择文件</option>{files.map((file) => <option key={file.id} value={file.id}>{file.original_name}</option>)}</select></Label><div className="grid gap-3 sm:grid-cols-2"><Label>任务类型<select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" name="task_type"><option value="file_inspect">文件安全检查</option><option value="extract_inert_text">提取惰性文本</option></select></Label><Label>输出<select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" name="output_format"><option value="metadata_json">元数据 JSON</option><option value="text_bundle">文本包</option></select></Label></div>{sandboxSessions.length ? <Label>复用已就绪 Session（可选）<select className="mt-2 h-9 w-full rounded-lg border bg-transparent px-3 text-sm" name="sandbox_session_id"><option value="">新建隔离 Session</option>{sandboxSessions.filter((session) => session.status === "ready").map((session) => <option key={session.id} value={session.id}>{session.id.slice(0, 12)} · {session.chat_session_id.slice(0, 8)}</option>)}</select></Label> : null}<Button disabled={busy || !chatSessions.length || !files.length} size="sm" type="submit"><Play className="size-4" />{busy ? "正在提交…" : "在 Sandbox 中执行"}</Button></form>; }

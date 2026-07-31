import type {
  ComponentArtifact,
  ComponentAuthorization,
  ComponentCheck,
  ComponentEventValidation,
  ComponentManifest,
  ComponentRegistration,
  CurrentUser,
  ManagedAuthSession,
  ManagedUser,
  McpTransportCapability,
  Membership,
  Organization,
  Permission,
  Role,
  SandboxAgentReadiness,
  SandboxBootstrapStartResult,
  SandboxBootstrapStatus,
  SandboxExecution,
  SandboxProfile,
  SandboxSession,
  SandboxTask,
} from "@/types/control";
import type {
  MCPServer,
  MCPServerManifest,
  MCPCapabilitySnapshot,
  Skill,
} from "@/types/extensions";

import { apiClient } from "./client";

export function getCurrentUser(): Promise<CurrentUser> {
  return apiClient.get<CurrentUser>("/auth/me", { workspace: false });
}

export function listAuthSessions(): Promise<ManagedAuthSession[]> {
  return apiClient.get<ManagedAuthSession[]>("/auth/sessions", {
    workspace: false,
  });
}

export function revokeAuthSession(sessionId: string): Promise<{ status: string }> {
  return apiClient.delete<{ status: string }>(
    `/auth/sessions/${encodeURIComponent(sessionId)}`,
    { workspace: false },
  );
}

export function listPermissions(): Promise<Permission[]> {
  return apiClient.get<Permission[]>("/permissions", { workspace: false });
}

export function listManagedUsers(): Promise<ManagedUser[]> {
  return apiClient.get<ManagedUser[]>("/users", { workspace: false });
}

export function createManagedUser(payload: {
  username: string;
  email?: string;
  display_name: string;
  password: string;
  is_system_admin: boolean;
}): Promise<ManagedUser> {
  return apiClient.post<ManagedUser, typeof payload>("/users", payload, {
    workspace: false,
  });
}

export function updateManagedUserStatus(
  userId: string,
  status: "active" | "disabled",
): Promise<ManagedUser> {
  return apiClient.patch<ManagedUser, { status: "active" | "disabled" }>(
    `/users/${encodeURIComponent(userId)}/status`,
    { status },
    { workspace: false },
  );
}

export function listOrganizations(): Promise<Organization[]> {
  return apiClient.get<Organization[]>("/organizations", { workspace: false });
}

export function createOrganization(payload: {
  name: string;
  workspace_name?: string;
}): Promise<Organization> {
  return apiClient.post<Organization, typeof payload>("/organizations", payload, {
    workspace: false,
  });
}

export function listRoles(organizationId: string): Promise<Role[]> {
  return apiClient.get<Role[]>(
    `/organizations/${encodeURIComponent(organizationId)}/roles`,
    { workspace: false },
  );
}

export function createRole(
  organizationId: string,
  payload: { name: string; description: string; permission_keys: string[] },
): Promise<Role> {
  return apiClient.post<Role, typeof payload>(
    `/organizations/${encodeURIComponent(organizationId)}/roles`,
    payload,
    { workspace: false },
  );
}

export function updateRole(
  organizationId: string,
  roleId: string,
  payload: { description?: string; permission_keys?: string[] },
): Promise<Role> {
  return apiClient.patch<Role, typeof payload>(
    `/organizations/${encodeURIComponent(organizationId)}/roles/${encodeURIComponent(roleId)}`,
    payload,
    { workspace: false },
  );
}

export function listMemberships(organizationId: string): Promise<Membership[]> {
  return apiClient.get<Membership[]>(
    `/organizations/${encodeURIComponent(organizationId)}/memberships`,
    { workspace: false },
  );
}

export function addMembership(
  organizationId: string,
  payload: { user_id: string; role_id: string },
): Promise<Membership> {
  return apiClient.post<Membership, typeof payload>(
    `/organizations/${encodeURIComponent(organizationId)}/memberships`,
    payload,
    { workspace: false },
  );
}

export function updateMembership(
  organizationId: string,
  membershipId: string,
  payload: { role_id?: string; status?: "active" | "revoked" },
): Promise<Membership> {
  return apiClient.patch<Membership, typeof payload>(
    `/organizations/${encodeURIComponent(organizationId)}/memberships/${encodeURIComponent(membershipId)}`,
    payload,
    { workspace: false },
  );
}

export function listMcpTransportCapabilities(): Promise<McpTransportCapability[]> {
  return apiClient.get<McpTransportCapability[]>("/mcp/transport-capabilities");
}

export function getMcpServer(serverId: string): Promise<MCPServer> {
  return apiClient.get<MCPServer>(`/mcp/servers/${encodeURIComponent(serverId)}`);
}

export function updateMcpServer(
  serverId: string,
  payload: {
    display_name?: string;
    source: string;
    version: string;
    endpoint_url: string | null;
    bearer_token?: string;
    clear_bearer_token?: boolean;
    manifest: MCPServerManifest;
    agent_auto_invoke?: boolean;
    timeout_ms?: number;
    max_input_bytes?: number;
    max_result_bytes?: number;
    max_concurrency?: number;
  },
): Promise<MCPServer> {
  return apiClient.put<MCPServer, typeof payload>(
    `/mcp/servers/${encodeURIComponent(serverId)}`,
    payload,
  );
}

export function listMcpSnapshots(
  serverId: string,
): Promise<MCPCapabilitySnapshot[]> {
  return apiClient.get<MCPCapabilitySnapshot[]>(
    `/mcp/servers/${encodeURIComponent(serverId)}/snapshots`,
  );
}

export function getSkill(skillId: string): Promise<Skill> {
  return apiClient.get<Skill>(`/skills/${encodeURIComponent(skillId)}`);
}

export function updateSkill(
  skillId: string,
  payload: {
    name?: string;
    source: string;
    version: string;
    manifest: Skill["manifest_json"];
  },
): Promise<Skill> {
  return apiClient.put<Skill, typeof payload>(
    `/skills/${encodeURIComponent(skillId)}`,
    payload,
  );
}

export function registerComponent(
  payload: Record<string, unknown>,
): Promise<ComponentRegistration> {
  return apiClient.post<ComponentRegistration, Record<string, unknown>>(
    "/plugins/components",
    payload,
  );
}

export function listComponentManifests(pluginId: string): Promise<ComponentManifest[]> {
  return apiClient.get<ComponentManifest[]>(
    `/plugins/components/${encodeURIComponent(pluginId)}/manifests`,
  );
}

export function listComponentAuthorizations(
  pluginId: string,
): Promise<ComponentAuthorization[]> {
  return apiClient.get<ComponentAuthorization[]>(
    `/plugins/components/${encodeURIComponent(pluginId)}/authorizations`,
  );
}

export function authorizeComponent(
  pluginId: string,
  manifestVersionId: string,
): Promise<ComponentAuthorization> {
  return apiClient.post<ComponentAuthorization, {
    manifest_version_id: string;
    scope: "current_workspace";
  }>(`/plugins/components/${encodeURIComponent(pluginId)}/authorizations`, {
    manifest_version_id: manifestVersionId,
    scope: "current_workspace",
  });
}

export function revokeComponentAuthorization(
  pluginId: string,
  reason: string,
): Promise<ComponentAuthorization> {
  return apiClient.post<ComponentAuthorization, { reason: string }>(
    `/plugins/components/${encodeURIComponent(pluginId)}/authorizations/revoke`,
    { reason },
  );
}

export function listComponentChecks(pluginId: string): Promise<ComponentCheck[]> {
  return apiClient.get<ComponentCheck[]>(
    `/plugins/components/${encodeURIComponent(pluginId)}/checks`,
  );
}

export function runComponentCheck(
  pluginId: string,
  payload: {
    manifest_version_id: string;
    check_type: "health" | "render";
    sample_data?: Record<string, unknown>;
  },
): Promise<ComponentCheck> {
  return apiClient.post<ComponentCheck, typeof payload>(
    `/plugins/components/${encodeURIComponent(pluginId)}/checks`,
    payload,
  );
}

export function prepareComponentArtifact(
  pluginId: string,
  payload: { manifest_version_id: string; data: Record<string, unknown> },
): Promise<ComponentArtifact> {
  return apiClient.post<ComponentArtifact, typeof payload>(
    `/plugins/components/${encodeURIComponent(pluginId)}/artifacts`,
    payload,
  );
}

export function validateComponentEvent(
  pluginId: string,
  payload: { manifest_version_id: string; event: Record<string, unknown> },
): Promise<ComponentEventValidation> {
  return apiClient.post<ComponentEventValidation, typeof payload>(
    `/plugins/components/${encodeURIComponent(pluginId)}/events/validate`,
    payload,
  );
}

export function listSandboxProfiles(): Promise<SandboxProfile[]> {
  return apiClient.get<SandboxProfile[]>("/sandbox/profiles");
}

export function getSandboxBootstrapStatus(): Promise<SandboxBootstrapStatus> {
  return apiClient.get<SandboxBootstrapStatus>("/sandbox/bootstrap/status");
}

export function getAgentSandboxReadiness(): Promise<SandboxAgentReadiness> {
  return apiClient.get<SandboxAgentReadiness>("/sandbox/agent/readiness");
}

export function startSandboxBootstrap(): Promise<SandboxBootstrapStartResult> {
  return apiClient.post<SandboxBootstrapStartResult>("/sandbox/bootstrap");
}

export function listSandboxSessions(chatSessionId?: string): Promise<SandboxSession[]> {
  return apiClient.get<SandboxSession[]>("/sandbox/sessions", {
    query: chatSessionId ? { chat_session_id: chatSessionId } : undefined,
  });
}

export function createSandboxTask(payload: {
  chat_session_id: string;
  file_id: string;
  task_type: "file_inspect" | "extract_inert_text";
  output_format: "metadata_json" | "text_bundle";
  sandbox_session_id?: string;
}): Promise<SandboxTask> {
  return apiClient.post<SandboxTask, typeof payload>("/sandbox/tasks", payload);
}

export function listSandboxTasks(chatSessionId?: string): Promise<SandboxTask[]> {
  return apiClient.get<SandboxTask[]>("/sandbox/tasks", {
    query: chatSessionId ? { chat_session_id: chatSessionId } : undefined,
  });
}

export function getSandboxTask(taskId: string): Promise<SandboxTask> {
  return apiClient.get<SandboxTask>(`/sandbox/tasks/${encodeURIComponent(taskId)}`);
}

export function listSandboxExecutions(taskId: string): Promise<SandboxExecution[]> {
  return apiClient.get<SandboxExecution[]>(
    `/sandbox/tasks/${encodeURIComponent(taskId)}/executions`,
  );
}

export function cancelSandboxTask(taskId: string): Promise<SandboxTask> {
  return apiClient.post<SandboxTask>(
    `/sandbox/tasks/${encodeURIComponent(taskId)}/cancel`,
  );
}

export function cleanupSandboxSession(sessionId: string): Promise<SandboxSession> {
  return apiClient.post<SandboxSession>(
    `/sandbox/sessions/${encodeURIComponent(sessionId)}/cleanup`,
  );
}

export function createSandboxDestructiveGrant(payload: {
  chat_session_id: string;
  path_prefix: string;
  action?: "delete_path";
  sandbox_session_id: string;
  command_intent_digest: string;
  ttl_seconds?: number;
  reason?: string;
}): Promise<{
  id: string;
  path_prefix: string;
  status: string;
  expires_at: string;
}> {
  return apiClient.post("/sandbox/authorizations", payload);
}

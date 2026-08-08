import type {
  BuiltinMcpTool,
  ExtensionInvocation,
  ExtensionPermissionGrant,
  ExternalCatalogSource,
  ExternalSkillSearchResult,
  MCPRefreshResult,
  MCPServer,
  MCPServerCreate,
  McpRegistrySearchResult,
  PermissionDecision,
  Skill,
  SkillCreate,
  SkillDeleteRequest,
  SkillFileContent,
  SkillFileTree,
  SkillFileWriteResult,
  SkillGitHubInstallPayload,
  SkillGitHubPreview,
  SkillLocalProbePolicy,
  SkillLocalProbeScan,
  SkillManualImport,
  SkillMarketList,
  SkillNpxImportResult,
  SkillPackageCreate,
  SkillSecurityScanResult,
  SkillSemanticReviewResult,
  SkillTranslateResult,
  SkillUpdateCheck,
  SkillValidateResult,
} from "@/types/extensions";

import { apiClient } from "./client";

export function listMcpServers(): Promise<MCPServer[]> {
  return apiClient.get<MCPServer[]>("/mcp/servers");
}

export function registerMcpServer(
  payload: MCPServerCreate,
): Promise<MCPServer> {
  return apiClient.post<MCPServer, MCPServerCreate>("/mcp/servers", payload);
}

export function listBuiltinMcpTools(): Promise<BuiltinMcpTool[]> {
  return apiClient.get<BuiltinMcpTool[]>("/skills/builtin-tools");
}

export function refreshMcpServer(serverId: string): Promise<MCPRefreshResult> {
  return apiClient.post<MCPRefreshResult>(
    `/mcp/servers/${encodeURIComponent(serverId)}/refresh`,
  );
}

export function authorizeMcpServer(
  serverId: string,
  decision: PermissionDecision,
  permissions: string[],
): Promise<ExtensionPermissionGrant> {
  return apiClient.post<
    ExtensionPermissionGrant,
    { decision: PermissionDecision; permissions: string[]; reason: string }
  >(`/mcp/servers/${encodeURIComponent(serverId)}/authorize`, {
    decision,
    permissions,
    reason: "workspace_user_decision",
  });
}

export function invokeMcpTool(
  serverId: string,
  toolName: string,
  argumentsJson: Record<string, unknown>,
): Promise<ExtensionInvocation> {
  return apiClient.post<
    ExtensionInvocation,
    { tool_name: string; arguments: Record<string, unknown> }
  >(`/mcp/servers/${encodeURIComponent(serverId)}/invoke`, {
    tool_name: toolName,
    arguments: argumentsJson,
  });
}

export function revokeMcpServer(serverId: string): Promise<MCPServer> {
  return apiClient.post<MCPServer, { reason: string }>(
    `/mcp/servers/${encodeURIComponent(serverId)}/revoke`,
    { reason: "workspace_user_revoked" },
  );
}

export function deleteMcpServer(serverId: string): Promise<void> {
  return apiClient.delete<void>(
    `/mcp/servers/${encodeURIComponent(serverId)}?reason=workspace_user_deleted`,
  );
}

export function listSkills(): Promise<Skill[]> {
  return apiClient.get<Skill[]>("/skills");
}

export function refreshOfficialSkills(): Promise<Skill[]> {
  return apiClient.post<Skill[]>("/skills/official-refresh");
}

export function installSkill(payload: SkillCreate): Promise<Skill> {
  return apiClient.post<Skill, SkillCreate>("/skills", payload);
}

export function createSkillPackage(
  payload: SkillPackageCreate,
): Promise<Skill> {
  return apiClient.post<Skill, SkillPackageCreate>("/skills/packages", payload);
}

export function listSkillFiles(skillId: string): Promise<SkillFileTree> {
  return apiClient.get<SkillFileTree>(
    `/skills/${encodeURIComponent(skillId)}/files`,
  );
}

export function readSkillFile(
  skillId: string,
  relativePath: string,
): Promise<SkillFileContent> {
  const encoded = relativePath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return apiClient.get<SkillFileContent>(
    `/skills/${encodeURIComponent(skillId)}/files/${encoded}`,
  );
}

export function writeSkillFile(
  skillId: string,
  relativePath: string,
  content: string,
  expectedContentHash?: string | null,
): Promise<SkillFileWriteResult> {
  const encoded = relativePath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return apiClient.put<
    SkillFileWriteResult,
    { content: string; expected_content_hash?: string | null }
  >(`/skills/${encodeURIComponent(skillId)}/files/${encoded}`, {
    content,
    expected_content_hash: expectedContentHash ?? null,
  });
}

export function deleteSkillFile(
  skillId: string,
  relativePath: string,
): Promise<Skill> {
  const encoded = relativePath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return apiClient.delete<Skill>(
    `/skills/${encodeURIComponent(skillId)}/files/${encoded}`,
  );
}

export function mkdirSkillPath(
  skillId: string,
  relativePath: string,
): Promise<SkillFileTree> {
  return apiClient.post<SkillFileTree, { relative_path: string }>(
    `/skills/${encodeURIComponent(skillId)}/files/mkdir`,
    { relative_path: relativePath },
  );
}

export function validateSkillPackage(
  skillId: string,
): Promise<SkillValidateResult> {
  return apiClient.post<SkillValidateResult>(
    `/skills/${encodeURIComponent(skillId)}/validate`,
  );
}

export function listSkillMarket(options?: {
  refresh?: boolean;
  q?: string;
  page?: number;
  pageSize?: number;
}): Promise<SkillMarketList> {
  const refresh = options?.refresh ?? false;
  const q = options?.q?.trim() ?? "";
  const page = options?.page ?? 1;
  const pageSize = options?.pageSize ?? 12;
  return apiClient.get<SkillMarketList>("/skills/market", {
    query: {
      ...(refresh ? { refresh: true } : {}),
      ...(q ? { q } : {}),
      page,
      page_size: pageSize,
    },
  });
}

export function installSkillFromMarket(payload: {
  market_id: string;
  skill_key?: string;
}): Promise<Skill> {
  return apiClient.post<Skill, { market_id: string; skill_key?: string }>(
    "/skills/market/install",
    payload,
  );
}

export function importSkillManual(payload: SkillManualImport): Promise<Skill> {
  return apiClient.post<Skill, SkillManualImport>("/skills/import", payload);
}

export function importSkillArchive(payload: {
  archive_base64: string;
  filename?: string;
  skill_key?: string;
  name?: string;
}): Promise<Skill> {
  return apiClient.post<
    Skill,
    { archive_base64: string; filename?: string; skill_key?: string; name?: string }
  >("/skills/import-archive", payload);
}

export function importSkillNpx(payload: {
  command: string;
  skill_key?: string;
}): Promise<SkillNpxImportResult> {
  return apiClient.post<
    SkillNpxImportResult,
    { command: string; skill_key?: string }
  >("/skills/npx-import", payload);
}

export function previewSkillGitHub(
  reference: string,
): Promise<SkillGitHubPreview> {
  return apiClient.post<SkillGitHubPreview, { reference: string }>(
    "/skills/github/preview",
    { reference },
  );
}

export function installSkillGitHub(
  payload: SkillGitHubInstallPayload,
): Promise<Skill> {
  return apiClient.post<Skill, SkillGitHubInstallPayload>(
    "/skills/github/install",
    payload,
  );
}

export function scanSkillSecurity(
  skillId: string,
): Promise<SkillSecurityScanResult> {
  return apiClient.post<SkillSecurityScanResult>(
    `/skills/${encodeURIComponent(skillId)}/security-scan`,
  );
}

export function reviewSkillSemantics(
  skillId: string,
  force = false,
): Promise<SkillSemanticReviewResult> {
  return apiClient.post<SkillSemanticReviewResult, { force: boolean }>(
    `/skills/${encodeURIComponent(skillId)}/semantic-review`,
    { force },
  );
}

export function checkSkillUpdate(skillId: string): Promise<SkillUpdateCheck> {
  return apiClient.post<SkillUpdateCheck>(
    `/skills/${encodeURIComponent(skillId)}/check-update`,
  );
}

export function upgradeSkill(skillId: string): Promise<Skill> {
  return apiClient.post<Skill>(
    `/skills/${encodeURIComponent(skillId)}/upgrade`,
  );
}

export function listSkillCatalogSources(): Promise<ExternalCatalogSource[]> {
  return apiClient.get<ExternalCatalogSource[]>("/skills/market/catalogs");
}

export function searchExternalSkillCatalog(
  catalog: string,
  q: string,
  limit = 10,
): Promise<ExternalSkillSearchResult> {
  return apiClient.get<ExternalSkillSearchResult>(
    "/skills/market/external-search",
    { query: { catalog, q, limit } },
  );
}

export function browseMcpRegistry(options?: {
  q?: string;
  cursor?: string;
  limit?: number;
}): Promise<McpRegistrySearchResult> {
  return apiClient.get<McpRegistrySearchResult>("/mcp/registry/browse", {
    query: {
      ...(options?.q?.trim() ? { q: options.q.trim() } : {}),
      ...(options?.cursor ? { cursor: options.cursor } : {}),
      limit: options?.limit ?? 12,
    },
  });
}

export function searchMcpRegistry(
  q: string,
  limit = 10,
): Promise<McpRegistrySearchResult> {
  return apiClient.get<McpRegistrySearchResult>("/mcp/registry/search", {
    query: { q, limit },
  });
}

export function getSkillLocalProbePolicy(): Promise<SkillLocalProbePolicy> {
  return apiClient.get<SkillLocalProbePolicy>("/skills/local-probe/policy");
}

export function updateSkillLocalProbePolicy(payload: {
  enabled: boolean;
  allowed_roots: string[];
}): Promise<SkillLocalProbePolicy> {
  return apiClient.put<
    SkillLocalProbePolicy,
    { enabled: boolean; allowed_roots: string[] }
  >("/skills/local-probe/policy", payload);
}

export function scanSkillLocalProbe(): Promise<SkillLocalProbeScan> {
  return apiClient.post<SkillLocalProbeScan>("/skills/local-probe/scan");
}

export function importLocalSkill(payload: {
  root_path: string;
  relative_dir: string;
  skill_key?: string;
}): Promise<Skill> {
  return apiClient.post<
    Skill,
    { root_path: string; relative_dir: string; skill_key?: string }
  >("/skills/local-probe/import", payload);
}

export function translateSkill(
  skillId: string,
  payload: {
    target_locale: string;
    source_path?: string;
    force?: boolean;
  },
): Promise<SkillTranslateResult> {
  return apiClient.post<
    SkillTranslateResult,
    { target_locale: string; source_path?: string; force?: boolean }
  >(`/skills/${encodeURIComponent(skillId)}/translate`, payload);
}

export function authorizeSkill(
  skillId: string,
  decision: PermissionDecision,
  permissions: string[],
): Promise<ExtensionPermissionGrant> {
  return apiClient.post<
    ExtensionPermissionGrant,
    { decision: PermissionDecision; permissions: string[]; reason: string }
  >(`/skills/${encodeURIComponent(skillId)}/authorize`, {
    decision,
    permissions,
    reason: "workspace_user_decision",
  });
}

export function invokeSkill(skillId: string): Promise<ExtensionInvocation> {
  return apiClient.post<ExtensionInvocation, { input: Record<string, never> }>(
    `/skills/${encodeURIComponent(skillId)}/invoke`,
    { input: {} },
  );
}

export function revokeSkill(skillId: string): Promise<Skill> {
  return apiClient.post<Skill, { reason: string }>(
    `/skills/${encodeURIComponent(skillId)}/revoke`,
    { reason: "workspace_user_revoked" },
  );
}

export function requestSkillDeletion(
  skillId: string,
): Promise<SkillDeleteRequest> {
  return apiClient.post<SkillDeleteRequest>(
    `/skills/${encodeURIComponent(skillId)}/delete-request`,
  );
}

export function confirmSkillDeletion(
  confirmationId: string,
  confirmationText: string,
  currentPassword: string,
): Promise<SkillDeleteRequest> {
  return apiClient.post<
    SkillDeleteRequest,
    { confirmation_text: string; current_password: string }
  >(
    `/skills/delete-confirmations/${encodeURIComponent(confirmationId)}/confirm`,
    {
      confirmation_text: confirmationText,
      current_password: currentPassword,
    },
  );
}

export function listExtensionGrants(): Promise<ExtensionPermissionGrant[]> {
  return apiClient.get<ExtensionPermissionGrant[]>(
    "/extension-permission-grants",
  );
}

export function listExtensionInvocations(): Promise<ExtensionInvocation[]> {
  return apiClient.get<ExtensionInvocation[]>("/extension-invocations");
}

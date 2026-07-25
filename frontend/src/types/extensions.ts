import type { IsoDateTime, UnknownRecord } from "./common";

export type PermissionDecision = "allow_once" | "always" | "deny";

export interface MCPServerManifest {
  schema_version: "1.0";
  identity: string;
  requested_tools: string[];
  permissions: string[];
  requested_resources: string[];
  requested_prompts: string[];
}

export interface MCPServerCreate {
  server_key: string;
  display_name: string;
  source: string;
  version: string;
  transport: "streamable_http" | "stdio";
  endpoint_url?: string | null;
  bearer_token?: string;
  manifest: MCPServerManifest;
  agent_auto_invoke?: boolean;
}

export interface MCPServer {
  id: string;
  workspace_id: string;
  server_key: string;
  display_name: string;
  source: string;
  version: string;
  transport: string;
  endpoint_url: string | null;
  auth_configured: boolean;
  auth_masked: string | null;
  manifest_json: UnknownRecord;
  manifest_hash: string;
  requested_tools: string[];
  required_permissions: string[];
  status: string;
  enabled: boolean;
  agent_auto_invoke: boolean;
  timeout_ms: number;
  max_input_bytes: number;
  max_result_bytes: number;
  max_concurrency: number;
  current_snapshot_id: string | null;
  authorization_generation: number;
  last_error: string | null;
  last_checked_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface MCPCapabilitySnapshot {
  id: string;
  workspace_id: string;
  server_id: string;
  sequence: number;
  protocol_version: string;
  server_identity: UnknownRecord;
  capabilities: UnknownRecord;
  tools: UnknownRecord[];
  resources: UnknownRecord[];
  prompts: UnknownRecord[];
  required_permissions: string[];
  snapshot_hash: string;
  changed: boolean;
  reauthorization_required: boolean;
  created_at: IsoDateTime;
}

export interface MCPRefreshResult {
  server: MCPServer;
  snapshot: MCPCapabilitySnapshot;
}

export interface ExtensionPermissionGrant {
  id: string;
  workspace_id: string;
  subject_type: string;
  subject_id: string;
  decision: PermissionDecision;
  status: string;
  permissions: string[];
  authorization_hash: string;
  decided_by: string;
  reason: string;
  consumed_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface SkillManifest {
  schema_version: "1.0";
  kind: "declarative_review";
  instructions_markdown: string;
  required_tools: Array<"builtin.review.list_due">;
  permissions: Array<"mastery.read">;
  allowed_components: string[];
  input_schema: UnknownRecord;
  steps: Array<{
    tool: "builtin.review.list_due";
    arguments: UnknownRecord;
  }>;
}

export interface SkillCreate {
  skill_key: string;
  name: string;
  source: string;
  version: string;
  generated_by: "user_import" | "agent" | "builtin";
  auto_enable_requested?: boolean;
  manifest: SkillManifest;
}

export interface Skill {
  id: string;
  workspace_id: string;
  skill_key: string;
  name: string;
  source: string;
  version: string;
  generated_by: string;
  kind?: string;
  package_format?: string;
  content_hash?: string;
  origin_type?: string;
  origin_ref?: string;
  origin_hash?: string;
  has_scripts?: boolean;
  locale_source?: string;
  manifest_json: UnknownRecord;
  manifest_hash: string;
  instructions_markdown: string;
  required_tools: string[];
  required_permissions: string[];
  allowed_components: string[];
  validation_report: UnknownRecord;
  status: string;
  enabled: boolean;
  authorization_generation: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface SkillPackageCreate {
  skill_key: string;
  name: string;
  description?: string;
  source?: string;
  version?: string;
  with_sample_script?: boolean;
}

export interface SkillFileEntry {
  relative_path: string;
  size_bytes: number;
  mime_type: string;
  is_directory: boolean;
  blob_sha256: string;
  updated_at: IsoDateTime | null;
}

export interface SkillFileTree {
  skill_id: string;
  content_hash: string;
  has_scripts: boolean;
  files: SkillFileEntry[];
}

export interface SkillFileContent {
  relative_path: string;
  content: string;
  encoding: "utf-8";
  size_bytes: number;
  mime_type: string;
  blob_sha256: string;
  content_hash: string;
}

export interface SkillFileWriteResult {
  skill: Skill;
  file: SkillFileContent;
  reauthorization_required: boolean;
}

export interface SkillValidateResult {
  skill_id: string;
  ok: boolean;
  content_hash: string;
  has_scripts: boolean;
  issues: string[];
  frontmatter: UnknownRecord;
}

export interface SkillSandboxRunRequest {
  script_path: string;
  chat_session_id?: string | null;
  argv_extra?: string[];
}

export interface SkillSandboxRunResult {
  status: string;
  available: boolean;
  skill_id: string;
  script_path: string;
  content_hash: string;
  chat_session_id: string | null;
  sandbox_session_id: string | null;
  command_id: string | null;
  argv_redacted: string[];
  exit_code: number | null;
  timed_out: boolean;
  latency_ms: number;
  stdout_summary: string;
  stderr_summary: string;
  error_code: string | null;
  error_message: string | null;
  invocation_id: string | null;
}

export interface SkillMarketCard {
  market_id: string;
  slug: string;
  name: string;
  source: string;
  description: string;
  install_url: string;
  homepage_url: string;
  installs: number;
  source_type: string;
  origin_hash: string;
  fetch_status: string;
  fetch_error: string | null;
  fetched_at: IsoDateTime | null;
  rank: number;
  file_count: number;
  has_scripts: boolean;
}

export interface SkillMarketList {
  source: string;
  refreshed_at: IsoDateTime | null;
  cards: SkillMarketCard[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  query: string;
}

export interface SkillManualImportFile {
  path: string;
  contents: string;
}

export interface SkillManualImport {
  skill_key: string;
  name?: string;
  source?: string;
  version?: string;
  files: SkillManualImportFile[];
}

export interface SkillLocalProbePolicy {
  enabled: boolean;
  allowed_roots: string[];
  same_host_available: boolean;
  unavailable_reason: string | null;
  last_scanned_at: IsoDateTime | null;
  last_scan_summary: UnknownRecord;
  candidate_roots: Array<{
    label: string;
    path: string;
    exists: boolean;
    readable: boolean;
  }>;
}

export interface SkillLocalProbeItem {
  root_label: string;
  root_path: string;
  skill_key: string;
  name: string;
  description: string;
  relative_dir: string;
  has_scripts: boolean;
  skill_md_present: boolean;
}

export interface SkillLocalProbeScan {
  available: boolean;
  unavailable_reason: string | null;
  scanned_roots: string[];
  items: SkillLocalProbeItem[];
}

export interface SkillTranslateResult {
  skill_id: string;
  source_path: string;
  content_hash: string;
  target_locale: string;
  translator_model_id: string;
  cached: boolean;
  translated_text: string;
  usage_event_id: string | null;
}

export interface ExtensionInvocation {
  id: string;
  workspace_id: string;
  target_type: string;
  target_id: string;
  skill_id: string | null;
  tool_name: string;
  status: string;
  grant_id: string | null;
  authorization_hash: string;
  input_json: UnknownRecord;
  input_size_bytes: number;
  input_hash: string;
  result_json: UnknownRecord;
  result_size_bytes: number;
  result_hash: string;
  timeout_ms: number;
  error_code: string | null;
  error_message: string | null;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

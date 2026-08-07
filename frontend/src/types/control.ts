import type { IsoDateTime, UnknownRecord } from "./common";

export interface CurrentUser {
  id: string;
  tenant_id: string;
  username: string;
  email: string | null;
  display_name: string;
  status: string;
  is_system_admin: boolean;
  must_change_password: boolean;
  session_id: string;
}

export interface ManagedAuthSession {
  id: string;
  created_at: IsoDateTime;
  expires_at: IsoDateTime;
  last_seen_at: IsoDateTime;
  revoked_at: IsoDateTime | null;
  revoked_reason: string;
  user_agent: string;
  ip_address: string;
  current: boolean;
}

export interface Permission {
  id: string;
  key: string;
  description: string;
}

export interface ManagedUser {
  id: string;
  tenant_id: string;
  username: string;
  email: string | null;
  display_name: string;
  status: string;
  is_system_admin: boolean;
  must_change_password: boolean;
  created_at: IsoDateTime;
}

export interface Organization {
  id: string;
  tenant_id: string;
  name: string;
  owner_user_id: string;
  status: string;
  created_at: IsoDateTime;
}

export interface Role {
  id: string;
  tenant_id: string;
  organization_id: string;
  name: string;
  description: string;
  is_system: boolean;
  permission_keys: string[];
  created_at: IsoDateTime;
}

export interface Membership {
  id: string;
  tenant_id: string;
  organization_id: string;
  user_id: string;
  username: string;
  display_name: string;
  role_id: string;
  role_name: string;
  status: string;
  joined_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface ComponentManifest {
  id: string;
  workspace_id: string;
  plugin_id: string;
  component_id: string;
  version: string;
  display_name: string;
  renderer: string;
  source: string;
  author: string;
  package_hash: string;
  package_hash_status: string;
  signature_status: string;
  signature_info: UnknownRecord;
  compatible_learngraph: UnknownRecord;
  uninstall_behavior: string;
  data_schema: UnknownRecord;
  event_schema: UnknownRecord;
  permissions: UnknownRecord;
  size_limits: UnknownRecord;
  skill_triggers: string[];
  example_data: UnknownRecord;
  schema_hash: string;
  permissions_hash: string;
  manifest_hash: string;
  created_at: IsoDateTime;
}

export interface ComponentAuthorization {
  id: string;
  workspace_id: string;
  plugin_id: string;
  manifest_version_id: string;
  scope: string;
  status: string;
  manifest_hash: string;
  permissions_hash: string;
  authorized_by: string;
  authorized_at: IsoDateTime;
  revoked_by: string | null;
  revoked_at: IsoDateTime | null;
  revoke_reason: string;
}

export interface ComponentCheck {
  id: string;
  workspace_id: string;
  plugin_id: string;
  manifest_version_id: string;
  check_type: "health" | "render";
  status: string;
  executor: string;
  runtime_executed: boolean;
  details: UnknownRecord;
  artifact_metadata: UnknownRecord;
  checked_by: string;
  checked_at: IsoDateTime;
  created_at: IsoDateTime;
}

export interface ComponentArtifact {
  delivery_mode: "trusted_component" | "sandbox_artifact";
  component_id: string;
  version: string;
  manifest_version_id: string;
  authorization_id: string;
  runtime_status: string;
  sandbox_executed: boolean;
  trusted_component: UnknownRecord | null;
  sandbox_artifact: UnknownRecord | null;
}

export interface ComponentEventValidation {
  accepted: boolean;
  component_id: string;
  version: string;
  event_hash: string;
  executed: boolean;
}

export interface ComponentRegistration {
  plugin: {
    id: string;
    workspace_id: string;
    plugin_key: string;
    name: string;
    version: string;
    plugin_type: string;
    status: string;
    enabled: boolean;
    permissions: string[];
    capabilities: string[];
  };
  manifest: ComponentManifest;
  reauthorization_required: boolean;
  reauthorization_reasons: string[];
  checks: ComponentCheck[];
}

export interface SandboxProfile {
  backend_id: string;
  /** Unified runner image; legacy deployments may still report python-node kinds. */
  runtime_kind: "unified" | "python-node" | "python-node-browser";
  platform: string;
  available: boolean;
  capabilities: string[];
  reason: string | null;
  image_pinned: boolean;
}

export interface SandboxBootstrapJob {
  job_id: string;
  phase: string;
  progress_percent: number;
  message: string;
  /** Real-time build detail derived from the docker stream, e.g. "正在下载镜像 ubuntu:24.04 · 15.1 MB / 28.7 MB". */
  detail: string | null;
  status: string;
  image_digest: string | null;
  browser_image_digest: string | null;
  error_code: string | null;
  error_message: string | null;
  log_tail: string[];
  started_at: number;
  finished_at: number | null;
}

export interface SandboxBootstrapPolicy {
  /** Whether ordinary workspace members may initialize the sandbox runtime. */
  member_allowed: boolean;
  /** False when no administrator has persisted an explicit choice yet. */
  persisted: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export interface SandboxPreviewConfig {
  /** Effective subapp preview origin (persisted, env, or local derivation). */
  origin: string | null;
  /** Resolution source: persisted | env | auto | none. */
  source: "persisted" | "env" | "auto" | "none";
  /** True when a deployment administrator persisted an explicit origin. */
  persisted: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export interface SandboxBootstrapStatus {
  docker_installed: boolean;
  docker_reachable: boolean;
  docker_detail: string | null;
  sandbox_enabled: boolean;
  image_ready: boolean;
  image_digest: string | null;
  browser_image_ready: boolean;
  browser_image_digest: string | null;
  image_source: string | null;
  phase: string;
  progress_percent: number;
  message: string;
  detail: string | null;
  can_initialize: boolean;
  /** Whether ordinary members may trigger bootstrap (admin-controllable). */
  member_bootstrap_allowed: boolean;
  bootstrap_policy: {
    member_allowed: boolean;
    updated_at: string | null;
    updated_by: string | null;
  } | null;
  active_job: SandboxBootstrapJob | null;
  last_failed_job: SandboxBootstrapJob | null;
  remediation_steps: string[];
}

export interface SandboxAgentReadiness {
  available: boolean;
  code: string | null;
  message: string;
  authorized: boolean;
  sandbox_enabled: boolean;
  agent_enabled: boolean;
  backend_id: string;
  platform: string;
  capabilities: string[];
  remediation_steps: string[];
}

export interface SandboxBootstrapStartResult {
  accepted: boolean;
  joined_existing: boolean;
  error_code: string | null;
  error_message: string | null;
  job: SandboxBootstrapJob | null;
  status: SandboxBootstrapStatus;
}

export interface SandboxSession {
  id: string;
  workspace_id: string;
  owner_user_id: string;
  chat_session_id: string;
  backend_id: string;
  manifest_hash: string;
  policy_revision: string;
  runtime_kind: "python-node" | "python-node-browser";
  lifecycle_state: "CREATED" | "COLD" | "STARTING" | "RUNNING" | "WARM_IDLE" | "EXPIRED";
  status: string;
  resource_limits: UnknownRecord;
  network_policy: UnknownRecord;
  last_used_at: IsoDateTime;
  expires_at: IsoDateTime;
  runtime_started_at: IsoDateTime | null;
  runtime_last_used_at: IsoDateTime | null;
  workspace_expires_at: IsoDateTime;
  absolute_expires_at: IsoDateTime;
  cleanup_status: string;
  cleanup_error_class: string | null;
  created_at: IsoDateTime;
}

export interface SandboxTask {
  id: string;
  workspace_id: string;
  sandbox_session_id: string;
  chat_session_id: string;
  file_id: string;
  task_type: "file_inspect" | "extract_inert_text";
  output_format: "metadata_json" | "text_bundle";
  status: string;
  artifact_json: UnknownRecord;
  error_class: string | null;
  error_message: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface SandboxExecution {
  id: string;
  sandbox_session_id: string;
  task_id: string;
  attempt_no: number;
  argv_redacted: string[];
  cwd_relative: string;
  status: string;
  exit_code: number | null;
  error_class: string | null;
  timed_out: boolean;
  latency_ms: number;
  resource_usage: UnknownRecord;
  stdout_summary: string;
  stderr_summary: string;
  truncated: boolean;
  created_at: IsoDateTime;
}

export interface McpTransportCapability {
  transport: string;
  available: boolean;
  protocol_version: string | null;
  supports_real_execution: boolean;
  supports_encrypted_bearer_reference: boolean;
  reason: string;
}

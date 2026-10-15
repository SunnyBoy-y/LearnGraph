import { apiClient } from "./client";

export interface VoiceTaskWire extends Record<string, unknown> {
  link_id?: string;
  linkId?: string;
  voice_session_id?: string;
  subagent_id?: string;
  task_id?: string;
  previous_task_id?: string;
  capability_id?: string;
  approval_status?: string;
  approval_expires_at?: string;
  approval_request?: Record<string, unknown>;
  approval_request_json?: Record<string, unknown>;
  result?: string | null;
  deliverables?: Record<string, unknown> | null;
  results?: VoiceResultWire[];
}

export interface VoiceSessionWire extends Record<string, unknown> {
  id?: string;
  session_id?: string;
  sessionId?: string;
  tasks?: VoiceTaskWire[];
  results?: VoiceResultWire[];
}

export interface VoiceResultWire extends Record<string, unknown> {
  id?: string;
  result_id?: string;
  subagent_id?: string;
  requirement_version?: number;
  result_version?: number;
  status?: string;
  summary?: string;
  safe_error?: string;
  payload?: Record<string, unknown>;
  source_count?: number;
  captured_at?: string;
  available_at?: string;
  delivered_at?: string | null;
  playback_status?: string | null;
  playback_ended_at?: string | null;
  speech_id?: string | null;
  evidence_refs?: unknown[];
}

export interface VoiceResultDeliveryWire extends Record<string, unknown> {
  result?: VoiceResultWire;
  delivery_id?: string;
  speech_id?: string;
  request_id?: string;
  playback_status?: string | null;
  already_delivered?: boolean;
}

export function deliverVoiceResult(
  voiceSessionId: string,
  resultId: string,
  requestId: string,
): Promise<VoiceResultDeliveryWire> {
  return apiClient.post<VoiceResultDeliveryWire, { request_id: string }>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/results/${encodeURIComponent(resultId)}/deliver`,
    { request_id: requestId },
  );
}

export interface VoiceCapabilityWire {
  id: string;
  description: string;
  tools: string[];
  requires_confirmation: boolean;
  required_permission: string;
  available: boolean;
  blocked_by: string[];
  reason?: string | null;
}

export interface VoiceCapabilitiesWire {
  runtime_ready: boolean;
  signaling_url?: string | null;
  reason?: string | null;
  voice_provider_ready?: boolean;
  voice_provider_blocked_by?: string[];
  voice_provider_reason?: string | null;
  asr_available?: boolean;
  tts_available?: boolean;
  model_available?: boolean;
  agent_capabilities: VoiceCapabilityWire[];
}

export function getVoiceCapabilities(): Promise<VoiceCapabilitiesWire> {
  return apiClient.get<VoiceCapabilitiesWire>("/voice/capabilities");
}

export interface VoiceTaskStartWire {
  prompt: string;
  title?: string;
  role_key?: string;
  capability_id?: string;
  idempotency_key?: string;
  thinking_mode?: string;
  tools?: string[];
  skills?: string[];
  write_set?: string[];
  output_contract?: Record<string, unknown>;
  sandbox_session_id?: string;
}

export function startVoiceTask(
  voiceSessionId: string,
  payload: VoiceTaskStartWire,
): Promise<VoiceTaskWire> {
  return apiClient.post<VoiceTaskWire, VoiceTaskStartWire>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks`,
    payload,
  );
}

export function getVoiceTask(
  voiceSessionId: string,
  taskId: string,
  afterEventSeq?: number,
): Promise<VoiceTaskWire> {
  return apiClient.get<VoiceTaskWire>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks/${encodeURIComponent(taskId)}`,
    afterEventSeq === undefined
      ? undefined
      : { query: { after_event_seq: afterEventSeq } },
  );
}

export function cancelVoiceTask(
  voiceSessionId: string,
  taskId: string,
): Promise<VoiceTaskWire> {
  return apiClient.post<VoiceTaskWire, Record<string, never>>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks/${encodeURIComponent(taskId)}/cancel`,
    {},
  );
}

export function approveVoiceTask(
  voiceSessionId: string,
  taskId: string,
  approved = true,
): Promise<VoiceTaskWire> {
  return apiClient.post<VoiceTaskWire, { approved: boolean }>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks/${encodeURIComponent(taskId)}/approve`,
    { approved },
  );
}

export function reviseVoiceTask(
  voiceSessionId: string,
  taskId: string,
  payload: { prompt: string; title?: string; note?: string; idempotency_key: string },
): Promise<VoiceTaskWire> {
  return apiClient.post<VoiceTaskWire, typeof payload>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}/tasks/${encodeURIComponent(taskId)}/revise`,
    payload,
  );
}

export function getVoiceSession(
  voiceSessionId: string,
  signal?: AbortSignal,
): Promise<VoiceSessionWire> {
  return apiClient.get<VoiceSessionWire>(
    `/voice/sessions/${encodeURIComponent(voiceSessionId)}`,
    signal ? { signal } : undefined,
  );
}

/* ------------------------------------------------------------------ relay -- */

/** ICE servers for the calling browser; empty when no relay is configured. */
export interface VoiceIceServersWire {
  iceServers: Array<{
    urls: string | string[];
    username?: string | null;
    credential?: string | null;
  }>;
  source?: string | null;
  detail?: string | null;
}

export interface VoiceRelayConfigWire {
  configured: boolean;
  enabled: boolean;
  mode: "cloudflare" | "manual";
  urls: string[];
  stun_urls: string[];
  stun_fallback_enabled: boolean;
  key_id: string | null;
  api_base: string;
  credential_ttl_seconds: number;
  secret_masked: string | null;
  secret_fingerprint: string | null;
  secret_configured: boolean;
  status: string;
  status_detail: string | null;
  last_checked_at: string | null;
  updated_by_user_id: string | null;
  defaults: {
    api_base: string;
    urls: string[];
    stun_urls: string[];
    credential_ttl_seconds: number;
  };
}

export interface VoiceRelayProbeWire {
  url: string;
  scheme?: string;
  transport?: string;
  ok: boolean;
  detail?: string | null;
  elapsed_ms: number;
  candidate?: Record<string, unknown> | null;
}

export interface VoiceRelayTestWire {
  ok: boolean;
  detail: string;
  credential_masked: string;
  cloudflare_urls: string[];
  configured_urls: string[];
  probes: VoiceRelayProbeWire[];
}

export interface VoiceRelaySaveWire {
  mode?: "cloudflare" | "manual";
  stun_urls?: string[];
  stun_fallback_enabled?: boolean;
  key_id?: string;
  api_base?: string;
  urls?: string[];
  credential_ttl_seconds?: number;
  /** Leave empty to keep the stored token. */
  secret?: string;
}

export function getVoiceIceServers(signal?: AbortSignal): Promise<VoiceIceServersWire> {
  return apiClient.get<VoiceIceServersWire>(
    "/voice/ice-servers",
    signal ? { signal } : undefined,
  );
}

export function getVoiceRelay(): Promise<VoiceRelayConfigWire> {
  return apiClient.get<VoiceRelayConfigWire>("/voice/relay");
}

export function saveVoiceRelay(
  payload: VoiceRelaySaveWire,
): Promise<VoiceRelayConfigWire> {
  return apiClient.put<VoiceRelayConfigWire, VoiceRelaySaveWire>("/voice/relay", payload);
}

export function setVoiceRelayEnabled(enabled: boolean): Promise<VoiceRelayConfigWire> {
  return apiClient.post<VoiceRelayConfigWire, { enabled: boolean }>("/voice/relay/enabled", {
    enabled,
  });
}

export function testVoiceRelay(payload: VoiceRelaySaveWire): Promise<VoiceRelayTestWire> {
  return apiClient.post<VoiceRelayTestWire, VoiceRelaySaveWire>("/voice/relay/test", payload);
}

export function clearVoiceRelay(): Promise<{ configured: boolean }> {
  return apiClient.delete<{ configured: boolean }>("/voice/relay");
}

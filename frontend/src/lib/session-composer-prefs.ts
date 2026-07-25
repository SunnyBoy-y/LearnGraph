/**
 * Per-session composer preferences (response mode / thinking / search).
 * Stored in localStorage so switching sessions retains each session's last
 * selection without leaking across tabs of different sessions.
 */

import type { MessageCreateRequest } from "@/types/sessions";

export type ResponseMode = "fast" | "thinking" | "agentic";
export type ThinkingMode = NonNullable<MessageCreateRequest["thinking_mode"]>;
export type SearchRoute = NonNullable<MessageCreateRequest["search_route"]>;
export type GenerationMode = NonNullable<MessageCreateRequest["generation_mode"]>;

export interface SessionComposerPrefs {
  responseMode: ResponseMode;
  thinkingMode: ThinkingMode;
  searchRoute: SearchRoute;
  generationMode: GenerationMode;
  providerId?: string;
  modelId?: string;
  imageProviderId?: string;
  imageModelId?: string;
}

const STORAGE_KEY = "learngraph:session-composer-prefs";

/** New-session defaults: 智能体 + 联网 + 中等思维度 (D-CHAT composer). */
const DEFAULT_PREFS: SessionComposerPrefs = {
  responseMode: "agentic",
  thinkingMode: "medium",
  searchRoute: "auto",
  generationMode: "text",
};

type PrefsMap = Record<string, SessionComposerPrefs>;

function readAll(): PrefsMap {
  try {
    if (typeof window === "undefined" || !window.localStorage) return {};
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as PrefsMap;
  } catch {
    return {};
  }
}

function writeAll(map: PrefsMap): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Quota / private mode — keep in-memory only for this call stack.
  }
}

function isResponseMode(value: unknown): value is ResponseMode {
  return value === "fast" || value === "thinking" || value === "agentic";
}

function isThinkingMode(value: unknown): value is ThinkingMode {
  return (
    value === "off" ||
    value === "low" ||
    value === "medium" ||
    value === "high" ||
    value === "xhigh"
  );
}

function isSearchRoute(value: unknown): value is SearchRoute {
  return (
    value === "disabled" ||
    value === "model_native" ||
    value === "external" ||
    value === "local" ||
    value === "auto"
  );
}

function isGenerationMode(value: unknown): value is GenerationMode {
  return value === "text" || value === "image";
}

export function normalizeComposerPrefs(
  value: unknown,
): SessionComposerPrefs {
  if (!value || typeof value !== "object") return { ...DEFAULT_PREFS };
  const record = value as Record<string, unknown>;
  return {
    responseMode: isResponseMode(record.responseMode)
      ? record.responseMode
      : DEFAULT_PREFS.responseMode,
    thinkingMode: isThinkingMode(record.thinkingMode)
      ? record.thinkingMode
      : DEFAULT_PREFS.thinkingMode,
    searchRoute: isSearchRoute(record.searchRoute)
      ? record.searchRoute
      : DEFAULT_PREFS.searchRoute,
    generationMode: isGenerationMode(record.generationMode)
      ? record.generationMode
      : DEFAULT_PREFS.generationMode,
    providerId:
      typeof record.providerId === "string" && record.providerId
        ? record.providerId
        : undefined,
    modelId:
      typeof record.modelId === "string" && record.modelId
        ? record.modelId
        : undefined,
    imageProviderId:
      typeof record.imageProviderId === "string" && record.imageProviderId
        ? record.imageProviderId
        : undefined,
    imageModelId:
      typeof record.imageModelId === "string" && record.imageModelId
        ? record.imageModelId
        : undefined,
  };
}

export function getSessionComposerPrefs(
  sessionId: string | null | undefined,
): SessionComposerPrefs {
  if (!sessionId || sessionId === "new") return { ...DEFAULT_PREFS };
  const map = readAll();
  return normalizeComposerPrefs(map[sessionId]);
}

export function setSessionComposerPrefs(
  sessionId: string | null | undefined,
  prefs: Partial<SessionComposerPrefs>,
): SessionComposerPrefs {
  if (!sessionId || sessionId === "new") {
    return normalizeComposerPrefs({ ...DEFAULT_PREFS, ...prefs });
  }
  const map = readAll();
  const next = normalizeComposerPrefs({
    ...getSessionComposerPrefs(sessionId),
    ...prefs,
  });
  map[sessionId] = next;
  writeAll(map);
  return next;
}

/** Copy prefs from a parent session onto a newly branched session. */
export function inheritSessionComposerPrefs(
  sourceSessionId: string | null | undefined,
  targetSessionId: string | null | undefined,
): SessionComposerPrefs {
  const source = getSessionComposerPrefs(sourceSessionId);
  return setSessionComposerPrefs(targetSessionId, source);
}

/**
 * Infer composer prefs from a durable server model_snapshot when no local
 * prefs exist yet (e.g. first open after refresh on another device).
 */
export function prefsFromModelSnapshot(
  snapshot: Record<string, unknown> | null | undefined,
): Partial<SessionComposerPrefs> {
  if (!snapshot || typeof snapshot !== "object") return {};
  const thinking = snapshot.thinking_mode;
  const agentMode = snapshot.agent_mode;
  const result: Partial<SessionComposerPrefs> = {};
  if (typeof snapshot.provider_id === "string" && snapshot.provider_id) {
    result.providerId = snapshot.provider_id;
  }
  if (typeof snapshot.model_id === "string" && snapshot.model_id) {
    result.modelId = snapshot.model_id;
  }
  if (agentMode === true) {
    result.responseMode = "agentic";
  } else if (isThinkingMode(thinking) && thinking !== "off") {
    result.responseMode = "thinking";
    result.thinkingMode = thinking;
  } else if (thinking === "off") {
    result.responseMode = "fast";
    result.thinkingMode = "off";
  }
  return result;
}

export function defaultComposerPrefs(): SessionComposerPrefs {
  return { ...DEFAULT_PREFS };
}

/** True when prefs still match the new-session defaults (no user override). */
export function isDefaultComposerPrefs(
  prefs: SessionComposerPrefs | null | undefined,
): boolean {
  if (!prefs) return true;
  return (
    prefs.responseMode === DEFAULT_PREFS.responseMode &&
    prefs.thinkingMode === DEFAULT_PREFS.thinkingMode &&
    prefs.searchRoute === DEFAULT_PREFS.searchRoute &&
    prefs.generationMode === DEFAULT_PREFS.generationMode
  );
}

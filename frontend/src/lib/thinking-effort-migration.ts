import type { ProviderModelCapabilities, ThinkingMode } from "@/types/providers";
import { getSessionComposerPrefs, hasSessionComposerPrefs, setSessionComposerPrefs } from "./session-composer-prefs";

const KEY = "learngraph:thinking-effort-migration-v2";
/** One-time, evidence-based migration. Never reinterpret an explicit mapping. */
export function migrateThinkingPreference(sessionId: string | undefined, providerId: string | undefined,
  modelId: string | undefined, capabilities: ProviderModelCapabilities | undefined, current: ThinkingMode): ThinkingMode {
  if (!sessionId || sessionId === "new" || !providerId || !modelId || !capabilities ||
    capabilities.thinking_mapping_version !== 2 || !capabilities.legacy_thinking_mapping ||
    capabilities.capability_source === "user_declared" || capabilities.capability_source === "provider_probe" ||
    capabilities.capability_source === "runtime_observation") return current;
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(KEY) || "{}");
    const seen: Record<string, boolean> = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, boolean> : {};
    const key = JSON.stringify([sessionId, providerId, modelId]);
    if (seen[key]) return current;
    // A stale device/model selection is not evidence about this preference.
    const prefs = getSessionComposerPrefs(sessionId);
    if (prefs.thinkingMappingVersion === 2) return current;
    if (!hasSessionComposerPrefs(sessionId) || prefs.providerId !== providerId || prefs.modelId !== modelId || prefs.thinkingMode !== current) return current;
    const actual = capabilities.legacy_thinking_mapping[current];
    const matches = capabilities.reasoning_efforts.filter(mode => capabilities.thinking_mapping[mode] === actual);
    // Preserve same-name choices; otherwise migrate only an unambiguous match.
    const next = actual == null ? current : matches.some(mode => mode === current) ? current : matches.length === 1 ? matches[0] : current;
    if (actual != null && !matches.length) return current; // Ask the existing selector to resolve; never guess a wire value.
    seen[key] = true;
    window.localStorage.setItem(KEY, JSON.stringify(seen));
    if (next !== current) setSessionComposerPrefs(sessionId, { thinkingMode: next });
    return next;
  } catch { return current; }
}

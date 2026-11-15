import { beforeEach, describe, expect, it } from "vitest";
import type { ProviderModelCapabilities } from "@/types/providers";
import { migrateThinkingPreference } from "./thinking-effort-migration";
import { getSessionComposerPrefs, setSessionComposerPrefs } from "./session-composer-prefs";
import { validateNativeComposerPrefs } from "./native-composer-contract";
const caps = { capability_source: "official_catalog", thinking_mapping_version: 2,
  reasoning_efforts: ["low", "high", "xhigh", "max"],
  thinking_mapping: { low: "low", high: "high", xhigh: "xhigh", max: "max" },
  legacy_thinking_mapping: { high: "xhigh", xhigh: "max" } } as ProviderModelCapabilities;
beforeEach(() => localStorage.clear());
describe("versioned, evidence-only thinking preference migration", () => {
  it("preserves actual maximum effort and migrates once", () => {
    setSessionComposerPrefs("s", { providerId: "p", modelId: "m", thinkingMode: "xhigh" });
    expect(migrateThinkingPreference("s", "p", "m", caps, "xhigh")).toBe("max");
    expect(getSessionComposerPrefs("s").thinkingMode).toBe("max");
    setSessionComposerPrefs("s", { thinkingMode: "xhigh" });
    expect(migrateThinkingPreference("s", "p", "m", caps, "xhigh")).toBe("xhigh");
  });
  it("does not alter explicit overrides or mismatched models", () => {
    setSessionComposerPrefs("s", { providerId: "p", modelId: "m", thinkingMode: "xhigh" });
    expect(migrateThinkingPreference("s", "p", "m", { ...caps, capability_source: "user_declared" }, "xhigh")).toBe("xhigh");
    expect(migrateThinkingPreference("s", "p", "other", caps, "xhigh")).toBe("xhigh");
    expect(migrateThinkingPreference("s", "p", "m", { ...caps, legacy_thinking_mapping: undefined }, "xhigh")).toBe("xhigh");
  });
  it("preserves an explicitly current-version native preference", () => {
    setSessionComposerPrefs("s", { providerId: "p", modelId: "m", thinkingMode: "xhigh", thinkingMappingVersion: 2 });
    expect(migrateThinkingPreference("s", "p", "m", caps, "xhigh")).toBe("xhigh");
  });
  it("accepts max through persisted prefs and the native bridge", () => {
    const prefs = setSessionComposerPrefs("s", { thinkingMode: "max" });
    expect(getSessionComposerPrefs("s").thinkingMode).toBe("max");
    expect(validateNativeComposerPrefs(prefs)?.thinkingMode).toBe("max");
  });
});

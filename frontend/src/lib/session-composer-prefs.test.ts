import { beforeEach, describe, expect, it } from "vitest";

import {
  defaultComposerPrefs,
  defaultComposerPrefsForResponseMode,
  getSessionComposerPrefs,
  hasSessionComposerPrefs,
  inheritSessionComposerPrefs,
  isDefaultComposerPrefs,
  normalizeComposerPrefs,
  normalizeResponseMode,
  prefsFromModelSnapshot,
  setSessionComposerPrefs,
} from "./session-composer-prefs";

const STORAGE_KEY = "learngraph:session-composer-prefs";

beforeEach(() => {
  window.localStorage.clear();
});

describe("session composer preferences", () => {
  it("uses safe defaults for malformed values and retired response modes", () => {
    expect(normalizeResponseMode("fast")).toBe("fast");
    expect(normalizeResponseMode("thinking")).toBe("agentic");
    expect(normalizeResponseMode(undefined)).toBe("agentic");

    expect(normalizeComposerPrefs(null)).toEqual(defaultComposerPrefs());
    expect(
      normalizeComposerPrefs({
        responseMode: "unknown",
        thinkingMode: "maximum",
        searchRoute: "internet",
        generationMode: "video",
        providerId: "",
        modelId: 42,
      }),
    ).toEqual(defaultComposerPrefs());
  });

  it("preserves valid optional provider and image selections", () => {
    expect(
      normalizeComposerPrefs({
        responseMode: "fast",
        thinkingMode: "xhigh",
        searchRoute: "model_native",
        generationMode: "image",
        providerId: "provider-a",
        modelId: "model-a",
        imageProviderId: "image-provider",
        imageModelId: "image-model",
      }),
    ).toEqual({
      responseMode: "fast",
      thinkingMode: "xhigh",
      searchRoute: "model_native",
      generationMode: "image",
      providerId: "provider-a",
      modelId: "model-a",
      imageProviderId: "image-provider",
      imageModelId: "image-model",
    });
  });

  it("does not persist anonymous or new-session preferences", () => {
    expect(setSessionComposerPrefs(undefined, { responseMode: "fast" })).toMatchObject({
      responseMode: "fast",
    });
    expect(setSessionComposerPrefs("new", { thinkingMode: "off" })).toMatchObject({
      thinkingMode: "off",
    });
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(hasSessionComposerPrefs(undefined)).toBe(false);
    expect(hasSessionComposerPrefs("new")).toBe(false);
  });

  it("merges partial updates without leaking preferences between sessions", () => {
    setSessionComposerPrefs("session-a", {
      responseMode: "fast",
      providerId: "provider-a",
      modelId: "model-a",
    });
    setSessionComposerPrefs("session-a", { generationMode: "image" });
    setSessionComposerPrefs("session-b", { thinkingMode: "high" });

    expect(getSessionComposerPrefs("session-a")).toMatchObject({
      responseMode: "fast",
      thinkingMode: "medium",
      generationMode: "image",
      providerId: "provider-a",
      modelId: "model-a",
    });
    expect(getSessionComposerPrefs("session-b")).toEqual({
      responseMode: "agentic",
      thinkingMode: "high",
      searchRoute: "auto",
      generationMode: "text",
    });
    expect(hasSessionComposerPrefs("session-a")).toBe(true);
    expect(hasSessionComposerPrefs("missing")).toBe(false);
  });

  it("falls back safely when persisted storage is invalid or has the wrong shape", () => {
    window.localStorage.setItem(STORAGE_KEY, "not-json");
    expect(getSessionComposerPrefs("session-a")).toEqual(defaultComposerPrefs());

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(["not", "a", "map"]));
    expect(getSessionComposerPrefs("session-a")).toEqual(defaultComposerPrefs());

    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        "session-a": {
          responseMode: "thinking",
          thinkingMode: "low",
          searchRoute: "external",
          generationMode: "text",
        },
      }),
    );
    expect(getSessionComposerPrefs("session-a")).toMatchObject({
      responseMode: "agentic",
      thinkingMode: "low",
      searchRoute: "external",
    });
  });

  it("copies a complete source preference set only to a real branch session", () => {
    setSessionComposerPrefs("parent", {
      responseMode: "fast",
      thinkingMode: "off",
      searchRoute: "disabled",
      generationMode: "image",
      providerId: "provider-a",
      modelId: "model-a",
    });

    expect(inheritSessionComposerPrefs("parent", "branch")).toEqual(
      getSessionComposerPrefs("parent"),
    );
    expect(getSessionComposerPrefs("branch")).toEqual(getSessionComposerPrefs("parent"));
    expect(inheritSessionComposerPrefs("parent", "new")).toEqual(
      getSessionComposerPrefs("parent"),
    );
    expect(hasSessionComposerPrefs("new")).toBe(false);
  });

  it("derives response mode from server snapshots without accepting invalid fields", () => {
    expect(
      prefsFromModelSnapshot({
        provider_id: "provider-a",
        model_id: "model-a",
        agent_mode: true,
        thinking_mode: "off",
      }),
    ).toEqual({
      providerId: "provider-a",
      modelId: "model-a",
      responseMode: "agentic",
    });
    expect(prefsFromModelSnapshot({ thinking_mode: "high" })).toEqual({
      responseMode: "agentic",
      thinkingMode: "high",
    });
    expect(prefsFromModelSnapshot({ thinking_mode: "off" })).toEqual({
      responseMode: "fast",
      thinkingMode: "off",
    });
    expect(prefsFromModelSnapshot({ provider_id: 5, model_id: "" })).toEqual({});
    expect(prefsFromModelSnapshot([])).toEqual({});
  });

  it("uses fast-mode defaults consistently and ignores model selections when checking defaults", () => {
    const fast = defaultComposerPrefsForResponseMode("fast");
    expect(fast).toMatchObject({ responseMode: "fast", thinkingMode: "off" });
    expect(defaultComposerPrefsForResponseMode("thinking")).toMatchObject({
      responseMode: "agentic",
      thinkingMode: "medium",
    });

    expect(
      isDefaultComposerPrefs({ ...fast, providerId: "provider-a", modelId: "model-a" }, "fast"),
    ).toBe(true);
    expect(isDefaultComposerPrefs({ ...fast, searchRoute: "disabled" }, "fast")).toBe(false);
    expect(isDefaultComposerPrefs(undefined, "fast")).toBe(true);
  });
});

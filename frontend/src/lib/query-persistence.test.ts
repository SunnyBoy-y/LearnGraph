import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authStore } from "@/api/auth-store";

import {
  clearPersistedQueryCache,
  hydratePersistedQueryCache,
  startPersistedQueryCache,
} from "./query-persistence";

describe("query persistence", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    sessionStorage.clear();
    authStore.setSession({
      accessToken: "cache-token",
      userId: "user-cache",
      workspaceId: "workspace-cache",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    authStore.clear();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("hydrates successful workspace data after a refresh", () => {
    const source = new QueryClient();
    const stop = startPersistedQueryCache(source);
    source.setQueryData(["workspace", "workspace-cache", "messages", "s1"], [
      { id: "m1", content: "完整的长文本" },
    ]);
    vi.advanceTimersByTime(700);
    stop();

    const restored = new QueryClient();
    hydratePersistedQueryCache(restored);
    expect(
      restored.getQueryData(["workspace", "workspace-cache", "messages", "s1"]),
    ).toEqual([{ id: "m1", content: "完整的长文本" }]);
  });

  it("does not persist credential-shaped query keys", () => {
    const source = new QueryClient();
    const stop = startPersistedQueryCache(source);
    source.setQueryData(["workspace", "workspace-cache", "provider-settings"], {
      secret: "must-not-be-cached",
    });
    vi.advanceTimersByTime(700);
    stop();

    const restored = new QueryClient();
    hydratePersistedQueryCache(restored);
    expect(
      restored.getQueryData(["workspace", "workspace-cache", "provider-settings"]),
    ).toBeUndefined();
  });

  it("clears the workspace snapshot without touching unrelated localStorage", () => {
    const source = new QueryClient();
    const stop = startPersistedQueryCache(source);
    source.setQueryData(["workspace", "workspace-cache", "messages", "s1"], [1]);
    vi.advanceTimersByTime(700);
    stop();
    localStorage.setItem("learngraph:test-setting", "keep");

    clearPersistedQueryCache({ workspaceId: "workspace-cache" });
    expect(localStorage.getItem("learngraph:test-setting")).toBe("keep");

    const restored = new QueryClient();
    hydratePersistedQueryCache(restored);
    expect(
      restored.getQueryData(["workspace", "workspace-cache", "messages", "s1"]),
    ).toBeUndefined();
  });
});

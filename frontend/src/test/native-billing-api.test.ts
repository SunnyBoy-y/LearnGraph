import { afterEach, describe, expect, it, vi } from "vitest";
import { authStore } from "@/api/auth-store";
import { apiClient } from "@/api/client";
import { getUsageSummary, listUsageEvents } from "@/api/usage";
import { billingQueryKey } from "@/lib/query-keys";

afterEach(() => { vi.restoreAllMocks(); authStore.clear(); });
describe("native billing scope", () => {
  it("separates workspace, user, currency and filter keys", () => {
    const usd = billingQueryKey("workspace-a", "user-a", "USD", "events", { feature: "chat" });
    expect(usd).not.toEqual(billingQueryKey("workspace-a", "user-a", "CNY", "events", { feature: "chat" }));
    expect(usd).not.toEqual(billingQueryKey("workspace-b", "user-a", "USD", "events", { feature: "chat" }));
    expect(usd).not.toEqual(billingQueryKey("workspace-a", "user-b", "USD", "events", { feature: "chat" }));
  });
  it("rejects old converted responses and opposite-currency events", async () => {
    vi.spyOn(authStore, "getSession").mockReturnValue({ accessToken: "test-only", workspaceId: "a", userId: "user" });
    vi.spyOn(apiClient, "get").mockResolvedValueOnce({ workspace_id: "a", cost_usd: 1, cost_cny: 7 });
    await expect(getUsageSummary({ workspaceId: "a", userId: "user", currency: "USD" })).rejects.toThrow("格式");
    vi.spyOn(apiClient, "get").mockResolvedValueOnce([{ workspace_id: "a", currency: "CNY", cost_amount: "1" }]);
    await expect(listUsageEvents({ workspaceId: "a", userId: "user", currency: "USD" })).rejects.toThrow("范围");
  });
  it("captures the workspace header and cancellation signal and rejects account changes", async () => {
    vi.spyOn(authStore, "getSession").mockReturnValue({ accessToken: "test-only", workspaceId: "b", userId: "user" });
    const get = vi.spyOn(apiClient, "get").mockResolvedValue({ schema_version: 2, workspace_id: "a", currency: "CNY", cost_amount: "0" });
    const signal = new AbortController().signal;
    await getUsageSummary({ workspaceId: "a", userId: "user", currency: "CNY" }, { feature: "chat" }, signal);
    expect(get.mock.calls[0][1]).toMatchObject({ headers: { "X-Workspace-ID": "a" }, signal, query: { currency: "CNY", feature: "chat" } });
    await expect(getUsageSummary({ workspaceId: "a", userId: "another", currency: "CNY" })).rejects.toThrow("账号");
  });
  it("rejects a delayed response after the logged-in account changes", async () => {
    const session = vi.spyOn(authStore, "getSession").mockReturnValue({ accessToken: "first-only", workspaceId: "a", userId: "first" });
    let finish!: (value: unknown) => void;
    vi.spyOn(apiClient, "get").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const pending = getUsageSummary({ workspaceId: "a", userId: "first", currency: "USD" });
    session.mockReturnValue({ accessToken: "second-only", workspaceId: "a", userId: "second" });
    finish({ schema_version: 2, workspace_id: "a", currency: "USD", cost_amount: "9" });
    await expect(pending).rejects.toThrow("账号");
  });
});

import { describe, expect, it } from "vitest";
import type { MessagePart } from "@/types/sessions";
import { webActivityLabel } from "./web-activity";
import { currentActivityLabel } from "@/features/chat/chat-message-parts";

const part = (status: MessagePart["status"], operation = "search", provider = "OpenAI"): MessagePart => ({
  id: "web-1", type: "tool_call", status, content: "",
  data: { tool_name: "native_web_search", title: "internal-tool-name",
    web_activity: { operation, provider_label: provider, native: true } },
});

describe("real web activity labels", () => {
  it.each(["OpenAI", "百炼", "AnySearch", "文献检索 MCP"])("names the actual provider %s", (provider) => {
    expect(webActivityLabel(part("streaming", "search", provider))).toBe(`正在搜索（${provider}）`);
  });
  it("distinguishes page reading, success and failure", () => {
    expect(webActivityLabel(part("streaming", "fetch", "隔离浏览器"))).toBe("正在读取网页（隔离浏览器）");
    expect(webActivityLabel(part("completed", "fetch", "隔离浏览器"))).toBe("网页读取完成（隔离浏览器）");
    expect(webActivityLabel(part("failed", "fetch", "隔离浏览器"))).toBe("读取网页失败（隔离浏览器）");
    expect(webActivityLabel(part("pending"))).toBe("等待搜索（OpenAI）");
  });
  it("uses the provider that actually completed a fallback read", () => {
    const value = part("completed", "fetch", "Firecrawl");
    value.data!.output = { provider_label: "百炼" };
    expect(webActivityLabel(value)).toBe("网页读取完成（百炼）");
  });
  it("does not call authorization requests failures or fabricate a successful search", () => {
    const value = part("failed", "fetch", "隔离浏览器");
    value.data!.output = { reason: "fetch_domain_authorization_required" };
    expect(webActivityLabel(value)).toBe("等待授权（隔离浏览器）");
    const uncertain = part("failed");
    uncertain.data!.web_activity = { operation: "search", provider_label: "OpenAI", completion_unconfirmed: true };
    expect(webActivityLabel(uncertain)).toBe("搜索状态未确认（OpenAI）");
  });
  it("surfaces the real network state in the existing collapsed activity line", () => {
    expect(currentActivityLabel("streaming", [part("streaming", "search", "AnySearch")])).toBe("正在搜索（AnySearch）");
    expect(currentActivityLabel("streaming", [part("streaming", "fetch", "隔离浏览器")])).toBe("正在读取网页（隔离浏览器）");
  });
  it("does not infer search from user/model prose", () => {
    expect(webActivityLabel({ id: "text", type: "text", status: "streaming", content: "正在搜索" })).toBeNull();
  });
});

import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import type { UnknownRecord } from "@/types/common";
import { RetryAnswerMenu, RetryMenuSettings } from "./retry-answer-menu";

function Harness({ onRetry, canSearch = true, supportsAgent = true, previousAnswerTrace = { model_id: "previous-model", web_search: false } }: {
  onRetry: (settings: object) => void;
  canSearch?: boolean;
  supportsAgent?: boolean;
  previousAnswerTrace?: UnknownRecord;
}) {
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState(["deepseek", "deepseek-flash"]);
  const [mode, setMode] = useState<ResponseMode>("agentic");
  const [thinking, setThinking] = useState<ThinkingMode>("low");
  const [search, setSearch] = useState(false);
  const [domains, setDomains] = useState("");
  return <>
    <button>外部区域</button>
    <RetryAnswerMenu open={open} onOpenChange={setOpen} content={
      <RetryMenuSettings
        previousAnswerTrace={previousAnswerTrace}
        groups={[
          { id: "deepseek", label: "DeepSeek", models: [{ id: "deepseek-flash", remote: true }, { id: "deepseek-chat", remote: true }] },
          { id: "other", label: "其他服务商", models: [{ id: "other-model", remote: true }] },
        ]}
        providerId={model[0]} modelId={model[1]} responseMode={mode}
        thinkingMode={thinking} thinkingModes={["off", "low", "high"]}
        supportsAgent={supportsAgent} webSearch={search} canSearch={canSearch}
        canRetry modelError={false}
        onModelChange={(provider, next) => setModel([provider, next])}
        onModeChange={setMode} onThinkingChange={setThinking} onSearchChange={setSearch}
        allowedDomains={domains} onDomainsChange={setDomains}
        onRetry={() => { onRetry({ model, mode, thinking, search }); setOpen(false); }}
        onRefreshModels={() => {}} onConfigure={() => {}}
      />
    }>
      <button aria-label="重试并保留版本">重试</button>
    </RetryAnswerMenu>
  </>;
}

describe("anchored retry menu", () => {
  it.each([
    [{ model_id: "old-model", web_search: true, search_route: "disabled" }, "old-model", "联网已开启"],
    [{ model_id: "old-model", web_search: false, search_route: "external" }, "old-model", "联网已关闭"],
    [{ model_id: "native-model", search_route: "model_native" }, "native-model", "联网已开启"],
    [{ model_id: "external-model", search_provider_id: "search-provider", search_route: "disabled" }, "external-model", "联网已开启"],
    [{ model_id: "offline-model", search_route: "disabled" }, "offline-model", "联网已关闭"],
    [{}, "模型未知", "联网状态未知"],
  ])("shows recorded answer metadata %#", async (trace, model, search) => {
    const user = userEvent.setup();
    render(<Harness onRetry={vi.fn()} previousAnswerTrace={trace} canSearch={false} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    const item = screen.getByRole("menuitem", { name: /直接重试/ });
    expect(item).toHaveTextContent(`上次回答：${model}`);
    expect(item).toHaveTextContent(search);
  });

  it("opens without generating; keeps temporary model/search choices until explicit retry", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<Harness onRetry={onRetry} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onRetry).not.toHaveBeenCalled();
    expect(screen.getByRole("menu", { name: "重新生成回答" })).toHaveAttribute("data-side", "top");
    await user.click(screen.getByRole("menuitem", { name: /使用模型/ }));
    expect(await screen.findByText("其他服务商")).toBeVisible();
    expect(screen.getByRole("menuitemradio", { name: "deepseek-flash" })).toHaveAttribute("aria-checked", "true");
    screen.getByRole("menuitemradio", { name: "other-model" }).focus();
    await user.keyboard("{Enter}");
    expect(onRetry).not.toHaveBeenCalled();
    expect(screen.getByRole("menuitem", { name: /使用模型/ })).toHaveTextContent("other-model");
    await user.click(screen.getByRole("menuitemcheckbox", { name: "联网搜索" }));
    expect(screen.getByRole("menuitemcheckbox", { name: "联网搜索" })).toHaveAttribute("aria-checked", "true");
    const retryItem = screen.getByRole("menuitem", { name: /更改后重试/ });
    expect(retryItem).toHaveTextContent("本次重试：other-model");
    expect(retryItem).toHaveTextContent("智能体 · 思考低 · 联网已开启");
    expect(retryItem).not.toHaveTextContent("previous-model");
    await user.click(retryItem);
    expect(onRetry).toHaveBeenCalledExactlyOnceWith({ model: ["other", "other-model"], mode: "agentic", thinking: "low", search: true });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("restores the last answer summary when all choices are reverted", async () => {
    const user = userEvent.setup();
    render(<Harness onRetry={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    await user.click(screen.getByRole("menuitem", { name: /思考强度/ }));
    (await screen.findByRole("menuitemradio", { name: "高" })).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: /更改后重试/ })).toHaveTextContent("思考高");
    screen.getByRole("menuitemradio", { name: "低" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: /直接重试/ })).toHaveTextContent("上次回答：previous-model");
    await user.click(screen.getByRole("menuitemcheckbox", { name: "联网搜索" }));
    expect(screen.getByRole("menuitem", { name: /更改后重试/ })).toHaveTextContent("联网已开启");
    await user.click(screen.getByRole("menuitemcheckbox", { name: "联网搜索" }));
    expect(screen.getByRole("menuitem", { name: /直接重试/ })).toHaveTextContent("联网已关闭");
  });

  it("supports keyboard navigation, capability-limited thinking choices and Escape focus return", async () => {
    const user = userEvent.setup();
    render(<Harness onRetry={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "重试并保留版本" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /直接重试/ })).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowRight}");
    const high = await screen.findByRole("menuitemradio", { name: "高" });
    expect(screen.queryByRole("menuitemradio", { name: "中" })).not.toBeInTheDocument();
    await user.click(high);
    expect(screen.getByRole("menuitem", { name: /思考强度/ })).toHaveTextContent("高");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("disables unavailable search and agent mode with an explanation", async () => {
    const user = userEvent.setup();
    render(<Harness onRetry={vi.fn()} canSearch={false} supportsAgent={false} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    expect(screen.getByRole("menuitemcheckbox", { name: "联网搜索" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("当前模型与工作区暂无可用搜索能力")).toBeVisible();
    await user.click(screen.getByRole("menuitem", { name: /响应模式/ }));
    expect(await screen.findByRole("menuitemradio", { name: "智能体" })).toHaveAttribute("aria-disabled", "true");
    screen.getByRole("menuitemradio", { name: "极速" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: /思考强度/ })).toHaveAttribute("aria-disabled", "true");
  });

  it("closes on outside click without generating", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<Harness onRetry={onRetry} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    await user.click(screen.getByRole("button", { name: "外部区域" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("reveals optional search sources progressively and allows typing without closing", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<Harness onRetry={onRetry} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    expect(screen.queryByRole("menuitem", { name: /搜索来源/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitemcheckbox", { name: "联网搜索" }));
    const sources = screen.getByRole("menuitem", { name: /^搜索来源/ });
    sources.focus();
    await user.keyboard("{ArrowRight}");
    const input = await screen.findByRole("textbox", { name: "限定来源域名（可选）" });
    expect(input).toHaveFocus();
    await user.keyboard("react.dev, python.org");
    expect(input).toHaveValue("react.dev, python.org");
    expect(sources).toHaveTextContent("已限定");
    expect(screen.getByRole("menuitem", { name: /更改后重试/ })).toHaveTextContent("搜索来源：python.org、react.dev");
    expect(onRetry).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

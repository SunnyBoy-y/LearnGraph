import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import type { UnknownRecord } from "@/types/common";
import { RetryAnswerMenu, RetryMenuSettings } from "./retry-answer-menu";

function Harness({
  onRetry,
  onDirectRetry,
  canSearch = true,
  supportsAgent = true,
  previousAnswerTrace = { model_id: "deepseek-flash", web_search: false },
}: {
  onRetry: (settings: object) => void;
  onDirectRetry?: () => void;
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
  return (
    <>
      <button>外部区域</button>
      <RetryAnswerMenu
        open={open}
        onOpenChange={setOpen}
        content={
          <RetryMenuSettings
            previousAnswerTrace={previousAnswerTrace}
            groups={[
              {
                id: "deepseek",
                label: "DeepSeek",
                models: [
                  {
                    id: "deepseek-flash",
                    remote: true,
                    capabilities: { supports_image_input: true },
                  },
                  { id: "deepseek-chat", remote: true },
                ],
              },
              {
                id: "other",
                label: "其他服务商",
                models: [{ id: "other-model", remote: true }],
              },
            ]}
            providerId={model[0]}
            modelId={model[1]}
            responseMode={mode}
            thinkingMode={thinking}
            thinkingModes={["low", "high"]}
            supportsAgent={supportsAgent}
            webSearch={search}
            canSearch={canSearch}
            canRetry
            modelError={false}
            onModelChange={(provider, next) => setModel([provider, next])}
            onModeChange={setMode}
            onThinkingChange={setThinking}
            onSearchChange={setSearch}
            allowedDomains={domains}
            onDomainsChange={setDomains}
            onDirectRetry={() => {
              onDirectRetry?.();
              setOpen(false);
            }}
            onRetry={() => {
              onRetry({ model, mode, thinking, search, domains });
              setOpen(false);
            }}
            onRefreshModels={() => {}}
            onConfigure={() => {}}
          />
        }
      >
        <button aria-label="重试并保留版本">重试</button>
      </RetryAnswerMenu>
    </>
  );
}

describe("anchored retry menu", () => {
  it.each([
    [{ model_id: "deepseek-flash", web_search: true, search_route: "disabled" }, "deepseek-flash", "联网已开启"],
    [{ model_id: "deepseek-flash", web_search: false, search_route: "external" }, "deepseek-flash", "联网已关闭"],
    [{ model_id: "deepseek-chat", search_route: "model_native" }, "deepseek-chat", "联网已开启"],
    [{ model_id: "other-model", search_provider_id: "search-provider", search_route: "disabled" }, "other-model", "联网已开启"],
    [{ model_id: "other-model", search_route: "disabled" }, "other-model", "联网已关闭"],
    [{}, "模型未知", "联网状态未知"],
  ])("shows recorded answer metadata %#", async (trace, model, search) => {
    const user = userEvent.setup();
    render(<Harness onRetry={vi.fn()} previousAnswerTrace={trace} canSearch={false} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    const item = screen.getByRole("menuitem", { name: /沿用上次回答配置/ });
    expect(item).toHaveTextContent(`上次回答：${model}`);
    expect(item).toHaveTextContent(search);
  });

  it("separates direct historical retry from temporary custom retry settings", async () => {
    const onRetry = vi.fn();
    const onDirectRetry = vi.fn();
    const user = userEvent.setup();
    render(<Harness onRetry={onRetry} onDirectRetry={onDirectRetry} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onRetry).not.toHaveBeenCalled();
    expect(screen.getByRole("menu", { name: "重新生成" })).toHaveAttribute("data-side", "top");

    // Open unified ModelSelector submenu: provider groups, search, icon badge (not "· 图片"), sticky footer
    await user.click(screen.getByRole("menuitem", { name: /^模型/ }));
    expect(await screen.findByText("其他服务商")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "搜索模型" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "管理模型" })).toBeVisible();
    const flashOption = screen.getByRole("menuitemradio", { name: /deepseek-flash/ });
    expect(flashOption).toHaveAttribute("aria-checked", "true");
    expect(flashOption).not.toHaveTextContent("· 图片");
    expect(flashOption.querySelector('[aria-label="支持图片理解"]')).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /deepseek-chat/ }).querySelector('[aria-label="支持图片理解"]')).toBeNull();

    // Select a different temporary model without triggering generation
    screen.getByRole("menuitemradio", { name: /other-model/ }).focus();
    await user.keyboard("{Enter}");
    expect(onRetry).not.toHaveBeenCalled();
    expect(screen.getByRole("menuitem", { name: /^模型/ })).toHaveTextContent("other-model");

    // Toggle web search in temporary draft
    await user.click(screen.getByRole("menuitemcheckbox", { name: "联网搜索" }));
    expect(screen.getByRole("menuitemcheckbox", { name: "联网搜索" })).toHaveAttribute("aria-checked", "true");

    // Top direct-retry item still shows the historical answer snapshot
    expect(screen.getByRole("menuitem", { name: /沿用上次回答配置/ })).toHaveTextContent("上次回答：deepseek-flash");

    // Submit with explicit bottom custom-retry CTA
    await user.click(screen.getByRole("menuitem", { name: "使用当前配置重新生成" }));
    expect(onDirectRetry).not.toHaveBeenCalled();
    expect(onRetry).toHaveBeenCalledExactlyOnceWith({
      model: ["other", "other-model"],
      mode: "agentic",
      thinking: "low",
      search: true,
      domains: "",
    });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("triggers direct historical retry without using edited draft settings", async () => {
    const onRetry = vi.fn();
    const onDirectRetry = vi.fn();
    const user = userEvent.setup();
    render(<Harness onRetry={onRetry} onDirectRetry={onDirectRetry} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    await user.click(screen.getByRole("menuitem", { name: /^模型/ }));
    await user.click(await screen.findByRole("menuitemradio", { name: /other-model/ }));
    await user.click(screen.getByRole("menuitem", { name: /沿用上次回答配置/ }));
    expect(onDirectRetry).toHaveBeenCalledTimes(1);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("supports keyboard navigation, capability-limited reasoning slider and Escape focus return", async () => {
    const user = userEvent.setup();
    render(<Harness onRetry={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "重试并保留版本" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /沿用上次回答配置/ })).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowRight}");
    const slider = await screen.findByRole("slider", { name: "思考强度" });
    expect(slider).toHaveFocus();
    // Only supported stops ("关闭", "低", "高") appear — no unsupported "中" or "最高"
    expect(screen.queryByRole("button", { name: "中" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "最高" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "高" }));
    expect(screen.getByRole("menuitem", { name: /^思考强度/ })).toHaveTextContent("高");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("keeps a thinking-only retry available when the model cannot use Agent tools", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<Harness onRetry={onRetry} supportsAgent={false} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    await user.click(screen.getByRole("menuitem", { name: /^响应模式/ }));
    const thinking = await screen.findByRole("menuitemradio", { name: "思考" });
    expect(thinking).not.toHaveAttribute("aria-disabled", "true");
    thinking.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: /^思考强度/ })).not.toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByRole("menuitem", { name: "使用当前配置重新生成" }));
    expect(onRetry).toHaveBeenCalledExactlyOnceWith({
      model: ["deepseek", "deepseek-flash"],
      mode: "thinking",
      thinking: "low",
      search: false,
      domains: "",
    });
  });

  it("disables unavailable search and agent mode with an explanation", async () => {
    const user = userEvent.setup();
    render(<Harness onRetry={vi.fn()} canSearch={false} supportsAgent={false} />);
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    expect(screen.getByRole("menuitemcheckbox", { name: "联网搜索" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("当前模型与工作区暂无可用搜索能力")).toBeVisible();
    await user.click(screen.getByRole("menuitem", { name: /^响应模式/ }));
    expect(await screen.findByRole("menuitemradio", { name: "智能体" })).toHaveAttribute("aria-disabled", "true");
    screen.getByRole("menuitemradio", { name: "极速" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: /^思考强度/ })).toHaveAttribute("aria-disabled", "true");
  });

  it("disables direct historical retry when recorded model is no longer available", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        onRetry={vi.fn()}
        previousAnswerTrace={{ model_id: "removed-legacy-model", web_search: true }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "重试并保留版本" }));
    const directItem = screen.getByRole("menuitem", { name: /沿用上次回答配置/ });
    expect(directItem).toHaveAttribute("aria-disabled", "true");
    expect(directItem).toHaveTextContent("removed-legacy-model（当前不可用）");
    expect(screen.getByRole("menuitem", { name: "使用当前配置重新生成" })).not.toHaveAttribute("aria-disabled", "true");
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
    expect(screen.queryByRole("menuitem", { name: /^搜索来源/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitemcheckbox", { name: "联网搜索" }));
    const sources = screen.getByRole("menuitem", { name: /^搜索来源/ });
    sources.focus();
    await user.keyboard("{ArrowRight}");
    const input = await screen.findByRole("textbox", { name: "限定来源域名（可选）" });
    expect(input).toHaveFocus();
    await user.keyboard("react.dev, python.org");
    expect(input).toHaveValue("react.dev, python.org");
    expect(sources).toHaveTextContent("已限定");
    expect(onRetry).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

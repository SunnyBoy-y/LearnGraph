import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  resolveHistoricalGenerationConfig,
  type GenerationConfig,
  type ModelSelectorGroup,
} from "@/lib/generation-config";
import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import type { Provider } from "@/types/providers";
import { GenerationConfigMenu } from "./generation-config-menu";

vi.mock("@/lib/native-bridge", () => ({ haptic: vi.fn() }));

const sampleGroups: ModelSelectorGroup[] = [
  {
    id: "custom",
    label: "自定义兼容服务",
    models: [
      {
        id: "gemini-4-hydrogen",
        remote: true,
        capabilities: { supports_image_input: true },
      },
      {
        id: "gemini-4-neon",
        remote: true,
        capabilities: { supports_image_input: true },
      },
    ],
  },
  {
    id: "octopus",
    label: "章鱼",
    models: [
      {
        id: "gpt-6.2-sol",
        remote: true,
        capabilities: {
          supports_image_input: true,
          reasoning_efforts: ["low", "high", "max"],
        },
      },
      {
        id: "gpt-6.1-terra",
        remote: true,
        capabilities: {
          supports_image_input: false,
          reasoning_efforts: ["high"],
          thinking_required: true,
        },
      },
    ],
  },
  {
    id: "deepseek",
    label: "DeepSeek1",
    models: [
      {
        id: "deepseek-flash",
        remote: true,
        capabilities: {
          supports_image_input: true,
          reasoning_efforts: ["low", "medium", "high"],
        },
      },
    ],
  },
];

function MenuHarness({
  layout = "submenu",
  thinkingModes = ["low", "high", "max"] as ThinkingMode[],
  thinkingRequired = false,
  supportsAgent = true,
  onManageModels = vi.fn(),
}: {
  layout?: "submenu" | "inline";
  thinkingModes?: ThinkingMode[];
  thinkingRequired?: boolean;
  supportsAgent?: boolean;
  onManageModels?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [providerId, setProviderId] = useState("deepseek");
  const [modelId, setModelId] = useState("deepseek-flash");
  const [responseMode, setResponseMode] = useState<ResponseMode>("agentic");
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>("low");
  return (
    <GenerationConfigMenu
      open={open}
      onOpenChange={setOpen}
      layout={layout}
      groups={sampleGroups}
      providerId={providerId}
      modelId={modelId}
      responseMode={responseMode}
      thinkingMode={thinkingMode}
      thinkingModes={thinkingModes}
      supportsAgent={supportsAgent}
      thinkingRequired={thinkingRequired}
      onModelChange={(nextProvider, nextModel) => {
        setProviderId(nextProvider);
        setModelId(nextModel);
      }}
      onModeChange={(nextMode) => {
        setResponseMode(nextMode);
        if (nextMode === "fast") setThinkingMode("off");
      }}
      onThinkingChange={setThinkingMode}
      onManageModels={onManageModels}
      trigger={<button type="button">生成配置触发器</button>}
    />
  );
}

describe("unified GenerationConfigMenu & ModelSelector", () => {
  it("groups models by provider without repeating provider/protocol labels and shows image icon tooltip", async () => {
    const onManageModels = vi.fn();
    const user = userEvent.setup();
    render(<MenuHarness onManageModels={onManageModels} />);

    await user.click(screen.getByRole("button", { name: "生成配置触发器" }));
    expect(screen.getByText("生成配置")).toBeVisible();
    expect(screen.getByText("应用于后续消息")).toBeVisible();
    expect(screen.queryByText("响应与思考力度")).not.toBeInTheDocument();

    // Open ModelSelector submenu
    await user.click(screen.getByRole("menuitem", { name: /^模型/ }));
    expect(await screen.findByText("自定义兼容服务")).toBeVisible();
    expect(screen.getAllByText("章鱼")).toHaveLength(1);
    expect(screen.getAllByText("DeepSeek1")).toHaveLength(1);

    // No redundant protocol strings or "· 图片" text
    expect(screen.queryByText(/Compatible Chat/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Responses/)).not.toBeInTheDocument();
    expect(screen.queryByText(/· 图片/)).not.toBeInTheDocument();

    // Image capability icon + hover explanation
    const solItem = screen.getByRole("menuitemradio", { name: /gpt-6\.2-sol/ });
    expect(solItem.querySelector('[data-tooltip="支持图片理解"]')).toBeInTheDocument();
    const terraItem = screen.getByRole("menuitemradio", { name: /gpt-6\.1-terra/ });
    expect(terraItem.querySelector('[data-tooltip="支持图片理解"]')).toBeNull();

    // Search by model name and provider name
    const search = screen.getByRole("textbox", { name: "搜索模型" });
    await user.type(search, "章鱼");
    expect(screen.getByRole("menuitemradio", { name: /gpt-6\.2-sol/ })).toBeVisible();
    expect(screen.queryByRole("menuitemradio", { name: /deepseek-flash/ })).not.toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "not-existing");
    expect(screen.getByText("未找到匹配的模型")).toBeVisible();

    // Fixed footer manage button remains accessible
    await user.click(screen.getByRole("menuitem", { name: "管理模型" }));
    expect(onManageModels).toHaveBeenCalledTimes(1);
  });

  it("renders dynamic capability-driven stops in ReasoningSelector with stop descriptions", async () => {
    const user = userEvent.setup();
    render(<MenuHarness thinkingModes={["low", "high", "max"]} />);

    await user.click(screen.getByRole("button", { name: "生成配置触发器" }));
    await user.click(screen.getByRole("menuitem", { name: /^思考强度/ }));

    expect(await screen.findByRole("slider", { name: "思考强度" })).toBeVisible();
    expect(screen.getByText("适合日常问答、基础分析和常规任务。")).toBeVisible();
    expect(screen.getByRole("button", { name: "关闭" })).toBeVisible();
    expect(screen.getByRole("button", { name: "低" })).toBeVisible();
    expect(screen.getByRole("button", { name: "高" })).toBeVisible();
    expect(screen.getByRole("button", { name: "最高" })).toBeVisible();
    // Unsupported efforts must not appear
    expect(screen.queryByRole("button", { name: "中" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "极高" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "最高" }));
    expect(screen.getByText("启用模型最高推理强度，推理更充分但耗时较长。")).toBeVisible();
    expect(screen.getByRole("menuitem", { name: /^思考强度/ })).toHaveTextContent("最高");
  });

  it("hides off stop when thinkingRequired is true and works in inline layout", async () => {
    const user = userEvent.setup();
    render(
      <MenuHarness
        layout="inline"
        thinkingModes={["high", "max"]}
        thinkingRequired
      />,
    );

    await user.click(screen.getByRole("button", { name: "生成配置触发器" }));
    expect(screen.queryByRole("button", { name: "关闭" })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "极速" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "最高" }));
    await waitFor(() =>
      expect(screen.getByRole("slider", { name: "思考强度" })).toHaveAttribute(
        "aria-valuetext",
        "最高",
      ),
    );
  });
});

describe("3-state retry configuration resolution", () => {
  const providers: Provider[] = [
    {
      id: "octopus",
      provider_type: "openai_responses",
      display_name: "章鱼",
      enabled: true,
      remote_capability: true,
      capabilities: { reasoning_efforts: ["low", "high", "max"] },
    } as unknown as Provider,
    {
      id: "deepseek",
      provider_type: "openai_compatible_chat",
      display_name: "DeepSeek1",
      enabled: true,
      remote_capability: true,
      capabilities: { reasoning_efforts: ["low", "medium", "high"] },
    } as unknown as Provider,
  ];

  const sessionConfig: GenerationConfig = {
    providerId: "deepseek",
    modelId: "deepseek-flash",
    responseMode: "agentic",
    thinkingMode: "low",
    webSearch: false,
    allowedDomains: "",
  };

  it("initializes retry draft from historical answer trace rather than session config", () => {
    const resolved = resolveHistoricalGenerationConfig({
      trace: {
        provider_id: "octopus",
        model_id: "gpt-6.2-sol",
        agent_mode: true,
        thinking_mode: "high",
        web_search: true,
        search_route: "model_native",
      },
      groups: sampleGroups,
      providers,
      sessionFallback: sessionConfig,
    });

    expect(resolved.summary.recordedModelId).toBe("gpt-6.2-sol");
    expect(resolved.summary.modelAvailable).toBe(true);
    expect(resolved.summary.recordedModelMissing).toBe(false);
    expect(resolved.historicalConfig).toEqual({
      providerId: "octopus",
      modelId: "gpt-6.2-sol",
      responseMode: "agentic",
      thinkingMode: "high",
      webSearch: true,
      allowedDomains: "",
      searchRoute: "model_native",
    });
    expect(resolved.draftInitialConfig).toMatchObject({ ...sessionConfig, providerId: "octopus", modelId: "gpt-6.2-sol", thinkingMode: "high", webSearch: true });
    expect(resolved.draftInitialConfig).not.toBe(resolved.historicalConfig);
  });

  it("marks missing historical model and falls back draft model to session config without losing historical metadata", () => {
    const resolved = resolveHistoricalGenerationConfig({
      trace: {
        provider_id: "removed",
        model_id: "retired-model",
        agent_mode: false,
        thinking_mode: "off",
        web_search: true,
      },
      groups: sampleGroups,
      providers,
      sessionFallback: sessionConfig,
    });

    expect(resolved.summary.recordedModelId).toBe("retired-model");
    expect(resolved.summary.recordedModelMissing).toBe(true);
    expect(resolved.draftInitialConfig.providerId).toBe("deepseek");
    expect(resolved.draftInitialConfig.modelId).toBe("deepseek-flash");
    expect(resolved.draftInitialConfig.responseMode).toBe("fast");
    expect(resolved.draftInitialConfig.webSearch).toBe(true);
  });
});

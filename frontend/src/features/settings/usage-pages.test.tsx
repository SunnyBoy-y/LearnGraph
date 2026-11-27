import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/client";
import { authStore } from "@/api/auth-store";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { renderWithProviders } from "@/test/render";
import type { Provider } from "@/types/providers";
import type { BudgetPolicy, BudgetStatus, UsageEvent, UsageSummary } from "@/types/usage";
import {
  formatCompactTokens,
  formatDisplayMoney,
  formatFeatureLabel,
  resolveProviderInfo,
} from "./usage-formatting";
import { UsagePage } from "./usage-pages";

const WORKSPACE_ID = "ws-usage-test";
const USER_ID = "user-usage-test";

function buildProvider(id: string, name: string): Provider {
  return {
    id,
    workspace_id: WORKSPACE_ID,
    display_name: name,
    provider_type: "openai_compatible_chat",
    base_url: "https://api.example.com/v1",
    api_key_masked: "sk-****1234",
    enabled: true,
    remote_capability: true,
    capabilities: { default_model: "deepseek-flash" },
    status: "healthy",
    secret_status: "active",
    secret_version: 1,
    secret_key_provider: "environment",
    secret_key_version: 1,
    created_at: "2026-11-01T00:00:00Z",
  };
}

const DELETED_ID = "368d613b-8ad9-4a04-9999-111111111111";
const ORPHAN_ID = "99999999-aaaa-bbbb-cccc-000000000000";

const SUMMARY_FIXTURE: UsageSummary = {
  schema_version: 2,
  workspace_id: WORKSPACE_ID,
  currency: "USD",
  input_tokens: 300_000_000,
  cached_input_tokens: 50_000_000,
  cache_creation_input_tokens: 0,
  output_tokens: 166_125_325,
  reasoning_tokens: 10_000_000,
  total_tokens: 466_125_325,
  attempts: 9047,
  cost_amount: "88.45",
  reserved_amount: "195.12",
  unpriced_events: 2545,
  unknown_currency_events: 12,
  remote_usage_recorded: true,
  provider_names: {
    "prov-1": "DeepSeek 主力节点",
    [DELETED_ID]: "旧版中转站",
  },
  breakdown: {
    day: [
      { label: "2026-11-24", cost_amount: "12.00", attempts: 1200, total_tokens: 45_000_000, unknown_events: 0 },
      { label: "2026-11-25", cost_amount: "24.10", attempts: 2400, total_tokens: 90_000_000, unknown_events: 5 },
    ],
    model_id: [
      { label: "deepseek-flash", cost_amount: "2.09", attempts: 2865, total_tokens: 120_000_000, unknown_events: 0 },
      { label: "deepseek-v4-flash", cost_amount: "1.72", attempts: 3498, total_tokens: 180_000_000, unknown_events: 0 },
      { label: "claude-opus-4.6", cost_amount: "0.88", attempts: 9, total_tokens: 1_200_000, unknown_events: 0 },
      { label: "deepseek-v4-pro", cost_amount: "0.49", attempts: 168, total_tokens: 12_000_000, unknown_events: 0 },
      { label: "gpt-5.4", cost_amount: "0.35", attempts: 112, total_tokens: 8_000_000, unknown_events: 0 },
      { label: "sixth-model", cost_amount: "0.15", attempts: 42, total_tokens: 2_000_000, unknown_events: 0 },
      { label: "unpriced-only-model", cost_amount: "0", attempts: 18, total_tokens: 500_000, unknown_events: 18 },
    ],
    provider_id: [
      { label: "prov-1", cost_amount: "65.00", attempts: 6000, total_tokens: 300_000_000, unknown_events: 0 },
      { label: DELETED_ID, cost_amount: "23.45", attempts: 3000, total_tokens: 160_000_000, unknown_events: 0 },
      { label: ORPHAN_ID, cost_amount: "0", attempts: 47, total_tokens: 6_125_325, unknown_events: 47 },
    ],
    feature: [
      { label: "chat", cost_amount: "60.00", attempts: 5000, total_tokens: 320_000_000, unknown_events: 0 },
      { label: "audio_transcription", cost_amount: "18.00", attempts: 1200, total_tokens: 0, unknown_events: 0 },
      { label: "chat_session_activity_summary", cost_amount: "5.45", attempts: 800, total_tokens: 60_000_000, unknown_events: 0 },
      { label: "chat_fast_preface", cost_amount: "3.00", attempts: 1200, total_tokens: 40_000_000, unknown_events: 0 },
      { label: "chat_goal_verification", cost_amount: "1.20", attempts: 600, total_tokens: 30_000_000, unknown_events: 0 },
      { label: "chat_retry", cost_amount: "0.80", attempts: 247, total_tokens: 16_125_325, unknown_events: 0 },
    ],
  },
};

const EVENTS_FIXTURE: UsageEvent[] = [
  {
    id: "evt-1",
    workspace_id: WORKSPACE_ID,
    provider_id: DELETED_ID,
    provider_display_name: "旧版中转站",
    model_id: "deepseek-flash",
    feature: "chat_fast_preface",
    currency: "USD",
    cost_amount: null,
    reserved_amount: "0.02",
    input_tokens: 1200,
    cached_input_tokens: 0,
    cache_creation_input_tokens: 0,
    output_tokens: 400,
    reasoning_tokens: 0,
    total_tokens: 1600,
    attempt: 2,
    cost_status: "unpriced",
    amount_source: "unknown",
    record_stage: "settled",
    price_version_id: null,
    pricing_snapshot: {},
    legacy_billing_snapshot: {},
    usage_units: {},
    archived_at: null,
    latency_ms: 420,
    created_at: "2026-11-25T10:00:00Z",
  },
];

const BUDGET_POLICIES: BudgetPolicy[] = [
  {
    id: "bp-1",
    workspace_id: WORKSPACE_ID,
    name: "月度上限",
    provider_id: "prov-1",
    model_id: "*",
    feature: "chat",
    period: "calendar_month_utc",
    currency: "USD",
    soft_limit: "80",
    hard_limit: "100",
    enabled: true,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
  },
];

const BUDGET_STATUSES: BudgetStatus[] = [
  {
    policy_id: "bp-1",
    name: "月度上限",
    provider_id: "prov-1",
    model_id: "*",
    feature: "chat",
    period: "calendar_month_utc",
    currency: "USD",
    period_start: "2026-11-01T00:00:00Z",
    period_end: "2026-12-01T00:00:00Z",
    spent_amount: "65",
    reserved_amount: "5",
    soft_limit: "80",
    hard_limit: "100",
    remaining_amount: "30",
    unknown_events: 0,
    status: "ready",
    usage_ratio: 0.7,
    soft_exceeded: false,
    hard_exceeded: false,
    enabled: true,
  },
];

function renderUsagePage(initialEntry = "/w/ws-usage-test/settings/models?tab=usage") {
  authStore.setSession({
    accessToken: "test-token",
    userId: USER_ID,
    username: "tester",
    displayName: "Tester",
    workspaceId: WORKSPACE_ID,
    sessionId: "sess-1",
    mustChangePassword: false,
  });

  const authValue = {
    workspaceId: WORKSPACE_ID,
    userId: USER_ID,
    effectivePermissions: ["workspace.read", "workspace.manage", "workspace.write"],
    isSystemAdmin: true,
  } as unknown as AuthContextValue;

  return renderWithProviders(
    <AuthContext.Provider value={authValue}>
      <UsagePage />
    </AuthContext.Provider>,
    { initialEntries: [initialEntry] },
  );
}

describe("usage-formatting helpers", () => {
  it("maps internal feature identifiers to Chinese labels", () => {
    expect(formatFeatureLabel("audio_transcription")).toBe("语音转写");
    expect(formatFeatureLabel("chat")).toBe("对话");
    expect(formatFeatureLabel("chat_retry")).toBe("对话重试");
    expect(formatFeatureLabel("chat_fast_preface")).toBe("快速响应");
    expect(formatFeatureLabel("chat_goal_verification")).toBe("目标验证");
    expect(formatFeatureLabel("chat_session_activity_summary")).toBe("会话摘要");
  });

  it("formats compact token numbers and preserves unknown cost semantics", () => {
    expect(formatCompactTokens(466_125_325)).toBe("466.13M");
    expect(formatDisplayMoney("0", "USD", { allUnknown: true })).toBe("待核实");
    expect(formatDisplayMoney("0.0006", "USD")).toBe("$0.0006");
    expect(formatDisplayMoney("88.45", "USD")).toBe("$88.45");
  });

  it("resolves active, historical deleted, and unknown deleted providers", () => {
    const active = new Map([["prov-1", buildProvider("prov-1", "DeepSeek 主力节点")]]);
    const history = { [DELETED_ID]: "旧版中转站" };
    expect(resolveProviderInfo("prov-1", active, history)).toMatchObject({
      name: "DeepSeek 主力节点",
      isDeleted: false,
    });
    expect(resolveProviderInfo(DELETED_ID, active, history)).toMatchObject({
      name: "旧版中转站",
      isDeleted: true,
    });
    expect(resolveProviderInfo(ORPHAN_ID, active, history)).toMatchObject({
      name: "已删除的连接",
      isDeleted: true,
    });
  });
});

describe("UsagePage information architecture", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, "get").mockImplementation(async (path) => {
      if (path === "/providers") return [buildProvider("prov-1", "DeepSeek 主力节点")];
      if (path === "/usage/summary") return SUMMARY_FIXTURE;
      if (path === "/usage/events") return EVENTS_FIXTURE;
      if (path === "/usage/budget-policies") return BUDGET_POLICIES;
      if (path === "/usage/budget-status") return BUDGET_STATUSES;
      if (path === "/usage/budget-alerts") return [];
      if (path === "/usage/alert-email") {
        return {
          enabled: false,
          smtp_host: "",
          smtp_port: 465,
          smtp_security: "ssl",
          smtp_username: "",
          has_password: false,
          from_address: "",
          to_addresses: [],
        };
      }
      if (path === "/usage/models-dev") {
        return {
          source: "https://models.dev/api.json",
          origin: "bundled",
          fetched_at: null,
          provider_count: 1,
          model_count: 1,
          priced_model_count: 1,
          auto_refresh_enabled: true,
          refresh_interval_seconds: 86400,
          next_refresh_at: null,
          stale: true,
        };
      }
      if (path === "/usage/price-catalog") return [];
      if (path === "/usage/manual-prices") return [];
      return [];
    });
  });

  it("renders single-row toolbar, hierarchical metrics panel, and Top 5 distribution without raw UUIDs", async () => {
    const user = userEvent.setup();
    renderUsagePage();

    expect(await screen.findByText("$88.45")).toBeInTheDocument();
    expect(screen.getByText("$195.12")).toBeInTheDocument();
    expect(screen.getByText("9,047")).toBeInTheDocument();
    expect(screen.getByText("466.13M")).toBeInTheDocument();
    expect(screen.getByText(/待核实记录 2,545 条/)).toBeInTheDocument();
    expect(screen.queryByText(/待核实金额/)).not.toBeInTheDocument();

    // Advanced filter inputs are collapsed inside the popover by default.
    expect(screen.queryByLabelText("筛选服务连接")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "筛选" }));
    expect(await screen.findByLabelText("筛选服务连接")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "完成" }));

    // Consumption distribution defaults to Top 5 models.
    const dist = screen.getByLabelText("消耗分布");
    expect(within(dist).getByText("deepseek-flash")).toBeInTheDocument();
    expect(within(dist).getByText("gpt-5.4")).toBeInTheDocument();
    expect(within(dist).queryByText("sixth-model")).not.toBeInTheDocument();

    // Opening "查看全部模型" shows all models including unknown-only model marked 待核实.
    await user.click(within(dist).getByRole("button", { name: /查看全部模型/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("sixth-model")).toBeInTheDocument();
    expect(within(dialog).getByText("unpriced-only-model")).toBeInTheDocument();
    expect(within(dialog).getByText("待核实")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    // Switching to provider dimension resolves connection names and never displays raw UUIDs.
    await user.click(within(dist).getByRole("tab", { name: "按供应商" }));
    expect(within(dist).getByText("DeepSeek 主力节点")).toBeInTheDocument();
    expect(within(dist).getByText("旧版中转站")).toBeInTheDocument();
    expect(within(dist).getByText("已删除的连接")).toBeInTheDocument();
    expect(within(dist).queryByText(DELETED_ID)).not.toBeInTheDocument();
    expect(within(dist).queryByText(ORPHAN_ID)).not.toBeInTheDocument();

    // Switching to feature dimension maps internal identifiers to readable Chinese labels.
    await user.click(within(dist).getByRole("tab", { name: "按功能" }));
    expect(within(dist).getByText("对话")).toBeInTheDocument();
    expect(within(dist).getByText("语音转写")).toBeInTheDocument();
    expect(within(dist).getByText("会话摘要")).toBeInTheDocument();
    expect(within(dist).queryByText("chat_session_activity_summary")).not.toBeInTheDocument();
  });

  it("isolates subpages without repeating overview cards and supports anomaly drilldown with debug details", async () => {
    const user = userEvent.setup();
    renderUsagePage();

    expect(await screen.findByText("$88.45")).toBeInTheDocument();

    // Clicking "查看异常记录" opens 用量明细 filtered to unpriced records and hides overview cards.
    await user.click(screen.getByRole("button", { name: /查看异常记录/ }));
    await waitFor(() => {
      expect(screen.queryByLabelText("核心指标")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("费用趋势")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /待核实费用/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("旧版中转站")).toBeInTheDocument();
    expect(screen.getByText("快速响应")).toBeInTheDocument();

    // Developer debug dialog retains raw UUID and internal feature identifier.
    await user.click(screen.getByRole("button", { name: "详情" }));
    const detail = await screen.findByRole("dialog");
    expect(within(detail).getByText(`UUID: ${DELETED_ID}`)).toBeInTheDocument();
    expect(within(detail).getByText("标识: chat_fast_preface")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    // Switching to 预算管理 renders budget policies independently without overview cards.
    await user.click(screen.getByRole("tab", { name: "预算管理" }));
    expect(await screen.findByText("月度上限")).toBeInTheDocument();
    expect(screen.queryByLabelText("核心指标")).not.toBeInTheDocument();
    expect(screen.getByText(/DeepSeek 主力节点 · 全部模型 · 对话/)).toBeInTheDocument();

    // Opening 价格配置 renders catalog independently without overview cards.
    await user.click(screen.getByRole("button", { name: /价格配置/ }));
    expect(await screen.findByText("价格来源：models.dev")).toBeInTheDocument();
    expect(screen.queryByLabelText("核心指标")).not.toBeInTheDocument();
  });
});

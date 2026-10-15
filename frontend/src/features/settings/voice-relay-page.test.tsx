import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCurrentUser } from "@/api/control";
import { getVoiceRelay, saveVoiceRelay, testVoiceRelay, type VoiceRelayConfigWire } from "@/api/voice";
import { VoiceRelayPage } from "./voice-relay-page";

vi.mock("@/api/control", () => ({ getCurrentUser: vi.fn() }));
vi.mock("@/api/voice", () => ({
  getVoiceRelay: vi.fn(), saveVoiceRelay: vi.fn(), testVoiceRelay: vi.fn(),
  clearVoiceRelay: vi.fn(), setVoiceRelayEnabled: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));

const pool = ["stun:stun.miwifi.com:3478", "stun:stun.hitv.com:3478"];
const urls = ["stun:custom.example:3478", "turn:turn.cloudflare.com:3478?transport=tcp", "turns:turn.cloudflare.com:443?transport=tcp", "turns:turn.cloudflare.com:5349?transport=tcp"];
function config(mode: "manual" | "cloudflare" = "manual"): VoiceRelayConfigWire {
  return {
    configured: true, enabled: true, mode, urls, stun_urls: pool, stun_fallback_enabled: false,
    key_id: mode === "cloudflare" ? "saved-key" : null, api_base: "https://rtc.live.cloudflare.com/v1",
    credential_ttl_seconds: 86400, secret_masked: null, secret_fingerprint: null,
    secret_configured: mode === "cloudflare", status: "configured", status_detail: null,
    last_checked_at: null, updated_by_user_id: null,
    defaults: { api_base: "https://rtc.live.cloudflare.com/v1", urls, stun_urls: pool, credential_ttl_seconds: 86400 },
  };
}

function mount(data = config()) {
  vi.mocked(getVoiceRelay).mockResolvedValue(data);
  vi.mocked(saveVoiceRelay).mockImplementation(async (payload) => ({ ...data, ...payload } as VoiceRelayConfigWire));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><VoiceRelayPage /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getCurrentUser).mockResolvedValue({ is_system_admin: true } as Awaited<ReturnType<typeof getCurrentUser>>);
  vi.mocked(testVoiceRelay).mockResolvedValue({ ok: true, detail: "测试通过", credential_masked: "无需凭据", cloudflare_urls: [], configured_urls: pool, probes: [] });
});
afterEach(cleanup);

describe("voice relay settings", () => {
  it("saves manual STUN without rendering or submitting Cloudflare secrets", async () => {
    mount();
    const textbox = await screen.findByLabelText("手动 STUN 地址池");
    expect(screen.queryByLabelText("Cloudflare TURN Key ID")).toBeNull();
    fireEvent.change(textbox, { target: { value: `${pool[0]}\n${pool[0]}\n${pool[1]}` } });
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(saveVoiceRelay).toHaveBeenCalledTimes(1));
    expect(vi.mocked(saveVoiceRelay).mock.calls[0][0]).toEqual({ mode: "manual", stun_urls: pool, stun_fallback_enabled: false });
    await waitFor(() => expect(testVoiceRelay).toHaveBeenCalledTimes(1));
  });

  it("preserves custom STUN and multiple TLS URLs during Cloudflare save", async () => {
    mount(config("cloudflare"));
    await waitFor(() => expect((screen.getByLabelText("Cloudflare TURN Key ID") as HTMLInputElement).value).toBe("saved-key"));
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(saveVoiceRelay).toHaveBeenCalledTimes(1));
    const saved = vi.mocked(saveVoiceRelay).mock.calls[0][0];
    expect(saved.urls).toEqual(urls);
    expect(saved.stun_fallback_enabled).toBe(false);
    expect(saved.secret).toBeUndefined();
  });

  it("requires explicit opt-in before enabling Cloudflare STUN fallback", async () => {
    mount(config("cloudflare"));
    await waitFor(() => expect((screen.getByLabelText("Cloudflare TURN Key ID") as HTMLInputElement).value).toBe("saved-key"));
    expect(screen.queryByLabelText("手动 STUN 地址池")).toBeNull();
    fireEvent.click(screen.getByRole("switch", { name: "启用独立 STUN 备选与兜底" }));
    await screen.findByLabelText("手动 STUN 地址池");
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(saveVoiceRelay).toHaveBeenCalledTimes(1));
    expect(vi.mocked(saveVoiceRelay).mock.calls[0][0].stun_fallback_enabled).toBe(true);
  });

  it("does not request deployment config for a non-admin", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({ is_system_admin: false } as Awaited<ReturnType<typeof getCurrentUser>>);
    mount();
    await screen.findByText("仅终端管理员（系统管理员）可配置全系统通用的语音中继。");
    expect(getVoiceRelay).not.toHaveBeenCalled();
  });
});

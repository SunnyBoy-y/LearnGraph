import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCurrentUser } from "@/api/control";
import { getVoiceRelay, saveVoiceRelay, testVoiceRelay, type VoiceRelayConfigWire } from "@/api/voice";
import { VoiceRelayPage } from "./voice-relay-page";
import { SettingsDirtyContext } from '@/lib/settings-dirty';

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

function mount(data = config(), registerDirty = (_id: string, _dirty: boolean) => {}) {
  let persisted = data;
  vi.mocked(getVoiceRelay).mockImplementation(async () => persisted);
  vi.mocked(saveVoiceRelay).mockImplementation(async ({ secret, ...payload }) => {
    persisted = {
      ...persisted, ...payload,
      ...(secret && payload.mode !== "manual" ? { secret_mode: payload.mode, secret_configured: true } : {}),
    } as VoiceRelayConfigWire;
    return persisted;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><SettingsDirtyContext.Provider value={registerDirty}><VoiceRelayPage /></SettingsDirtyContext.Provider></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getCurrentUser).mockResolvedValue({ is_system_admin: true } as Awaited<ReturnType<typeof getCurrentUser>>);
  vi.mocked(testVoiceRelay).mockResolvedValue({ ok: true, detail: "测试通过", credential_masked: "无需凭据", cloudflare_urls: [], configured_urls: pool, probes: [] });
});
afterEach(cleanup);

describe("voice relay settings", () => {
  it("protects a draft populated by the preset button", async () => {
    const dirty = vi.fn();
    mount(config(), dirty);
    await screen.findByLabelText('手动 STUN 地址池');
    fireEvent.click(screen.getByRole('button', { name: '填入国内候选（小米 / 芒果 / B 站）' }));
    await waitFor(() => expect(dirty).toHaveBeenLastCalledWith('voice-relay', true));
  });

  it("locks edits and destructive actions while a connection probe is pending", async () => {
    mount();
    vi.mocked(testVoiceRelay).mockImplementationOnce(() => new Promise(() => {}));
    const input = await screen.findByLabelText('手动 STUN 地址池');
    fireEvent.click(screen.getByRole('button', { name: '仅测试' }));
    await waitFor(() => expect(testVoiceRelay).toHaveBeenCalledOnce());
    expect(input).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除配置' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '紧急停用' })).toBeDisabled();
  });

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

  it("saves generic UDP/TCP TURN credentials and omits the password from the saved-config probe", async () => {
    mount(config("cloudflare"));
    await screen.findByLabelText("Cloudflare TURN Key ID");
    fireEvent.click(screen.getByRole("radio", { name: "通用 TURN（自建或第三方，用户名与密码）" }));
    const addresses = screen.getByLabelText("TURN 服务地址");
    expect(addresses).toHaveValue("");
    expect(screen.queryByLabelText("Cloudflare API Token")).toBeNull();
    const turnUrls = ["turn:relay.example.com:3478?transport=udp", "turn:relay.example.com:3478?transport=tcp"];
    fireEvent.change(addresses, { target: { value: [...turnUrls, turnUrls[0]].join("\n") } });
    fireEvent.change(screen.getByLabelText("TURN 用户名"), { target: { value: "test-user" } });
    expect(screen.getByRole("button", { name: "保存并测试连接" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("TURN 密码"), { target: { value: " test-only-password " } });
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(saveVoiceRelay).toHaveBeenCalledOnce());
    expect(vi.mocked(saveVoiceRelay).mock.calls[0][0]).toEqual({
      mode: "turn", urls: turnUrls, username: "test-user", secret: " test-only-password ",
      stun_urls: pool, stun_fallback_enabled: false,
    });
    await waitFor(() => expect(testVoiceRelay).toHaveBeenCalledOnce());
    expect(vi.mocked(testVoiceRelay).mock.calls[0][0].secret).toBeUndefined();
    expect(screen.getByLabelText("TURN 密码")).toHaveValue("");
  });

  it("loads saved generic TURN and keeps a blank password while editing URL order", async () => {
    const turnUrls = ["turn:relay.example.com:3478?transport=udp", "turn:relay.example.com:3478?transport=tcp"];
    mount({ ...config(), mode: "turn", secret_mode: "turn", urls: turnUrls, username: "saved-user", secret_configured: true, secret_masked: "••••" });
    const addresses = await screen.findByLabelText("TURN 服务地址");
    await waitFor(() => expect(addresses).toHaveValue(turnUrls.join("\n")));
    expect(screen.getByLabelText("TURN 用户名")).toHaveValue("saved-user");
    expect(screen.getByLabelText("TURN 密码")).toHaveValue("");
    fireEvent.change(addresses, { target: { value: [...turnUrls].reverse().join("\n") } });
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(saveVoiceRelay).toHaveBeenCalledOnce());
    const saved = vi.mocked(saveVoiceRelay).mock.calls[0][0];
    expect(saved.urls).toEqual([...turnUrls].reverse());
    expect(saved.secret).toBeUndefined();
    expect(saved.key_id).toBeUndefined();
  });

  it("restores a retained generic credential from manual STUN without asking for the password again", async () => {
    const turnUrls = ["turns:relay.example.com:5349?transport=tcp"];
    mount({ ...config(), secret_mode: "turn", urls: turnUrls, username: "retained-user", secret_configured: true });
    await screen.findByLabelText("手动 STUN 地址池");
    fireEvent.click(screen.getByRole("radio", { name: "通用 TURN（自建或第三方，用户名与密码）" }));
    expect(screen.getByLabelText("TURN 服务地址")).toHaveValue(turnUrls[0]);
    expect(screen.getByRole("button", { name: "保存并测试连接" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(saveVoiceRelay).toHaveBeenCalledOnce());
    expect(vi.mocked(saveVoiceRelay).mock.calls[0][0].secret).toBeUndefined();
  });

  it("requires a fresh Cloudflare token when switching from generic TURN and clears draft passwords", async () => {
    mount({ ...config("cloudflare"), mode: "turn", secret_mode: "turn", urls: ["turn:relay.example.com:3478"], username: "saved-user" });
    await screen.findByLabelText("TURN 密码");
    fireEvent.change(screen.getByLabelText("TURN 密码"), { target: { value: "draft-only-password" } });
    fireEvent.click(screen.getByRole("radio", { name: "Cloudflare TURN（可启用独立 STUN 兜底）" }));
    expect(screen.getByRole("button", { name: "保存并测试连接" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Cloudflare API Token"), { target: { value: "new-test-token" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));
    await waitFor(() => expect(saveVoiceRelay).toHaveBeenCalledOnce());
    expect(vi.mocked(saveVoiceRelay).mock.calls[0][0].urls).toEqual(urls);
    expect(vi.mocked(saveVoiceRelay).mock.calls[0][0].secret).toBe("new-test-token");
    await waitFor(() => expect(testVoiceRelay).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("radio", { name: "通用 TURN（自建或第三方，用户名与密码）" }));
    expect(screen.getByLabelText("TURN 密码")).toHaveValue("");
  });

  it("does not request deployment config for a non-admin", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({ is_system_admin: false } as Awaited<ReturnType<typeof getCurrentUser>>);
    mount();
    await screen.findByText("仅终端管理员（系统管理员）可配置全系统通用的语音中继。");
    expect(getVoiceRelay).not.toHaveBeenCalled();
  });
});

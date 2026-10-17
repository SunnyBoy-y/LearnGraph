import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { generateDefaultAvatar, updateProfile, uploadAvatar } from "@/api/auth";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { ProfileEditor } from "./profile-editor";

vi.mock("@/api/auth", () => ({ updateProfile: vi.fn(), uploadAvatar: vi.fn(), generateDefaultAvatar: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const profile = { id: "user-1", display_name: "小林", avatar_url: "data:image/png;base64,avatar" };
const applyProfile = vi.fn();

function renderEditor() {
  const value = {
    authenticated: true, username: "旧昵称", avatarUrl: null, updateProfile: applyProfile,
    userId: "user-1", workspaceId: "ws-1", workspaceName: "学习空间", isSystemAdmin: false,
    effectivePermissions: [], mustChangePassword: false,
  } as unknown as AuthContextValue;
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
    <AuthContext.Provider value={value}><ProfileEditor /></AuthContext.Provider>
  </QueryClientProvider>);
}

describe("profile editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(updateProfile).mockResolvedValue(profile);
    vi.mocked(uploadAvatar).mockResolvedValue(profile);
    vi.mocked(generateDefaultAvatar).mockResolvedValue(profile);
  });

  it("saves a trimmed nickname and publishes the returned profile", async () => {
    renderEditor();
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "保存昵称" })).toBeDisabled();
    await user.clear(screen.getByLabelText("昵称"));
    await user.type(screen.getByLabelText("昵称"), "  小林  ");
    await user.click(screen.getByRole("button", { name: "保存昵称" }));
    await waitFor(() => expect(applyProfile).toHaveBeenCalledWith(profile));
    expect(updateProfile).toHaveBeenCalledWith("小林");
  });

  it("rejects blank names and keeps the old profile when saving fails", async () => {
    vi.mocked(updateProfile).mockRejectedValue(new Error("保存失败"));
    renderEditor();
    fireEvent.change(screen.getByLabelText("昵称"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "保存昵称" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("昵称"), { target: { value: "新昵称" } });
    await userEvent.click(screen.getByRole("button", { name: "保存昵称" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存昵称" })).toBeEnabled());
    expect(applyProfile).not.toHaveBeenCalled();
    expect(screen.getByLabelText("昵称")).toHaveValue("新昵称");
  });

  it("uploads an image and permits selecting the same file again", async () => {
    renderEditor();
    const file = new File(["image"], "avatar.png", { type: "image/png" });
    const input = screen.getByLabelText("选择头像图片");
    await userEvent.upload(input, file);
    await waitFor(() => expect(applyProfile).toHaveBeenCalledWith(profile));
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.upload(input, file);
    await waitFor(() => expect(uploadAvatar).toHaveBeenCalledTimes(2));
    expect(uploadAvatar).toHaveBeenCalledWith(file);
  });

  it("rejects oversized and unsupported files before upload", async () => {
    renderEditor();
    const input = screen.getByLabelText("选择头像图片");
    fireEvent.change(input, { target: { files: [new File([new Uint8Array(5 * 1024 * 1024 + 1)], "large.png", { type: "image/png" })] } });
    fireEvent.change(input, { target: { files: [new File(["svg"], "avatar.svg", { type: "image/svg+xml" })] } });
    expect(uploadAvatar).not.toHaveBeenCalled();
  });

  it("restores the default avatar and prevents overlapping profile operations", async () => {
    const defaultProfile = { ...profile, avatar_url: null };
    let finish!: (value: typeof profile | typeof defaultProfile) => void;
    vi.mocked(generateDefaultAvatar).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    renderEditor();
    await userEvent.click(screen.getByRole("button", { name: "生成默认头像" }));
    expect(screen.getByRole("button", { name: "上传图片" })).toBeDisabled();
    expect(screen.getByLabelText("昵称")).toBeDisabled();
    await act(async () => finish(defaultProfile));
    await waitFor(() => expect(applyProfile).toHaveBeenCalledWith(defaultProfile));
    expect(generateDefaultAvatar).toHaveBeenCalledTimes(1);
  });
});

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";

import { authStore } from "@/api/auth-store";
import { listWorkspaces } from "@/api/auth";
import { getCurrentUser } from "@/api/control";
import type { CurrentUser } from "@/types/control";
import { AuthProvider } from "./auth-context";
import { useAuth } from "./auth-context-value";

vi.mock("@/api/control", () => ({ getCurrentUser: vi.fn() }));
vi.mock("@/api/auth", () => ({ listWorkspaces: vi.fn() }));

const freshProfile = { id: "user-1", display_name: "新昵称", avatar_url: "data:image/png;base64,new" };
const serverUser: CurrentUser = {
  id: "user-1", tenant_id: "tenant-1", username: "login-name", email: null,
  display_name: "服务端昵称", avatar_url: "data:image/png;base64,server", status: "active",
  is_system_admin: false, must_change_password: false, session_id: "session-1",
};

function ProfileProbe() {
  const auth = useAuth();
  return <>
    <output>{auth.userId}|{auth.username}|{auth.avatarUrl}</output>
    <button onClick={() => auth.updateProfile(freshProfile)}>同步已保存资料</button>
  </>;
}

function renderProfile() {
  return render(<MemoryRouter><AuthProvider><ProfileProbe /></AuthProvider></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listWorkspaces).mockResolvedValue([]);
});

it("hydrates profile and user identity for legacy stored sessions", async () => {
  authStore.setSession({ accessToken: "token", workspaceId: "ws-1", username: "login-name" });
  vi.mocked(getCurrentUser).mockResolvedValue(serverUser);
  renderProfile();
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("user-1|服务端昵称|data:image/png;base64,server"));
  expect(authStore.getSession()).toMatchObject({ userId: "user-1", displayName: "服务端昵称", avatarUrl: serverUser.avatar_url });
});

it("does not let an older heartbeat response undo a saved profile", async () => {
  authStore.setSession({ accessToken: "token", workspaceId: "ws-1", userId: "user-1", displayName: "旧昵称" });
  let resolveUser!: (user: CurrentUser) => void;
  vi.mocked(getCurrentUser).mockImplementation(() => new Promise((resolve) => { resolveUser = resolve; }));
  renderProfile();
  await userEvent.click(screen.getByRole("button", { name: "同步已保存资料" }));
  await act(async () => resolveUser(serverUser));
  expect(screen.getByRole("status")).toHaveTextContent("user-1|新昵称|data:image/png;base64,new");
  expect(authStore.getSession()).toMatchObject({ displayName: "新昵称", avatarUrl: freshProfile.avatar_url });
});

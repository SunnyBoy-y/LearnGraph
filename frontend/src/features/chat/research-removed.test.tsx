import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { ProvidersPage } from "@/features/settings/provider-pages";
import { renderWithProviders } from "@/test/render";
import { ChatCanvasPage } from "./chat-pages";

describe("removed research controls", () => {
  beforeEach(() => {
    vi.spyOn(apiClient, "get").mockResolvedValue([]);
    vi.spyOn(apiClient, "post").mockRejectedValue(new Error("Unexpected write"));
  });

  it("keeps chat functions available without research actions", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AuthContext.Provider value={{ workspaceId: "test-workspace" } as AuthContextValue}>
        <TooltipProvider>
          <Routes>
            <Route path="/w/:workspaceId/chat/:sessionId" element={<ChatCanvasPage />} />
          </Routes>
        </TooltipProvider>
      </AuthContext.Provider>,
      { initialEntries: ["/w/test-workspace/chat/new"] },
    );
    await screen.findByRole("heading", { name: "从一个问题开始" });
    await user.click(screen.getByRole("button", { name: "打开对话功能菜单" }));
    expect(await screen.findByText("添加资料")).toBeInTheDocument();
    expect(screen.queryByText(/深度研究|Deep Research/i)).not.toBeInTheDocument();
    expect(document.querySelector('a[href*="/research/tasks"]')).toBeNull();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("shows the remaining service categories without a research tab", async () => {
    renderWithProviders(
      <AuthContext.Provider value={{ workspaceId: "test-workspace" } as AuthContextValue}>
        <TooltipProvider><ProvidersPage /></TooltipProvider>
      </AuthContext.Provider>,
    );
    expect(await screen.findByRole("tab", { name: "网页抓取", exact: true })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "搜索", exact: true })).toBeInTheDocument();
    expect(screen.queryByText(/深度研究|Deep Research/i)).not.toBeInTheDocument();
  });
});

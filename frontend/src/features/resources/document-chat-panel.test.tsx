import "@/test/pdfjs-polyfills";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { discoverProviderModels, listProviders } from "@/api/providers";
import { listSessionMessages, listSessions } from "@/api/sessions";
import { documentPreviewFile as file, documentPreviewModels, documentPreviewProviders as providers } from "@/dev/document-model-fixtures";
import { getSessionComposerPrefs } from "@/lib/session-composer-prefs";
import { renderWithProviders } from "@/test/render";
import { DocumentChatPanel } from "./document-chat-panel";

vi.mock("@/api/providers", () => ({ listProviders: vi.fn(), discoverProviderModels: vi.fn() }));
vi.mock("@/api/sessions", () => ({
  listSessions: vi.fn(), listSessionMessages: vi.fn(), createSession: vi.fn(),
  streamSessionMessage: vi.fn(), cancelSessionMessage: vi.fn(),
}));

function renderPanel(sessionId = "") {
  const send = vi.fn();
  renderWithProviders(<DocumentChatPanel compact file={file} sessionId={sessionId} workspaceId={file.workspace_id}
    selection={null} onClearSelection={vi.fn()} onSessionChange={vi.fn()} onSendToFullChat={send} />);
  return send;
}

async function openMenu() {
  const trigger = screen.getByRole("button", { name: "选择响应模式、思考力度和模型" });
  await waitFor(() => expect(trigger).toBeEnabled());
  await userEvent.click(trigger);
  await screen.findByRole("menuitemradio", { name: /demo-model-b/ });
  return trigger;
}

describe("compact document model selector", () => {
  beforeEach(() => {
    vi.mocked(listProviders).mockResolvedValue(providers);
    vi.mocked(discoverProviderModels).mockImplementation(async (id) => ({
      provider_id: id, status: "ok", models: documentPreviewModels(providers.find((provider) => provider.id === id)!),
    }));
    vi.mocked(listSessions).mockResolvedValue([]);
    vi.mocked(listSessionMessages).mockResolvedValue([]);
  });

  it("searches across providers and forwards the selected model and effort when sending", async () => {
    const send = renderPanel();
    const trigger = await openMenu();
    const search = screen.getByRole("textbox", { name: "搜索模型" });
    await userEvent.type(search, "nonexistent-model");
    expect(screen.queryByRole("menuitemradio")).not.toBeInTheDocument();
    await userEvent.clear(search);
    await userEvent.type(search, "demo-model-b");
    await userEvent.click(screen.getByRole("menuitemradio", { name: /demo-model-b/ }));
    await waitFor(() => expect(trigger).toHaveAttribute("title", expect.stringContaining("demo-model-b")));
    await userEvent.click(trigger);
    const slider = screen.getByRole("slider");
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({ left: 0, width: 400 } as DOMRect);
    fireEvent(slider, new MouseEvent("pointerdown", { bubbles: true, clientX: 300 }));
    fireEvent.pointerUp(slider);
    expect(slider).toHaveAttribute("aria-valuenow", "3");
    await userEvent.keyboard("{Escape}");
    expect(trigger).toHaveTextContent("高");
    await userEvent.type(screen.getByRole("textbox", { name: "询问当前文档" }), "请解释这张图{Enter}");
    expect(send).toHaveBeenCalledWith("请解释这张图", {
      providerId: providers[1].id, modelId: "demo-model-b", responseMode: "agentic", thinkingMode: "high",
    });
  });

  it("remembers model changes for an existing session and supports fast mode", async () => {
    const send = renderPanel("existing-document-session");
    const trigger = await openMenu();
    await userEvent.click(screen.getByRole("menuitemradio", { name: /demo-model-b/ }));
    expect(getSessionComposerPrefs("existing-document-session").modelId).toBe("demo-model-b");
    await userEvent.click(trigger);
    const slider = screen.getByRole("slider");
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({ left: 0, width: 400 } as DOMRect);
    fireEvent(slider, new MouseEvent("pointerdown", { bubbles: true, clientX: 0 }));
    fireEvent.pointerUp(slider);
    await userEvent.keyboard("{Escape}");
    expect(trigger).toHaveTextContent("快");
    expect(getSessionComposerPrefs("existing-document-session")).toMatchObject({ responseMode: "fast", thinkingMode: "off" });
    await userEvent.type(screen.getByRole("textbox", { name: "询问当前文档" }), "解释{Enter}");
    expect(send).toHaveBeenCalledWith("解释", expect.objectContaining({ thinkingMode: "off", responseMode: "fast" }));
  });

  it("disables the selector when no model provider is available", async () => {
    vi.mocked(listProviders).mockResolvedValue([]);
    renderPanel();
    await waitFor(() => expect(listProviders).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "选择响应模式、思考力度和模型" })).toBeDisabled();
  });
});

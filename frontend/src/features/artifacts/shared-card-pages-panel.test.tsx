/**
 * 「已分享页面」批量勾选行为测试。
 *
 * 覆盖：全选/单选计数、批量按钮的可用性门槛（有效链接不参与删除、已撤销链接
 * 不参与撤销、未留存令牌不参与复制）、确认弹窗文案，以及真正下发的批量请求
 * 只包含符合条件的那部分 id。
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  batchArtifactCardShareTokens,
  cardShareUrl,
  deleteArtifactCardShareTokenRecord,
  listAllArtifactCardShares,
  revealArtifactCardShareToken,
  revokeArtifactCardShareToken,
} from "@/api/artifacts";
import { renderWithProviders } from "@/test/render";
import type {
  ArtifactCardShareBatchResult,
  ArtifactCardShareManagement,
} from "@/types/artifacts";

import { SharedCardPagesPanel } from "./artifacts-page";

vi.mock("@/api/artifacts", () => ({
  batchArtifactCardShareTokens: vi.fn(),
  cardShareUrl: (rawToken: string) => `/api/v1/card-share/${encodeURIComponent(rawToken)}`,
  deleteArtifactCardShareTokenRecord: vi.fn(),
  listAllArtifactCardShares: vi.fn(),
  revealArtifactCardShareToken: vi.fn(),
  revokeArtifactCardShareToken: vi.fn(),
}));

const listShares = vi.mocked(listAllArtifactCardShares);
const batchShares = vi.mocked(batchArtifactCardShareTokens);
const revealShare = vi.mocked(revealArtifactCardShareToken);
const purgeShare = vi.mocked(deleteArtifactCardShareTokenRecord);
const revokeShare = vi.mocked(revokeArtifactCardShareToken);

function share(
  overrides: Partial<ArtifactCardShareManagement> & { id: string },
): ArtifactCardShareManagement {
  return {
    artifact_card_version_id: `version-${overrides.id}`,
    token_prefix: `prefix-${overrides.id}`,
    label: "",
    expires_at: null,
    max_views: null,
    view_count: 0,
    revoked_at: null,
    created_at: "2026-09-13T00:00:00Z",
    card_id: "card-1",
    card_title: `卡片 ${overrides.id}`,
    card_version: 1,
    card_type: "magic_card",
    share_token_available: true,
    ...overrides,
  };
}

function batchResult(
  action: "revoke" | "purge",
  affected: number,
  skipped = 0,
): ArtifactCardShareBatchResult {
  return {
    action,
    requested_count: affected + skipped,
    affected_count: affected,
    skipped: Array.from({ length: skipped }, (_, index) => ({
      id: `skipped-${index}`,
      reason: action === "purge" ? ("still_active" as const) : ("already_revoked" as const),
    })),
  };
}

const ROWS = [
  share({ id: "active" }),
  share({ id: "revoked", revoked_at: "2026-09-12T00:00:00Z" }),
  share({ id: "legacy", share_token_available: false }),
];

const writeText = vi.fn<(text: string) => Promise<void>>();

/** 计数行由多个文本节点拼成（共 <strong>3</strong> 条分享），按元素文本整体断言。 */
function headerNode(): HTMLElement {
  return screen.getByText(
    (_content, element) =>
      element?.tagName === "P" && (element.textContent ?? "").trim().startsWith("共 "),
  );
}

/** 等首屏加载完成（列表渲染前计数行不存在，避免断言到占位状态）。 */
async function waitForCount(expected: string): Promise<HTMLElement> {
  await waitFor(() => expect(headerNode().textContent).toContain(expected));
  return headerNode();
}

/**
 * userEvent.setup() 会装上自己的 navigator.clipboard，必须在 setup 之后再覆盖，
 * 否则断言到的是 user-event 的实现，而不是面板真正写出去的内容。
 */
function stubClipboard(): void {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
}

beforeEach(() => {
  listShares.mockResolvedValue(ROWS);
  writeText.mockResolvedValue(undefined);
});

describe("SharedCardPagesPanel 批量操作", () => {
  it("默认不显示批量操作栏，单选已撤销记录时只放行可用的动作", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SharedCardPagesPanel workspaceId="ws-1" />);

    await waitForCount("共 3 条分享");
    expect(screen.queryByRole("button", { name: "批量撤销分享" })).toBeNull();

    await user.click(screen.getByRole("checkbox", { name: "选择 卡片 revoked 的分享" }));

    expect((await waitForCount("已选 1 项")).textContent).toContain("共 3 条分享");
    // 已撤销的行不能再撤销，但可以删除记录，也可以复制链接。
    expect(screen.getByRole("button", { name: "批量撤销分享" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "批量删除记录" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批量复制链接" })).toBeEnabled();
  });

  it("全选后按行状态分别统计可撤销/可删除/可复制范围", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SharedCardPagesPanel workspaceId="ws-1" />);

    await waitForCount("共 3 条分享");
    await user.click(screen.getByRole("checkbox", { name: "全选已分享页面" }));

    expect((await waitForCount("已选 3 项")).textContent).toContain("共 3 条分享");
    // revoked 行已撤销 → 批量撤销仍可用（active/legacy 未撤销）；legacy 行无令牌但 revoked 行可复制。
    expect(screen.getByRole("button", { name: "批量撤销分享" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批量删除记录" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批量复制链接" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "取消选择" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "批量撤销分享" })).toBeNull());
  });

  it("批量删除只下发失效记录，并在确认弹窗里说明会被跳过的条数", async () => {
    const user = userEvent.setup();
    batchShares.mockResolvedValue(batchResult("purge", 1, 2));
    renderWithProviders(<SharedCardPagesPanel workspaceId="ws-1" />);

    await waitForCount("共 3 条分享");
    await user.click(screen.getByRole("checkbox", { name: "全选已分享页面" }));
    await user.click(screen.getByRole("button", { name: "批量删除记录" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("删除选中的 1 条分享记录？")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/选中项里另有 2 条仍有效或已不存在，不会被删除。/),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(batchShares).toHaveBeenCalledTimes(1));
    expect(batchShares).toHaveBeenCalledWith(["revoked"], "purge");
    // 选择集在批量操作后清空，避免残留幽灵选中项。
    await waitFor(() => expect(screen.queryByRole("button", { name: "批量删除记录" })).toBeNull());
    expect(headerNode().textContent).not.toContain("已选");
  });

  it("批量撤销只下发未撤销的链接", async () => {
    const user = userEvent.setup();
    batchShares.mockResolvedValue(batchResult("revoke", 2, 1));
    renderWithProviders(<SharedCardPagesPanel workspaceId="ws-1" />);

    await waitForCount("共 3 条分享");
    await user.click(screen.getByRole("checkbox", { name: "全选已分享页面" }));
    await user.click(screen.getByRole("button", { name: "批量撤销分享" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("撤销选中的 2 条分享链接？")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "确认撤销" }));

    await waitFor(() => expect(batchShares).toHaveBeenCalledTimes(1));
    expect(batchShares).toHaveBeenCalledWith(["active", "legacy"], "revoke");
  });

  it("批量复制只 reveal 留存了令牌的行，并以换行合并写入剪贴板", async () => {
    const user = userEvent.setup();
    stubClipboard();
    revealShare.mockImplementation(async (tokenId: string) => ({ token: `raw-${tokenId}` }));
    renderWithProviders(<SharedCardPagesPanel workspaceId="ws-1" />);

    await waitForCount("共 3 条分享");
    await user.click(screen.getByRole("checkbox", { name: "全选已分享页面" }));
    await user.click(screen.getByRole("button", { name: "批量复制链接" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(revealShare.mock.calls.map(([id]) => id)).toEqual(["active", "revoked"]);
    expect(writeText).toHaveBeenCalledWith(
      [
        `${window.location.origin}${cardShareUrl("raw-active")}`,
        `${window.location.origin}${cardShareUrl("raw-revoked")}`,
      ].join("\n"),
    );
  });

  it("有效链接的行内删除记录禁用，撤销后单个操作仍可用", async () => {
    revokeShare.mockResolvedValue(ROWS[0]);
    purgeShare.mockResolvedValue(ROWS[1]);
    renderWithProviders(<SharedCardPagesPanel workspaceId="ws-1" />);

    await waitForCount("共 3 条分享");

    const activeRow = screen.getByText("卡片 active").closest("div.flex.flex-col");
    expect(
      within(activeRow as HTMLElement).getByRole("button", { name: "删除记录" }),
    ).toBeDisabled();
    expect(
      within(activeRow as HTMLElement).getByRole("button", { name: "撤销分享" }),
    ).toBeEnabled();

    const revokedRow = screen.getByText("卡片 revoked").closest("div.flex.flex-col");
    expect(
      within(revokedRow as HTMLElement).getByRole("button", { name: "删除记录" }),
    ).toBeEnabled();
  });
});

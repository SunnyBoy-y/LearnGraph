import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installSkillFromMarket, listSkillMarket } from "@/api";
import { getSkillMarketDetail, translateSkillMarket } from "@/api/extensions";
import type { SkillMarketDetail, SkillMarketList } from "@/types/extensions";
import { MarketPanel } from "./skills-hub-extras";

vi.mock("@/api", () => ({ installSkillFromMarket: vi.fn(), listSkillMarket: vi.fn() }));
vi.mock("@/api/extensions", () => ({ getSkillMarketDetail: vi.fn(), translateSkillMarket: vi.fn() }));
vi.mock("@/components/shared/feature-model-select", async (original) => ({
  ...await original<typeof import("@/components/shared/feature-model-select")>(),
  useFeatureModelChoices: () => ({ choices: [{ value: "provider-one::model-one", label: "Test · model-one" }], providers: [] }),
}));
vi.mock("@/components/ai-elements/lazy-streamdown", () => ({ LazyStreamdown: ({ children }: { children: string }) => <div>{children}</div> }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const result: SkillMarketList = {
  source: "clawhub", refreshed_at: null, page: 1, page_size: 12,
  total: 1, total_pages: 0, query: "", next_cursor: "next-page",
  cards: [{ market_id: "clawhub:alice/example", slug: "example", name: "Example Skill",
    source: "clawhub/alice", description: "An example", install_url: "https://clawhub.ai/alice/skills/example",
    homepage_url: "https://clawhub.ai/alice/skills/example", installs: 42, source_type: "clawhub",
    origin_hash: "", fetch_status: "available", fetch_error: null, fetched_at: null,
    rank: 0, file_count: 0, has_scripts: false, version: "1.2.3", installable: true }],
};
const detail: SkillMarketDetail = {
  market_id: "clawhub:alice/example", name: "Example Skill", owner: "alice", version: "1.2.3",
  description: "Complete summary", homepage_url: "https://clawhub.ai/alice/skills/example",
  instructions_markdown: "# Full instructions\n" + "Complete explanation. ".repeat(500) + "END OF FULL DOCUMENT",
  skill_md: "---\nname: example\n---\n# Full instructions", instructions_error: null, files_error: null,
  files: [{ path: "SKILL.md", size_bytes: 1200, sha256: "a".repeat(64), content_type: "text/markdown" },
    { path: "references/complete-guide.md", size_bytes: 2048, sha256: "", content_type: "text/markdown" }],
  changelog: "Version changes", installable: true,
};
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MarketPanel /></QueryClientProvider>);
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listSkillMarket).mockResolvedValue(structuredClone(result));
  vi.mocked(installSkillFromMarket).mockResolvedValue({ name: "Example Skill" } as Awaited<ReturnType<typeof installSkillFromMarket>>);
  vi.mocked(getSkillMarketDetail).mockResolvedValue(structuredClone(detail));
});
afterEach(cleanup);

it("defaults translation to Chinese, forwards selected model, and can return to the source", async () => {
  vi.mocked(translateSkillMarket).mockResolvedValue({ market_id: detail.market_id, version: detail.version, target_locale: "zh-cn", translator_model_id: "model-one", cached: false, description: "完整摘要", instructions_markdown: "完整中文说明与结尾", changelog: "更新说明译文" });
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  expect(await screen.findByRole("combobox", { name: "翻译为" })).toHaveValue("zh-CN");
  expect(translateSkillMarket).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "翻译模型" }));
  await userEvent.click(await screen.findByRole("option", { name: "Test · model-one" }));
  fireEvent.click(screen.getByRole("button", { name: "模型翻译", exact: true }));
  expect(await screen.findByText("完整摘要")).toBeInTheDocument();
  expect(screen.getByText("完整中文说明与结尾")).toBeInTheDocument();
  expect(translateSkillMarket).toHaveBeenCalledWith({ market_id: detail.market_id, version: detail.version, target_locale: "zh-CN", provider_id: "provider-one", model_id: "model-one" });
  fireEvent.click(screen.getByRole("button", { name: "查看原文" }));
  expect(await screen.findByText("Complete summary")).toBeInTheDocument();
  expect(screen.getByRole("dialog").textContent).toContain("END OF FULL DOCUMENT");
  fireEvent.click(screen.getByRole("button", { name: "查看译文" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "翻译为" }), "ja");
  expect(screen.queryByText("完整摘要")).toBeNull();
  expect(screen.getByText("Complete summary")).toBeInTheDocument();
});

it("keeps source available after translation failure and supports a custom locale", async () => {
  vi.mocked(translateSkillMarket).mockRejectedValue(new Error("模型暂不可用"));
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: "翻译为" }), "custom");
  expect(screen.getByRole("button", { name: "模型翻译", exact: true })).toBeDisabled();
  await userEvent.type(screen.getByRole("textbox", { name: "自定义目标语言代码" }), "it");
  fireEvent.click(screen.getByRole("button", { name: "模型翻译", exact: true }));
  expect(await screen.findByRole("alert")).toHaveTextContent("模型暂不可用");
  expect(screen.getByText("Complete summary")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试翻译" })).toBeEnabled();
  expect(translateSkillMarket).toHaveBeenCalledWith(expect.objectContaining({ target_locale: "it" }));
});

it("does not show a late translation after the user switches language", async () => {
  let finish!: (value: Awaited<ReturnType<typeof translateSkillMarket>>) => void;
  vi.mocked(translateSkillMarket).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "模型翻译", exact: true }));
  await waitFor(() => expect(translateSkillMarket).toHaveBeenCalledTimes(1));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "翻译为" }), "ja");
  finish({ market_id: detail.market_id, version: detail.version, target_locale: "zh-cn", translator_model_id: "model-one", cached: false, description: "迟到的中文摘要", instructions_markdown: "迟到的中文", changelog: "更新说明" });
  await waitFor(() => expect(screen.getByText("Complete summary")).toBeInTheDocument());
  expect(screen.queryByText("迟到的中文摘要")).toBeNull();
});

it("enables remote installation without cached files and submits the displayed version", async () => {
  mount();
  expect(await screen.findByText("ClawHub 市场")).toBeInTheDocument();
  const button = await screen.findByRole("button", { name: "安装" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(installSkillFromMarket).toHaveBeenCalledWith({ market_id: "clawhub:alice/example", version: "1.2.3" }));
  expect(screen.queryByText("外部目录发现")).toBeNull();
});

it("follows the returned cursor and can return to the previous page", async () => {
  mount();
  await screen.findByText("Example Skill");
  fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
  await waitFor(() => expect(listSkillMarket).toHaveBeenCalledWith(expect.objectContaining({ cursor: "next-page", page: 2 })));
  fireEvent.click(await screen.findByRole("button", { name: "上一页" }));
  await screen.findByText("第 1 页");
});

it("searches ClawHub and clears the browse cursor", async () => {
  mount();
  await screen.findByText("Example Skill");
  fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
  await waitFor(() => expect(listSkillMarket).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })));
  fireEvent.change(screen.getByRole("textbox", { name: "搜索 ClawHub Skill" }), { target: { value: "calendar" } });
  await waitFor(() => expect(listSkillMarket).toHaveBeenCalledWith(expect.objectContaining({ q: "calendar", page: 1, cursor: undefined })));
});

it("shows upstream failure instead of stale seed cards", async () => {
  vi.mocked(listSkillMarket).mockRejectedValue(new Error("ClawHub 暂时不可用"));
  mount();
  expect(await screen.findByText("ClawHub 暂时不可用")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "安装" })).toBeNull();
});

it("allows installation when unified search leaves the version to be resolved", async () => {
  const search = structuredClone(result);
  search.cards[0].version = "";
  vi.mocked(listSkillMarket).mockResolvedValue(search);
  mount();
  expect(await screen.findByText("安装时获取版本")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "安装" }));
  await waitFor(() => expect(installSkillFromMarket).toHaveBeenCalledWith({ market_id: "clawhub:alice/example", version: undefined }));
});

it("opens complete instructions and version files without installing", async () => {
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  await screen.findByText("Complete summary");
  expect(screen.getByRole("dialog").textContent).toContain("END OF FULL DOCUMENT");
  expect(getSkillMarketDetail).toHaveBeenCalledWith("clawhub:alice/example", "1.2.3");
  expect(installSkillFromMarket).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("tab", { name: "文件列表 (2)" }));
  expect(await screen.findByText("references/complete-guide.md")).toBeInTheDocument();
  expect(screen.getByText("2.0 KB")).toBeInTheDocument();
});

it("installs the version resolved in details for a versionless search card", async () => {
  const card = structuredClone(result); card.cards[0].version = "";
  vi.mocked(listSkillMarket).mockResolvedValue(card);
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "安装此版本" }));
  await waitFor(() => expect(installSkillFromMarket).toHaveBeenCalledWith({ market_id: "clawhub:alice/example", version: "1.2.3" }));
});

it("preserves instructions when the file list fails and offers retry", async () => {
  vi.mocked(getSkillMarketDetail).mockResolvedValue({ ...detail, files: [], files_error: "文件列表暂不可用" });
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  await screen.findByText("Complete summary");
  await userEvent.click(screen.getByRole("tab", { name: "文件列表", exact: true }));
  expect(await screen.findByText("文件列表暂不可用")).toBeInTheDocument();
  expect(screen.queryByText(/共 0 个文件/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重试文件列表" }));
  await waitFor(() => expect(getSkillMarketDetail).toHaveBeenCalledTimes(2));
});

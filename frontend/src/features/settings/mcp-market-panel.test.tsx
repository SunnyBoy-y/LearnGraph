import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { browseMcpRegistry, getMcpRegistryDetail, translateMcpMarket } from "@/api/extensions";
import type { McpRegistryDetail, McpRegistrySearchResult } from "@/types/extensions";
import { McpMarketPanel } from "./mcp-market-panel";

vi.mock("@/api/extensions", () => ({ browseMcpRegistry: vi.fn(), getMcpRegistryDetail: vi.fn(), translateMcpMarket: vi.fn() }));
vi.mock("@/components/shared/feature-model-select", async (original) => ({
  ...await original<typeof import("@/components/shared/feature-model-select")>(),
  useFeatureModelChoices: () => ({ choices: [{ value: "provider-one::model-one", label: "Test · model-one" }], providers: [] }),
}));
const result: McpRegistrySearchResult = { registry_url: "https://registry.modelcontextprotocol.io", query: "", next_cursor: "page-2", items: [{
  name: "io.example/files", title: "Files MCP", description: "Read files", version: "1.2.3", status: "active",
  repository_url: "https://github.com/example/files", website_url: "", endpoint_url: null, transport: null,
  packages: ["npm:@example/files"], env_hints: ["ROOT"], supported: false, unsupported_reason: "仅提供本地安装包",
  catalog: "official", catalog_url: "https://registry.modelcontextprotocol.io/?q=io.example%2Ffiles",
}] };
const detail: McpRegistryDetail = { item: result.items[0], registry_url: result.registry_url, remotes: [],
  package_details: [{ registryType: "npm", identifier: "@example/files" }], tools: [],
  configuration: [{ name: "ROOT", isRequired: true, location: "环境变量" }], published_at: "", updated_at: "", raw: {} };
function mount(onSelect = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><McpMarketPanel onSelect={onSelect} /></QueryClientProvider>);
  return onSelect;
}
beforeEach(() => { vi.clearAllMocks(); vi.mocked(browseMcpRegistry).mockResolvedValue(structuredClone(result)); vi.mocked(getMcpRegistryDetail).mockResolvedValue(structuredClone(detail)); });
afterEach(cleanup);

it("defaults to Chinese and translates all descriptions with the selected model while preserving registration", async () => {
  const source = structuredClone(detail);
  source.item.supported = true; source.item.endpoint_url = "https://example.com/mcp";
  source.configuration[0].description = "Root directory";
  source.translation_source_hash = "a".repeat(64);
  source.tools = [{ name: "search_web", description: "Search public pages", inputSchema: { description: "ORIGINAL SCHEMA" } }];
  vi.mocked(getMcpRegistryDetail).mockResolvedValue(source);
  vi.mocked(translateMcpMarket).mockResolvedValue({ name: source.item.name, catalog: "official", version: source.item.version, source_hash: source.translation_source_hash, target_locale: "zh-cn", translator_model_id: "model-one", cached: false, description: "读取文件", configuration_descriptions: ["根目录"], tool_descriptions: ["搜索公开网页"] });
  const select = mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  expect(await screen.findByRole("combobox", { name: "翻译为" })).toHaveValue("zh-CN");
  expect(translateMcpMarket).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "翻译模型" }));
  await userEvent.click(await screen.findByRole("option", { name: "Test · model-one" }));
  fireEvent.click(screen.getByRole("button", { name: "模型翻译", exact: true }));
  expect(await screen.findByText("读取文件")).toBeInTheDocument();
  expect(screen.getByText("根目录")).toBeInTheDocument(); expect(screen.getByText("搜索公开网页")).toBeInTheDocument();
  expect(screen.getByText("ROOT")).toBeInTheDocument(); expect(screen.getByText("search_web")).toBeInTheDocument();
  expect(translateMcpMarket).toHaveBeenCalledWith(expect.objectContaining({ provider_id: "provider-one", model_id: "model-one", target_locale: "zh-CN", version: "1.2.3", source_hash: source.translation_source_hash }));
  fireEvent.click(screen.getByRole("button", { name: "查看原文" }));
  expect(within(screen.getByRole("dialog")).getByText("Read files")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看译文" }));
  fireEvent.click(screen.getByRole("button", { name: "填入注册表" }));
  expect(select).toHaveBeenCalledWith(source);
});

it("keeps source and allows retry when the model fails", async () => {
  vi.mocked(translateMcpMarket).mockRejectedValue(new Error("模型暂不可用"));
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "模型翻译", exact: true }));
  expect(await screen.findByRole("alert")).toHaveTextContent("模型暂不可用");
  expect(within(screen.getByRole("dialog")).getByText("Read files")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试翻译" })).toBeEnabled();
});

it("rejects a translation with mismatched source or description counts", async () => {
  vi.mocked(translateMcpMarket).mockResolvedValue({ name: detail.item.name, catalog: "official", version: detail.item.version, source_hash: "a".repeat(64), target_locale: "zh-cn", translator_model_id: "model-one", cached: false, description: "错误译文", configuration_descriptions: [], tool_descriptions: [] });
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "模型翻译", exact: true }));
  expect(await screen.findByRole("alert")).toHaveTextContent("目录说明已更新");
  expect(screen.queryByText("错误译文")).toBeNull();
});

it("opens full details for a local-only entry without registering it", async () => {
  const select = mount();
  fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  const dialog = await screen.findByRole("dialog", { name: "Files MCP" });
  expect(await within(dialog).findByText("@example/files", { exact: false })).toBeInTheDocument();
  expect(within(dialog).getByText("ROOT")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "填入注册表" })).toBeDisabled();
  expect(getMcpRegistryDetail).toHaveBeenCalledWith("io.example/files", "official", "1.2.3");
  expect(select).not.toHaveBeenCalled();
});

it("follows the cursor and resets it when switching markets", async () => {
  mount();
  await screen.findByText("Files MCP");
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() => expect(browseMcpRegistry).toHaveBeenCalledWith(expect.objectContaining({ cursor: "page-2", catalog: "official" })));
  fireEvent.click(screen.getByRole("button", { name: "Smithery", exact: true }));
  await waitFor(() => expect(browseMcpRegistry).toHaveBeenCalledWith(expect.objectContaining({ cursor: undefined, catalog: "smithery" })));
});

it("submits the search to the live catalog adapter", async () => {
  mount(); await screen.findByText("Files MCP");
  fireEvent.change(screen.getByRole("textbox", { name: "搜索 MCP 市场" }), { target: { value: "postgres" } });
  fireEvent.click(screen.getByRole("button", { name: "搜索", exact: true }));
  await waitFor(() => expect(browseMcpRegistry).toHaveBeenCalledWith(expect.objectContaining({ q: "postgres", cursor: undefined })));
});

it("shows declared tools and only prefills after an explicit click", async () => {
  const remote = structuredClone(detail);
  remote.item.supported = true; remote.item.endpoint_url = "https://example.com/mcp";
  remote.tools = [{ name: "search_web", description: "Search public pages", inputSchema: { type: "object" } }];
  vi.mocked(getMcpRegistryDetail).mockResolvedValue(remote);
  const select = mount();
  fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  expect(await screen.findByText("search_web")).toBeInTheDocument();
  expect(select).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "填入注册表" }));
  expect(select).toHaveBeenCalledWith(remote);
});

it("shows an error and allows retry when details fail", async () => {
  vi.mocked(getMcpRegistryDetail).mockRejectedValueOnce(new Error("市场详情暂不可用"));
  mount(); fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  expect(await screen.findByText("市场详情暂不可用")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试详情" }));
  expect(await screen.findByText("ROOT")).toBeInTheDocument();
});

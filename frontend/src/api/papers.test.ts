import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "./client";
import { getPaperBibtex, listPaperLibrary, readPaper, savePaper, searchPapers } from "./papers";

afterEach(() => vi.restoreAllMocks());

describe("scholarly API isolation", () => {
  it("uses independent authenticated workspace endpoints without general search", async () => {
    const post = vi.spyOn(apiClient, "post").mockResolvedValue({});
    const get = vi.spyOn(apiClient, "get").mockResolvedValue([]);
    await searchPapers({ query: "attention", provider: "openalex", year_from: 2023 });
    await readPaper({ provider: "arxiv", paper_id: "2401.12345v2", authorized_domains: ["arxiv.org"] });
    await savePaper({ provider: "arxiv", paper_id: "2401.12345v2", include_full_text: false });
    await listPaperLibrary();
    await getPaperBibtex("paper/1");
    expect(post.mock.calls.map((call) => call[0])).toEqual(["/papers/search", "/papers/read", "/papers/save"]);
    expect(get).toHaveBeenCalledWith("/papers/library");
    expect(get).toHaveBeenCalledWith("/papers/library/paper%2F1/bibtex");
  });
});

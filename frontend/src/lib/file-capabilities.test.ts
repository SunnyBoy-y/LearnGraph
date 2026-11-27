import { describe, expect, it } from "vitest";

import {
  fileQuestionSuggestions,
  fileViewerLabel,
  resolveFileCapabilities,
} from "./file-capabilities";

describe("file capabilities", () => {
  it("keeps document concepts away from images", () => {
    const capabilities = resolveFileCapabilities("image (44).png", "image/png", "attachment_only");

    expect(capabilities.surface).toBe("image");
    expect(capabilities.preview).toBe(true);
    expect(capabilities.imageTools).toBe(true);
    expect(capabilities.multimodalQA).toBe(true);
    expect(capabilities.outline).toBe(false);
    expect(capabilities.textSelection).toBe(false);
    expect(capabilities.citationLocation).toBe(false);
    expect(capabilities.versionHistory).toBe(false);
    expect(capabilities.indexedQA).toBe(false);
    expect(fileViewerLabel(capabilities)).toBe("图片");
    expect(fileQuestionSuggestions(capabilities)).toContain("解释图中内容");
  });

  it("gives pdf documents outline, citation and version history", () => {
    const capabilities = resolveFileCapabilities("paper.pdf", "application/pdf", "built_in");

    expect(capabilities.surface).toBe("document");
    expect(capabilities.outline).toBe(true);
    expect(capabilities.textSelection).toBe(true);
    expect(capabilities.citationLocation).toBe(true);
    expect(capabilities.versionHistory).toBe(true);
    expect(capabilities.indexedQA).toBe(true);
    expect(capabilities.multimodalQA).toBe(false);
    expect(capabilities.imageTools).toBe(false);
    expect(fileViewerLabel(capabilities)).toBe("原文");
  });

  it("drops document versioning for attachment-only documents", () => {
    const capabilities = resolveFileCapabilities("scan.docx", "", "attachment_only");

    expect(capabilities.surface).toBe("document");
    expect(capabilities.outline).toBe(true);
    expect(capabilities.indexedQA).toBe(false);
    expect(capabilities.versionHistory).toBe(false);
  });

  it("treats spreadsheets as data surfaces without an outline", () => {
    const capabilities = resolveFileCapabilities("metrics.csv", "text/csv", "built_in");

    expect(capabilities.surface).toBe("data");
    expect(capabilities.outline).toBe(false);
    expect(capabilities.textSelection).toBe(false);
    expect(capabilities.versionHistory).toBe(false);
    expect(fileViewerLabel(capabilities)).toBe("表格");
  });

  it("keeps audio and video on the media surface", () => {
    const capabilities = resolveFileCapabilities("lecture.mp3", "audio/mpeg", "attachment_only");

    expect(capabilities.surface).toBe("media");
    expect(capabilities.multimodalQA).toBe(true);
    expect(capabilities.outline).toBe(false);
    expect(fileViewerLabel(capabilities)).toBe("播放");
  });

  it("marks unsupported archives as an opaque file surface", () => {
    const capabilities = resolveFileCapabilities("bundle.zip", "application/zip", "attachment_only");

    expect(capabilities.surface).toBe("other");
    expect(capabilities.preview).toBe(false);
    expect(capabilities.outline).toBe(false);
    expect(fileViewerLabel(capabilities)).toBe("文件");
  });
});

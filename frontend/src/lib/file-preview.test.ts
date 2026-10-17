import { describe, expect, it } from "vitest";

import { requiresLearningIndex, resolveFilePreviewKind } from "./file-preview";

describe("file preview routing", () => {
  it.each(["main.java", "main.c", "main.cpp", "main.rs", "main.go"])("reads %s with an unknown MIME type", (filename) => {
    expect(resolveFilePreviewKind(filename, "application/octet-stream")).toBe("text");
    expect(resolveFilePreviewKind(filename)).toBe("text");
  });
  it("keeps XHTML in the isolated HTML viewer", () => {
    expect(resolveFilePreviewKind("page.xhtml", "application/xhtml+xml")).toBe("html");
  });
  it.each([["data.csv", "text/plain"], ["data.tsv", "application/octet-stream"]])("uses the workbook viewer for %s", (filename, mime) => {
    expect(resolveFilePreviewKind(filename, mime)).toBe("spreadsheet");
  });
  it("uses the RTF parser for RTF documents", () => {
    expect(resolveFilePreviewKind("notes.rtf", "application/rtf")).toBe("rtf");
  });
});


describe("requiresLearningIndex", () => {
  it.each([
    ["component.tsx", "text/plain", "local_text"],
    ["page.html", "text/html", "local_text"],
    ["photo.png", "image/png", "optional_processor"],
    ["lesson.mp3", "audio/mpeg", "attachment_only"],
    ["clip.mp4", "video/mp4", "attachment_only"],
    ["archive.zip", "application/zip", "attachment_only"],
  ])("does not require an index for %s", (filename, mimeType, capability) => {
    expect(requiresLearningIndex(filename, mimeType, capability)).toBe(false);
  });

  it.each([
    ["book.pdf", "application/pdf", "built_in_document"],
    ["notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "built_in_document"],
    ["slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "built_in_document"],
    ["table.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "built_in_document"],
    ["readme.md", "text/markdown", "local_text"],
  ])("keeps indexing available for %s", (filename, mimeType, capability) => {
    expect(requiresLearningIndex(filename, mimeType, capability)).toBe(true);
  });
});

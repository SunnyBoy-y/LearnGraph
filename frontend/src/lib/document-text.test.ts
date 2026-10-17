import { describe, expect, it } from "vitest";
import { readDocumentText } from "./document-text";

describe("document text decoding", () => {
  it("handles empty and UTF-8 files", async () => {
    expect(await readDocumentText(new Blob([]))).toBe("");
    expect(await readDocumentText(new Blob(["中文 UTF-8"])) ).toBe("中文 UTF-8");
  });
  it.each(["text/plain", "text/plain;charset=gbk"])("reads GBK Chinese with MIME %s", async (type) => {
    expect(await readDocumentText(new Blob([new Uint8Array([0xd6, 0xd0, 0xce, 0xc4])], { type }))).toBe("中文");
  });
  it("honors UTF-16 BOM before a conflicting charset", async () => {
    expect(await readDocumentText(new Blob([new Uint8Array([0xff, 0xfe, 0x2d, 0x4e, 0x87, 0x65])], { type: "text/plain;charset=utf-8" }))).toBe("中文");
  });
});

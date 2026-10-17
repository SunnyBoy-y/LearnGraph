import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { openEpub } from "./epub";

describe("chapter-based EPUB reading", () => {
  it.each(["book2.epub", "book3.epub"])("reads %s spine, titles and self-contained selected resources", async (name) => {
    const bytes = readFileSync(`src/test/fixtures/readers/${name}`);
    const read = vi.fn(async (begin: number, end: number) => new Uint8Array(bytes.subarray(begin, end)));
    const book = await openEpub({ size: bytes.length, read });
    try {
      expect(book.chapters.map((chapter) => chapter.title)).toEqual(["First chapter", "Second chapter"]);
      const first = await book.chapter(0);
      expect(first.html).toContain("EPUB chapter one");
      expect(first.html).toContain("data:image/svg+xml;base64,");
      expect(first.html).not.toContain("<script");
      expect(first.html).not.toContain("never.epub.invalid");
      expect(first.html).not.toContain("EPUB chapter two");
      first.dispose();
      const second = await book.chapter(1);
      expect(second.html).toContain("EPUB chapter two");
      expect(second.html).not.toContain("EPUB chapter one");
      second.dispose();
    } finally { await book.close(); }
  });

  it("reports a broken book instead of hanging", async () => {
    const bytes = new TextEncoder().encode("not an EPUB");
    await expect(openEpub({ size: bytes.length, read: async (begin, end) => bytes.slice(begin, end) })).rejects.toThrow();
  });
});

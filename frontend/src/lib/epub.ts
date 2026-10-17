import type { FileEntry } from "@zip.js/zip.js";
import type { SeekableFile } from "./seekable-file";

const ENTRY_LIMIT = 4 * 1024 * 1024;
const CHAPTER_RESOURCE_LIMIT = 16 * 1024 * 1024;
const ORIGIN = "https://epub.invalid/";

function resolvePath(base: string, href: string) {
  try {
    const url = new URL(href, ORIGIN + base);
    return url.origin === ORIGIN.slice(0, -1) ? decodeURIComponent(url.pathname.slice(1)) : undefined;
  } catch { return undefined; }
}

function xml(text: string) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new Error("EPUB 目录文件无效");
  return doc;
}

function elements(doc: Document, name: string) { return [...doc.getElementsByTagNameNS("*", name)]; }

async function readEntry(entry: FileEntry | undefined, limit = ENTRY_LIMIT): Promise<Uint8Array> {
  if (!entry) throw new Error("EPUB 中缺少所需文件");
  if (entry.encrypted) throw new Error("此 EPUB 已加密，无法直接阅读");
  if (entry.uncompressedSize > limit) throw new Error("EPUB 章节或资源超过读取限制");
  const chunks: Uint8Array[] = [];
  let size = 0;
  await entry.getData(new WritableStream<Uint8Array>({
    write(chunk) {
      size += chunk.length;
      if (size > limit) throw new Error("EPUB 解压内容超过读取限制");
      chunks.push(chunk);
    },
  }), { useWebWorkers: false });
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}

async function readText(entry: FileEntry | undefined, limit = ENTRY_LIMIT) {
  const bytes = await readEntry(entry, limit);
  const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? "utf-16le" : bytes[0] === 0xfe && bytes[1] === 0xff ? "utf-16be" : "utf-8";
  return new TextDecoder(encoding).decode(bytes);
}

export interface EpubChapter { path: string; title: string; }
export interface EpubBook {
  title: string;
  chapters: EpubChapter[];
  chapter: (index: number) => Promise<{ html: string; dispose: () => void }>;
  close: () => Promise<void>;
}

/** Read the ZIP directory and selected chapter, never unzip the whole book. */
export async function openEpub(source: SeekableFile): Promise<EpubBook> {
  const { Reader, ZipReader } = await import("@zip.js/zip.js");
  class SourceReader extends Reader<SeekableFile> {
    file: SeekableFile;
    constructor(file: SeekableFile) { super(file); this.file = file; this.size = file.size; }
    readUint8Array(index: number, length: number) { return this.file.read(index, Math.min(this.size, index + length)); }
  }
  const archive = new ZipReader(new SourceReader(source), { useWebWorkers: false });
  try {
    const files = new Map<string, FileEntry>();
    let count = 0;
    for await (const entry of archive.getEntriesGenerator()) {
      if (++count > 10000) throw new Error("EPUB 文件条目过多");
      if (!entry.directory && !entry.filename.startsWith("/") && !entry.filename.split(/[\\/]/).includes("..")) files.set(entry.filename, entry as FileEntry);
    }
    if (files.has("META-INF/encryption.xml")) {
      const encryption = xml(await readText(files.get("META-INF/encryption.xml"), 64 * 1024));
      if (elements(encryption, "EncryptionMethod").some((method) => (method.getAttribute("Algorithm") ?? "").includes("xmlenc"))) throw new Error("此 EPUB 使用了 DRM 加密，无法直接阅读");
    }
    const container = xml(await readText(files.get("META-INF/container.xml"), 64 * 1024));
    const packagePath = elements(container, "rootfile")[0]?.getAttribute("full-path");
    if (!packagePath) throw new Error("EPUB 缺少书籍目录");
    const opf = xml(await readText(files.get(packagePath), 1024 * 1024));
    const items = new Map(elements(opf, "item").map((item) => [item.getAttribute("id") ?? "", item]));
    const titles = new Map<string, string>();
    const nav = [...items.values()].find((item) => (item.getAttribute("properties") ?? "").split(/\s+/).includes("nav"));
    const ncx = [...items.values()].find((item) => item.getAttribute("media-type") === "application/x-dtbncx+xml");
    if (nav || ncx) {
      const entry = nav ?? ncx!;
      const navPath = resolvePath(packagePath, entry.getAttribute("href") ?? "");
      if (navPath && files.has(navPath)) {
        const navigation = xml(await readText(files.get(navPath), 1024 * 1024));
        if (nav) {
          for (const anchor of elements(navigation, "a")) {
            const path = resolvePath(navPath, anchor.getAttribute("href") ?? "");
            if (path && anchor.textContent?.trim()) titles.set(path, anchor.textContent.trim());
          }
        } else {
          for (const point of elements(navigation, "navPoint")) {
            const href = point.getElementsByTagNameNS("*", "content")[0]?.getAttribute("src") ?? "";
            const title = point.getElementsByTagNameNS("*", "text")[0]?.textContent?.trim();
            const path = resolvePath(navPath, href);
            if (path && title) titles.set(path, title);
          }
        }
      }
    }
    const chapters = elements(opf, "itemref").filter((item) => item.getAttribute("linear") !== "no").map((ref, index) => {
      const item = items.get(ref.getAttribute("idref") ?? "");
      const path = resolvePath(packagePath, item?.getAttribute("href") ?? "");
      if (!item || !path || !files.has(path)) throw new Error("EPUB 章节目录不完整");
      return { path, title: titles.get(path) ?? `第 ${index + 1} 章` };
    });
    if (!chapters.length) throw new Error("EPUB 没有可阅读章节");
    return {
      title: elements(opf, "title")[0]?.textContent?.trim() || "EPUB",
      chapters,
      close: () => archive.close(),
      async chapter(index) {
        const chapter = chapters[index];
        if (!chapter) throw new Error("EPUB 章节不存在");
        const resources = new Map<string, Promise<string>>();
        let total = 0;
        let resourceCount = 0;
        const budgetedEntry = async (path: string) => {
          if (++resourceCount > 64) throw new Error("EPUB 章节资源过多");
          const bytes = await readEntry(files.get(path), Math.min(ENTRY_LIMIT, CHAPTER_RESOURCE_LIMIT - total));
          total += bytes.length;
          return bytes;
        };
        const dispose = () => resources.clear();
        const resource = (base: string, href: string): Promise<string> => {
          if (/^(data:|#)/i.test(href)) return Promise.resolve(href);
          const path = resolvePath(base, href);
          if (!path || !files.has(path)) return Promise.resolve("");
          const cached = resources.get(path);
          if (cached) return cached;
          if (resources.size >= 64) throw new Error("EPUB 章节资源过多");
          const pending = (async () => {
            const bytes = await budgetedEntry(path);
            const extension = path.split(".").pop()?.toLowerCase();
            const type = ({ png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", svg: "image/svg+xml", webp: "image/webp", woff: "font/woff", woff2: "font/woff2", ttf: "font/ttf" } as Record<string, string>)[extension ?? ""] ?? "application/octet-stream";
            // Opaque-origin iframes cannot fetch parent-origin blob URLs.
            // Inline only the bounded resources of this chapter, preserving
            // script-disabled, same-origin-disabled book isolation.
            return new Promise<string>((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(reader.result as string);
              reader.onerror = () => reject(reader.error ?? new Error("EPUB 资源读取失败"));
              reader.readAsDataURL(new Blob([bytes as Uint8Array<ArrayBuffer>], { type }));
            });
          })();
          resources.set(path, pending);
          return pending;
        };
        const css = async (text: string, base: string) => {
          // Imported styles are optional; do not chase recursive CSS or external resources.
          const cleaned = text.replace(/@import\s+[^;]+;?/giu, "");
          const matches = [...cleaned.matchAll(/url\(\s*['"]?([^)'"]*)['"]?\s*\)/giu)];
          let output = cleaned;
          for (const match of matches) output = output.replace(match[0], `url("${await resource(base, match[1].trim())}")`);
          return output;
        };
        try {
          const doc = new DOMParser().parseFromString(await readText(files.get(chapter.path)), "text/html");
          doc.querySelectorAll("script,iframe,object,embed,form,base,meta[http-equiv]").forEach((node) => node.remove());
          for (const anchor of doc.querySelectorAll("a[href]")) {
            if (!anchor.getAttribute("href")?.startsWith("#")) anchor.removeAttribute("href");
          }
          for (const node of doc.querySelectorAll("[src],image[href],image[xlink\\:href]")) {
            for (const attribute of ["src", "href", "xlink:href"]) {
              const href = node.getAttribute(attribute);
              if (href) node.setAttribute(attribute, await resource(chapter.path, href));
            }
            node.removeAttribute("srcset");
          }
          for (const link of doc.querySelectorAll("link")) {
            const path = resolvePath(chapter.path, link.getAttribute("href") ?? "");
            if (link.getAttribute("rel") === "stylesheet" && path && files.has(path)) {
              const style = doc.createElement("style");
              style.textContent = await css(new TextDecoder().decode(await budgetedEntry(path)), path);
              link.replaceWith(style);
            } else link.remove();
          }
          for (const style of doc.querySelectorAll("style")) style.textContent = await css(style.textContent ?? "", chapter.path);
          for (const node of doc.querySelectorAll<HTMLElement>("[style]")) node.setAttribute("style", await css(node.getAttribute("style") ?? "", chapter.path));
          const style = doc.createElement("style");
          style.textContent = "body{max-width:48rem;margin:auto;padding:24px;font-size:18px;line-height:1.8}img,svg{max-width:100%;height:auto}";
          doc.head.append(style);
          return { html: doc.documentElement.outerHTML, dispose };
        } catch (error) {
          dispose();
          throw error;
        }
      },
    };
  } catch (error) {
    await archive.close();
    throw error;
  }
}

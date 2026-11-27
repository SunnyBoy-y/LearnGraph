import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router-dom";
import { getArtifactCardPreview } from "@/api/artifacts";
import type { ArtifactCard } from "@/types/artifacts";
import { extractPureSvgDataUrl, resolveArtifactGalleryKind } from "./card-types";

export function artifactDownload(card: ArtifactCard, snapshot: Record<string, unknown>) {
  const html = typeof snapshot.preview_html === "string" ? snapshot.preview_html : "";
  if (!html.trim()) return null;
  const svg = resolveArtifactGalleryKind(card, snapshot) === "svg" ? extractPureSvgDataUrl(html).svgMarkup : undefined;
  const name = card.title.replace(/[\\/:*?"<>|\x00-\x1f]+/g, "_").trim().slice(0, 100).replace(/^[.]+|[. ]+$/g, "") || "产物";
  return { name, extension: svg ? "svg" : "html", content: svg || html };
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2_000);
}

export async function processArtifactBatch<T, R>(items: T[], run: (item: T) => Promise<R>) {
  const results: PromiseSettledResult<R>[] = [];
  for (let i = 0; i < items.length; i += 5) {
    results.push(...await Promise.allSettled(items.slice(i, i + 5).map(run)));
  }
  return results;
}

export async function downloadArtifacts(cards: ArtifactCard[]) {
  const { ZipWriter, BlobWriter, TextReader } = await import("@zip.js/zip.js");
  const zip = new ZipWriter(new BlobWriter("application/zip"));
  const results = await processArtifactBatch(cards, async (card) => {
    const preview = await getArtifactCardPreview(card.card_id);
    const file = artifactDownload(card, preview.preview_snapshot ?? {});
    if (!file) throw new Error("没有可导出的 HTML 或 SVG");
    return file;
  });
  let count = 0;
  for (const result of results) {
    if (result.status !== "fulfilled") continue;
    const { name, extension, content } = result.value;
    await zip.add(`${++count}-${name}.${extension}`, new TextReader(content));
  }
  const blob = await zip.close();
  if (count) downloadBlob(blob, "LearnGraph-产物.zip");
  return { count, skipped: cards.length - count };
}

const galleryScroll = new Map<string, number>();

export function useGalleryScroll(ready: boolean, workspaceId: string, gallery: { current: HTMLElement | null }) {
  const { key } = useLocation();
  const navigation = useNavigationType();
  const entry = `${workspaceId}:${key}`;
  const handled = useRef<string | null>(null);
  const getScroller = () => {
    const main = gallery.current?.closest<HTMLElement>(".workspace-main");
    return main && main.scrollHeight > main.clientHeight ? main : window;
  };
  useLayoutEffect(() => {
    if (!ready || handled.current === entry) return;
    handled.current = entry;
    const saved = navigation === "POP" ? galleryScroll.get(entry) : undefined;
    if (saved != null) getScroller().scrollTo({ top: saved, behavior: "instant" });
  }, [entry, navigation, ready]);
  useEffect(() => {
    if (!ready) return;
    const main = gallery.current?.closest<HTMLElement>(".workspace-main");
    const save = () => {
      const scroller = getScroller();
      galleryScroll.set(entry, scroller instanceof HTMLElement ? scroller.scrollTop : window.scrollY);
      if (galleryScroll.size > 100) galleryScroll.delete(galleryScroll.keys().next().value!);
    };
    main?.addEventListener("scroll", save, { passive: true });
    window.addEventListener("scroll", save, { passive: true });
    save();
    return () => {
      main?.removeEventListener("scroll", save);
      window.removeEventListener("scroll", save);
    };
  }, [entry, ready]);
}

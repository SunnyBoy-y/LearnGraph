import { useEffect, useState, type ReactNode } from "react";
import {
  ChevronLeft,
  ChevronRight,
  FileQuestion,
  Minus,
  Plus,
} from "lucide-react";

import { AudioLearningPlayer } from "@/components/resources/audio-learning-player";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { resolveFilePreviewKind } from "@/lib/file-preview";
import {
  BlobImage,
  ExcelWorkbookViewer,
  HtmlDocumentViewer,
  PdfDocumentViewer,
  PowerPointViewer,
  TextDocumentViewer,
  WordDocumentViewer,
  type EmbeddedImageHandler,
  type PdfBackgroundTheme,
  type TextSelectionHandler,
} from "@/components/resources/document-previewers";


function VideoPreview({ blob, filename }: { blob: Blob; filename: string }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    const url = URL.createObjectURL(blob);
    setSource(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);
  if (!source) return null;
  return (
    <video
      className="max-h-[calc(100svh-12rem)] w-full bg-black"
      controls
      playsInline
      preload="metadata"
      src={source}
      title={filename}
    >
      当前浏览器无法播放此视频。
    </video>
  );
}

export interface FilePreviewCanvasProps {
  blob: Blob;
  filename: string;
  mimeType?: string;
  className?: string;
  onEmbeddedImage?: EmbeddedImageHandler;
  onTextSelection?: TextSelectionHandler;
  imageActions?: ReactNode;
  audioDetails?: ReactNode;
  pdfPage?: number;
  onPdfPageChange?: (page: number) => void;
}

export function FilePreviewCanvas({
  blob,
  filename,
  mimeType = blob.type,
  className,
  onEmbeddedImage,
  onTextSelection,
  imageActions,
  audioDetails,
  pdfPage,
  onPdfPageChange,
}: FilePreviewCanvasProps) {
  const kind = resolveFilePreviewKind(filename, mimeType);
  const [internalPageNumber, setInternalPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [scale, setScale] = useState(1);
  const [pdfTheme, setPdfTheme] = useState<PdfBackgroundTheme>("paper");
  const pageNumber = pdfPage ?? internalPageNumber;
  const setPageNumber = (next: number | ((current: number) => number)) => {
    const resolved = typeof next === "function" ? next(pageNumber) : next;
    if (pdfPage === undefined) setInternalPageNumber(resolved);
    onPdfPageChange?.(resolved);
  };

  return (
    <div className={cn("file-preview-canvas min-h-0 min-w-0", className)} data-preview-kind={kind}>
      {kind === "pdf" ? (
        <>
          <div className="file-preview-toolbar">
            <Button aria-label="上一页" disabled={pageNumber <= 1} onClick={() => setPageNumber((value) => value - 1)} size="icon-sm" variant="ghost"><ChevronLeft /></Button>
            <span className="min-w-20 text-center text-xs tabular-nums">{pageNumber} / {pageCount}</span>
            <Button aria-label="下一页" disabled={pageNumber >= pageCount} onClick={() => setPageNumber((value) => value + 1)} size="icon-sm" variant="ghost"><ChevronRight /></Button>
            <span className="mx-1 h-4 w-px bg-border" />
            <Button aria-label="缩小 PDF" onClick={() => setScale((value) => Math.max(.65, value - .15))} size="icon-sm" variant="ghost"><Minus /></Button>
            <span className="w-12 text-center text-[11px]">{Math.round(scale * 100)}%</span>
            <Button aria-label="放大 PDF" onClick={() => setScale((value) => Math.min(2.2, value + .15))} size="icon-sm" variant="ghost"><Plus /></Button>
            <span className="mx-1 h-4 w-px bg-border" />
            <div aria-label="PDF 阅读背景" className="flex items-center gap-1" role="group">
              {([
                ["paper", "纸白", "bg-white"],
                ["warm", "柔和", "bg-[#e9dfc8]"],
                ["night", "夜间", "bg-[#262a30]"],
              ] as const).map(([theme, label, color]) => (
                <button
                  aria-label={`切换为${label}背景`}
                  aria-pressed={pdfTheme === theme}
                  className={cn("size-5 rounded-full border-2 transition-transform hover:scale-110", color, pdfTheme === theme ? "border-foreground" : "border-border")}
                  key={theme}
                  onClick={() => setPdfTheme(theme)}
                  title={`${label}背景`}
                  type="button"
                />
              ))}
            </div>
          </div>
          <PdfDocumentViewer
            blob={blob}
            onDocument={(pages) => {
              setPageCount(pages);
              setPageNumber((page) => Math.min(page, pages));
            }}
            onTextSelection={onTextSelection}
            pageNumber={pageNumber}
            scale={scale}
            theme={pdfTheme}
          />
        </>
      ) : kind === "word" ? (
        <WordDocumentViewer blob={blob} onEmbeddedImage={onEmbeddedImage} onTextSelection={onTextSelection} />
      ) : kind === "powerpoint" ? (
        <PowerPointViewer blob={blob} onEmbeddedImage={onEmbeddedImage} onTextSelection={onTextSelection} />
      ) : kind === "spreadsheet" ? (
        <ExcelWorkbookViewer blob={blob} onTextSelection={onTextSelection} />
      ) : kind === "audio" ? (
        <div className="audio-document-workspace">
          <AudioLearningPlayer blob={blob} filename={filename} />
          {audioDetails}
        </div>
      ) : kind === "video" ? (
        <VideoPreview blob={blob} filename={filename} />
      ) : kind === "image" ? (
        <div className="grid min-h-[36rem] place-items-center gap-4 bg-muted/30 p-8">
          <BlobImage alt={filename} blob={blob} />
          {imageActions}
        </div>
      ) : kind === "html" ? (
        <HtmlDocumentViewer blob={blob} filename={filename} />
      ) : kind === "text" ? (
        <TextDocumentViewer blob={blob} filename={filename} />
      ) : (
        <div className="grid min-h-[36rem] place-items-center p-8 text-center text-sm text-muted-foreground">
          <div className="max-w-md">
            <FileQuestion className="mx-auto mb-3 size-7" />
            {kind === "legacy-office"
              ? "旧版 Office 格式需要隔离转换器。请另存为 .docx、.xlsx 或 .pptx 后重新上传。"
              : "当前格式没有安全的浏览器内预览器，请下载后使用本地应用查看。"}
          </div>
        </div>
      )}
    </div>
  );
}

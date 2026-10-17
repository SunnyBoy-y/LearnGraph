import { useEffect, useMemo, useState, type ReactNode } from "react";
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
import { filePreviewBlobKey, resolveFilePreviewKind } from "@/lib/file-preview";
import { blobFileSource, type SeekableFile } from "@/lib/seekable-file";
import { EpubViewer } from "./epub-viewer";
import { RangeTextViewer } from "./range-text-viewer";
import {
  BlobImage,
  ExcelWorkbookViewer,
  HtmlDocumentViewer,
  PdfDocumentViewer,
  PowerPointViewer,
  RtfDocumentViewer,
  TextDocumentViewer,
  WordDocumentViewer,
  type EmbeddedImageHandler,
  type PdfBackgroundTheme,
  type TextSelectionHandler,
} from "@/components/resources/document-previewers";


function VideoPreview({ blob, url, filename }: { blob?: Blob; url?: string; filename: string }) {
  const [source, setSource] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    setError(false);
    if (url) { setSource(url); return; }
    if (!blob) return;
    const objectUrl = URL.createObjectURL(blob);
    setSource(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob, url]);
  if (!source) return null;
  if (error) return <p className="p-6 text-sm text-destructive" role="alert">当前浏览器无法解码此视频，请下载后使用本地播放器查看。</p>;
  return (
    <video
      className="max-h-[calc(100svh-12rem)] w-full bg-black"
      controls
      onError={() => setError(true)}
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
  blob?: Blob;
  source?: SeekableFile;
  mediaUrl?: string;
  filename: string;
  mimeType?: string;
  className?: string;
  onEmbeddedImage?: EmbeddedImageHandler;
  onTextSelection?: TextSelectionHandler;
  imageActions?: ReactNode;
  /** 图片预览是否支持点击放大（复用共享灯箱）。 */
  imageZoom?: boolean;
  audioDetails?: ReactNode;
  pdfPage?: number;
  onPdfPageChange?: (page: number) => void;
}

export function FilePreviewCanvas(props: FilePreviewCanvasProps) {
  // A new document must not inherit page, password, error or zoom state.
  const documentKey = `${props.source ? filePreviewBlobKey(props.source) : props.blob ? filePreviewBlobKey(props.blob) : props.mediaUrl ?? "empty"}:${props.filename}:${props.mimeType ?? ""}`;
  return <FilePreviewDocument key={documentKey} {...props} />;
}

function FilePreviewDocument({
  blob,
  source,
  mediaUrl,
  filename,
  mimeType = blob?.type ?? "",
  className,
  onEmbeddedImage,
  onTextSelection,
  imageActions,
  imageZoom = false,
  audioDetails,
  pdfPage,
  onPdfPageChange,
}: FilePreviewCanvasProps) {
  const kind = resolveFilePreviewKind(filename, mimeType);
  const seekable = useMemo(() => source ?? (blob ? blobFileSource(blob) : undefined), [source, blob]);
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
            source={seekable}
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
      ) : kind === "epub" && seekable ? (
        <EpubViewer source={seekable} filename={filename} />
      ) : kind === "word" && blob ? (
        <WordDocumentViewer blob={blob} onEmbeddedImage={onEmbeddedImage} onTextSelection={onTextSelection} />
      ) : kind === "rtf" && blob ? (
        <RtfDocumentViewer blob={blob} filename={filename} />
      ) : kind === "powerpoint" && blob ? (
        <PowerPointViewer blob={blob} onEmbeddedImage={onEmbeddedImage} onTextSelection={onTextSelection} />
      ) : kind === "spreadsheet" && blob ? (
        <ExcelWorkbookViewer blob={blob} filename={filename} mimeType={mimeType} onTextSelection={onTextSelection} />
      ) : kind === "audio" ? (
        <div className="audio-document-workspace">
          <AudioLearningPlayer blob={blob} url={mediaUrl} filename={filename} />
          {audioDetails}
        </div>
      ) : kind === "video" ? (
        <VideoPreview blob={blob} url={mediaUrl} filename={filename} />
      ) : kind === "image" ? (
        <div className="grid min-h-[36rem] place-items-center gap-4 bg-muted/30 p-8">
          <BlobImage alt={filename} blob={blob} url={mediaUrl} zoomable={imageZoom} />
          {imageActions}
        </div>
      ) : kind === "html" && blob ? (
        <HtmlDocumentViewer blob={blob} filename={filename} />
      ) : kind === "text" && source ? (
        <RangeTextViewer source={source} />
      ) : kind === "text" && blob ? (
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

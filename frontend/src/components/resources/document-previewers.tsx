import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight, LoaderCircle } from "lucide-react";
import { renderAsync } from "docx-preview";
import {
  GlobalWorkerOptions,
  TextLayer,
  getDocument,
  type PDFDocumentProxy,
  type PDFPageProxy,
} from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Workbook, type Cell, type Worksheet } from "exceljs";

import { Button } from "@/components/ui/button";


GlobalWorkerOptions.workerSrc = pdfWorkerUrl;


export type DocumentLocatorHint = Record<string, unknown>;

export type TextSelectionHandler = (
  text: string,
  locatorHint?: DocumentLocatorHint,
  firstLineRect?: DOMRect,
) => void;

export type EmbeddedImageHandler = (image: { blob: Blob; filename: string; locator: DocumentLocatorHint }) => void;

export type PdfBackgroundTheme = "paper" | "warm" | "night";


export interface PdfDocumentViewerProps {
  blob: Blob;
  pageNumber: number;
  scale: number;
  theme: PdfBackgroundTheme;
  onDocument?: (pages: number) => void;
  onTextSelection?: TextSelectionHandler;
}


export interface WordDocumentViewerProps {
  blob: Blob;
  onEmbeddedImage?: EmbeddedImageHandler;
  onTextSelection?: TextSelectionHandler;
}

export interface PowerPointViewerProps {
  blob: Blob;
  onEmbeddedImage?: EmbeddedImageHandler;
  onTextSelection?: TextSelectionHandler;
}


export interface ExcelWorkbookViewerProps {
  blob: Blob;
  onTextSelection?: TextSelectionHandler;
  rowsPerPage?: number;
  columnsPerPage?: number;
}


function selectionWithin(container: HTMLElement) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return "";
  const range = selection.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return "";
  return selection.toString().trim();
}


function reportSelection(
  container: HTMLElement,
  onTextSelection?: TextSelectionHandler,
  locatorHint?: DocumentLocatorHint,
) {
  const text = selectionWithin(container);
  const selection = window.getSelection();
  const firstLineRect = selection?.rangeCount ? selection.getRangeAt(0).getClientRects()[0] : undefined;
  if (text) onTextSelection?.(text, locatorHint, firstLineRect);
}


function ViewerState({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return (
    <div
      className={
        error
          ? "grid min-h-[32rem] place-items-center p-8 text-center text-sm text-destructive"
          : "grid min-h-[32rem] place-items-center p-8 text-sm text-muted-foreground"
      }
      role={error ? "alert" : "status"}
    >
      <div className="flex max-w-md items-center gap-2">
        {!error ? <LoaderCircle className="size-4 animate-spin" /> : null}
        <span>{children}</span>
      </div>
    </div>
  );
}


export function PdfDocumentViewer({
  blob,
  pageNumber,
  scale,
  theme,
  onDocument,
  onTextSelection,
}: PdfDocumentViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const onDocumentRef = useRef(onDocument);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [loadError, setLoadError] = useState("");
  const [pageError, setPageError] = useState("");
  const [rendering, setRendering] = useState(false);

  useEffect(() => {
    onDocumentRef.current = onDocument;
  }, [onDocument]);

  useEffect(() => {
    let cancelled = false;
    let loadedDocument: PDFDocumentProxy | null = null;
    let loadingTask: ReturnType<typeof getDocument> | null = null;
    setPdf(null);
    setLoadError("");

    void blob
      .arrayBuffer()
      .then((data) => {
        if (cancelled) return null;
        loadingTask = getDocument({ data: new Uint8Array(data) });
        return loadingTask.promise;
      })
      .then((document) => {
        if (!document) return;
        if (cancelled) {
          void document.destroy();
          return;
        }
        loadedDocument = document;
        setPdf(document);
        onDocumentRef.current?.(document.numPages);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setLoadError(reason instanceof Error ? reason.message : "PDF 加载失败");
        }
      });

    return () => {
      cancelled = true;
      if (loadedDocument) {
        void loadedDocument.destroy();
      } else {
        void loadingTask?.destroy();
      }
    };
  }, [blob]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const pageElement = pageRef.current;
    const textContainer = textLayerRef.current;
    if (!pdf || !canvas || !pageElement || !textContainer) return;

    let cancelled = false;
    let page: PDFPageProxy | null = null;
    let renderTask: ReturnType<PDFPageProxy["render"]> | null = null;
    let textLayer: TextLayer | null = null;
    const effectivePage = Math.min(pdf.numPages, Math.max(1, pageNumber));
    setPageError("");
    setRendering(true);
    textContainer.replaceChildren();

    void pdf
      .getPage(effectivePage)
      .then(async (loadedPage) => {
        if (cancelled) return;
        page = loadedPage;
        const viewport = loadedPage.getViewport({ scale });
        const outputScale = window.devicePixelRatio || 1;
        const context = canvas.getContext("2d", { alpha: false });
        if (!context) throw new Error("浏览器无法创建 PDF Canvas");

        pageElement.style.width = `${viewport.width}px`;
        pageElement.style.height = `${viewport.height}px`;
        pageElement.style.setProperty("--total-scale-factor", String(viewport.scale));
        canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
        canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;

        renderTask = loadedPage.render({
          canvas,
          canvasContext: context,
          transform:
            outputScale === 1
              ? undefined
              : [outputScale, 0, 0, outputScale, 0, 0],
          viewport,
        });
        textLayer = new TextLayer({
          container: textContainer,
          textContentSource: loadedPage.streamTextContent({ includeMarkedContent: true }),
          viewport,
        });
        await Promise.all([renderTask.promise, textLayer.render()]);
      })
      .then(() => {
        if (!cancelled) setRendering(false);
      })
      .catch((reason: unknown) => {
        if (
          cancelled ||
          (reason instanceof Error &&
            ["AbortException", "RenderingCancelledException"].includes(reason.name))
        ) {
          return;
        }
        setRendering(false);
        setPageError(reason instanceof Error ? reason.message : "PDF 页面渲染失败");
      });

    return () => {
      cancelled = true;
      renderTask?.cancel();
      textLayer?.cancel();
      page?.cleanup();
    };
  }, [pageNumber, pdf, scale]);

  if (loadError) return <ViewerState error>{loadError}</ViewerState>;
  if (!pdf) return <ViewerState>正在读取 PDF 原始文件...</ViewerState>;

  return (
    <div className="document-pdf-surface" data-theme={theme}>
      <div
        aria-label={`PDF 第 ${Math.min(pdf.numPages, Math.max(1, pageNumber))} 页`}
        className="document-pdf-page"
        onMouseUp={(event) =>
          reportSelection(event.currentTarget, onTextSelection, {
            page: Math.min(pdf.numPages, Math.max(1, pageNumber)),
          })
        }
        ref={pageRef}
      >
        <canvas aria-hidden="true" ref={canvasRef} />
        <div className="document-pdf-text-layer" ref={textLayerRef} />
        {rendering ? (
          <div className="document-pdf-loading" role="status">
            <LoaderCircle className="size-4 animate-spin" />
            <span>正在渲染第 {Math.min(pdf.numPages, Math.max(1, pageNumber))} 页</span>
          </div>
        ) : null}
      </div>
      {pageError ? (
        <p className="document-viewer-error" role="alert">{pageError}</p>
      ) : null}
    </div>
  );
}


function sanitizeWordOutput(...roots: HTMLElement[]) {
  for (const root of roots) {
    root.querySelectorAll("script, iframe, object, embed").forEach((node) => node.remove());
    root.querySelectorAll<HTMLElement>("*").forEach((element) => {
      for (const attribute of Array.from(element.attributes)) {
        if (attribute.name.toLowerCase().startsWith("on")) {
          element.removeAttribute(attribute.name);
        }
      }
    });
    root.querySelectorAll<HTMLElement>("[src], [href], [xlink\\:href]").forEach((element) => {
      for (const attributeName of ["src", "href", "xlink:href"]) {
        const value = element.getAttribute(attributeName)?.trim();
        if (
          value &&
          !value.startsWith("#") &&
          !value.startsWith("blob:") &&
          !value.startsWith("data:")
        ) {
          element.removeAttribute(attributeName);
        }
      }
    });
    root.querySelectorAll("style").forEach((style) => {
      style.textContent = (style.textContent ?? "")
        .replace(/@import\s+[^;]+;?/giu, "")
        .replace(/url\(\s*(['"]?)https?:[^)]+\)/giu, "none");
    });
  }
}


export function WordDocumentViewer({ blob, onEmbeddedImage, onTextSelection }: WordDocumentViewerProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const stylesRef = useRef<HTMLDivElement>(null);
  const renderGenerationRef = useRef(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [missingFonts, setMissingFonts] = useState<string[]>([]);

  useEffect(() => {
    const generation = renderGenerationRef.current + 1;
    renderGenerationRef.current = generation;
    const detachedBody = document.createElement("div");
    const detachedStyles = document.createElement("div");
    setLoading(true);
    setError("");
    bodyRef.current?.replaceChildren();
    stylesRef.current?.replaceChildren();

    void renderAsync(blob, detachedBody, detachedStyles, {
      breakPages: true,
      debug: false,
      experimental: false,
      ignoreFonts: false,
      ignoreHeight: false,
      ignoreLastRenderedPageBreak: true,
      ignoreWidth: false,
      inWrapper: true,
      renderAltChunks: false,
      renderComments: true,
      renderEndnotes: true,
      renderFooters: true,
      renderFootnotes: true,
      renderHeaders: true,
      renderChanges: true,
      trimXmlDeclaration: true,
      useBase64URL: true,
    })
      .then(() => {
        if (renderGenerationRef.current !== generation) return;
        sanitizeWordOutput(detachedBody, detachedStyles);
        bodyRef.current?.replaceChildren(...Array.from(detachedBody.childNodes));
        stylesRef.current?.replaceChildren(...Array.from(detachedStyles.childNodes));
        const declaredFonts = new Set<string>();
        bodyRef.current?.querySelectorAll<HTMLElement>("*").forEach((element) => {
          const family = getComputedStyle(element).fontFamily;
          family.split(",").forEach((item) => {
            const normalized = item.trim().replace(/^['"]|['"]$/g, "");
            if (normalized && !["serif", "sans-serif", "monospace"].includes(normalized)) {
              declaredFonts.add(normalized);
            }
          });
        });
        setMissingFonts(
          [...declaredFonts].filter((family) => !document.fonts.check(`12px "${family}"`)).slice(0, 8),
        );
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (renderGenerationRef.current !== generation) return;
        setLoading(false);
        setError(reason instanceof Error ? reason.message : "Word 文件渲染失败");
      });

    return () => {
      if (renderGenerationRef.current === generation) {
        renderGenerationRef.current += 1;
      }
    };
  }, [blob]);

  function preventExternalNavigation(event: ReactMouseEvent<HTMLDivElement>) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const anchor = target.closest("a");
    const href = anchor?.getAttribute("href") ?? "";
    if (anchor && href && !href.startsWith("#")) {
      event.preventDefault();
      event.stopPropagation();
    }
    const image = target.closest<HTMLImageElement>("img");
    const source = image?.getAttribute("src") ?? image?.getAttribute("href");
    if (image && source && onEmbeddedImage) {
      event.preventDefault();
      const index = [...event.currentTarget.querySelectorAll("img")].indexOf(image);
      void fetch(source)
        .then((response) => response.blob())
        .then((imageBlob) => onEmbeddedImage({
          blob: imageBlob,
          filename: image.getAttribute("alt")?.trim() || "word-image.png",
          locator: { kind: "docx_image", index },
        }));
    }
  }

  return (
    <div className="document-word-viewer">
      {missingFonts.length ? (
        <div className="document-font-notice" role="status">
          源文件字体优先渲染；当前设备缺少 {missingFonts.join("、")}，浏览器已使用兼容字体替代。
        </div>
      ) : null}
      <div aria-hidden="true" className="document-word-styles" ref={stylesRef} />
      <div
        className="document-word-preview"
        onClickCapture={preventExternalNavigation}
        onMouseUp={(event) => reportSelection(event.currentTarget, onTextSelection)}
        ref={bodyRef}
      />
      {loading ? <ViewerState>正在本地渲染 Word 原始文件...</ViewerState> : null}
      {error ? <ViewerState error>{error}</ViewerState> : null}
    </div>
  );
}


export function PowerPointViewer({ blob, onEmbeddedImage, onTextSelection }: PowerPointViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let destroy: (() => void) | undefined;
    const container = containerRef.current;
    if (!container) return;
    container.replaceChildren();
    setLoading(true);
    setError("");
    void Promise.all([import("pptx-preview"), blob.arrayBuffer()])
      .then(async ([module, data]) => {
        if (cancelled) return;
        const previewer = module.init(container, { width: 960, height: 540, mode: "list" });
        destroy = () => previewer.destroy();
        await previewer.preview(data);
        if (!cancelled) setLoading(false);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setLoading(false);
          setError(reason instanceof Error ? reason.message : "PowerPoint 文件渲染失败");
        }
      });
    return () => {
      cancelled = true;
      destroy?.();
      container.replaceChildren();
    };
  }, [blob]);

  function handleClick(event: ReactMouseEvent<HTMLDivElement>) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const image = target.closest("img");
    const source = image?.getAttribute("src");
    if (!image || !source || !onEmbeddedImage) return;
    event.preventDefault();
    const index = [...event.currentTarget.querySelectorAll("img, image")].indexOf(image);
    void fetch(source)
      .then((response) => response.blob())
      .then((imageBlob) => onEmbeddedImage({
        blob: imageBlob,
        filename: image.getAttribute("alt")?.trim() || "slide-image.png",
        locator: { kind: "pptx_image", index },
      }));
  }

  return (
    <div className="document-ppt-viewer">
      <div
        className="document-ppt-preview"
        onClickCapture={handleClick}
        onMouseUp={(event) => reportSelection(event.currentTarget, onTextSelection)}
        ref={containerRef}
      />
      {loading ? <ViewerState>正在本地渲染 PowerPoint 原始文件...</ViewerState> : null}
      {error ? <ViewerState error>{error}</ViewerState> : null}
    </div>
  );
}


interface SelectedCell {
  address: string;
  display: string;
  formula?: string;
}


interface RenderedMerge {
  sourceAddress: string;
  rowSpan: number;
  colSpan: number;
}


interface SheetBounds {
  top: number;
  left: number;
  bottom: number;
  right: number;
}


function columnName(column: number) {
  let current = Math.max(1, Math.floor(column));
  let result = "";
  while (current > 0) {
    const remainder = (current - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    current = Math.floor((current - 1) / 26);
  }
  return result;
}


function decodeCellAddress(address: string) {
  const match = address.replaceAll("$", "").match(/^([A-Z]+)(\d+)$/iu);
  if (!match) return null;
  let column = 0;
  for (const character of match[1].toUpperCase()) {
    column = column * 26 + character.charCodeAt(0) - 64;
  }
  return { row: Number(match[2]), column };
}


function decodeMergeRange(value: string) {
  const [startValue, endValue = startValue] = value.split(":");
  const start = decodeCellAddress(startValue);
  const end = decodeCellAddress(endValue);
  if (!start || !end) return null;
  return {
    top: Math.min(start.row, end.row),
    left: Math.min(start.column, end.column),
    bottom: Math.max(start.row, end.row),
    right: Math.max(start.column, end.column),
  } satisfies SheetBounds;
}


function displayExcelValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (value instanceof Date) return value.toLocaleString();
  if (["string", "number", "boolean", "bigint"].includes(typeof value)) {
    return String(value);
  }
  if (typeof value !== "object") return "";

  const record = value as Record<string, unknown>;
  if ("result" in record) return displayExcelValue(record.result);
  if (Array.isArray(record.richText)) {
    return record.richText
      .map((part) =>
        typeof part === "object" && part !== null && "text" in part
          ? String(part.text)
          : "",
      )
      .join("");
  }
  if (typeof record.text === "string") return record.text;
  if (typeof record.error === "string") return record.error;
  return "";
}


function formattedCellValue(cell?: Cell) {
  if (!cell) return "";
  const value = displayExcelValue(cell.value);
  return value || cell.text || "";
}


function sheetBounds(worksheet?: Worksheet): SheetBounds {
  const dimensions = worksheet?.dimensions;
  return {
    top: dimensions?.top ?? 1,
    left: dimensions?.left ?? 1,
    bottom: dimensions?.bottom ?? 1,
    right: dimensions?.right ?? 1,
  };
}


function existingCell(worksheet: Worksheet, row: number, column: number) {
  return worksheet.findRow(row)?.findCell(column);
}


export function ExcelWorkbookViewer({
  blob,
  onTextSelection,
  rowsPerPage = 80,
  columnsPerPage = 32,
}: ExcelWorkbookViewerProps) {
  const [workbook, setWorkbook] = useState<Workbook | null>(null);
  const [activeSheet, setActiveSheet] = useState("");
  const [rowPage, setRowPage] = useState(0);
  const [columnPage, setColumnPage] = useState(0);
  const [selectedCell, setSelectedCell] = useState<SelectedCell | null>(null);
  const [error, setError] = useState("");

  const effectiveRowsPerPage = Math.min(200, Math.max(10, Math.floor(rowsPerPage)));
  const effectiveColumnsPerPage = Math.min(64, Math.max(8, Math.floor(columnsPerPage)));

  useEffect(() => {
    let cancelled = false;
    setWorkbook(null);
    setError("");

    void blob
      .arrayBuffer()
      .then(async (data) => {
        const signature = new Uint8Array(data, 0, Math.min(4, data.byteLength));
        if (
          signature.length < 4 ||
          signature[0] !== 0x50 ||
          signature[1] !== 0x4b ||
          signature[2] !== 0x03 ||
          signature[3] !== 0x04
        ) {
          throw new Error("仅支持读取原始 .xlsx 文件；旧版 .xls 需要在隔离转换服务中处理");
        }
        const parsedWorkbook = new Workbook();
        await parsedWorkbook.xlsx.load(data);
        return parsedWorkbook;
      })
      .then((parsedWorkbook) => {
        if (cancelled) return;
        setWorkbook(parsedWorkbook);
        const firstVisibleSheet = parsedWorkbook.worksheets.find(
          (sheet) => sheet.state === "visible",
        );
        setActiveSheet(firstVisibleSheet?.name ?? parsedWorkbook.worksheets[0]?.name ?? "");
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Excel 文件读取失败");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [blob]);

  useEffect(() => {
    setRowPage(0);
    setColumnPage(0);
    setSelectedCell(null);
  }, [activeSheet]);

  const visibleSheetNames = useMemo(() => {
    if (!workbook) return [];
    const visible = workbook.worksheets
      .filter((sheet) => sheet.state === "visible")
      .map((sheet) => sheet.name);
    return visible.length ? visible : workbook.worksheets.map((sheet) => sheet.name);
  }, [workbook]);
  const worksheet = workbook?.getWorksheet(activeSheet);
  const range = useMemo(() => sheetBounds(worksheet), [worksheet]);
  const totalRows = range.bottom - range.top + 1;
  const totalColumns = range.right - range.left + 1;
  const rowPageCount = Math.max(1, Math.ceil(totalRows / effectiveRowsPerPage));
  const columnPageCount = Math.max(1, Math.ceil(totalColumns / effectiveColumnsPerPage));
  const firstRow = range.top + rowPage * effectiveRowsPerPage;
  const lastRow = Math.min(range.bottom, firstRow + effectiveRowsPerPage - 1);
  const firstColumn = range.left + columnPage * effectiveColumnsPerPage;
  const lastColumn = Math.min(range.right, firstColumn + effectiveColumnsPerPage - 1);
  const rows = useMemo(
    () => Array.from({ length: lastRow - firstRow + 1 }, (_, index) => firstRow + index),
    [firstRow, lastRow],
  );
  const columns = useMemo(
    () =>
      Array.from(
        { length: lastColumn - firstColumn + 1 },
        (_, index) => firstColumn + index,
      ),
    [firstColumn, lastColumn],
  );
  const mergeCells = useMemo(() => {
    const cells = new Map<string, RenderedMerge | null>();
    for (const mergeValue of worksheet?.model.merges ?? []) {
      const merge = decodeMergeRange(mergeValue);
      if (!merge) continue;
      const startRow = Math.max(firstRow, merge.top);
      const endRow = Math.min(lastRow, merge.bottom);
      const startColumn = Math.max(firstColumn, merge.left);
      const endColumn = Math.min(lastColumn, merge.right);
      if (startRow > endRow || startColumn > endColumn) continue;
      const renderAddress = `${columnName(startColumn)}${startRow}`;
      cells.set(renderAddress, {
        sourceAddress: `${columnName(merge.left)}${merge.top}`,
        rowSpan: endRow - startRow + 1,
        colSpan: endColumn - startColumn + 1,
      });
      for (let row = startRow; row <= endRow; row += 1) {
        for (let column = startColumn; column <= endColumn; column += 1) {
          const address = `${columnName(column)}${row}`;
          if (address !== renderAddress) cells.set(address, null);
        }
      }
    }
    return cells;
  }, [firstColumn, firstRow, lastColumn, lastRow, worksheet]);

  if (error) return <ViewerState error>{error}</ViewerState>;
  if (!workbook) return <ViewerState>正在读取 Excel 原始文件...</ViewerState>;
  if (!worksheet || !activeSheet) {
    return <ViewerState error>工作簿中没有可读取的工作表</ViewerState>;
  }

  return (
    <div className="document-excel-viewer">
      <div className="document-excel-tabs" role="tablist" aria-label="工作表">
        {visibleSheetNames.map((sheetName) => (
          <button
            aria-selected={sheetName === activeSheet}
            className={sheetName === activeSheet ? "is-active" : undefined}
            key={sheetName}
            onClick={() => setActiveSheet(sheetName)}
            role="tab"
            title={sheetName}
            type="button"
          >
            {sheetName}
          </button>
        ))}
      </div>

      <div className="document-excel-formula" aria-live="polite">
        <span>{selectedCell?.address ?? "-"}</span>
        <code>{selectedCell?.formula ?? selectedCell?.display ?? ""}</code>
      </div>

      <div className="document-excel-pagination">
        <div>
          <Button
            aria-label="上一组行"
            disabled={rowPage <= 0}
            onClick={() => setRowPage((current) => Math.max(0, current - 1))}
            size="icon-xs"
            title="上一组行"
            variant="ghost"
          >
            <ChevronLeft />
          </Button>
          <span>行 {firstRow}-{lastRow} / {range.bottom}</span>
          <Button
            aria-label="下一组行"
            disabled={rowPage >= rowPageCount - 1}
            onClick={() => setRowPage((current) => Math.min(rowPageCount - 1, current + 1))}
            size="icon-xs"
            title="下一组行"
            variant="ghost"
          >
            <ChevronRight />
          </Button>
        </div>
        <div>
          <Button
            aria-label="上一组列"
            disabled={columnPage <= 0}
            onClick={() => setColumnPage((current) => Math.max(0, current - 1))}
            size="icon-xs"
            title="上一组列"
            variant="ghost"
          >
            <ChevronLeft />
          </Button>
          <span>列 {columnName(firstColumn)}-{columnName(lastColumn)} / {columnName(range.right)}</span>
          <Button
            aria-label="下一组列"
            disabled={columnPage >= columnPageCount - 1}
            onClick={() =>
              setColumnPage((current) => Math.min(columnPageCount - 1, current + 1))
            }
            size="icon-xs"
            title="下一组列"
            variant="ghost"
          >
            <ChevronRight />
          </Button>
        </div>
      </div>

      <div className="document-excel-grid" role="region" aria-label={`${activeSheet} 工作表`}>
        <table>
          <thead>
            <tr>
              <th aria-label="行号" className="document-excel-corner" />
              {columns.map((column) => (
                <th key={column} scope="col">{columnName(column)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row}>
                <th scope="row">{row}</th>
                {columns.map((column) => {
                  const address = `${columnName(column)}${row}`;
                  const renderedMerge = mergeCells.get(address);
                  if (renderedMerge === null) return null;
                  const sourceAddress = renderedMerge?.sourceAddress ?? address;
                  const source = decodeCellAddress(sourceAddress);
                  const cell = source
                    ? existingCell(worksheet, source.row, source.column)
                    : undefined;
                  const display = formattedCellValue(cell);
                  const formula = cell?.formula ? `=${cell.formula}` : undefined;
                  return (
                    <td
                      className={selectedCell?.address === sourceAddress ? "is-selected" : undefined}
                      colSpan={renderedMerge?.colSpan}
                      data-cell-address={sourceAddress}
                      key={address}
                      onClick={() => setSelectedCell({ address: sourceAddress, display, formula })}
                      onMouseUp={(event) =>
                        reportSelection(event.currentTarget, onTextSelection, {
                          sheet: activeSheet,
                          cell: sourceAddress,
                        })
                      }
                      rowSpan={renderedMerge?.rowSpan}
                      title={formula ? `${formula}\n${display}` : display}
                    >
                      <span>{display}</span>
                      {formula ? <code>{formula}</code> : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


export function BlobImage({ blob, alt }: { blob: Blob; alt: string }) {
  const [source, setSource] = useState("");

  useEffect(() => {
    const objectUrl = URL.createObjectURL(blob);
    setSource(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  if (!source) return <ViewerState>正在读取图片原始文件...</ViewerState>;
  return (
    <img
      alt={alt}
      className="document-blob-image"
      draggable={false}
      src={source}
    />
  );
}


const HTML_PREVIEW_CSP = [
  "default-src 'none'",
  "img-src data: blob:",
  "media-src data: blob:",
  "font-src data:",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline' 'unsafe-eval' blob:",
  "worker-src blob:",
  "connect-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

function sandboxedHtmlDocument(html: string): string {
  // Parse in a detached document. Scripts are retained because they execute
  // only inside a unique-origin iframe without storage, network, frames, forms,
  // popups, top navigation, or access to the LearnGraph application.
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, "text/html");
  doc
    .querySelectorAll("iframe, object, embed, form, link[rel=import]")
    .forEach((node) => node.remove());
  doc.querySelectorAll<HTMLElement>("*").forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name === "srcdoc") {
        element.removeAttribute(attribute.name);
      }
    }
  });
  doc.querySelectorAll<HTMLElement>("[src], [href], [xlink\\:href]").forEach((element) => {
    for (const attributeName of ["src", "href", "xlink:href"]) {
      const value = element.getAttribute(attributeName)?.trim();
      if (
        value &&
        !value.startsWith("#") &&
        !value.startsWith("blob:") &&
        !value.startsWith("data:") &&
        // Allow relative assets to fail closed; keep only safe in-document anchors/data.
        !value.startsWith("./") &&
        !value.startsWith("../") &&
        !value.startsWith("/")
      ) {
        // Drop external network loads from untrusted HTML previews.
        if (/^(https?:|\/\/|javascript:|vbscript:)/iu.test(value)) {
          element.removeAttribute(attributeName);
        }
      }
      if (
        value &&
        /^javascript:/iu.test(value) &&
        !attributeName.toLowerCase().includes("href")
      ) {
        element.removeAttribute(attributeName);
      }
    }
  });
  doc.querySelectorAll("style").forEach((style) => {
    style.textContent = (style.textContent ?? "")
      .replace(/@import\s+[^;]+;?/giu, "")
      .replace(/url\(\s*(['"]?)https?:[^)]+\)/giu, "none")
      .replace(/expression\s*\(/giu, "invalid(");
  });
  doc.querySelectorAll<HTMLScriptElement>("script[src]").forEach((script) => {
    // A standalone uploaded HTML file has no authorized asset origin. Inline
    // scripts run; remote and relative script fetches fail closed.
    script.removeAttribute("src");
  });
  return `<!DOCTYPE html><html><head><meta charset="utf-8" /><meta http-equiv="Content-Security-Policy" content="${HTML_PREVIEW_CSP}" />${
    doc.head?.innerHTML ?? ""
  }</head><body>${doc.body?.innerHTML ?? ""}</body></html>`;
}


export function HtmlDocumentViewer({
  blob,
  filename,
}: {
  blob: Blob;
  filename: string;
}) {
  const [mode, setMode] = useState<"preview" | "source">("preview");
  const [sourceText, setSourceText] = useState("");
  const [previewHtml, setPreviewHtml] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    setSourceText("");
    setPreviewHtml("");
    void blob
      .text()
      .then((text) => {
        if (cancelled) return;
        setSourceText(text);
        setPreviewHtml(sandboxedHtmlDocument(text));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "无法读取 HTML 文件");
      });
    return () => {
      cancelled = true;
    };
  }, [blob]);

  return (
    <div className="flex min-h-[36rem] flex-col">
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b bg-background/95 p-2 backdrop-blur">
        <div className="flex rounded-lg border p-0.5" role="group" aria-label="HTML 查看模式">
          <button
            aria-pressed={mode === "preview"}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === "preview"
                ? "bg-secondary text-secondary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setMode("preview")}
            type="button"
          >
            预览
          </button>
          <button
            aria-pressed={mode === "source"}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === "source"
                ? "bg-secondary text-secondary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setMode("source")}
            type="button"
          >
            源码
          </button>
        </div>
        <span className="truncate text-xs text-muted-foreground" title={filename}>
          {filename}
        </span>
        <span className="text-[10px] text-muted-foreground">
          内联脚本在隔离沙箱中运行；网络、存储与宿主页面访问已禁用
        </span>
      </div>
      {error ? (
        <p className="document-viewer-error p-4" role="alert">
          {error}
        </p>
      ) : null}
      {!error && !sourceText ? (
        <ViewerState>正在读取 HTML…</ViewerState>
      ) : null}
      {mode === "source" && sourceText ? (
        <pre className="m-0 max-h-[calc(100svh-12rem)] overflow-auto bg-muted/20 p-4 text-xs leading-5 text-foreground">
          <code>{sourceText}</code>
        </pre>
      ) : null}
      {mode === "preview" && previewHtml ? (
        <iframe
          className="min-h-[36rem] w-full flex-1 border-0 bg-white"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts"
          srcDoc={previewHtml}
          title={`预览 ${filename}`}
        />
      ) : null}
    </div>
  );
}


export function TextDocumentViewer({
  blob,
  filename,
}: {
  blob: Blob;
  filename: string;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void blob
      .text()
      .then((value) => {
        if (!cancelled) setText(value);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "无法读取文本文件");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [blob]);

  if (error) {
    return (
      <p className="document-viewer-error p-4" role="alert">
        {error}
      </p>
    );
  }
  if (!text) return <ViewerState>正在读取 {filename}…</ViewerState>;
  return (
    <pre className="m-0 max-h-[calc(100svh-12rem)] overflow-auto bg-muted/20 p-4 text-xs leading-5">
      <code>{text}</code>
    </pre>
  );
}

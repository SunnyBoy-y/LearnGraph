import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BlobImage, ExcelWorkbookViewer, HtmlDocumentViewer, RtfDocumentViewer, TextDocumentViewer } from "./document-previewers";

/**
 * Regression guard for the PDF preview crash "Cannot convert object to
 * primitive value".
 *
 * A dynamic `import("...?url")` resolves to the module namespace object of the
 * Vite asset module (`{ default: "<url>" }`), not to a bare URL string. A
 * namespace object has a null prototype, so coercing it with `String(...)`
 * throws a TypeError before `GlobalWorkerOptions.workerSrc` is ever assigned
 * and every PDF preview fails to load. These tests therefore mock the `?url`
 * module exactly the way Vite shapes it and assert the worker source ends up as
 * the URL string.
 */

const workerSrcAssignments: unknown[] = [];
const passwordChallenge = vi.hoisted(() => ({ enabled: false, attempts: [] as string[] }));

vi.mock("pdfjs-dist", () => {
  const GlobalWorkerOptions = {
    get workerSrc() {
      return "";
    },
    set workerSrc(value: unknown) {
      workerSrcAssignments.push(value);
    },
  };
  return {
    GlobalWorkerOptions,
    PasswordResponses: { INCORRECT_PASSWORD: 2 },
    getDocument: () => {
      const document = {
        numPages: 3,
        getPage: async () => {
          throw new Error("page rendering is out of scope for this test");
        },
        destroy: async () => undefined,
      };
      if (!passwordChallenge.enabled) return { promise: Promise.resolve(document) };
      let resolveDocument!: (value: typeof document) => void;
      const task = {
        onPassword: undefined as undefined | ((submit: (password: string) => void, reason: number) => void),
        promise: new Promise<typeof document>((resolve) => { resolveDocument = resolve; }),
        destroy: async () => undefined,
      };
      const submit = (password: string) => {
        passwordChallenge.attempts.push(password);
        if (password === "correct") resolveDocument(document);
        else queueMicrotask(() => task.onPassword?.(submit, 2));
      };
      queueMicrotask(() => task.onPassword?.(submit, 1));
      return task;
    },
    TextLayer: class {
      render() {
        return Promise.resolve();
      }
      cancel() {}
    },
  };
});

vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({
  default: "/assets/pdf.worker.min-test.mjs",
}));

const { PdfDocumentViewer } = await import("./document-previewers");

function pdfBlob() {
  return {
    arrayBuffer: async () => new ArrayBuffer(16),
  } as unknown as Blob;
}

describe("PdfDocumentViewer worker bootstrap", () => {
  beforeEach(() => { passwordChallenge.enabled = false; passwordChallenge.attempts = []; });
  it("assigns the ?url default export as the worker source", async () => {
    workerSrcAssignments.length = 0;

    render(
      <PdfDocumentViewer blob={pdfBlob()} pageNumber={1} scale={1} theme="paper" />,
    );

    await waitFor(() => {
      expect(workerSrcAssignments).toContain("/assets/pdf.worker.min-test.mjs");
    });
    // The URL must be the raw string; coercing the module namespace instead
    // yields "[object Object]" (or throws for a real namespace object).
    for (const assigned of workerSrcAssignments) {
      expect(typeof assigned).toBe("string");
    }
  });

  it("loads the document without a viewer-level error", async () => {
    render(
      <PdfDocumentViewer blob={pdfBlob()} pageNumber={1} scale={1} theme="paper" />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText(/PDF 第 1 页/u)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Cannot convert object to primitive value/u)).toBeNull();
  });

  it("allows retrying an incorrect document password", async () => {
    passwordChallenge.enabled = true;
    render(<PdfDocumentViewer blob={pdfBlob()} pageNumber={1} scale={1} theme="paper" />);
    fireEvent.change(await screen.findByLabelText("PDF 打开密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "打开 PDF" }));
    await screen.findByText("密码不正确，请重试。");
    fireEvent.change(screen.getByLabelText("PDF 打开密码"), { target: { value: "correct" } });
    fireEvent.click(screen.getByRole("button", { name: "打开 PDF" }));
    await screen.findByLabelText(/PDF 第 1 页/u);
    expect(passwordChallenge.attempts).toEqual(["wrong", "correct"]);
  });
});

describe("document text readers", () => {
  it("finishes loading an empty text file", async () => {
    render(<TextDocumentViewer blob={new Blob([])} filename="empty.txt" />);
    await screen.findByText("此文件为空。");
    expect(screen.queryByRole("status")).toBeNull();
  });
  it("shows Markdown through the existing renderer and retains source mode", async () => {
    render(<TextDocumentViewer blob={new Blob(["# Reader heading\n\n**bold**"])} filename="readme.md" />);
    await screen.findByRole("heading", { name: "Reader heading" });
    fireEvent.click(screen.getByRole("button", { name: "源码" }));
    expect(document.querySelector("pre")?.textContent).toContain("# Reader heading");
  });
  it("finishes loading empty HTML without removing iframe isolation", async () => {
    render(<HtmlDocumentViewer blob={new Blob([])} filename="empty.html" />);
    expect(await screen.findByTitle("预览 empty.html")).toHaveAttribute("sandbox", "allow-scripts");
    expect(screen.queryByText("正在读取 HTML…")).toBeNull();
  });

  it("uses the workbook grid for quoted CSV fields, line breaks and literal formulas", async () => {
    const csv = 'name,note,value\r\n"A,B","two\nlines","=1+1"\r\n"say ""hi""",end,42';
    render(<ExcelWorkbookViewer blob={new Blob([csv], { type: "text/csv" })} filename="data.csv" />);
    await screen.findByText("A,B", {}, { timeout: 5000 });
    expect(screen.getByText("two lines")).toBeInTheDocument();
    expect(screen.getByText("=1+1")).toBeInTheDocument();
    expect(screen.getByText('say "hi"')).toBeInTheDocument();
  });

  it("renders RTF formatting inside the shared sandbox instead of showing control words", async () => {
    const rtf = '{\\rtf1\\ansi Hello \\b bold\\b0\\par}';
    render(<RtfDocumentViewer blob={new Blob([rtf], { type: "application/rtf" })} filename="notes.rtf" />);
    const frame = await screen.findByTitle("预览 notes.rtf", {}, { timeout: 5000 });
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");
    expect(frame.getAttribute("srcdoc")).toContain("bold");
    expect(frame.getAttribute("srcdoc")).not.toContain("\\rtf1");
  });
});

const pngBlob = () => new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });

describe("BlobImage · 资料库图片预览的缩放入口", () => {
  beforeEach(() => {
    // jsdom 不实现 objectURL，而预览靠它把 blob 变成 <img src>。
    if (typeof URL.createObjectURL !== "function") {
      Object.defineProperty(URL, "createObjectURL", {
        configurable: true,
        value: vi.fn(() => "blob:mock-image"),
      });
    }
    if (typeof URL.revokeObjectURL !== "function") {
      Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    }
  });

  it("zoomable：点击图片进入共享灯箱，可继续放大/重置", async () => {
    render(<BlobImage alt="结构图.png" blob={pngBlob()} zoomable />);

    const trigger = await screen.findByRole("button", { name: "放大预览 结构图.png" });
    expect(trigger).toHaveTextContent("点击放大");

    fireEvent.click(trigger);

    // 复用共享灯箱：工具条就是聊天里那一套，不是另写一份。
    const zoomIn = await screen.findByRole("button", { name: "放大图片" });
    expect(screen.getByLabelText("当前缩放 100%")).toBeInTheDocument();

    fireEvent.click(zoomIn);
    expect(screen.getByLabelText("当前缩放 125%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重置缩放" })).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "重置缩放" }));
    expect(screen.getByLabelText("当前缩放 100%")).toBeInTheDocument();
  });

  it("默认不开缩放：没有放大入口，其它预览调用点行为不变", async () => {
    render(<BlobImage alt="结构图.png" blob={pngBlob()} />);

    expect(await screen.findByAltText("结构图.png")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /放大预览/u })).toBeNull();
    expect(screen.queryByRole("button", { name: "放大图片" })).toBeNull();
  });
});

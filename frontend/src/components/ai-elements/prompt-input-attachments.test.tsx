import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PromptInput, PromptInputAttachments } from "./prompt-input";

beforeEach(() => {
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = () => "blob:attachment-preview";
    static revokeObjectURL = vi.fn();
  });
});

afterEach(() => vi.unstubAllGlobals());

function upload(name: string, type: string) {
  render(
    <PromptInput onSubmit={() => undefined}>
      <PromptInputAttachments />
    </PromptInput>,
  );
  fireEvent.change(screen.getByLabelText("Upload files"), {
    target: { files: [new File(["preview"], name, { type })] },
  });
  fireEvent.click(screen.getByRole("button", { name: `预览附件 ${name}` }));
}

describe("待发送附件预览", () => {
  it("图片直接打开共享灯箱，支持按钮及滚轮缩放，关闭后重新打开恢复适配", () => {
    upload("image.png", "image/png");
    expect(screen.getByRole("dialog")).toHaveClass("chat-image-lightbox");
    expect(screen.getByRole("img", { name: "image.png" })).toHaveAttribute("src", "blob:attachment-preview");

    fireEvent.click(screen.getByRole("button", { name: "放大图片" }));
    expect(screen.getByLabelText("当前缩放 125%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "缩小图片" }));
    expect(screen.getByLabelText("当前缩放 100%")).toBeInTheDocument();

    fireEvent.wheel(document.querySelector(".chat-image-lightbox__surface")!, { deltaY: -100 });
    expect(screen.getByLabelText("当前缩放 125%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "预览附件 image.png" }));
    expect(screen.getByLabelText("当前缩放 100%")).toBeInTheDocument();
  });

  it("PDF 附件进入文档预览，不会误入图片灯箱", async () => {
    upload("notes.pdf", "application/pdf");
    expect(screen.getByRole("dialog")).not.toHaveClass("chat-image-lightbox");
    expect(screen.getByRole("heading", { name: "notes.pdf" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("dialog").querySelector('[data-preview-kind="pdf"]')).toBeInTheDocument());
    expect(screen.getByRole("dialog").querySelector("iframe")).toBeNull();
    expect(screen.queryByRole("button", { name: "放大图片" })).toBeNull();
  });
});

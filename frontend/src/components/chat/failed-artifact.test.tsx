import "@/test/pdfjs-polyfills";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import * as files from "@/api/files";
import { ChatStreamPartRenderer } from "@/components/chat/chat-stream-part-renderer";
import { ThinkingChain } from "@/components/chat/thinking-chain";
import { groupPartsForDisplay } from "@/features/chat/chat-message-parts";
import type { MessagePart } from "@/types/sessions";

function MessageParts({ parts }: { parts: MessagePart[] }) {
  return groupPartsForDisplay(parts).map((segment) => {
    const children = segment.parts.map((part) => <ChatStreamPartRenderer key={part.id} part={part} />);
    return segment.kind === "chain" ? (
      <ThinkingChain key="chain" chainParts={segment.parts} messageStatus="completed">
        {children}
      </ThinkingChain>
    ) : <div key="answer" data-testid="answer">{children}</div>;
  });
}

describe("failed artifact presentation", () => {
  it("hides failed image and publication attempts until the thinking chain is opened", async () => {
    const download = vi.spyOn(files, "downloadFile");
    const { container } = render(<MessageParts parts={[
      { id: "file", type: "sandbox_artifact", status: "failed", sequence: 0,
        data: { title: "bridge-test", kind: "file", file_id: "unpublished", error_message: "产物发布失败：文件不存在" } },
      { id: "image", type: "image", status: "failed", sequence: 1,
        data: { title: "bridge-probe", file_id: "unpublished-image", error_message: "图片服务超时" } },
    ]} />);
    expect(screen.queryByText(/bridge-test/)).not.toBeInTheDocument();
    expect(screen.queryByText(/bridge-probe/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("answer")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "展开思维链条" }));
    expect(screen.getByText("产物生成失败 · bridge-test")).toBeVisible();
    expect(screen.getByText("图片生成失败 · bridge-probe")).toBeVisible();
    expect(screen.getByText("产物发布失败：文件不存在")).toBeVisible();
    expect(screen.getByText("图片服务超时")).toBeVisible();
    expect(screen.queryByRole("button", { name: /下载|预览|放大/ })).not.toBeInTheDocument();
    expect(container.querySelector("figure, iframe, img")).toBeNull();
    expect(download).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "收起思维链条" }));
    expect(screen.queryByText(/bridge-probe/)).not.toBeInTheDocument();
  });

  it("keeps the successful retry visible while the failed attempt stays folded", () => {
    render(<MessageParts parts={[
      { id: "failed", type: "image", status: "failed", sequence: 0, data: { title: "第一次尝试" } },
      { id: "success", type: "image", status: "completed", sequence: 1,
        data: { title: "重试结果", url: "data:image/png;base64,iVBORw0KGgo=" } },
    ]} />);
    expect(within(screen.getByTestId("answer")).getByRole("img", { name: "重试结果" })).toBeVisible();
    expect(screen.queryByText(/第一次尝试/)).not.toBeInTheDocument();
  });
});

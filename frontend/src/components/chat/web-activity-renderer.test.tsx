import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MessagePartRenderer } from "./message-part-renderer";
import type { MessagePart } from "@/types/sessions";

const value: MessagePart = { id: "web-visible", type: "tool_call", status: "streaming", content: "",
  data: { tool_name: "native_web_search", web_activity: { operation: "search", provider_label: "OpenAI" } } };

describe("web activity renderer", () => {
  it("shows real provider/status transitions without timer-generated progress", () => {
    const view = render(<TooltipProvider><MessagePartRenderer part={value} streaming /></TooltipProvider>);
    expect(screen.getByText("正在搜索（OpenAI）")).toBeInTheDocument();
    view.rerender(<TooltipProvider><MessagePartRenderer part={{ ...value, status: "completed" }} /></TooltipProvider>);
    expect(screen.getByText("搜索完成（OpenAI）")).toBeInTheDocument();
    expect(screen.queryByText("正在搜索（OpenAI）")).not.toBeInTheDocument();
    view.rerender(<TooltipProvider><MessagePartRenderer part={{ ...value, status: "failed", data: {
      tool_name: "browser_read_page", web_activity: { operation: "browser", provider_label: "隔离浏览器" },
    } }} /></TooltipProvider>);
    expect(screen.getByText("读取网页失败（隔离浏览器）")).toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import {
  LIVE_SHELL_MAX_CHARS,
  LIVE_SHELL_MAX_LINES,
  normalizeLiveShellOutput,
} from "@/features/chat/live-shell-output";

describe("live-shell-output normalization and ToolCallPart rendering", () => {
  it("strips ANSI escape codes and resolves carriage-return progress bars", () => {
    const raw =
      "\x1b[32mInstalling\x1b[0m\n10% [==]\r50% [=====]\r100% [==========]\r\n\x1b]0;title\x07Done";
    const normalized = normalizeLiveShellOutput(raw);
    expect(normalized.text).toBe("Installing\n100% [==========]\nDone");
    expect(normalized.truncated).toBe(false);
    expect(normalized.lineCount).toBe(3);
  });

  it("caps output to trailing 200 lines and 8000 characters", () => {
    const many = Array.from({ length: 260 }, (_, idx) => `line-${idx}`).join("\n");
    const byLines = normalizeLiveShellOutput(many);
    expect(byLines.truncated).toBe(true);
    expect(byLines.lineCount).toBe(LIVE_SHELL_MAX_LINES);
    expect(byLines.text.startsWith("line-60")).toBe(true);
    expect(byLines.text.endsWith("line-259")).toBe(true);

    const wide = Array.from({ length: 100 }, (_, idx) => `row-${idx}-${"a".repeat(120)}`).join("\n");
    const byChars = normalizeLiveShellOutput(wide);
    expect(byChars.truncated).toBe(true);
    expect(byChars.text.length).toBeLessThanOrEqual(LIVE_SHELL_MAX_CHARS);
  });

  it("renders bounded live shell output while a sandbox command tool_call is streaming", () => {
    render(
      <MessagePartRenderer
        part={{
          id: "tool-1",
          type: "tool_call",
          status: "streaming",
          data: {
            tool_name: "sandbox_bash",
            input: { command: "pytest -q" },
            live_output: "\x1b[36mcollecting...\x1b[0m\n10% \r100% passed",
            live_output_truncated: true,
          },
        }}
        streaming
      />,
    );

    expect(screen.getByText(/沙箱执行 · pytest -q/)).toBeInTheDocument();
    expect(screen.getByText("实时输出（尾部）")).toBeInTheDocument();
    expect(screen.getByText("前面输出已省略")).toBeInTheDocument();
    expect(screen.getByText(/collecting\.\.\./)).toBeInTheDocument();
    expect(screen.getByText(/100% passed/)).toBeInTheDocument();
  });
});

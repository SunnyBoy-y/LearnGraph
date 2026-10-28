import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VoiceLowLatencyToggle } from "./voice-low-latency-toggle";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

function ControlledMenu() {
  const [enabled, setEnabled] = useState(false);
  return (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger>选择语音模型</DropdownMenuTrigger>
      <DropdownMenuContent>
        <VoiceLowLatencyToggle enabled={enabled} onCheckedChange={setEnabled} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

describe("voice low latency mode", () => {
  it("starts off and lets the user explicitly enable and disable it", () => {
    render(<ControlledMenu />);
    const toggle = screen.getByRole("menuitemcheckbox", { name: "低延迟模式" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("已关闭")).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("已开启")).toBeInTheDocument();
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("prevents duplicate changes while saving", () => {
    const change = vi.fn();
    render(<DropdownMenu defaultOpen>
      <DropdownMenuTrigger>选择语音模型</DropdownMenuTrigger>
      <DropdownMenuContent><VoiceLowLatencyToggle enabled={false} pending onCheckedChange={change} /></DropdownMenuContent>
    </DropdownMenu>);
    const toggle = screen.getByRole("menuitemcheckbox", { name: "低延迟模式" });
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(toggle);
    expect(change).not.toHaveBeenCalled();
    expect(screen.getByText("保存中…")).toBeInTheDocument();
  });

  it("supports keyboard selection without closing the model menu", () => {
    render(<ControlledMenu />);
    const toggle = screen.getByRole("menuitemcheckbox", { name: "低延迟模式" });
    toggle.focus();
    fireEvent.keyDown(toggle, { key: "Enter" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });
});

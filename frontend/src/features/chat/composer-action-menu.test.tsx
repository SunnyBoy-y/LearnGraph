import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ComposerActionMenu } from "./composer-action-menu";

const menuLabel = "打开对话功能菜单";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("caps the open menu to the space on either side of its trigger and updates on resize", async () => {
  vi.stubGlobal("innerHeight", 550);
  render(<TooltipProvider><ComposerActionMenu>菜单内容</ComposerActionMenu></TooltipProvider>);
  const trigger = screen.getByRole("button", { name: menuLabel });
  let top = 260;
  vi.spyOn(trigger, "getBoundingClientRect").mockImplementation(() => new DOMRect(60, top, 36, 36));

  fireEvent.keyDown(trigger, { key: "Enter" });
  const menu = await screen.findByRole("menu", { name: menuLabel });
  expect(menu.style.getPropertyValue("--chat-plus-menu-above")).toBe("248px");
  expect(menu.style.getPropertyValue("--chat-plus-menu-below")).toBe("242px");

  vi.stubGlobal("innerHeight", 360);
  top = 45;
  fireEvent(window, new Event("resize"));
  expect(menu.style.getPropertyValue("--chat-plus-menu-above")).toBe("33px");
  expect(menu.style.getPropertyValue("--chat-plus-menu-below")).toBe("267px");
});

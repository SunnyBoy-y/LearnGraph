import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuTrigger,
} from "@/components/ai-elements/prompt-input";

const viewportPadding = 8;
const triggerGap = 4;
const menuHeightLimit = 470;

export function ComposerActionMenu({ children }: { children: ReactNode }) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [space, setSpace] = useState({ above: 0, below: 0 });

  useLayoutEffect(() => {
    const trigger = triggerRef.current;
    if (!open || !trigger) return;

    const viewport = window.visualViewport;
    const update = () => {
      const rect = trigger.getBoundingClientRect();
      const top = viewport?.offsetTop ?? 0;
      const bottom = top + (viewport?.height ?? window.innerHeight);
      const above = Math.max(0, Math.floor(rect.top - top - viewportPadding - triggerGap));
      const below = Math.max(0, Math.floor(bottom - rect.bottom - viewportPadding - triggerGap));
      setSpace((current) => current.above === above && current.below === below ? current : { above, below });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(trigger);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    viewport?.addEventListener("resize", update);
    viewport?.addEventListener("scroll", update);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      viewport?.removeEventListener("resize", update);
      viewport?.removeEventListener("scroll", update);
    };
  }, [open]);

  return (
    <PromptInputActionMenu onOpenChange={setOpen} open={open}>
      <PromptInputActionMenuTrigger
        aria-label="打开对话功能菜单"
        className="chat-composer__add"
        ref={triggerRef}
        tooltip="打开功能菜单"
      />
      <PromptInputActionMenuContent
        className="chat-plus-menu"
        collisionPadding={viewportPadding}
        side={space.above >= Math.min(menuHeightLimit, space.below) ? "top" : "bottom"}
        sideOffset={triggerGap}
        style={{
          "--chat-plus-menu-above": `${Math.min(menuHeightLimit, space.above)}px`,
          "--chat-plus-menu-below": `${Math.min(menuHeightLimit, space.below)}px`,
        } as CSSProperties}
      >
        {children}
      </PromptInputActionMenuContent>
    </PromptInputActionMenu>
  );
}

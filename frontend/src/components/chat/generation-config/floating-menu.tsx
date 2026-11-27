import {
  useCallback,
  useEffect,
  useRef,
  type ComponentProps,
} from "react";

import {
  DropdownMenuContent,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { withScrollLockBypass } from "@/lib/scroll-lock";
import { cn } from "@/lib/utils";
import "./generation-config.css";

export const CONFIG_MENU_SURFACE =
  "generation-config-menu retry-answer-menu rounded-[12px] border border-border bg-popover p-1.5 text-popover-foreground shadow-[0_8px_28px_rgba(0,0,0,0.09)] ring-0";

export const CONFIG_MENU_ROW =
  "min-h-9 gap-2.5 rounded-md px-2.5 text-[13px] font-normal focus:bg-muted data-[highlighted]:bg-muted data-[state=open]:bg-muted data-disabled:opacity-50 [&_svg]:text-muted-foreground";

export const CONFIG_MENU_SUMMARY =
  "ml-auto max-w-32 truncate text-xs text-muted-foreground";

export const keepMenuOpen = (event: Event) => {
  event.preventDefault();
};

const VIEWPORT_EDGE_PADDING = 12;
const SUBMENU_MAX_HEIGHT_RATIO = 0.65;
const SUBMENU_MAX_HEIGHT_PX = 460;

export function FloatingMenuContent({
  className,
  collisionPadding = VIEWPORT_EDGE_PADDING,
  sideOffset = 8,
  ...props
}: ComponentProps<typeof DropdownMenuContent>) {
  return (
    <DropdownMenuContent
      collisionPadding={collisionPadding}
      sideOffset={sideOffset}
      className={cn(
        CONFIG_MENU_SURFACE,
        "w-[280px] max-w-[calc(100vw-24px)]",
        className,
      )}
      {...props}
    />
  );
}

export const FloatingSubmenu = DropdownMenuSub;

/**
 * Submenu trigger that prevents accidental dismissal when the pointer crosses
 * into an overlapping, flipped, or viewport-shifted submenu panel.
 */
export function FloatingSubmenuTrigger({
  className,
  onPointerLeave,
  ...props
}: ComponentProps<typeof DropdownMenuSubTrigger>) {
  return (
    <DropdownMenuSubTrigger
      {...props}
      className={cn(CONFIG_MENU_ROW, className)}
      onPointerLeave={(event) => {
        onPointerLeave?.(event);
        const submenuId = event.currentTarget.getAttribute("aria-controls");
        const submenu = submenuId ? document.getElementById(submenuId) : null;
        if (
          event.relatedTarget instanceof Node &&
          submenu?.contains(event.relatedTarget)
        ) {
          event.preventDefault();
        }
      }}
    />
  );
}

/**
 * Viewport-aware submenu panel with portal rendering, collision flipping,
 * horizontal + vertical edge clamping, dynamic max-height, and scroll-lock bypass.
 */
export function FloatingSubmenuContent({
  className,
  collisionPadding = VIEWPORT_EDGE_PADDING,
  sideOffset = 6,
  alignOffset = -6,
  onWheel,
  onTouchMove,
  maxHeightPx = SUBMENU_MAX_HEIGHT_PX,
  ...props
}: ComponentProps<typeof DropdownMenuSubContent> & {
  maxHeightPx?: number;
}) {
  const contentRef = useRef<HTMLDivElement>(null);

  const constrain = useCallback(() => {
    const content = contentRef.current;
    if (!content) return;
    const viewportWidth =
      document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight =
      document.documentElement.clientHeight || window.innerHeight;
    if (!viewportWidth || !viewportHeight) return;

    const boundedMaxHeight = Math.max(
      180,
      Math.min(
        Math.round(viewportHeight * SUBMENU_MAX_HEIGHT_RATIO),
        viewportHeight - VIEWPORT_EDGE_PADDING * 2,
        maxHeightPx,
      ),
    );
    content.style.maxHeight = `${boundedMaxHeight}px`;

    // Measure after Floating UI placement without our own previous shift.
    content.style.translate = "none";
    const rect = content.getBoundingClientRect();
    if (!rect.width && !rect.height) return;

    const shiftX = Math.max(
      VIEWPORT_EDGE_PADDING - rect.left,
      Math.min(0, viewportWidth - VIEWPORT_EDGE_PADDING - rect.right),
    );
    const shiftY = Math.max(
      VIEWPORT_EDGE_PADDING - rect.top,
      Math.min(0, viewportHeight - VIEWPORT_EDGE_PADDING - rect.bottom),
    );
    content.style.translate = `${Math.round(shiftX)}px ${Math.round(shiftY)}px`;
  }, [maxHeightPx]);

  const setContent = useCallback(
    (content: HTMLDivElement | null) => {
      contentRef.current = content;
      if (!content) return;
      const observers: Array<() => void> = [];
      if (content.parentElement) {
        const mutationObserver = new MutationObserver(constrain);
        mutationObserver.observe(content.parentElement, {
          attributes: true,
          attributeFilter: ["style"],
        });
        observers.push(() => mutationObserver.disconnect());
      }
      if (typeof ResizeObserver !== "undefined") {
        const resizeObserver = new ResizeObserver(constrain);
        resizeObserver.observe(content);
        observers.push(() => resizeObserver.disconnect());
      }
      const frame = requestAnimationFrame(constrain);
      return () => {
        for (const disconnect of observers) disconnect();
        cancelAnimationFrame(frame);
        contentRef.current = null;
      };
    },
    [constrain],
  );

  useEffect(() => {
    let frame = 0;
    const onResize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(constrain);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(frame);
    };
  }, [constrain]);

  return (
    <DropdownMenuPortal>
      <DropdownMenuSubContent
        {...props}
        ref={setContent}
        avoidCollisions
        collisionPadding={collisionPadding}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        onFocusOutside={(event) => {
          props.onFocusOutside?.(event);
          // Prevent accidental submenu dismissal when:
          // 1. Focus moves to an interactive child inside the portaled submenu
          //    before React's capture handler updates Radix's focus-inside ref.
          // 2. Radix's parent MenuItemImpl focuses the parent menu container on
          //    pointer leave across the parent/submenu gap.
          // Focus moving to a sibling item or outside the menu still closes it.
          const target = event.target;
          if (
            target instanceof Node &&
            (contentRef.current?.contains(target) ||
              (target instanceof HTMLElement &&
                target.getAttribute("data-slot") === "dropdown-menu-content"))
          ) {
            event.preventDefault();
          }
        }}
        onWheel={withScrollLockBypass(onWheel)}
        onTouchMove={withScrollLockBypass(onTouchMove)}
        className={cn(
          CONFIG_MENU_SURFACE,
          "flex flex-col overflow-hidden max-w-[calc(100vw-24px)] data-open:animate-none data-closed:animate-none",
          className,
        )}
      />
    </DropdownMenuPortal>
  );
}

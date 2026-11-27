import { useEffect, useRef } from "react";

export interface EscapeStopRunContext {
  isRunning: boolean;
  hasPendingApproval?: boolean;
  hasLocalOverlay?: boolean;
  /** Optional predicate to identify the main chat composer input (Esc may stop from it when idle). */
  isComposerElement?: (element: Element | null) => boolean;
}

const OPEN_OVERLAY_SELECTOR = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[role="menu"]',
  '[role="listbox"]',
  '[data-radix-popper-content-wrapper]',
  '[data-state="open"][role="combobox"]',
].join(", ");

function isEditableElement(target: EventTarget | null): target is HTMLElement {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return !(target as HTMLInputElement).readOnly && !(target as HTMLInputElement).disabled;
  }
  return target.isContentEditable;
}

function hasActiveTextSelection(): boolean {
  if (typeof window === "undefined" || typeof window.getSelection !== "function") {
    return false;
  }
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) return false;
  return selection.toString().trim().length > 0;
}

function hasOpenDomOverlay(): boolean {
  if (typeof document === "undefined") return false;
  const nodes = document.querySelectorAll(OPEN_OVERLAY_SELECTOR);
  for (const node of nodes) {
    if (node instanceof HTMLElement && node.getAttribute("aria-hidden") !== "true") {
      // Skip hidden display:none containers.
      if (node.hidden) continue;
      return true;
    }
  }
  return false;
}

/**
 * Determine whether a window-level Escape keydown should trigger stopping the
 * active chat generation. Layered priority prevents accidental cancellations:
 * 1. IME composition / modifier keys / repeat / already handled -> never stop
 * 2. Pending approval bar or open modal/popover/menu -> never stop
 * 3. Active non-empty text selection -> never stop
 * 4. Focus inside a non-composer editable input (rename, search, inline edit) -> never stop
 * 5. Idle reading or main composer focus while running -> stop run
 */
export function shouldEscapeStopRun(
  event: Pick<
    KeyboardEvent,
    | "key"
    | "defaultPrevented"
    | "repeat"
    | "isComposing"
    | "keyCode"
    | "metaKey"
    | "ctrlKey"
    | "altKey"
    | "shiftKey"
    | "target"
  >,
  context: EscapeStopRunContext,
): boolean {
  if (!context.isRunning) return false;
  if (event.key !== "Escape") return false;
  if (event.defaultPrevented || event.repeat) return false;
  if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return false;
  if (event.isComposing || event.keyCode === 229) return false;
  if (context.hasPendingApproval || context.hasLocalOverlay) return false;
  if (hasOpenDomOverlay()) return false;
  if (hasActiveTextSelection()) return false;

  const activeEl =
    (event.target instanceof Element ? event.target : null) ??
    (typeof document !== "undefined" ? document.activeElement : null);

  if (isEditableElement(activeEl)) {
    const isComposer = context.isComposerElement?.(activeEl) ?? false;
    if (!isComposer) return false;
  }

  return true;
}

export function useEscapeStopRun(
  context: EscapeStopRunContext,
  onStop: () => void,
): void {
  const contextRef = useRef(context);
  const onStopRef = useRef(onStop);
  contextRef.current = context;
  onStopRef.current = onStop;

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!shouldEscapeStopRun(event, contextRef.current)) return;
      event.preventDefault();
      onStopRef.current();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);
}

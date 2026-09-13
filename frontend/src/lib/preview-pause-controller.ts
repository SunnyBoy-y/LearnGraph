import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";

export type PreviewPauseAction = "pause" | "resume";

export interface PreviewPauseController {
  /** Whether the controller is currently holding playback paused. */
  readonly paused: boolean;
  pause(): void;
  resume(): void;
  toggle(): void;
  destroy(): void;
}

const CONTROLLERS = new Set<PreviewPauseController>();
const GLOBAL_PAUSED_CONTROLLERS = new Set<PreviewPauseController>();
const GLOBAL_EVENT = "learngraph:preview-global-control";
let globalPaused = false;
const globalListeners = new Set<() => void>();

/** Pause/resume every mounted preview in the current application shell. */
export function pauseAllPreviews(): void {
  globalPaused = true;
  globalListeners.forEach((listener) => listener());
  CONTROLLERS.forEach((controller) => {
    if (!controller.paused) GLOBAL_PAUSED_CONTROLLERS.add(controller);
    controller.pause();
  });
}

export function resumeAllPreviews(): void {
  globalPaused = false;
  globalListeners.forEach((listener) => listener());
  GLOBAL_PAUSED_CONTROLLERS.forEach((controller) => controller.resume());
  GLOBAL_PAUSED_CONTROLLERS.clear();
}

export function toggleAllPreviews(): void {
  const shouldPause = [...CONTROLLERS].some((controller) => !controller.paused);
  (shouldPause ? pauseAllPreviews : resumeAllPreviews)();
}

export function useAllPreviewsPaused(): boolean {
  return useSyncExternalStore(
    (listener) => {
      globalListeners.add(listener);
      return () => globalListeners.delete(listener);
    },
    () => globalPaused,
    () => false,
  );
}

export interface PreviewPauseOptions {
  /** Pause descendants and notify embedded previews when the document is hidden. */
  autoVisibility?: boolean;
  /** Called when the paused state changes. */
  onStateChange?: (paused: boolean) => void;
}

type RootResolver = HTMLElement | null | (() => HTMLElement | null);

function resolveRoot(root: RootResolver): HTMLElement | null {
  return typeof root === "function" ? root() : root;
}

/**
 * Coordinates playback for a preview region.
 *
 * Native media descendants are paused directly. Embedded sandbox previews are
 * notified through a tiny postMessage protocol so the preview can pause its
 * own animation/audio without crossing the iframe boundary. Only media that
 * was playing when a pause happened is resumed afterwards.
 */
export function createPreviewPauseController(
  root: RootResolver,
  options: PreviewPauseOptions = {},
): PreviewPauseController {
  const autoVisibility = options.autoVisibility !== false;
  let paused = false;
  let visibilityPaused = false;
  let destroyed = false;
  let requestId = 0;
  const activeMedia = new Set<HTMLMediaElement>();
  const suspendedFrames = new Map<HTMLIFrameElement, { src: string | null; srcdoc: string | null }>();
  const fallbackTimers = new Map<HTMLIFrameElement, number>();

  function setPaused(next: boolean): void {
    if (paused === next) return;
    paused = next;
    options.onStateChange?.(paused);
  }

  function post(action: PreviewPauseAction): void {
    const element = resolveRoot(root);
    if (!element) return;
    const currentRequest = ++requestId;
    element.querySelectorAll<HTMLIFrameElement>("iframe").forEach((frame) => {
      frame.dataset.previewPaused = action === "pause" ? "true" : "false";
      try {
        frame.contentWindow?.postMessage(
          { lg: 1, kind: "preview.control", action, requestId: currentRequest },
          "*",
        );
      } catch {
        // A preview can disappear while the host is changing routes.
      }
      if (action === "pause") {
        const timer = window.setTimeout(() => {
          // Unsupported/remote previews cannot acknowledge the protocol. Blank
          // their browsing context to terminate timers, workers and audio.
          if (suspendedFrames.has(frame)) return;
          suspendedFrames.set(frame, {
            src: frame.getAttribute("src"),
            srcdoc: frame.getAttribute("srcdoc"),
          });
          frame.setAttribute("srcdoc", "<!doctype html><html><body></body></html>");
          frame.removeAttribute("src");
        }, 350);
        fallbackTimers.set(frame, timer);
      } else {
        const timer = fallbackTimers.get(frame);
        if (timer) window.clearTimeout(timer);
        fallbackTimers.delete(frame);
        const saved = suspendedFrames.get(frame);
        if (saved) {
          suspendedFrames.delete(frame);
          if (saved.srcdoc === null) frame.removeAttribute("srcdoc");
          else frame.setAttribute("srcdoc", saved.srcdoc);
          if (saved.src === null) frame.removeAttribute("src");
          else frame.setAttribute("src", saved.src);
        }
      }
    });
  }

  function pause(): void {
    if (destroyed) return;
    if (paused) return;
    const element = resolveRoot(root);
    activeMedia.clear();
    element?.querySelectorAll<HTMLMediaElement>("audio,video").forEach((media) => {
      if (!media.paused && !media.ended) activeMedia.add(media);
      media.pause();
    });
    post("pause");
    setPaused(true);
  }

  function resume(): void {
    if (destroyed) return;
    post("resume");
    activeMedia.forEach((media) => {
      void media.play().catch(() => undefined);
    });
    activeMedia.clear();
    setPaused(false);
  }

  const onFrameAck = (event: MessageEvent) => {
    const element = resolveRoot(root);
    if (!element || !event.data || event.data.lg !== 1 || event.data.kind !== "preview.control.ack") return;
    const frame = [...element.querySelectorAll<HTMLIFrameElement>("iframe")].find((candidate) => candidate.contentWindow === event.source);
    if (!frame || event.data.action !== "pause") return;
    const timer = fallbackTimers.get(frame);
    if (timer) window.clearTimeout(timer);
    fallbackTimers.delete(frame);
  };
  if (typeof window !== "undefined") window.addEventListener("message", onFrameAck);
  const onFrameLoad = (event: Event) => {
    const frame = event.target instanceof HTMLIFrameElement ? event.target : null;
    if (frame && paused) frame.contentWindow?.postMessage({ lg: 1, kind: "preview.control", action: "pause", requestId: ++requestId }, "*");
  };
  const resolvedRoot = resolveRoot(root);
  resolvedRoot?.addEventListener("load", onFrameLoad, true);

  const onVisibilityChange = () => {
    if (document.visibilityState === "hidden") {
      if (!paused) {
        visibilityPaused = true;
        pause();
      }
    } else if (visibilityPaused && !globalPaused) {
      visibilityPaused = false;
      resume();
    }
  };

  if (autoVisibility && typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }

  const onGlobalControl = (event: Event) => {
    const action = (event as CustomEvent<PreviewPauseAction>).detail;
    if (action === "pause") pause();
    if (action === "resume") resume();
  };
  if (typeof window !== "undefined") {
    window.addEventListener(GLOBAL_EVENT, onGlobalControl);
  }

  const controller: PreviewPauseController = {
    get paused() {
      return paused;
    },
    pause,
    resume,
    toggle: () => (paused ? resume() : pause()),
    destroy: () => {
      if (destroyed) return;
      destroyed = true;
      CONTROLLERS.delete(controller);
      GLOBAL_PAUSED_CONTROLLERS.delete(controller);
      if (typeof window !== "undefined") window.removeEventListener(GLOBAL_EVENT, onGlobalControl);
      if (typeof window !== "undefined") window.removeEventListener("message", onFrameAck);
      resolvedRoot?.removeEventListener("load", onFrameLoad, true);
      fallbackTimers.forEach((timer) => window.clearTimeout(timer));
      fallbackTimers.clear();
      suspendedFrames.clear();
      if (autoVisibility && typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
      activeMedia.clear();
    },
  };
  CONTROLLERS.add(controller);
  if (globalPaused) {
    GLOBAL_PAUSED_CONTROLLERS.add(controller);
    controller.pause();
  }

  return controller;
}

/** React adapter used by preview shells and media players. */
export function usePreviewPause(
  rootRef: RefObject<HTMLElement | null>,
  options: Omit<PreviewPauseOptions, "onStateChange"> = {},
) {
  const [paused, setPaused] = useState(false);
  const controllerRef = useRef<PreviewPauseController | null>(null);
  const autoVisibility = options.autoVisibility !== false;

  useEffect(() => {
    const controller = createPreviewPauseController(() => rootRef.current, {
      autoVisibility,
      onStateChange: setPaused,
    });
    controllerRef.current = controller;
    return () => {
      controller.destroy();
      controllerRef.current = null;
    };
  }, [rootRef, autoVisibility]);

  const pause = useCallback(() => controllerRef.current?.pause(), []);
  const resume = useCallback(() => controllerRef.current?.resume(), []);
  const toggle = useCallback(() => controllerRef.current?.toggle(), []);
  return { paused, pause, resume, toggle };
}

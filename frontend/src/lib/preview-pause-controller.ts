import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type RefObject,
} from "react";

export type PreviewPauseAction = "pause" | "resume";

/**
 * Why a preview is currently held paused.
 *
 * Channels are independent: a preview runs only while *no* channel holds it,
 * and releasing one channel never overrides another. That separation is what
 * keeps two competing controls from fighting: resuming the page-level "pause
 * every preview" switch must not start a card the artifact wall itself keeps
 * paused (and vice versa).
 */
export type PreviewPauseReason =
  /** The preview's own play/pause control, or an imperative caller. */
  | "manual"
  /** `pauseAllPreviews()` — the page-level "pause every preview" switch. */
  | "global"
  /** The document went hidden. */
  | "visibility"
  /** The owning group (the artifact card wall) keeps this preview paused. */
  | "scope";

/** Reason used by `PreviewPauseScope` consumers; one scope per preview. */
const SCOPE_REASON: PreviewPauseReason = "scope";

export interface PreviewPauseController {
  /** Whether the controller is currently holding playback paused. */
  readonly paused: boolean;
  pause(): void;
  resume(): void;
  toggle(): void;
  /** Hold playback for one reason; every other channel keeps its own state. */
  suspend(reason: PreviewPauseReason): void;
  /** Release one reason; playback resumes only when no channel is left. */
  release(reason: PreviewPauseReason): void;
  destroy(): void;
}

const CONTROLLERS = new Set<PreviewPauseController>();
const GLOBAL_EVENT = "learngraph:preview-global-control";
let globalPaused = false;
const globalListeners = new Set<() => void>();

/**
 * Pause/resume every mounted preview in the current application shell.
 *
 * The `global` channel is applied to every controller — including ones that are
 * already paused for their own reasons — so releasing it restores exactly the
 * previews that should be running again (the active card on the artifact wall)
 * and leaves the rest paused.
 */
export function pauseAllPreviews(): void {
  globalPaused = true;
  globalListeners.forEach((listener) => listener());
  CONTROLLERS.forEach((controller) => controller.suspend("global"));
}

export function resumeAllPreviews(): void {
  globalPaused = false;
  globalListeners.forEach((listener) => listener());
  CONTROLLERS.forEach((controller) => controller.release("global"));
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
 * Playback authority for one preview group (the artifact card wall).
 *
 * A group owns *when* its previews may run; each preview owns *how* it pauses.
 * The wall renders one provider per card, so `activate` already knows which card
 * the user engaged with.
 */
export interface PreviewPauseScope {
  /** True while this group keeps the previews inside it paused. */
  suspended: boolean;
  /** The user engaged with this group: make it the playing one. */
  activate: () => void;
  /** A preview inside the group took over the viewport (expanded/fullscreen). */
  pin: (pinned: boolean) => void;
}

/**
 * Absent provider means "no group authority": previews inside run unless a
 * `manual`/`global`/`visibility` channel says otherwise. The chat canvas mounts
 * no provider, so chat cards keep their existing playback behaviour.
 */
export const PreviewPauseScopeContext = createContext<PreviewPauseScope | null>(null);

export function usePreviewPauseScope(): PreviewPauseScope | null {
  return useContext(PreviewPauseScopeContext);
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
  const reasons = new Set<PreviewPauseReason>();
  const activeMedia = new Set<HTMLMediaElement>();
  const suspendedFrames = new Map<HTMLIFrameElement, { src: string | null; srcdoc: string | null }>();
  const fallbackTimers = new Map<HTMLIFrameElement, number>();

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

  /** Apply the pause transition itself (media, embedded previews). */
  function applyPause(): void {
    const element = resolveRoot(root);
    activeMedia.clear();
    element?.querySelectorAll<HTMLMediaElement>("audio,video").forEach((media) => {
      if (!media.paused && !media.ended) activeMedia.add(media);
      media.pause();
    });
    post("pause");
  }

  function applyResume(): void {
    post("resume");
    activeMedia.forEach((media) => {
      void media.play().catch(() => undefined);
    });
    activeMedia.clear();
  }

  /** Recompute the effective state from every channel; act only on a change. */
  function sync(): void {
    if (destroyed) return;
    const next = reasons.size > 0;
    if (paused === next) return;
    if (next) applyPause();
    else applyResume();
    paused = next;
    options.onStateChange?.(paused);
  }

  function suspend(reason: PreviewPauseReason): void {
    if (destroyed) return;
    reasons.add(reason);
    sync();
  }

  function release(reason: PreviewPauseReason): void {
    if (destroyed) return;
    reasons.delete(reason);
    sync();
  }

  function pause(): void {
    suspend("manual");
  }

  function resume(): void {
    release("manual");
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
      visibilityPaused = true;
      suspend("visibility");
    } else if (visibilityPaused && !globalPaused) {
      visibilityPaused = false;
      release("visibility");
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
    // The preview's own control owns the `manual` channel only; a preview held
    // by another channel (e.g. its group) is started through that channel.
    toggle: () => (reasons.has("manual") ? resume() : pause()),
    suspend,
    release,
    destroy: () => {
      if (destroyed) return;
      destroyed = true;
      CONTROLLERS.delete(controller);
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
      reasons.clear();
    },
  };
  CONTROLLERS.add(controller);
  if (globalPaused) controller.suspend("global");

  return controller;
}

/** React adapter used by preview shells and media players. */
export function usePreviewPause(
  rootRef: RefObject<HTMLElement | null>,
  options: Omit<PreviewPauseOptions, "onStateChange"> = {},
) {
  const scope = usePreviewPauseScope();
  const scopeSuspended = scope?.suspended === true;
  const [paused, setPaused] = useState(scopeSuspended);
  const controllerRef = useRef<PreviewPauseController | null>(null);
  const autoVisibility = options.autoVisibility !== false;
  // Read at controller creation so a preview mounted into an already-suspended
  // group never runs (and never flashes) before the scope effect below lands.
  const scopeSuspendedRef = useRef(scopeSuspended);
  scopeSuspendedRef.current = scopeSuspended;

  useEffect(() => {
    const controller = createPreviewPauseController(() => rootRef.current, {
      autoVisibility,
      onStateChange: setPaused,
    });
    controllerRef.current = controller;
    if (scopeSuspendedRef.current) controller.suspend(SCOPE_REASON);
    return () => {
      controller.destroy();
      controllerRef.current = null;
    };
  }, [rootRef, autoVisibility]);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller) return;
    if (scopeSuspended) controller.suspend(SCOPE_REASON);
    else controller.release(SCOPE_REASON);
  }, [scopeSuspended]);

  const pause = useCallback(() => controllerRef.current?.pause(), []);
  const resume = useCallback(() => controllerRef.current?.resume(), []);
  const toggle = useCallback(() => controllerRef.current?.toggle(), []);
  return { paused, pause, resume, toggle };
}

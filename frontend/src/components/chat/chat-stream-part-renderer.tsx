import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type SyntheticEvent,
} from "react";
import {
  CircleAlert,
  Download,
  LoaderCircle,
  Maximize2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { downloadFile } from "@/api/files";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import type { TrustedComponentAction } from "@/components/chat/trusted-component-renderer";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import type { MessagePart } from "@/types/sessions";

function safeImageSource(value: unknown) {
  if (typeof value !== "string") return "";
  if (value.startsWith("data:image/") || value.startsWith("/")) return value;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol)
      ? parsed.toString()
      : "";
  } catch {
    return "";
  }
}

function positiveNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

/**
 * ChatGPT-style particle field for image generation wait states.
 *
 * Layered motion:
 * 1. Outward radial ring (primary "scan")
 * 2. Slow whole-field breath (scale + opacity)
 * 3. Soft secondary counter-wave for depth
 * Peak dots scale up and get a tiny glow so the field feels premium, not static.
 */
function ImageParticleField({ active }: { active: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !active) return;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    const context = canvas.getContext("2d");
    if (!context) return;

    let raf = 0;
    let disposed = false;
    let width = 0;
    let height = 0;
    let dpr = 1;

    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      // Particle field occupies the remaining grid row under the title.
      width = parent.clientWidth;
      height = Math.max(parent.clientHeight, 160);
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const isDark = document.documentElement.classList.contains("dark");
    // Slightly cooler neutrals; peak dots push toward near-black / near-white.
    const baseR = isDark ? 168 : 96;
    const baseG = isDark ? 170 : 98;
    const baseB = isDark ? 180 : 108;
    const peakR = isDark ? 236 : 28;
    const peakG = isDark ? 238 : 30;
    const peakB = isDark ? 246 : 36;

    const smoothstep = (edge0: number, edge1: number, x: number) => {
      const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
      return t * t * (3 - 2 * t);
    };

    const draw = (timeMs: number) => {
      if (disposed) return;
      const t = timeMs * 0.001;
      context.clearRect(0, 0, width, height);

      // Keep the field under the title with soft edge breathing room.
      const padX = Math.max(20, width * 0.07);
      const padTop = Math.max(44, height * 0.14);
      const padBottom = Math.max(22, height * 0.1);
      const fieldW = Math.max(1, width - padX * 2);
      const fieldH = Math.max(1, height - padTop - padBottom);
      // Slightly denser grid so scale changes read clearly.
      const cols = Math.max(22, Math.round(fieldW / 9.5));
      const rows = Math.max(18, Math.round(fieldH / 9.5));
      const stepX = fieldW / Math.max(1, cols - 1);
      const stepY = fieldH / Math.max(1, rows - 1);
      const cx = width / 2;
      const cy = padTop + fieldH * 0.5;
      const maxDist = Math.hypot(fieldW * 0.55, fieldH * 0.55) || 1;

      // Global breath: the whole field gently expands / contracts (~3.4s cycle).
      const globalBreath = reducedMotion
        ? 0.55
        : 0.5 + 0.5 * Math.sin(t * 1.85);
      // Expanding ring travels from center to edge (~2.6s cycle).
      const ringPhase = ((t * 0.38) % 1 + 1) % 1;
      const ringCenter = ringPhase * 1.12;
      // Secondary slower inward wash for layered depth.
      const counterPhase = ((t * 0.22 + 0.5) % 1 + 1) % 1;
      const counterCenter = 1 - counterPhase * 0.95;

      for (let row = 0; row < rows; row += 1) {
        for (let col = 0; col < cols; col += 1) {
          const x = padX + col * stepX;
          const y = padTop + row * stepY;
          const dx = x - cx;
          const dy = y - cy;
          // Mild elliptical squash so the field feels wider than tall.
          const dist = Math.hypot(dx, dy * 1.08);
          const norm = Math.min(1.15, dist / maxDist);

          // Deterministic micro-offset so neighbors don't pulse in lockstep.
          const hash = Math.sin(col * 12.9898 + row * 78.233) * 43758.5453;
          const jitter = hash - Math.floor(hash);

          // Primary travelling ring: gaussian-ish band around ringCenter.
          const ringDist = Math.abs(norm - ringCenter);
          const ring = Math.exp(-((ringDist * 4.6) ** 2));

          // Secondary counter band (softer, broader).
          const counterDist = Math.abs(norm - counterCenter);
          const counter = Math.exp(-((counterDist * 3.1) ** 2)) * 0.45;

          // Local breath: slower undulation with spatial phase.
          const localBreath = reducedMotion
            ? 0.5
            : 0.5 +
              0.5 *
                Math.sin(
                  t * 2.05 +
                    norm * 3.4 +
                    col * 0.17 -
                    row * 0.11 +
                    jitter * 6.28,
                );

          // Compose excitation 0..1. Ring dominates; breath keeps idle alive.
          const excitation = reducedMotion
            ? 0.42
            : Math.min(
                1,
                ring * 0.92 +
                  counter * 0.55 +
                  localBreath * 0.28 +
                  globalBreath * 0.18,
              );

          // Soft edge vignette — center stays richer, corners dissolve.
          const falloff = 1 - smoothstep(0.42, 1.08, norm);
          if (falloff <= 0.02) continue;

          // Scale: resting dots stay tiny; peaks grow ~3× for a clear breath.
          const baseRadius = 0.75 + (1 - norm) * 0.35;
          const scale = reducedMotion
            ? 1
            : 0.55 + excitation * 1.85 + globalBreath * 0.25;
          const radius = Math.max(0.35, baseRadius * scale * falloff ** 0.35);

          // Opacity rides the same wave; peaks near opaque, idle still visible.
          const alpha = Math.max(
            0.08,
            Math.min(0.92, (0.16 + excitation * 0.78) * falloff),
          );

          // Mix base → peak color as intensity rises.
          const mix = Math.min(1, excitation * 1.15);
          const r = Math.round(baseR * (1 - mix) + peakR * mix);
          const g = Math.round(baseG * (1 - mix) + peakG * mix);
          const b = Math.round(baseB * (1 - mix) + peakB * mix);

          // Soft glow under peak dots (skip on reduced motion for clarity).
          if (!reducedMotion && excitation > 0.62 && radius > 1.1) {
            const glow = (excitation - 0.62) * 1.6;
            context.beginPath();
            context.fillStyle = `rgba(${r}, ${g}, ${b}, ${(glow * 0.18 * falloff).toFixed(3)})`;
            context.arc(x, y, radius * (2.4 + glow * 1.4), 0, Math.PI * 2);
            context.fill();
          }

          context.beginPath();
          context.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
          context.arc(x, y, radius, 0, Math.PI * 2);
          context.fill();
        }
      }

      if (!reducedMotion) {
        raf = window.requestAnimationFrame(draw);
      }
    };

    resize();
    const observer = new ResizeObserver(() => {
      resize();
      if (reducedMotion) draw(0);
    });
    if (canvas.parentElement) observer.observe(canvas.parentElement);

    if (reducedMotion) {
      draw(0);
    } else {
      raf = window.requestAnimationFrame(draw);
    }

    return () => {
      disposed = true;
      observer.disconnect();
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [active]);

  return (
    <canvas
      aria-hidden="true"
      className="chat-generated-image__particles"
      ref={canvasRef}
    />
  );
}

function ChatImagePart({ part }: { part: MessagePart }) {
  const data = part.data;
  const directSource = safeImageSource(
    data?.preview_url ?? data?.src ?? data?.url,
  );
  const fileId = typeof data?.file_id === "string" ? data.file_id : "";
  const revision = String(
    data?.preview_revision ?? data?.revision ?? data?.updated_at ?? "initial",
  );
  const [downloadedSource, setDownloadedSource] = useState("");
  const [loadingRevision, setLoadingRevision] = useState(Boolean(fileId));
  const [downloadFailed, setDownloadFailed] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  useEffect(() => {
    if (!fileId) {
      setDownloadedSource("");
      setLoadingRevision(false);
      setDownloadFailed(false);
      return;
    }
    let cancelled = false;
    setLoadingRevision(true);
    setDownloadFailed(false);
    void downloadFile(fileId)
      .then((blob) => {
        if (cancelled) return;
        const nextObjectUrl = URL.createObjectURL(blob);
        setDownloadedSource(nextObjectUrl);
      })
      .catch(() => {
        if (!cancelled) {
          setDownloadedSource("");
          setDownloadFailed(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingRevision(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fileId, revision]);

  useEffect(
    () => () => {
      if (downloadedSource.startsWith("blob:"))
        URL.revokeObjectURL(downloadedSource);
    },
    [downloadedSource],
  );

  const width = positiveNumber(data?.width);
  const height = positiveNumber(data?.height);
  const declaredAspect =
    typeof data?.aspect_ratio === "string" && data.aspect_ratio.includes("/")
      ? data.aspect_ratio
      : undefined;
  // Prefer real pixel dimensions; fall back to declared ratio; only use a
  // neutral wait frame (4/3) while the canvas is still empty. Never force 1/1.
  const [measuredAspect, setMeasuredAspect] = useState<string | undefined>();
  const source = downloadedSource || directSource;
  const aspectRatio =
    width && height
      ? `${width} / ${height}`
      : measuredAspect || declaredAspect || (source ? undefined : "4 / 3");
  const title =
    typeof data?.title === "string" ? data.title : "正在生成图片";
  const alt = typeof data?.alt === "string" ? data.alt : title;
  const isWorking =
    (part.status === "pending" ||
      part.status === "streaming" ||
      loadingRevision) &&
    !downloadFailed;
  const failed = part.status === "failed" || downloadFailed;
  const done = part.status === "completed" && Boolean(source) && !loadingRevision;
  // Particle field only while there is no usable preview yet; once a partial
  // or final image is present we keep the canvas off to avoid covering it.
  const showParticles = isWorking && !failed && !source;

  const stateLabel = downloadFailed
    ? "图片预览加载失败"
    : part.status === "failed"
      ? "图片生成失败"
      : done
        ? "图片已生成"
        : source
          ? "正在优化预览"
          : "正在创建图片";
  const imageKey = useMemo(
    () => `${fileId || directSource}-${revision}`,
    [directSource, fileId, revision],
  );

  useEffect(() => {
    setMeasuredAspect(undefined);
  }, [imageKey]);

  const handleImageLoad = useCallback(
    (event: SyntheticEvent<HTMLImageElement>) => {
      if (width && height) return;
      const node = event.currentTarget;
      if (node.naturalWidth > 0 && node.naturalHeight > 0) {
        setMeasuredAspect(`${node.naturalWidth} / ${node.naturalHeight}`);
      }
    },
    [height, width],
  );

  const mimeType = typeof data?.mime_type === "string" ? data.mime_type : "";
  const downloadName = `learngraph-image-${fileId || "generated"}.${
    mimeType.includes("jpeg") || mimeType.includes("jpg")
      ? "jpg"
      : mimeType.includes("webp")
        ? "webp"
        : "png"
  }`;
  const handleDownload = useCallback(async () => {
    if (!source) return;
    try {
      let href = source;
      let revoke = false;
      if (!source.startsWith("blob:") && !source.startsWith("data:")) {
        const response = await fetch(source);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        href = URL.createObjectURL(await response.blob());
        revoke = true;
      }
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = downloadName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      if (revoke) window.setTimeout(() => URL.revokeObjectURL(href), 2_000);
    } catch {
      toast.error("图片下载失败");
    }
  }, [downloadName, source]);

  const interactive = done && !failed;
  // Failed states, or partial-preview polishing, keep a compact status chip.
  // Pure wait (no preview yet) is handled entirely by the particle card.
  const showOverlay = failed || (isWorking && Boolean(source));

  return (
    <figure
      aria-busy={isWorking}
      className={`chat-generated-image${
        source ? " chat-generated-image--has-preview" : ""
      }${showParticles ? " chat-generated-image--particles" : ""}`}
      style={aspectRatio ? { aspectRatio } : undefined}
    >
      {source ? (
        interactive ? (
          <button
            aria-label="放大查看图片"
            className="chat-generated-image__zoom"
            onClick={() => setLightboxOpen(true)}
            type="button"
          >
            <img
              alt={alt}
              className="chat-generated-image__preview"
              key={imageKey}
              onLoad={handleImageLoad}
              src={source}
            />
          </button>
        ) : (
          <img
            alt={alt}
            className="chat-generated-image__preview"
            key={imageKey}
            onLoad={handleImageLoad}
            src={source}
          />
        )
      ) : null}
      {showParticles ? (
        <div
          aria-live="polite"
          className="chat-generated-image__particle-card"
          role="status"
        >
          <span className="chat-generated-image__particle-title">
            {stateLabel}
          </span>
          <ImageParticleField active={showParticles} />
        </div>
      ) : null}
      {showOverlay ? (
        <div
          className={`chat-generated-image__state${failed ? " is-failed" : ""}`}
          role={failed ? "alert" : "status"}
        >
          <span className="chat-generated-image__icon">
            {failed ? (
              <CircleAlert className="size-5" />
            ) : (
              <LoaderCircle className="size-5 animate-spin" />
            )}
          </span>
          <strong>{stateLabel}</strong>
          {failed || title !== stateLabel ? <span>{title}</span> : null}
        </div>
      ) : null}
      {interactive ? (
        <div className="chat-generated-image__actions">
          <button
            aria-label="放大查看"
            onClick={() => setLightboxOpen(true)}
            title="放大查看"
            type="button"
          >
            <Maximize2 className="size-3.5" />
          </button>
          <button
            aria-label="下载图片"
            onClick={() => void handleDownload()}
            title="下载图片"
            type="button"
          >
            <Download className="size-3.5" />
          </button>
        </div>
      ) : null}
      {interactive ? (
        <Dialog onOpenChange={setLightboxOpen} open={lightboxOpen}>
          <DialogContent
            aria-describedby={undefined}
            className="chat-image-lightbox"
            showCloseButton={false}
          >
            <DialogTitle className="sr-only">查看生成的图片</DialogTitle>
            <img alt={alt} className="chat-image-lightbox__image" src={source} />
            <div className="chat-image-lightbox__toolbar">
              <button
                onClick={() => void handleDownload()}
                type="button"
              >
                <Download className="size-4" />
                下载图片
              </button>
              <DialogClose asChild>
                <button type="button">
                  <X className="size-4" />
                  关闭
                </button>
              </DialogClose>
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </figure>
  );
}

export function ChatStreamPartRenderer({
  interactive = true,
  onAction,
  part,
  siblingParts,
  streaming = false,
}: {
  interactive?: boolean;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  part: MessagePart;
  siblingParts?: MessagePart[];
  streaming?: boolean;
}) {
  if (part.type === "image") return <ChatImagePart part={part} />;
  return (
    <MessagePartRenderer
      interactive={interactive}
      onAction={onAction}
      part={part}
      siblingParts={siblingParts}
      streaming={streaming}
    />
  );
}

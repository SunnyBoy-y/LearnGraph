import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Box, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";

type MagicCardData = {
  card_instance_id?: string;
  card_id?: string;
  version?: number | string;
  runtime?: string;
  fallback_text?: string;
  title?: string;
  status?: string;
  origin_verified?: boolean;
  artifact_url?: string;
  preview_html?: string;
  preferred_height?: number;
  viewport?: {
    mode?: string;
    preferred_height?: number;
    max_height?: number;
  };
};

function asRecord(value: unknown): MagicCardData {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as MagicCardData)
    : {};
}

function positiveHeight(value: unknown, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) && value >= 120
    ? Math.min(900, Math.round(value))
    : fallback;
}

/** CSP for sandboxed dynamic HTML previews (scripts on, no network/frames). */
const DYNAMIC_PREVIEW_CSP =
  "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; script-src 'unsafe-inline' 'unsafe-eval' blob:; worker-src blob:; connect-src 'none'; frame-src 'none'; media-src data: blob:; object-src 'none'; base-uri 'none'; form-action 'none'";

/**
 * Build srcDoc for agent HTML. Full documents pass through (with CSP meta
 * injected); fragments are wrapped so scripts/canvas animations can execute.
 */
function buildDynamicPreviewSrcDoc(html: string): string {
  const trimmed = html.trim();
  const cspMeta = `<meta http-equiv="Content-Security-Policy" content="${DYNAMIC_PREVIEW_CSP}">`;
  const isFullDocument =
    /^<!doctype\s+html/i.test(trimmed) || /^<html[\s>]/i.test(trimmed);

  if (isFullDocument) {
    if (/http-equiv\s*=\s*["']?Content-Security-Policy/i.test(trimmed)) {
      return trimmed;
    }
    if (/<head[\s>]/i.test(trimmed)) {
      return trimmed.replace(/<head([^>]*)>/i, `<head$1>${cspMeta}`);
    }
    if (/^<!doctype\s+html[^>]*>/i.test(trimmed)) {
      return trimmed.replace(
        /^(<!doctype\s+html[^>]*>)/i,
        `$1<head>${cspMeta}</head>`,
      );
    }
    return `<head>${cspMeta}</head>${trimmed}`;
  }

  return `<!doctype html><html><head><meta charset="utf-8">${cspMeta}<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;min-height:100%;font-family:system-ui,sans-serif;color:#171717;background:#fff}*{box-sizing:border-box}</style></head><body>${html}</body></html>`;
}

function MagicCardFallback({
  title,
  reason,
}: {
  title: string;
  reason: string;
}) {
  return (
    <section
      aria-live="polite"
      className="magic-card magic-card--failed"
      role="alert"
    >
      <div className="magic-card__heading">
        <AlertTriangle className="size-4" />
        <div>
          <strong>卡片渲染失败</strong>
          <span>{title}</span>
        </div>
        <Badge variant="secondary">fallback</Badge>
      </div>
      <p className="magic-card__reason">{reason}</p>
    </section>
  );
}

/**
 * Host shell for channel-B generative micro-apps.
 *
 * AI-generated React never mounts in the main app tree. Only an origin-verified
 * HTTPS runtime URL (or a sandboxed srcDoc dynamic preview with scripts) is
 * allowed. Any load, runtime, or heartbeat failure collapses to a local fallback
 * card.
 */
export function MagicCardHost({ data }: { data: Record<string, unknown> }) {
  const card = useMemo(() => asRecord(data), [data]);
  const title =
    (typeof card.title === "string" && card.title) ||
    (typeof card.fallback_text === "string" && card.fallback_text) ||
    (typeof card.card_id === "string" && card.card_id) ||
    "交互卡片";
  const height = positiveHeight(
    card.preferred_height ?? card.viewport?.preferred_height,
    360,
  );
  const maxHeight = positiveHeight(card.viewport?.max_height, 720);
  const artifactUrl =
    typeof card.artifact_url === "string" ? card.artifact_url : "";
  const previewHtml =
    typeof card.preview_html === "string" ? card.preview_html : "";
  const originVerified = card.origin_verified === true;
  const canRenderRemote =
    originVerified && /^https:\/\//i.test(artifactUrl.trim());
  const [failed, setFailed] = useState(false);
  const [failureReason, setFailureReason] = useState("卡片运行时不可用。");
  const lastHeartbeatRef = useRef(Date.now());
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    setFailed(false);
    setFailureReason("卡片运行时不可用。");
    lastHeartbeatRef.current = Date.now();
  }, [artifactUrl, previewHtml, originVerified]);

  useEffect(() => {
    if (!canRenderRemote || failed) return;

    function onMessage(event: MessageEvent) {
      const frame = iframeRef.current;
      if (!frame || event.source !== frame.contentWindow) return;
      const payload = event.data;
      if (!payload || typeof payload !== "object") return;
      const method =
        typeof (payload as { method?: unknown }).method === "string"
          ? (payload as { method: string }).method
          : typeof (payload as { type?: unknown }).type === "string"
            ? (payload as { type: string }).type
            : "";
      if (
        method === "card/heartbeat" ||
        method === "card/ready" ||
        method === "card/initialize/result"
      ) {
        lastHeartbeatRef.current = Date.now();
        return;
      }
      if (
        method === "card/runtime-error" ||
        method === "card/fatal" ||
        method === "error"
      ) {
        const message =
          typeof (payload as { message?: unknown }).message === "string"
            ? (payload as { message: string }).message
            : typeof (payload as { params?: { message?: unknown } }).params
                  ?.message === "string"
              ? String(
                  (payload as { params: { message: string } }).params.message,
                )
              : "卡片内部运行异常";
        setFailureReason(message);
        setFailed(true);
      }
    }

    window.addEventListener("message", onMessage);
    const timer = window.setInterval(() => {
      if (Date.now() - lastHeartbeatRef.current > 20_000) {
        setFailureReason("卡片长时间无响应，已自动隔离。");
        setFailed(true);
      }
    }, 5_000);
    return () => {
      window.removeEventListener("message", onMessage);
      window.clearInterval(timer);
    };
  }, [canRenderRemote, failed]);

  if (failed) {
    return <MagicCardFallback reason={failureReason} title={title} />;
  }

  if (canRenderRemote) {
    return (
      <section
        aria-label={title}
        className="magic-card"
        style={{ maxHeight }}
      >
        <div className="magic-card__heading">
          <Sparkles className="size-4" />
          <div>
            <strong>{title}</strong>
            <span>
              {typeof card.card_id === "string" ? card.card_id : "magic-card"}
              {card.version != null ? ` · v${card.version}` : ""}
            </span>
          </div>
          <Badge variant="secondary">
            {typeof card.runtime === "string"
              ? card.runtime
              : "react-sandbox-v1"}
          </Badge>
        </div>
        <iframe
          allow=""
          className="magic-card__frame"
          onError={() => {
            setFailureReason("卡片 iframe 加载失败。");
            setFailed(true);
          }}
          ref={iframeRef}
          referrerPolicy="no-referrer"
          sandbox="allow-scripts"
          src={artifactUrl}
          style={{ height }}
          title={`${title} 隔离运行时`}
        />
      </section>
    );
  }

  if (previewHtml) {
    // Dynamic preview: allow scripts inside an opaque-origin sandbox (no
    // allow-same-origin) so agent HTML/canvas/animation can run without
    // host-DOM access. Still block network, frames, and forms.
    const srcDoc = buildDynamicPreviewSrcDoc(previewHtml);
    return (
      <section aria-label={title} className="magic-card">
        <div className="magic-card__heading">
          <Box className="size-4" />
          <div>
            <strong>{title}</strong>
            <span>动态预览（脚本已启用）</span>
          </div>
          <Badge variant="secondary">preview</Badge>
        </div>
        <iframe
          allow=""
          className="magic-card__frame"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts"
          srcDoc={srcDoc}
          style={{ height }}
          title={`${title} 动态预览`}
        />
      </section>
    );
  }

  return (
    <MagicCardFallback
      reason={
        typeof card.status === "string" && card.status === "building"
          ? "卡片仍在构建或尚未发布可验证运行时。"
          : "缺少已签名的独立源运行时，无法安全加载可执行卡片。"
      }
      title={title}
    />
  );
}

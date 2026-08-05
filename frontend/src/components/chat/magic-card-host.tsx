import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Box, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import {
  createSubappChannel,
  subappFailureText,
  subappSessionTrigger,
} from "@/lib/subapp-bridge";
import type { SubappChannel } from "@/lib/subapp-bridge";

type MagicCardData = {
  card_instance_id?: string;
  card_id?: string;
  version?: number | string;
  runtime?: string;
  fallback_text?: string;
  title?: string;
  status?: string;
  reason?: string;
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

/** Reasons persisted by the backend for cards without an executable preview. */
const CARD_REASON_TEXT: Record<string, string> = {
  isolated_browser_renderer_not_configured:
    "这张卡片没有随消息保存可执行的隔离预览内容，无法重新运行。",
  magic_card_preview_required:
    "这张卡片没有随消息保存可执行的隔离预览内容，无法重新运行。",
};

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
  const maxHeight = positiveHeight(card.viewport?.max_height, 720);
  const height = Math.min(
    maxHeight,
    positiveHeight(card.preferred_height ?? card.viewport?.preferred_height, 360),
  );
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

  // T2.6 interactive sub-application mode: an artifact carrying a sub-app session
  // marker (or an interactive contract/version) runs the bidirectional
  // user → component.event → AI → renderer.state → render loop instead of the
  // static card host below. Absent the marker, the existing card paths are
  // untouched.
  const subappTrigger = useMemo(() => subappSessionTrigger(data), [data]);
  const chatSessionId = useMemo(
    () => (typeof data.chat_session_id === "string" ? data.chat_session_id : null),
    [data],
  );
  const subappIframeRef = useRef<HTMLIFrameElement | null>(null);
  const subappLoadedRef = useRef(false);
  const subappChannelRef = useRef<SubappChannel | null>(null);
  const [subappFailed, setSubappFailed] = useState<string | null>(null);

  useEffect(() => {
    if (!subappTrigger) return;
    setSubappFailed(null);
    const channel = createSubappChannel({
      getIframe: () => subappIframeRef.current,
      getIframeLoaded: () => subappLoadedRef.current,
      artifactVersionId:
        subappTrigger.kind === "instantiate" ? subappTrigger.artifactVersionId : undefined,
      provisioned:
        subappTrigger.kind === "provisioned"
          ? {
              sessionId: subappTrigger.sessionId,
              token: subappTrigger.token,
              unlockMessage: subappTrigger.unlockMessage,
            }
          : undefined,
      chatSessionId,
      onFailed: (reason) => setSubappFailed(reason),
    });
    subappChannelRef.current = channel;
    return () => {
      channel.destroy();
      subappChannelRef.current = null;
    };
  }, [subappTrigger, chatSessionId]);

  const handleSubappLoad = () => {
    subappLoadedRef.current = true;
    subappChannelRef.current?.handleIframeLoad();
  };

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

  if (subappTrigger) {
    const hasSubappContent = Boolean(previewHtml || canRenderRemote);
    return (
      <section aria-label={title} className="magic-card">
        <div className="magic-card__heading">
          <Sparkles className="size-4" />
          <div>
            <strong>{title}</strong>
            <span>双向交互子应用</span>
          </div>
          <Badge variant="secondary">subapp</Badge>
        </div>
        {hasSubappContent ? (
          <iframe
            allow=""
            className="magic-card__frame"
            onLoad={handleSubappLoad}
            ref={subappIframeRef}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
            srcDoc={previewHtml ? sandboxedHtmlPreviewDocument(previewHtml) : undefined}
            src={!previewHtml && canRenderRemote ? artifactUrl : undefined}
            style={{ height }}
            title={`${title} 子应用`}
          />
        ) : (
          <MagicCardFallback
            reason="子应用尚未准备好，没有可加载的交互式页面内容。"
            title={title}
          />
        )}
        {subappFailed ? (
          <p className="magic-card__reason" role="status">
            {subappFailureText(subappFailed)}
          </p>
        ) : null}
      </section>
    );
  }

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
    // host-DOM access. The host owns the CSP; network, frames, and forms stay
    // blocked even if the card ships its own policy.
    const srcDoc = sandboxedHtmlPreviewDocument(previewHtml);
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

  if (card.status === "building") {
    return <MagicCardFallback reason="卡片仍在构建，尚未产出可运行内容。" title={title} />;
  }

  const mappedReason =
    (typeof card.reason === "string" ? CARD_REASON_TEXT[card.reason] : "") ||
    (typeof card.fallback_text === "string" ? card.fallback_text.trim() : "") ||
    "这张卡片没有随消息保存可执行的隔离预览内容，无法重新运行。";
  return <MagicCardFallback reason={mappedReason} title={title} />;
}

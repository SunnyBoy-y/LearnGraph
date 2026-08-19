import { useEffect, useMemo, useRef, useState } from "react";
import { Box, FileCode2, ShieldAlert, ShieldCheck } from "lucide-react";
import type { BundledLanguage } from "shiki";

import {
  CodeBlock,
  CodeBlockCopyButton,
  CodeBlockHeader,
} from "@/components/ai-elements/code-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FullscreenPreview } from "@/components/chat/fullscreen-preview";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { apiClient } from "@/api/client";
import {
  createSubappChannel,
  subappFailureText,
  subappSessionTrigger,
} from "@/lib/subapp-bridge";
import type { SubappChannel } from "@/lib/subapp-bridge";
import {
  postRendererUnlock,
  rendererUnlockMessage,
  trustedRendererEligible,
  trustedRendererReason,
} from "@/lib/trusted-renderer";
import { createSandboxRuntimeBridge } from "@/lib/sandbox-runtime-bridge";
import type { SandboxRuntimeBridge } from "@/lib/sandbox-runtime-bridge";

const supportedLanguages = new Set<BundledLanguage>([
  "css",
  "html",
  "javascript",
  "jsx",
  "json",
  "tsx",
  "typescript",
]);

function codeLanguage(value: unknown): BundledLanguage {
  return typeof value === "string" && supportedLanguages.has(value as BundledLanguage)
    ? (value as BundledLanguage)
    : "tsx";
}

export function SandboxArtifact({ data }: { data: Record<string, unknown> }) {
  const title = typeof data.title === "string" ? data.title : "生成式组件产物";
  const status = typeof data.status === "string" ? data.status : "preview";
  const sourceCode = typeof data.source_code === "string" ? data.source_code : "";
  const previewHtml = typeof data.preview_html === "string" ? data.preview_html : "";
  const artifactUrl = typeof data.artifact_url === "string" ? data.artifact_url : "";
  const bundleId = typeof data.bundle_id === "string" ? data.bundle_id : "";
  const [bundlePreviewUrl, setBundlePreviewUrl] = useState<string | null>(null);
  const originVerified = data.origin_verified === true;
  const canRenderRemote = originVerified && /^https:\/\//i.test(artifactUrl);
  // P2-A trusted renderer decision is server-side only. The host surfaces it
  // and posts the sealed unlock handshake to the inert opaque iframe; it never
  // relaxes the iframe boundary or executes component code in the main DOM.
  const trustedEligible = trustedRendererEligible(data);
  const trustedReason = trustedRendererReason(data);
  const unlockMessage = rendererUnlockMessage(data);

  // T2.6 interactive sub-application mode: an artifact carrying a sub-app
  // session marker (or an interactive contract/version) runs the bidirectional
  // user → component.event → AI → renderer.state → render loop instead of the
  // static preview below. Absent the marker, the static path is untouched.
  const subappTrigger = useMemo(() => subappSessionTrigger(data), [data]);
  const chatSessionId = useMemo(
    () => (typeof data.chat_session_id === "string" ? data.chat_session_id : null),
    [data],
  );
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const subappLoadedRef = useRef(false);
  const subappChannelRef = useRef<SubappChannel | null>(null);
  const runtimeIframeRef = useRef<HTMLIFrameElement | null>(null);
  const runtimeBridgeRef = useRef<SandboxRuntimeBridge | null>(null);
  const [subappFailed, setSubappFailed] = useState<string | null>(null);
  const [consentRequest, setConsentRequest] = useState<{
    eventId: string
    pendingConsentId: string
    triggers: string[]
  } | null>(null);
  const [agentStatus, setAgentStatus] = useState<'idle' | 'queued' | 'processing' | 'failed'>('idle');
  const [agentError, setAgentError] = useState<string | null>(null);

  useEffect(() => {
    if (!subappTrigger) return;
    setSubappFailed(null);
    const channel = createSubappChannel({
      getIframe: () => iframeRef.current,
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
      onConsentRequired: (info) => setConsentRequest(info),
      onEventQueued: () => setConsentRequest(null),
      onAgentStatus: (status) => {
        setAgentStatus(status.agentStatus)
        setAgentError(status.error ?? null)
      },
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
    let cancelled = false;
    setBundlePreviewUrl(null);
    if (!bundleId) return () => { cancelled = true; };
    void apiClient
      .get<{ url: string }>(`/subapps/bundles/${bundleId}/preview`)
      .then((response) => {
        if (!cancelled && typeof response?.url === "string") setBundlePreviewUrl(response.url);
      })
      .catch(() => {
        if (!cancelled) setBundlePreviewUrl(null);
      });
    return () => { cancelled = true; };
  }, [bundleId]);

  // Browser-sandbox runtime bridge: relays vfs.read (multi-file) and
  // net.fetch (approval-free networking) between the sandbox shim and the
  // backend for every non-subapp preview (srcDoc or bundle URL).
  const runtimePreviewActive = !subappTrigger && Boolean(previewHtml || bundlePreviewUrl);
  useEffect(() => {
    if (!runtimePreviewActive) return;
    const bridge = createSandboxRuntimeBridge(runtimeIframeRef.current, {
      bundleId: bundleId || null,
      bundlePreviewUrl,
    });
    runtimeBridgeRef.current = bridge;
    return () => {
      bridge.destroy();
      runtimeBridgeRef.current = null;
    };
  }, [runtimePreviewActive, bundleId, bundlePreviewUrl, subappTrigger]);

  const hasSubappContent = Boolean(previewHtml || bundlePreviewUrl || canRenderRemote);

  return (
    <section className="sandbox-artifact" aria-label={title}>
      <div className="sandbox-artifact__heading">
        <FileCode2 className="size-4" />
        <div>
          <strong>{title}</strong>
          <span>{subappTrigger ? "双向交互子应用" : "生成代码不会进入主应用 DOM"}</span>
        </div>
        {trustedEligible ? (
          <Badge variant="default">
            <ShieldCheck className="size-3" /> 可信发行者
          </Badge>
        ) : null}
        <Badge variant="secondary">{status}</Badge>
      </div>

      {subappTrigger ? (
        hasSubappContent ? (
          <FullscreenPreview className="sandbox-artifact__preview-wrap" label={title}>
            <iframe
              allow=""
              className="sandbox-artifact__preview"
              onLoad={handleSubappLoad}
              ref={iframeRef}
              referrerPolicy="no-referrer"
              sandbox="allow-scripts"
              srcDoc={previewHtml ? sandboxedHtmlPreviewDocument(previewHtml) : undefined}
              src={!previewHtml ? (bundlePreviewUrl || (canRenderRemote ? artifactUrl : undefined)) : undefined}
              title={`${title}子应用`}
            />
          </FullscreenPreview>
        ) : (
          <div className="sandbox-artifact__empty">
            <Box className="size-6" />
            <div>
              <strong>子应用尚未准备好</strong>
              <span>没有可加载的交互式页面内容。</span>
            </div>
          </div>
        )
      ) : bundlePreviewUrl ? (
        <FullscreenPreview className="sandbox-artifact__preview-wrap" label={title}>
          <iframe
            allow=""
            className="sandbox-artifact__preview"
            ref={runtimeIframeRef}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
            src={bundlePreviewUrl}
            title={`${title}多文件沙箱预览`}
          />
        </FullscreenPreview>
      ) : previewHtml ? (
        <FullscreenPreview className="sandbox-artifact__preview-wrap" label={title}>
          <iframe
            allow=""
            className="sandbox-artifact__preview"
            onLoad={(event) => postRendererUnlock(event.currentTarget, unlockMessage)}
            ref={runtimeIframeRef}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
            srcDoc={sandboxedHtmlPreviewDocument(previewHtml, { runtimeShim: true })}
            title={`${title}动态沙箱预览`}
          />
        </FullscreenPreview>
      ) : canRenderRemote ? (
        <FullscreenPreview className="sandbox-artifact__preview-wrap" label={title}>
          <iframe
            allow=""
            className="sandbox-artifact__preview"
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
            src={artifactUrl}
            title={`${title}隔离运行预览`}
          />
        </FullscreenPreview>
      ) : (
        <div className="sandbox-artifact__empty">
          <Box className="size-6" />
          <div>
            <strong>尚无可验证的沙箱预览</strong>
            <span>需要后端返回动态预览，或来自独立来源且已校验的 Artifact URL。</span>
          </div>
        </div>
      )}

      {subappTrigger && subappFailed ? (
        <p className="sandbox-artifact__warning" role="status">
          <ShieldAlert className="size-3.5" />
          {subappFailureText(subappFailed)}
        </p>
      ) : null}

      {subappTrigger && consentRequest ? (
        <div className="sandbox-artifact__consent" role="region" aria-label="Agent 协同模式授权">
          <p>AI建议采用Agent协同模式，是否开启？</p>
          <div className="sandbox-artifact__consent-actions">
            <Button
              size="sm"
              type="button"
              variant="default"
              onClick={() => void subappChannelRef.current?.decideConsent('allow_session')}
            >
              允许本次
            </Button>
            <Button
              size="sm"
              type="button"
              variant="secondary"
              onClick={() => void subappChannelRef.current?.decideConsent('allow_app')}
            >
              加入白名单
            </Button>
            <Button
              size="sm"
              type="button"
              variant="secondary"
              onClick={() => void subappChannelRef.current?.decideConsent('allow_global')}
            >
              全局允许
            </Button>
            <Button
              size="sm"
              type="button"
              variant="ghost"
              onClick={() => void subappChannelRef.current?.decideConsent('deny')}
            >
              暂不开启
            </Button>
          </div>
        </div>
      ) : null}

      {subappTrigger && (agentStatus === 'processing' || agentStatus === 'queued') ? (
        <p className="sandbox-artifact__agent-status" role="status">
          <ShieldCheck className="size-3.5" />
          AI 正在处理子应用事件…
        </p>
      ) : null}

      {subappTrigger && agentStatus === 'failed' ? (
        <div className="sandbox-artifact__agent-error" role="alert">
          <ShieldAlert className="size-3.5" />
          <span>{agentError || '子应用 Agent 处理失败。'}</span>
          <Button
            size="sm"
            type="button"
            variant="outline"
            onClick={() => void subappChannelRef.current?.retryAgentTask()}
          >
            重试
          </Button>
        </div>
      ) : null}

      {artifactUrl && !canRenderRemote ? (
        <p className="sandbox-artifact__warning" role="status">
          <ShieldAlert className="size-3.5" />
          产物来源未通过隔离校验，因此没有加载远程页面。
        </p>
      ) : null}

      {!trustedEligible && trustedReason ? (
        <p className="sandbox-artifact__warning" role="status">
          <ShieldAlert className="size-3.5" />
          {trustedReason}
        </p>
      ) : null}

      {sourceCode ? (
        <CodeBlock
          className="sandbox-artifact__code"
          code={sourceCode}
          language={codeLanguage(data.language)}
        >
          <CodeBlockHeader>
            <span>{codeLanguage(data.language)}</span>
            <CodeBlockCopyButton aria-label="复制组件源码" size="icon-xs" />
          </CodeBlockHeader>
        </CodeBlock>
      ) : null}
    </section>
  );
}

import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { QueryClientContext } from "@tanstack/react-query";
import { BarChart3, Box, FileCode2, LoaderCircle, ShieldAlert, ShieldCheck } from "lucide-react";
import type { BundledLanguage } from "shiki";

import {
  CodeBlock,
  CodeBlockCopyButton,
  CodeBlockHeader,
} from "@/components/ai-elements/code-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FullscreenPreview } from "@/components/chat/fullscreen-preview";
import { ExpandableContent } from "@/components/shared/expandable-content";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import {
  resolveArtifactFramePolicy,
  useArtifactFrameHeight,
} from "@/lib/artifact-frame-size";
import { apiClient } from "@/api/client";
import { streamSessionMessage } from "@/api/sessions";
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

export function SandboxArtifact({
  data,
  streaming = false,
}: {
  data: Record<string, unknown>;
  streaming?: boolean;
}) {
  const title = typeof data.title === "string" ? data.title : "生成式组件产物";
  const status = typeof data.status === "string" ? data.status : "preview";
  const sourceCode = typeof data.source_code === "string" ? data.source_code : "";
  const previewHtml = typeof data.preview_html === "string" ? data.preview_html : "";
  const artifactUrl = typeof data.artifact_url === "string" ? data.artifact_url : "";
  const bundleId = typeof data.bundle_id === "string" ? data.bundle_id : "";
  const [bundlePreviewUrl, setBundlePreviewUrl] = useState<string | null>(null);
  const [bundleLoading, setBundleLoading] = useState(Boolean(bundleId));
  const queryClient = useContext(QueryClientContext);
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
    pendingConsentId: string
    triggers: string[]
  } | null>(null);
  const [consentPending, setConsentPending] = useState(false);
  const [agentStatus, setAgentStatus] = useState<'idle' | 'queued' | 'processing' | 'failed'>('idle');
  const [agentError, setAgentError] = useState<string | null>(null);
  const [analysisSending, setAnalysisSending] = useState(false);
  const [previewExpanded, setPreviewExpanded] = useState(false);

  const framePolicy = useMemo(
    () => resolveArtifactFramePolicy(data, { defaultMode: "auto" }),
    [data],
  );
  const autoSizeEnabled =
    Boolean(previewHtml) &&
    (Boolean(subappTrigger) || !bundlePreviewUrl) &&
    (framePolicy.mode === "auto" || framePolicy.mode === "bounded");

  const subappSrcDoc = useMemo(
    () =>
      previewHtml
        ? sandboxedHtmlPreviewDocument(previewHtml, {
            subappClient: true,
            measureArtifact: autoSizeEnabled,
          })
        : undefined,
    [previewHtml, autoSizeEnabled],
  );
  const staticSrcDoc = useMemo(
    () =>
      previewHtml
        ? sandboxedHtmlPreviewDocument(previewHtml, {
            runtimeShim: true,
            measureArtifact: autoSizeEnabled,
          })
        : undefined,
    [previewHtml, autoSizeEnabled],
  );
  const activeSrcDoc = subappTrigger ? subappSrcDoc : staticSrcDoc;
  const adaptiveHeight = useArtifactFrameHeight(
    autoSizeEnabled,
    previewExpanded,
    subappTrigger ? iframeRef : runtimeIframeRef,
    {
      mode: framePolicy.mode,
      minHeight: framePolicy.minHeight,
      maxHeight: framePolicy.maxHeight,
      measureKey: streaming ? "streaming" : activeSrcDoc?.length ?? 0,
    },
  );
  const previewFrameStyle =
    framePolicy.mode === "aspect-ratio"
      ? { aspectRatio: framePolicy.aspectRatio, height: "auto" as const }
      : {
          height:
            (autoSizeEnabled ? adaptiveHeight : undefined) ??
            framePolicy.initialHeight,
        };
  const subappFrameSourceKey = subappTrigger
    ? `${subappSrcDoc ?? ""}|${!previewHtml ? (bundlePreviewUrl || (canRenderRemote ? artifactUrl : "")) : ""}`
    : "";
  const prevSubappSourceKeyRef = useRef(subappFrameSourceKey);
  if (prevSubappSourceKeyRef.current !== subappFrameSourceKey) {
    prevSubappSourceKeyRef.current = subappFrameSourceKey;
    subappLoadedRef.current = false;
  }

  /** Built-in data-analysis entry: send a structured message to the Agent. */
  const sendAnalysisRequest = async () => {
    if (!chatSessionId || analysisSending) return
    setAnalysisSending(true)
    try {
      const content =
        `分析双向交互子应用「${title}」的数据（bundle_id: ${bundleId}）\n` +
        `目的：汇总我的作答与交互行为，找出反复出错、卡顿或停留最久的环节，并给出定制化指导。`
      for await (const _event of streamSessionMessage(chatSessionId, {
        content,
        agent_mode: true,
        message_kind: 'normal',
        search_route: 'disabled',
      })) {
        // 消费 SSE 流；消息与结果由后端持久化。
      }
      if (queryClient) {
        await queryClient.invalidateQueries({
          predicate: (query) =>
            query.queryKey[2] === "messages" && query.queryKey[3] === chatSessionId,
        });
      }
    } catch {
      // 发送失败由聊天流 UI 呈现；这里不阻断 artifact 渲染。
    } finally {
      setAnalysisSending(false)
    }
  };

  const handleConsentDecision = async (
    decision: 'allow_session' | 'allow_app' | 'allow_global' | 'deny',
  ) => {
    if (consentPending || !subappChannelRef.current) return;
    setConsentPending(true);
    try {
      const ok = await subappChannelRef.current.decideConsent(decision);
      if (ok) {
        setConsentRequest(null);
      }
    } finally {
      setConsentPending(false);
    }
  };

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
    if (!bundleId) {
      setBundleLoading(false);
      return () => { cancelled = true; };
    }
    setBundleLoading(true);
    void apiClient
      .get<{ url: string }>(`/subapps/bundles/${bundleId}/preview`)
      .then((response) => {
        if (!cancelled && typeof response?.url === "string") setBundlePreviewUrl(response.url);
      })
      .catch(() => {
        if (!cancelled) setBundlePreviewUrl(null);
      })
      .finally(() => {
        if (!cancelled) setBundleLoading(false);
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
    <section className="sandbox-artifact" aria-label={title} data-height-mode={framePolicy.mode}>
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
          <FullscreenPreview
            className="sandbox-artifact__preview-wrap"
            label={title}
            onExpandedChange={setPreviewExpanded}
          >
            <iframe loading="lazy"
            allow=""
              className="sandbox-artifact__preview"
              onLoad={handleSubappLoad}
              ref={iframeRef}
              referrerPolicy="no-referrer"
              sandbox="allow-scripts"
              srcDoc={subappSrcDoc}
              src={!previewHtml ? (bundlePreviewUrl || (canRenderRemote ? artifactUrl : undefined)) : undefined}
              style={previewFrameStyle}
              title={`${title}子应用`}
            />
          </FullscreenPreview>
        ) : bundleLoading ? (
          <div className="sandbox-artifact__empty" role="status">
            <LoaderCircle className="size-5 animate-spin" />
            <div>
              <strong>正在加载子应用预览…</strong>
            </div>
          </div>
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
        <FullscreenPreview
          className="sandbox-artifact__preview-wrap"
          label={title}
          onExpandedChange={setPreviewExpanded}
        >
          <iframe loading="lazy"
            allow=""
            className="sandbox-artifact__preview"
            ref={runtimeIframeRef}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
            src={bundlePreviewUrl}
            style={previewFrameStyle}
            title={`${title}多文件沙箱预览`}
          />
        </FullscreenPreview>
      ) : previewHtml ? (
        <FullscreenPreview
          className="sandbox-artifact__preview-wrap"
          label={title}
          onExpandedChange={setPreviewExpanded}
        >
          <iframe loading="lazy"
            allow=""
            className="sandbox-artifact__preview"
            onLoad={(event) => postRendererUnlock(event.currentTarget, unlockMessage)}
            ref={runtimeIframeRef}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
            srcDoc={staticSrcDoc}
            style={previewFrameStyle}
            title={`${title}动态沙箱预览`}
          />
        </FullscreenPreview>
      ) : canRenderRemote ? (
        <FullscreenPreview
          className="sandbox-artifact__preview-wrap"
          label={title}
          onExpandedChange={setPreviewExpanded}
        >
          <iframe loading="lazy"
            allow=""
            className="sandbox-artifact__preview"
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
            src={artifactUrl}
            style={previewFrameStyle}
            title={`${title}隔离运行预览`}
          />
        </FullscreenPreview>
      ) : bundleLoading ? (
        <div className="sandbox-artifact__empty" role="status">
          <LoaderCircle className="size-5 animate-spin" />
          <div>
            <strong>正在加载沙箱预览…</strong>
          </div>
        </div>
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
              disabled={consentPending}
              size="sm"
              type="button"
              variant="default"
              onClick={() => void handleConsentDecision('allow_session')}
            >
              允许本次
            </Button>
            <Button
              disabled={consentPending}
              size="sm"
              type="button"
              variant="secondary"
              onClick={() => void handleConsentDecision('allow_app')}
            >
              加入白名单
            </Button>
            <Button
              disabled={consentPending}
              size="sm"
              type="button"
              variant="secondary"
              onClick={() => void handleConsentDecision('allow_global')}
            >
              全局允许
            </Button>
            <Button
              disabled={consentPending}
              size="sm"
              type="button"
              variant="ghost"
              onClick={() => void handleConsentDecision('deny')}
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

      {subappTrigger ? (
        <div className="sandbox-artifact__analysis">
          <Button
            size="sm"
            type="button"
            variant="outline"
            disabled={analysisSending || !chatSessionId}
            onClick={() => void sendAnalysisRequest()}
          >
            <BarChart3 className="size-3.5" />
            {analysisSending ? '分析请求发送中…' : '数据分析'}
          </Button>
        </div>
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
        <ExpandableContent
          className="sandbox-artifact__source"
          label="源码"
          lineLimit={24}
        >
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
        </ExpandableContent>
      ) : null}
    </section>
  );
}



import { useEffect, useMemo, useRef } from "react";

import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { createSandboxRuntimeBridge } from "@/lib/sandbox-runtime-bridge";
import { usePreviewPause } from "@/lib/preview-pause-controller";

/** Shared single-document HTML runtime. Bundle/subapp hosts retain their own channels. */
export function SandboxedHtmlFrame({ html, title, className, network = false, offline = false, scripts = true }: {
  html: string;
  title: string;
  className?: string;
  network?: boolean;
  offline?: boolean;
  scripts?: boolean;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const document = useMemo(() => sandboxedHtmlPreviewDocument(html, { runtimeShim: network && !offline && scripts, offline, scripts }), [html, network, offline, scripts]);
  usePreviewPause(rootRef);
  useEffect(() => {
    if (!network || offline || !scripts) return;
    const bridge = createSandboxRuntimeBridge(iframeRef.current, {});
    return () => bridge.destroy();
  }, [document, network, offline, scripts]);
  return (
    <div className="flex min-h-0 flex-1 flex-col" ref={rootRef}>
      <iframe className={className} ref={iframeRef} referrerPolicy="no-referrer" sandbox={scripts ? "allow-scripts" : ""} srcDoc={document} title={title} />
    </div>
  );
}

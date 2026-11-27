import { useEffect, useMemo, useRef } from "react";

import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { createSandboxRuntimeBridge } from "@/lib/sandbox-runtime-bridge";
import { usePreviewPause } from "@/lib/preview-pause-controller";
import {
  useArtifactFrameHeight,
  type HeightMode,
} from "@/lib/artifact-frame-size";

/** Shared single-document HTML runtime. Bundle/subapp hosts retain their own channels. */
export function SandboxedHtmlFrame({
  html,
  title,
  className,
  network = false,
  offline = false,
  scripts = true,
  autoHeight = false,
  heightMode = "auto",
  initialHeight = 320,
  maxHeight,
  expanded = false,
}: {
  html: string;
  title: string;
  className?: string;
  network?: boolean;
  offline?: boolean;
  scripts?: boolean;
  autoHeight?: boolean;
  heightMode?: HeightMode;
  initialHeight?: number;
  maxHeight?: number;
  expanded?: boolean;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const measureArtifact =
    autoHeight && scripts && (heightMode === "auto" || heightMode === "bounded");
  const document = useMemo(
    () =>
      sandboxedHtmlPreviewDocument(html, {
        runtimeShim: network && !offline && scripts,
        offline,
        scripts,
        measureArtifact,
      }),
    [html, network, offline, scripts, measureArtifact],
  );
  const adaptiveHeight = useArtifactFrameHeight(
    measureArtifact,
    expanded,
    iframeRef,
    {
      mode: heightMode,
      maxHeight,
      measureKey: document.length,
    },
  );
  usePreviewPause(rootRef);
  useEffect(() => {
    if (!network || offline || !scripts) return;
    const bridge = createSandboxRuntimeBridge(iframeRef.current, {});
    return () => bridge.destroy();
  }, [document, network, offline, scripts]);
  const frameStyle = measureArtifact
    ? { height: adaptiveHeight ?? initialHeight }
    : undefined;
  return (
    <div className="flex min-h-0 flex-1 flex-col" ref={rootRef}>
      <iframe
        className={className}
        ref={iframeRef}
        referrerPolicy="no-referrer"
        sandbox={scripts ? "allow-scripts" : ""}
        srcDoc={document}
        style={frameStyle}
        title={title}
      />
    </div>
  );
}

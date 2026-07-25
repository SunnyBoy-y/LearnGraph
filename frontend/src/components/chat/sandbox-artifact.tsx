import { Box, FileCode2, ShieldAlert } from "lucide-react";
import type { BundledLanguage } from "shiki";

import {
  CodeBlock,
  CodeBlockCopyButton,
  CodeBlockHeader,
} from "@/components/ai-elements/code-block";
import { Badge } from "@/components/ui/badge";

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

const DYNAMIC_PREVIEW_CSP =
  "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; script-src 'unsafe-inline' 'unsafe-eval' blob:; worker-src blob:; connect-src 'none'; frame-src 'none'; media-src data: blob:; object-src 'none'; base-uri 'none'; form-action 'none'";

/** Sandboxed dynamic preview: scripts run; no same-origin / network / frames. */
function dynamicPreviewDocument(html: string) {
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

export function SandboxArtifact({ data }: { data: Record<string, unknown> }) {
  const title = typeof data.title === "string" ? data.title : "生成式组件产物";
  const status = typeof data.status === "string" ? data.status : "preview";
  const sourceCode = typeof data.source_code === "string" ? data.source_code : "";
  const previewHtml = typeof data.preview_html === "string" ? data.preview_html : "";
  const artifactUrl = typeof data.artifact_url === "string" ? data.artifact_url : "";
  const originVerified = data.origin_verified === true;
  const canRenderRemote = originVerified && /^https:\/\//i.test(artifactUrl);

  return (
    <section className="sandbox-artifact" aria-label={title}>
      <div className="sandbox-artifact__heading">
        <FileCode2 className="size-4" />
        <div>
          <strong>{title}</strong>
          <span>生成代码不会进入主应用 DOM</span>
        </div>
        <Badge variant="secondary">{status}</Badge>
      </div>

      {previewHtml ? (
        <iframe
          allow=""
          className="sandbox-artifact__preview"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts"
          srcDoc={dynamicPreviewDocument(previewHtml)}
          title={`${title}动态沙箱预览`}
        />
      ) : canRenderRemote ? (
        <iframe
          allow=""
          className="sandbox-artifact__preview"
          referrerPolicy="no-referrer"
          sandbox="allow-scripts"
          src={artifactUrl}
          title={`${title}隔离运行预览`}
        />
      ) : (
        <div className="sandbox-artifact__empty">
          <Box className="size-6" />
          <div>
            <strong>尚无可验证的沙箱预览</strong>
            <span>需要后端返回动态预览，或来自独立来源且已校验的 Artifact URL。</span>
          </div>
        </div>
      )}

      {artifactUrl && !canRenderRemote ? (
        <p className="sandbox-artifact__warning" role="status">
          <ShieldAlert className="size-3.5" />
          产物来源未通过隔离校验，因此没有加载远程页面。
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

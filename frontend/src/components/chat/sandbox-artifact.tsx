import { Box, FileCode2, ShieldAlert } from "lucide-react";
import type { BundledLanguage } from "shiki";

import {
  CodeBlock,
  CodeBlockCopyButton,
  CodeBlockHeader,
} from "@/components/ai-elements/code-block";
import { Badge } from "@/components/ui/badge";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";

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
          srcDoc={sandboxedHtmlPreviewDocument(previewHtml)}
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

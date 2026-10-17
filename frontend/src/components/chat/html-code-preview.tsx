import {
  Children,
  cloneElement,
  isValidElement,
  useState,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";
import { EyeOff, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ExpandableContent } from "@/components/shared/expandable-content";
import { SandboxedHtmlFrame } from "@/components/resources/sandboxed-html-frame";
import { cn } from "@/lib/utils";

/** Languages whose fenced source can be executed as a browser HTML document. */
const HTML_CODE_LANGUAGES = new Set(["html", "htm", "xhtml"]);

export function isHtmlCodeLanguage(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return HTML_CODE_LANGUAGES.has(
    value.trim().toLowerCase().replace(/^language-/, ""),
  );
}

type MarkdownCodeNode = {
  type?: string;
  lang?: string | null;
  value?: string;
};

function textFromReactNode(value: ReactNode): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(textFromReactNode).join("");
  if (isValidElement<{ children?: ReactNode }>(value)) {
    return textFromReactNode(value.props.children);
  }
  return "";
}

function languageFromProps(
  node: MarkdownCodeNode | undefined,
  className: string | undefined,
  children: ReactNode,
): string {
  if (typeof node?.lang === "string" && node.lang.trim()) return node.lang;
  const classLanguage = className?.match(/(?:^|\s)language-([^\s]+)/i)?.[1];
  if (classLanguage) return classLanguage;
  const child = Children.toArray(children)[0];
  if (isValidElement<{ className?: string }>(child)) {
    return child.props.className?.match(/(?:^|\s)language-([^\s]+)/i)?.[1] ?? "";
  }
  return "";
}

export interface HtmlCodePreviewProps {
  /** Raw HTML source that will be inserted into the isolated iframe. */
  code: string;
  /** Code content, normally the syntax-highlighted `<pre>` from Streamdown. */
  children: ReactNode;
  language?: string;
  className?: string;
  title?: string;
}

type StreamdownBlockElement = ReactElement<{
  children?: ReactNode;
  "data-block"?: boolean | string;
}>;

function isStreamdownBlockElement(value: ReactNode): value is StreamdownBlockElement {
  return isValidElement<{ "data-block"?: boolean | string }>(value)
    && Boolean(value.props["data-block"]);
}

/**
 * Render a code block with an opt-in, frontend-only HTML preview.
 *
 * The preview is intentionally opt-in: code emitted in an answer is inert
 * until the user clicks the button. Once opened, it runs in an opaque-origin
 * sandboxed iframe built by sandboxedHtmlPreviewDocument, never in the host DOM.
 */
export function HtmlCodePreview({
  children,
  className,
  code,
  language = "html",
  title = "HTML 预览",
}: HtmlCodePreviewProps) {
  const [open, setOpen] = useState(false);
  const hasCode = code.trim().length > 0;

  const previewLabel = open ? "关闭预览" : "预览运行";
  const nativeSource = isStreamdownBlockElement(children);
  const previewButton = (
    <Button
      aria-expanded={open}
      aria-label={`${previewLabel} HTML`}
      data-html-preview-toggle=""
      disabled={!hasCode}
      onClick={() => setOpen((value) => !value)}
      size="xs"
      type="button"
      variant={open ? "secondary" : "outline"}
    >
      {open ? <EyeOff className="size-3.5" /> : <Play className="size-3.5" />}
      {previewLabel}
    </Button>
  );

  return (
    <div
      className={cn(
        "html-code-preview",
        nativeSource && !open && "html-code-preview--native",
        className,
      )}
      data-html-preview=""
    >
      <div
        className={cn(
          "html-code-preview__toolbar",
          nativeSource && !open && "html-code-preview__toolbar--overlay",
        )}
        data-html-preview-toolbar=""
      >
        {nativeSource && !open ? null : (
          <span className="html-code-preview__language">
            {language.trim() || "html"}
          </span>
        )}
        {previewButton}
      </div>

      {open ? (
        <div
          aria-label={title}
          className="html-code-preview__frame"
          data-html-preview-frame=""
          role="region"
        >
          {hasCode ? (
            <SandboxedHtmlFrame
              className="html-code-preview__iframe"
              html={code}
              title={title}
            />
          ) : (
            <p className="html-code-preview__error" role="alert">
              HTML 预览生成失败，请检查源码。
            </p>
          )}
        </div>
      ) : (
        <ExpandableContent
          className="html-code-preview__source"
          label="代码"
          lineLimit={24}
        >
          {children}
        </ExpandableContent>
      )}
    </div>
  );
}

export type MarkdownPreProps = HTMLAttributes<HTMLPreElement> & {
  node?: MarkdownCodeNode;
};

/** Streamdown `pre` override that adds the preview affordance to HTML fences. */
export function PreviewableMarkdownPre({
  children,
  className,
  node,
  ...props
}: MarkdownPreProps) {
  const language = languageFromProps(node, className, children);
  const code =
    typeof node?.value === "string" ? node.value : textFromReactNode(children);

  if (isHtmlCodeLanguage(language)) {
    const markdownChild = isValidElement<Record<string, unknown>>(children)
      ? children
      : null;
    const sourceClassName = markdownChild && typeof markdownChild.props.className === "string"
      ? markdownChild.props.className
      : "";
    let source: ReactNode;
    if (markdownChild && "node" in markdownChild.props) {
      // Streamdown memoizes its `code` renderer by className/node. The marker
      // both promotes the element to the block renderer and forces that
      // memoized element to re-evaluate with the original source text.
      source = cloneElement(markdownChild, {
        "data-block": "true",
        className: `${sourceClassName} html-preview-code-block`.trim(),
      });
    } else {
      source = (
        <pre className={className} {...props}>
          {children}
        </pre>
      );
    }
    return (
      <HtmlCodePreview code={code} language={language}>
        {source}
      </HtmlCodePreview>
    );
  }

  // A custom `pre` replaces Streamdown's default pre, which promotes the code
  // child to a block using data-block. Without it, fences render as inline code
  // and never reach the Mermaid renderer (or the native code toolbar).
  const source = isValidElement<Record<string, unknown>>(children) && "node" in children.props
    ? cloneElement(children, {
      "data-block": "true",
      className: `${typeof children.props.className === "string" ? children.props.className : ""} markdown-preview-code-block`.trim(),
    })
    : <pre className={className} {...props}>{children}</pre>;

  if (language === "mermaid") return source;

  return (
    <ExpandableContent className="markdown-code-block" label="代码" lineLimit={24}>
      {source}
    </ExpandableContent>
  );
}

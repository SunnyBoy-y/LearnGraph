import {
  Children, isValidElement, useEffect, useRef, useState,
  type ComponentProps, type ReactNode,
} from "react";
import { ArrowUpRight, Check, Copy, Eye, Globe } from "lucide-react";
import { toast } from "sonner";
import {
  Popover, PopoverAnchor, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover";
import { citationHttpUrl } from "@/lib/citation-url";
import { decodeUrlForDisplay } from "@/lib/url-display";
import { cn } from "@/lib/utils";
import "./message-link.css";

function linkText(children: ReactNode): string {
  return Children.toArray(children).map((child) => {
    if (typeof child === "string" || typeof child === "number") return String(child);
    return isValidElement<{ children?: ReactNode }>(child) ? linkText(child.props.children) : "";
  }).join("");
}

export function LinkSiteIcon({ href, faviconUrl }: { href: string; faviconUrl?: string }) {
  const url = citationHttpUrl(href);
  const icon = citationHttpUrl(faviconUrl) ?? (url ? new URL("/favicon.ico", url).href : undefined);
  const [failedIcon, setFailedIcon] = useState<string>();
  return (
    <span aria-hidden="true" className="message-link-site-icon">
      {icon && failedIcon !== icon ? (
        <img alt="" src={icon} loading="lazy" referrerPolicy="no-referrer"
          onError={() => setFailedIcon(icon)} />
      ) : <Globe />}
    </span>
  );
}

type MessageLinkProps = ComponentProps<"a"> & {
  node?: unknown;
  faviconUrl?: string;
  variant?: "inline" | "result";
};

/** Normal Markdown links keep native navigation and gain an accessible URL preview. */
export function MessageLink({
  href, children, className, node: _node, faviconUrl, variant = "inline", ...props
}: MessageLinkProps) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const contentRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<HTMLAnchorElement>(null);
  useEffect(() => () => {
    clearTimeout(timer.current);
    clearTimeout(copyTimer.current);
  }, []);

  const external = citationHttpUrl(href);
  const internal = typeof href === "string" && /^\/(?!\/)/.test(href) ? href : undefined;
  const text = linkText(children);
  const label = text && /%[0-9a-f]{2}/i.test(text) ? decodeUrlForDisplay(text) : children;
  if (!external) {
    return internal ? <a {...props} href={internal} className={className}>{label}</a> : <span>{label}</span>;
  }

  const changeOpen = (value: boolean) => {
    clearTimeout(timer.current);
    setOpen(value);
    if (!value) setCopied(false);
  };
  const scheduleClose = () => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (!contentRef.current?.contains(document.activeElement) && document.activeElement !== anchorRef.current) {
        setOpen(false);
      }
    }, 200);
  };
  const name = text || new URL(external).hostname;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(external);
      setCopied(true);
      clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      toast.error("复制失败，请选择链接后手动复制");
    }
  };

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverAnchor asChild>
        <span className={cn("message-link-group", variant === "result" && "message-link-group--result")}
          onPointerEnter={(event) => {
            if (event.pointerType === "touch") return;
            clearTimeout(timer.current);
            timer.current = setTimeout(() => setOpen(true), 140);
          }}
          onPointerLeave={(event) => { if (event.pointerType !== "touch") scheduleClose(); }}
          onBlurCapture={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) scheduleClose();
          }}>
          <a {...props} ref={anchorRef} href={external} target="_blank" rel="noopener noreferrer"
            className={cn("message-link", className)} data-streamdown="link"
            onFocus={(event) => { changeOpen(true); props.onFocus?.(event); }}>
            <span className="message-link-label">{label}</span>
            <ArrowUpRight aria-hidden="true" className="message-link-arrow" />
          </a>
          <PopoverTrigger asChild>
            <button type="button" className="message-link-preview-button"
              aria-label={`预览链接：${name}`} title="预览链接"
              onPointerDown={() => clearTimeout(timer.current)}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                changeOpen(true);
              }}>
              <Eye aria-hidden="true" />
            </button>
          </PopoverTrigger>
        </span>
      </PopoverAnchor>
      <PopoverContent ref={contentRef} className="message-link-preview" aria-label="链接预览"
        side="top" align="start" sideOffset={8} collisionPadding={16}
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onPointerEnter={() => clearTimeout(timer.current)}
        onPointerLeave={(event) => { if (event.pointerType !== "touch") scheduleClose(); }}
        onFocusCapture={() => clearTimeout(timer.current)}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) scheduleClose();
        }}>
        <LinkSiteIcon href={external} faviconUrl={faviconUrl} />
        <a href={external} target="_blank" rel="noopener noreferrer" className="message-link-preview-url"
          title={decodeUrlForDisplay(external)}>
          <span>{decodeUrlForDisplay(external)}</span>
          <ArrowUpRight aria-hidden="true" />
        </a>
        <button type="button" className="message-link-copy" onClick={() => void copy()}
          aria-label={copied ? "已复制链接" : "复制链接"} title={copied ? "已复制" : "复制链接"}>
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        </button>
        <span className="sr-only" role="status">{copied ? "链接已复制" : ""}</span>
      </PopoverContent>
    </Popover>
  );
}

import { sandboxRuntimeShimInlineTag } from "./sandbox-runtime-shim";
import { subappClientInlineTag } from "./subapp-client-shim";

export const SANDBOXED_HTML_PREVIEW_CSP = [
  "default-src 'none'",
  "img-src data: blob: https: http:",
  "media-src data: blob: https: http:",
  "font-src data: https: http:",
  "style-src 'unsafe-inline' https: http:",
  "script-src 'unsafe-inline' 'unsafe-eval' blob: https: http:",
  "worker-src blob:",
  "connect-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

const DEFAULT_PREVIEW_STYLE =
  "html,body{margin:0;min-height:100%;font-family:system-ui,sans-serif;color:#171717;background:#fff}*{box-sizing:border-box}";

function isAllowedEmbeddedUrl(value: string) {
  return value.startsWith("#") || value.startsWith("blob:") || value.startsWith("data:");
}

/** http(s) absolute or protocol-relative network URL (static assets load directly). */
function isNetworkUrl(value: string) {
  return /^(https?:)?\/\//i.test(value);
}

/** Resource URL that may load directly: embedded or network. */
function isAllowedResourceUrl(value: string) {
  return isAllowedEmbeddedUrl(value) || isNetworkUrl(value);
}

export interface SandboxedHtmlPreviewOptions {
  /**
   * Inject the browser-sandbox runtime shim (`window.__lg` + `fetch` relay).
   * The shim talks to the host bridge over postMessage (`lg:1` protocol) so
   * sandbox code can read multi-file bundle paths (`vfs.read`) and reach the
   * approval-free network relay (`net.fetch`). Network still never leaves the
   * iframe directly — `connect-src 'none'` is unchanged.
   */
  runtimeShim?: boolean
  /**
   * Inject the bidirectional subapp client SDK (`window.__lgSubapp`) for
   * subapp_mode srcDoc previews. Must be paired with runtimeShim on the bundle
   * path; on the gateway path the SDK is a same-origin static file instead.
   */
  subappClient?: boolean
}

/** Build an opaque-origin srcDoc whose executable policy is owned by the host. */
export function sandboxedHtmlPreviewDocument(
  html: string,
  options: SandboxedHtmlPreviewOptions = {},
): string {
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, "text/html");
  // Navigation / embedding / form / base markup is always stripped — the
  // sandbox never navigates the top window, never embeds third-party frames
  // and never lets the preview change the document base. External CSS links
  // (rel=stylesheet) are kept so previews can load stylesheets over the network.
  doc.querySelectorAll("iframe, object, embed, form, base").forEach((node) => node.remove());
  doc.querySelectorAll("link").forEach((node) => {
    const rel = node.getAttribute("rel")?.trim().toLowerCase();
    const href = node.getAttribute("href")?.trim();
    if (rel !== "stylesheet" || !href || !isAllowedResourceUrl(href)) {
      node.remove();
    }
  });
  doc.querySelectorAll("meta[http-equiv]").forEach((meta) => {
    const directive = meta.getAttribute("http-equiv")?.trim().toLowerCase();
    if (directive === "content-security-policy" || directive === "refresh") {
      meta.remove();
    }
  });

  doc.querySelectorAll<HTMLElement>("*").forEach((element) => {
    element.removeAttribute("srcdoc");
    for (const attributeName of ["src", "href", "xlink:href"]) {
      const value = element.getAttribute(attributeName)?.trim();
      if (!value) continue;
      // Keep embedded (data:/blob:/#) and network (http(s)) references so
      // previews can load images, fonts, media and scripts over the network;
      // every other URL scheme is dropped.
      if (!isAllowedEmbeddedUrl(value) && !isNetworkUrl(value)) {
        element.removeAttribute(attributeName);
      }
    }
  });
  doc.querySelectorAll<HTMLScriptElement>("script[src]").forEach((script) => {
    const src = script.getAttribute("src")?.trim();
    if (!src || (!isAllowedEmbeddedUrl(src) && !isNetworkUrl(src))) {
      script.removeAttribute("src");
    }
  });
  doc.querySelectorAll("style").forEach((style) => {
    style.textContent = (style.textContent ?? "")
      // Keep network @imports (https://...), drop every other one.
      .replace(/@import\s+[^;]+;?/giu, (match) => (/(?:https?:)?\/\//i.test(match) ? match : ""))
      // Keep data:/blob:/# and network url() references (external images,
      // fonts, gradients); drop every other URL so nothing hits non-http
      // schemes.
      .replace(/url\(\s*['"]?([^)'"]*)\)/giu, (match, inner: string) => {
        const value = inner.trim();
        if (/^(https?:)?\/\//i.test(value) || /^(data:|blob:|#)/i.test(value)) return match;
        return "none";
      })
      .replace(/expression\s*\(/giu, "invalid(");
  });

  const shimTag = options.runtimeShim ? sandboxRuntimeShimInlineTag() : "";
  const clientTag = options.subappClient ? subappClientInlineTag() : "";
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${SANDBOXED_HTML_PREVIEW_CSP}"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${DEFAULT_PREVIEW_STYLE}</style>${shimTag}${clientTag}${doc.head?.innerHTML ?? ""}</head><body>${doc.body?.innerHTML ?? ""}</body></html>`;
}

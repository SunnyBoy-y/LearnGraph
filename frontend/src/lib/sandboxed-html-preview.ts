export const SANDBOXED_HTML_PREVIEW_CSP = [
  "default-src 'none'",
  "img-src data: blob:",
  "media-src data: blob:",
  "font-src data:",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline' 'unsafe-eval' blob:",
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

/** Build an opaque-origin srcDoc whose executable policy is owned by the host. */
export function sandboxedHtmlPreviewDocument(html: string): string {
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, "text/html");

  doc
    .querySelectorAll("iframe, object, embed, form, base, link")
    .forEach((node) => node.remove());
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
      if (!isAllowedEmbeddedUrl(value)) {
        element.removeAttribute(attributeName);
      }
    }
  });
  doc.querySelectorAll<HTMLScriptElement>("script[src]").forEach((script) => {
    script.removeAttribute("src");
  });
  doc.querySelectorAll("style").forEach((style) => {
    style.textContent = (style.textContent ?? "")
      .replace(/@import\s+[^;]+;?/giu, "")
      // Keep data:/blob: assets and in-document references (SVG gradients,
      // filters, clip paths); drop every other URL so nothing hits the network.
      .replace(/url\(\s*['"]?(?!data:|blob:|#)[^)]*\)/giu, "none")
      .replace(/expression\s*\(/giu, "invalid(");
  });

  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${SANDBOXED_HTML_PREVIEW_CSP}"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${DEFAULT_PREVIEW_STYLE}</style>${doc.head?.innerHTML ?? ""}</head><body>${doc.body?.innerHTML ?? ""}</body></html>`;
}

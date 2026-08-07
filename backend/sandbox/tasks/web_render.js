// web_render.js — bounded in-container Chromium render fallback for the
// fixed web_fetch runner (M4).
//
// Invoked only by the fixed runner inside the isolated sandbox container:
//   node /opt/learngraph/tasks/web_render.js <url> <timeout_seconds> <out_path> [profile_root]
//
// Uses the image's Chromium-only Playwright Core runtime (Debian Chromium).
//
// Network model: Chromium cannot attach credentials to the egress
// proxy (Proxy-Authorization / proxy-URL userinfo fail with Chromium),
// and the multi-tenant egress proxy rejects any CONNECT without the approved
// policy digest. web_render therefore runs a tiny local CONNECT forwarder on
// 127.0.0.1 that relays browser tunnels to the egress proxy, attaching the
// container's LEARNGRAPH_EGRESS_POLICY_DIGEST header (the same mechanism the
// httpx fetch runner uses). The browser's only outbound path is still the
// egress proxy; the digest never leaves the container.
//
// Security properties:
//   - browser -> 127.0.0.1 forwarder -> egress proxy only (no direct egress);
//   - a non-persistent launch keeps zero browser state (no user cookies);
//   - downloads are cancelled; navigation + settle bounded; HTML capped 8 MiB;
//   - any failure exits non-zero; the caller (runner) fails the task rather
//     than falling back to arbitrary fetching.

const { chromium } = require("playwright-core");
const http = require("http");
const net = require("net");
const fs = require("fs");

const MAX_HTML_BYTES = 8 * 1024 * 1024;

function proxyFromEnv() {
  const raw =
    process.env.HTTPS_PROXY ||
    process.env.https_proxy ||
    process.env.HTTP_PROXY ||
    process.env.http_proxy;
  if (!raw) return null;
  return /^https?:\/\//i.test(raw) ? raw : "http://" + raw;
}

function egressDigest() {
  return process.env.LEARNGRAPH_EGRESS_POLICY_DIGEST || "";
}

function startLocalForwarder(upstreamUrl, digest) {
  const upstream = new URL(upstreamUrl);
  const server = http.createServer((req, res) => {
    res.writeHead(405);
    res.end("only CONNECT is supported");
  });
  server.on("connect", (req, clientSocket, head) => {
    const upstreamSocket = net.connect(Number(upstream.port), upstream.hostname, () => {
      const connectLine = `CONNECT ${req.url} HTTP/1.1\r\nHost: ${req.url}\r\n`;
      const digestHeader = digest ? `X-LearnGraph-Policy-Digest: ${digest}\r\n` : "";
      upstreamSocket.write(`${connectLine}${digestHeader}\r\n`);
      if (head && head.length) upstreamSocket.write(head);
    });
    let tunneled = false;
    let buffer = Buffer.alloc(0);
    upstreamSocket.on("data", (chunk) => {
      if (!tunneled) {
        buffer = Buffer.concat([buffer, chunk]);
        const idx = buffer.indexOf("\r\n\r\n");
        if (idx === -1) return; // wait for the full CONNECT response
        const statusLine = buffer.slice(0, idx).toString("latin1");
        buffer = buffer.slice(idx + 4);
        if (!/^HTTP\/1\.1 200/.test(statusLine)) {
          clientSocket.end();
          upstreamSocket.destroy();
          return;
        }
        tunneled = true;
        clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        if (buffer.length) clientSocket.write(buffer);
      } else {
        clientSocket.write(chunk);
      }
    });
    clientSocket.on("data", (chunk) => {
      if (tunneled) upstreamSocket.write(chunk);
    });
    clientSocket.on("end", () => upstreamSocket.end());
    upstreamSocket.on("end", () => clientSocket.end());
    clientSocket.on("error", () => upstreamSocket.destroy());
    upstreamSocket.on("error", () => clientSocket.destroy());
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.removeListener("error", reject);
      resolve({ server, port: server.address().port });
    });
  });
}

async function main() {
  const [url, timeoutSec, outPath] = process.argv.slice(2);
  if (!url || !outPath) {
    console.error("usage: web_render.js <url> <timeout_seconds> <out_path> [profile_root]");
    process.exit(2);
  }
  const proxyUrl = proxyFromEnv();
  if (!proxyUrl) {
    console.error("web_render requires the sandbox egress proxy (HTTPS_PROXY)");
    process.exit(2);
  }
  const digest = egressDigest();
  const timeoutMs = Math.max(1000, Math.min(parseInt(timeoutSec, 10) || 15, 30) * 1000);
  let forwarder = null;
  let browser = null;
  try {
    forwarder = await startLocalForwarder(proxyUrl, digest);
    browser = await chromium.launch({
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium",
      headless: true,
      chromiumSandbox: false,
      args: ["--no-sandbox", "--disable-gpu", `--proxy-server=http://127.0.0.1:${forwarder.port}`],
    });
    const page = await browser.newPage();
    page.on("download", (download) => {
      download.cancel().catch(() => {});
    });
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    // Bounded settle for client-rendered apps. domcontentloaded + a fixed wait
    // is preferred over networkidle (which can hang on long-poll/streams).
    await page.waitForTimeout(Math.min(3000, timeoutMs));
    const html = await page.content();
    const buf = Buffer.from(html, "utf8");
    if (buf.length > MAX_HTML_BYTES) {
      throw new Error("rendered HTML exceeds the byte limit");
    }
    fs.writeFileSync(outPath, buf);
    process.exit(0);
  } catch (error) {
    console.error(String(error && error.message ? error.message : error));
    process.exit(1);
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (error) {
        console.error(String(error && error.message ? error.message : error));
      }
    }
    if (forwarder) {
      forwarder.server.close();
    }
  }
}

main().catch((error) => {
  console.error(String(error && error.message ? error.message : error));
  process.exit(1);
});

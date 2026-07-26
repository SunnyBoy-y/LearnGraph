// Fixed render helper: local HTML file -> PDF or PNG via the image's Chromium.
// Usage: node /opt/learngraph/tasks/render.js pdf  <input.html> <output.pdf>
//        node /opt/learngraph/tasks/render.js png  <input.html> <output.png> [width] [height] [fullPage]
// The sandbox has no network; only file:// content is reachable by design.
const path = require("path");
const { chromium } = require("playwright-core");

const [mode, input, output, widthArg, heightArg, fullPageArg] = process.argv.slice(2);

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

if (!["pdf", "png"].includes(mode)) fail("mode must be 'pdf' or 'png'");
if (!input || !output) fail("usage: render.js <pdf|png> <input.html> <output>");

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser",
    headless: true,
    chromiumSandbox: true,
  });
  try {
    const page = await browser.newPage({
      viewport: {
        width: Math.min(3840, parseInt(widthArg, 10) || 1280),
        height: Math.min(2160, parseInt(heightArg, 10) || 720),
      },
    });
    await page.goto(`file://${path.resolve(input)}`, { waitUntil: "networkidle" });
    if (mode === "pdf") {
      await page.pdf({ path: output, format: "A4", printBackground: true });
    } else {
      await page.screenshot({ path: output, fullPage: fullPageArg !== "false" });
    }
  } finally {
    await browser.close();
  }
})().catch((error) => fail(String(error)));

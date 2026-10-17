const { chromium } = require("playwright-core");
const { browserLaunchOptions } = require("./browser-launch");

// An abandoned asynchronous operation must never become a successful smoke.
process.exitCode = 1;

(async () => {
  const browser = await chromium.launch(browserLaunchOptions());
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(30_000);
    await page.setContent("<main data-smoke='ok'>LearnGraph 沙箱</main>");
    const value = await page.getAttribute("main", "data-smoke");
    // Test rasterization and the print compositor, not only DOM creation.
    const shot = await page.screenshot({ type: "png" });
    const pdf = await page.pdf({ format: "A4", timeout: 30_000 });
    if (value !== "ok" || shot.length < 1000 || pdf.length < 1000 ||
        pdf.subarray(0, 5).toString() !== "%PDF-") {
      throw new Error("Chromium smoke produced an invalid DOM, PNG or PDF");
    }
    process.stdout.write(JSON.stringify({ arch: process.arch, png_bytes: shot.length, pdf_bytes: pdf.length }) + "\n");
  } finally {
    await browser.close();
  }
  process.exitCode = 0;
})().catch((error) => {
  process.stderr.write(String(error));
  process.exit(1);
});

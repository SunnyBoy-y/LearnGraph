const { chromium } = require("playwright-core");

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell",
    headless: true,
    chromiumSandbox: false,
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  await page.setContent("<main data-smoke='ok'>LearnGraph 沙箱</main>");
  const value = await page.getAttribute("main", "data-smoke");
  // Rasterization must work too (screenshot/PDF path uses the viz process);
  // a DOM-only check once hid a missing-SwiftShader defect.
  const shot = await page.screenshot({ type: "png" });
  await browser.close();
  if (value !== "ok" || !shot || shot.length < 1000) process.exit(2);
})().catch((error) => {
  process.stderr.write(String(error));
  process.exit(1);
});

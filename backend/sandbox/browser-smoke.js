const { chromium } = require("playwright-core");

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium-browser",
    headless: true,
    chromiumSandbox: true,
  });
  const page = await browser.newPage();
  await page.setContent("<main data-smoke='ok'>LearnGraph</main>");
  const value = await page.getAttribute("main", "data-smoke");
  await browser.close();
  if (value !== "ok") process.exit(2);
})().catch((error) => {
  process.stderr.write(String(error));
  process.exit(1);
});

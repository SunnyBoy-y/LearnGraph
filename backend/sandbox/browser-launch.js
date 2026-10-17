// Shared by smoke, local rendering and the reviewed web-render fallback.
// QEMU linux-user cannot reliably fork Chromium's ARM64 zygote. Exec-based
// child creation keeps the browser multi-process and also works on native ARM.
function browserLaunchOptions(extraArgs = []) {
  return {
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "/usr/bin/chromium",
    headless: true,
    chromiumSandbox: false,
    args: [
      "--no-sandbox",
      "--disable-gpu",
      ...(process.arch === "arm64" ? ["--no-zygote"] : []),
      ...extraArgs,
    ],
  };
}

module.exports = { browserLaunchOptions };

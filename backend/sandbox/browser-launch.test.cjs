const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function optionsFor(arch, extraArgs = []) {
  const context = { module: { exports: {} }, process: { arch, env: {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "browser-launch.js"), "utf8"), context);
  return context.module.exports.browserLaunchOptions(extraArgs);
}

test("ARM64 uses exec-based child creation and keeps multiple processes", () => {
  const options = optionsFor("arm64");
  assert.ok(options.args.includes("--no-zygote"));
  assert.ok(!options.args.includes("--single-process"));
  assert.equal(options.chromiumSandbox, false);
});

test("AMD64 keeps the native launch configuration", () => {
  assert.ok(!optionsFor("x64").args.includes("--no-zygote"));
});

test("web-render proxy remains in the launch arguments", () => {
  assert.ok(optionsFor("arm64", ["--proxy-server=http://127.0.0.1:1234"])
    .args.includes("--proxy-server=http://127.0.0.1:1234"));
});

test("a prematurely abandoned smoke cannot report success", () => {
  const context = {
    process: { exitCode: 0, stdout: { write() {} }, stderr: { write() {} }, exit() {} },
    Buffer,
    require(name) {
      if (name === "./browser-launch") return { browserLaunchOptions: () => ({}) };
      return { chromium: { launch: () => new Promise(() => {}) } };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "browser-smoke.js"), "utf8"), context);
  assert.equal(context.process.exitCode, 1);
});

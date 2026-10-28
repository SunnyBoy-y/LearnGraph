import { afterEach, expect, it, vi } from "vitest";
import { resolveVoiceEnvironment } from "./voice-client-environment";
import { readFileSync } from "node:fs";

it("permits only the selected IP service in the application connection policy", () => {
  const html = readFileSync("index.html", "utf8");
  const policy = html.match(/connect-src[^;]+/)?.[0] || "";
  expect(policy).toContain("https://get.geojs.io");
  expect(policy).not.toContain("https: ");
});

afterEach(() => vi.unstubAllGlobals());

it("uses client-origin IP coordinates and omits raw IP and credentials", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ success: true, city: "Paris", latitude: "48.8566", longitude: "2.3522", timezone: "Europe/Paris", ip: "unneeded" }) });
  vi.stubGlobal("fetch", fetcher);
  const value = await resolveVoiceEnvironment();
  expect(value.location).toMatchObject({ city: "Paris", latitude: 48.86, longitude: 2.35, source: "client_ip" });
  expect(value.location).not.toHaveProperty("ip");
  expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials: "omit", referrerPolicy: "no-referrer" });
});

it.each([{}, { success: true, city: "x", latitude: 999, longitude: 1, timezone: "UTC" }])("does not invent a location on invalid response", async (data) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => data }));
  expect(await resolveVoiceEnvironment()).toMatchObject({ location_status: "unavailable" });
});

it("cancels lookup with its voice connection", async () => {
  const controller = new AbortController();
  vi.stubGlobal("fetch", vi.fn((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new Error("aborted"))))));
  const pending = resolveVoiceEnvironment(controller.signal);
  controller.abort();
  expect(await pending).toMatchObject({ location_status: "unavailable" });
});

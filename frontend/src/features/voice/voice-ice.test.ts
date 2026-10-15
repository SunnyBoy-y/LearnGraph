import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getVoiceIceServers } from "@/api/voice";
import { loadIceServers } from "./voice-ice";

vi.mock("@/api/voice", () => ({ getVoiceIceServers: vi.fn() }));
const fetchIce = vi.mocked(getVoiceIceServers);

beforeEach(() => { fetchIce.mockReset(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("per-connection ICE configuration", () => {
  it("uses a changed manual STUN pool on the very next call", async () => {
    fetchIce.mockResolvedValueOnce({ iceServers: [{ urls: ["stun:first.example:3478"] }], source: "manual" });
    fetchIce.mockResolvedValueOnce({ iceServers: [{ urls: ["stun:second.example:3478"] }], source: "manual" });
    expect((await loadIceServers())[0].urls).toEqual(["stun:first.example:3478"]);
    expect((await loadIceServers())[0].urls).toEqual(["stun:second.example:3478"]);
    expect(fetchIce).toHaveBeenCalledTimes(2);
  });

  it("does not revive prior TURN or invent public STUN after disable", async () => {
    fetchIce.mockResolvedValueOnce({ iceServers: [{ urls: "turn:relay.example:3478", username: "u", credential: "c" }] });
    fetchIce.mockResolvedValueOnce({ iceServers: [], source: "none" });
    expect(await loadIceServers()).toHaveLength(1);
    expect(await loadIceServers()).toEqual([]);
  });

  it("retries after both HTTP failure and a successful empty response", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    fetchIce.mockRejectedValueOnce(new Error("offline"));
    fetchIce.mockResolvedValueOnce({ iceServers: [], source: "none" });
    fetchIce.mockResolvedValueOnce({ iceServers: [{ urls: "stun:recovered.example:3478" }], source: "fallback" });
    expect(await loadIceServers()).toEqual([]);
    expect(await loadIceServers()).toEqual([]);
    expect(await loadIceServers()).toHaveLength(1);
    expect(fetchIce).toHaveBeenCalledTimes(3);
  });

  it("preserves all supplied URLs and only real TURN credentials", async () => {
    fetchIce.mockResolvedValueOnce({ iceServers: [
      { urls: [], username: null, credential: null },
      { urls: "" },
      { urls: ["stun:a.example:3478", "stun:b.example:3478"], username: null, credential: null },
      { urls: "turn:relay.example:3478", username: "u", credential: "c" },
    ] });
    const servers = await loadIceServers();
    expect(servers).toHaveLength(2);
    expect(servers[0].credential).toBeUndefined();
    expect(servers[0].urls).toEqual(["stun:a.example:3478", "stun:b.example:3478"]);
    expect(servers[1].credential).toBe("c");
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { packVoiceAudio, selectVoiceSocketCodec, VoiceSocketTransport, voiceSocketUrl } from "./voice-wss";

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  url: string;
  constructor(url: string) { this.url = url; Socket.instances.push(this); }
  message(value: unknown) { this.onmessage?.({ data: value instanceof ArrayBuffer ? value : JSON.stringify(value) }); }
}

function audioContext() {
  const tracks = [{ stop: vi.fn() }];
  const stream = { getTracks: () => tracks } as unknown as MediaStream;
  const nodes: Array<{ stop: ReturnType<typeof vi.fn>; start: ReturnType<typeof vi.fn>; onended: (() => void) | null }> = [];
  const context = {
    currentTime: 0, state: "running", destination: {},
    createMediaStreamDestination: () => ({ stream, disconnect: vi.fn() }),
    createBuffer: (_: number, samples: number, rate: number) => ({ copyToChannel: vi.fn(), duration: samples / rate }),
    createBufferSource: () => {
      const node = { stop: vi.fn(), start: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), onended: null };
      nodes.push(node);
      return node;
    },
    audioWorklet: { addModule: vi.fn().mockResolvedValue(undefined) },
    createMediaStreamSource: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
    resume: vi.fn().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
  };
  return { context: context as unknown as AudioContext, nodes, tracks };
}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

beforeEach(() => {
  vi.useFakeTimers();
  Socket.instances = [];
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("AudioEncoder", undefined);
  vi.stubGlobal("AudioDecoder", undefined);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

async function connected() {
  const audio = audioContext();
  const controller = new AbortController();
  const onMessage = vi.fn();
  const onClose = vi.fn();
  const transport = new VoiceSocketTransport(audio.context, {
    url: "/api/v1/voice/sessions/test/api/audio",
    token: "test-token", workspaceId: "test-workspace", reconnecting: true,
    signal: controller.signal, onMessage, onClose,
  });
  const connection = transport.connect();
  await flush();
  const socket = Socket.instances[0];
  socket.onopen?.();
  socket.message({ type: "voice.ready", codec: "pcm", version: 1 });
  await connection;
  return { ...audio, transport, socket, controller, onMessage, onClose };
}

describe("duplex WSS audio transport", () => {
  it("upgrades HTTPS and never exposes the token in a URL", () => {
    expect(voiceSocketUrl("/api/audio", "https://example.com/chat")).toBe("wss://example.com/api/audio");
    expect(() => voiceSocketUrl("wss://other.example/api/audio", "https://example.com")).toThrow("origin");
    expect(() => voiceSocketUrl("ws://example.com/api/audio", "https://example.com")).toThrow("WSS");
  });
  it("falls back to PCM when browser Opus support is unavailable", async () => {
    expect(await selectVoiceSocketCodec()).toBe("pcm");
  });
  it("authenticates in the first frame and keeps existing RTVI envelopes", async () => {
    const { transport, socket, onMessage } = await connected();
    expect(socket.url).not.toContain("test-token");
    expect(JSON.parse(socket.send.mock.calls[0][0])).toMatchObject({ token: "test-token", workspace_id: "test-workspace", reconnecting: true });
    const event = { label: "rtvi-ai", type: "server-message", data: { event: { type: "user.final" } } };
    socket.message(event);
    expect(onMessage).toHaveBeenCalledWith(event);
    expect(transport.send(JSON.stringify({ label: "rtvi-ai", type: "client-ready" }))).toBe(true);
    transport.close();
  });
  it("clears playback immediately and fences stale generations on interruption", async () => {
    const { transport, socket, nodes } = await connected();
    const pcm = new Uint8Array(960);
    socket.message(packVoiceAudio("pcm", 0, 0, 0, pcm));
    expect(nodes).toHaveLength(1);
    transport.clearPlayback(true);
    expect(nodes[0].stop).toHaveBeenCalled();
    socket.message(packVoiceAudio("pcm", 1, 0, 0, pcm));
    expect(nodes).toHaveLength(1);
    socket.message({ type: "voice.clear", generation: 1 });
    socket.message(packVoiceAudio("pcm", 2, 0, 0, pcm));
    expect(nodes).toHaveLength(1);
    socket.message(packVoiceAudio("pcm", 3, 0, 1, pcm));
    expect(nodes).toHaveLength(2);
    transport.close();
  });
  it("muting the microphone does not mute the assistant", async () => {
    const { transport, socket, nodes } = await connected();
    transport.setMuted(true);
    socket.message(packVoiceAudio("pcm", 0, 0, 0, new Uint8Array(960)));
    expect(nodes).toHaveLength(1);
    transport.close();
  });
  it("detects a dead half-open socket and requests one reconnect", async () => {
    const { transport, onClose } = await connected();
    await vi.advanceTimersByTimeAsync(45_000);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(transport.isOpen).toBe(false);
    transport.close();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it("tears down without requesting reconnect after an intentional abort", async () => {
    const { transport, controller, onClose, context, tracks } = await connected();
    controller.abort();
    expect(transport.isOpen).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
    expect(context.close).toHaveBeenCalledTimes(1);
    expect(tracks[0].stop).toHaveBeenCalledTimes(1);
  });
  it("rejects oversized or malformed downstream audio", async () => {
    const { socket, onClose } = await connected();
    socket.message(new ArrayBuffer(17_000));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it("surfaces a pending autoplay unlock instead of hanging connection setup", async () => {
    const { transport, context } = await connected();
    vi.mocked(context.resume).mockReturnValue(new Promise(() => {}));
    const resumed = transport.resume();
    await vi.advanceTimersByTimeAsync(1000);
    expect(await resumed).toBe(false);
    transport.close();
  });
  it("captures 20ms frames and reconnects instead of accumulating stale audio", async () => {
    const { transport, socket, onClose } = await connected();
    let capture!: { port: { onmessage: (event: { data: { audio: Float32Array; capturedAt: number } }) => void } };
    vi.stubGlobal("AudioWorkletNode", class {
      port = { onmessage: null, close: vi.fn() };
      connect = vi.fn(); disconnect = vi.fn();
      constructor() { capture = this as unknown as typeof capture; }
    });
    await transport.startCapture({} as MediaStream);
    capture.port.onmessage({ data: { audio: new Float32Array(960), capturedAt: 0 } });
    const packet = socket.send.mock.calls.at(-1)![0] as ArrayBuffer;
    expect(packet.byteLength).toBe(656);
    expect(new DataView(packet).getUint8(3)).toBe(2);
    transport.setMuted(true);
    capture.port.onmessage({ data: { audio: new Float32Array(960).fill(0.5), capturedAt: 0 } });
    const silent = socket.send.mock.calls.at(-1)![0] as ArrayBuffer;
    expect(new Uint8Array(silent, 16).every(byte => byte === 0)).toBe(true);
    expect(socket.send.mock.calls.filter(([value]) => value instanceof ArrayBuffer)).toHaveLength(2);
    socket.bufferedAmount = 9000;
    capture.port.onmessage({ data: { audio: new Float32Array(960), capturedAt: 0 } });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

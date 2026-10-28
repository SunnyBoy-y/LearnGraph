/** Duplex media transport. Business events remain the existing RTVI protocol. */
import captureWorkletUrl from "./voice-wss-worklet.js?worker&url";
type Codec = "opus" | "pcm";
type AudioConfig = { codec: string; sampleRate: number; numberOfChannels: number; bitrate?: number };
interface EncodedAudio {
  byteLength: number;
  timestamp: number;
  copyTo(destination: Uint8Array): void;
}
interface DecodedAudio {
  numberOfFrames: number;
  numberOfChannels: number;
  sampleRate: number;
  copyTo(destination: Float32Array, options: { planeIndex: number; format: string }): void;
  close(): void;
}
interface AudioCoder {
  state: string;
  close(): void;
}
interface Encoder extends AudioCoder {
  encodeQueueSize: number;
  configure(config: AudioConfig): void;
  encode(data: DecodedAudio): void;
}
interface Decoder extends AudioCoder {
  decodeQueueSize: number;
  configure(config: AudioConfig): void;
  decode(chunk: EncodedAudio): void;
}
interface AudioCodecs {
  AudioEncoder?: {
    new(init: { output: (chunk: EncodedAudio) => void; error: (error: Error) => void }): Encoder;
    isConfigSupported(config: AudioConfig): Promise<{ supported?: boolean }>;
  };
  AudioDecoder?: {
    new(init: { output: (data: DecodedAudio) => void; error: (error: Error) => void }): Decoder;
    isConfigSupported(config: AudioConfig): Promise<{ supported?: boolean }>;
  };
  AudioData?: new(init: { format: string; sampleRate: number; numberOfFrames: number; numberOfChannels: number; timestamp: number; data: Float32Array }) => DecodedAudio;
  EncodedAudioChunk?: new(init: { type: string; timestamp: number; data: Uint8Array }) => EncodedAudio;
}
const codecs = globalThis as unknown as AudioCodecs;
const opusConfig: AudioConfig = { codec: "opus", sampleRate: 48_000, numberOfChannels: 1 };

export async function selectVoiceSocketCodec(): Promise<Codec> {
  if (!codecs.AudioEncoder || !codecs.AudioDecoder || !codecs.AudioData || !codecs.EncodedAudioChunk) return "pcm";
  try {
    const [encoder, decoder] = await Promise.all([
      codecs.AudioEncoder.isConfigSupported({ ...opusConfig, bitrate: 32_000 }),
      codecs.AudioDecoder.isConfigSupported(opusConfig),
    ]);
    return encoder.supported && decoder.supported ? "opus" : "pcm";
  } catch {
    return "pcm";
  }
}

export function voiceSocketUrl(path: string, pageUrl = window.location.href): string {
  const url = new URL(path, pageUrl);
  if (!["http:", "https:", "ws:", "wss:"].includes(url.protocol)) throw new Error("Invalid voice WebSocket URL");
  url.protocol = url.protocol === "https:" || url.protocol === "wss:" ? "wss:" : "ws:";
  if (new URL(pageUrl).protocol === "https:" && url.protocol !== "wss:") throw new Error("Voice audio requires WSS on HTTPS");
  // The login token must never be sent to a server supplied through an
  // untrusted session response. This transport uses the API's same origin.
  const page = new URL(pageUrl);
  if (url.host !== page.host) throw new Error("Voice WebSocket must use the page origin");
  return url.href;
}

export function packVoiceAudio(codec: Codec, sequence: number, timestamp: number, generation: number, audio: Uint8Array): ArrayBuffer {
  const packet = new ArrayBuffer(16 + audio.byteLength + (codec === "opus" ? 2 : 0));
  const view = new DataView(packet);
  view.setUint16(0, 0x4c47);
  view.setUint8(2, 1);
  view.setUint8(3, codec === "opus" ? 1 : 2);
  view.setUint32(4, sequence);
  view.setUint32(8, timestamp);
  view.setUint32(12, generation);
  if (codec === "opus") view.setUint16(16, audio.byteLength);
  new Uint8Array(packet, codec === "opus" ? 18 : 16).set(audio);
  return packet;
}

export interface VoiceSocketOptions {
  url: string;
  token: string;
  workspaceId: string;
  reconnecting: boolean;
  signal: AbortSignal;
  onMessage: (message: unknown) => void;
  onClose: () => void;
}

export class VoiceSocketError extends Error {
  readonly code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}

export class VoiceSocketTransport {
  readonly playbackStream: MediaStream;
  private readonly destination: MediaStreamAudioDestinationNode;
  private socket: WebSocket | null = null;
  private capture: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private encoder: Encoder | null = null;
  private decoder: Decoder | null = null;
  private nodes = new Set<AudioBufferSourceNode>();
  private codec: Codec = "pcm";
  private closed = false;
  private ready = false;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private lastPong = 0;
  private pongs = 0;
  private sequence = 0;
  private generation = 0;
  private decodeEpoch = 0;
  private nextPlaybackAt = 0;
  private abort: (() => void) | null = null;
  private captureClock: Array<{ timestamp: number; silent: boolean }> = [];
  private captureSequence = 0;
  private muted = false;
  private awaitingClear = false;
  private receivedFrames = 0;
  private readonly context: AudioContext;
  private readonly options: VoiceSocketOptions;

  constructor(context: AudioContext, options: VoiceSocketOptions) {
    this.context = context;
    this.options = options;
    this.destination = context.createMediaStreamDestination();
    this.playbackStream = this.destination.stream;
  }

  get isOpen(): boolean {
    return !this.closed && this.ready && this.socket?.readyState === WebSocket.OPEN;
  }

  get stats() {
    return { codec: this.codec, sentFrames: this.sequence, receivedFrames: this.receivedFrames, bufferedBytes: this.socket?.bufferedAmount ?? 0, pongs: this.pongs };
  }

  async connect(): Promise<void> {
    this.codec = await selectVoiceSocketCodec();
    if (this.closed || this.options.signal.aborted) throw new DOMException("Aborted", "AbortError");
    if (this.codec === "opus") this.createCodecs();
    const socket = new WebSocket(voiceSocketUrl(this.options.url));
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => fail(new Error("WSS 语音连接超时")), 15_000);
      const fail = (error: Error) => {
        window.clearTimeout(timer);
        reject(error);
        this.close();
      };
      this.abort = () => fail(new DOMException("Aborted", "AbortError"));
      this.options.signal.addEventListener("abort", this.abort, { once: true });
      socket.onopen = () => {
        if (this.closed) return;
        socket.send(JSON.stringify({
          type: "voice.start", version: 1, token: this.options.token,
          workspace_id: this.options.workspaceId, codec: this.codec,
          reconnecting: this.options.reconnecting,
        }));
      };
      socket.onmessage = (event: MessageEvent) => {
        if (this.closed) return;
        try {
          if (typeof event.data !== "string") {
            if (this.ready) this.receiveAudio(event.data as ArrayBuffer);
            return;
          }
          const message = JSON.parse(event.data) as Record<string, unknown>;
          if (message.type === "voice.ready") {
            if (this.ready || message.version !== 1 || message.codec !== this.codec) throw new Error("WSS 语音协商失败");
            this.ready = true;
            this.lastPong = performance.now();
            window.clearTimeout(timer);
            this.heartbeat = setInterval(() => {
              if (!this.isOpen) return;
              if (performance.now() - this.lastPong > 40_000) { this.fail(); return; }
              socket.send('{"type":"voice.ping"}');
            }, 15_000);
            resolve();
          } else if (message.type === "voice.pong") {
            this.lastPong = performance.now();
            this.pongs++;
          } else if (message.type === "voice.clear") {
            this.generation = Number(message.generation);
            this.awaitingClear = false;
            this.clearPlayback();
            this.resetDecoder();
          } else if (message.type === "voice.error") {
            const error = new VoiceSocketError(String(message.code ?? "voice_runtime_failed"), String(message.message ?? "WSS 语音服务异常"));
            if (!this.ready) fail(error);
            else this.fail();
          } else if (this.ready) {
            this.options.onMessage(message);
          }
        } catch (error) {
          if (!this.ready) fail(error instanceof Error ? error : new Error("Invalid WSS response"));
          else this.fail();
        }
      };
      socket.onerror = () => {
        if (!this.ready) fail(new Error("WSS 语音连接失败，请检查代理是否允许 WebSocket"));
        else this.fail();
      };
      socket.onclose = () => {
        if (!this.ready) fail(new Error("WSS 语音连接已关闭"));
        else this.fail();
      };
    });
  }

  private createCodecs(): void {
    this.encoder = new codecs.AudioEncoder!({
      output: (chunk) => {
        if (this.closed) return;
        const capture = this.captureClock.shift();
        if (capture === undefined) return;
        const bytes = new Uint8Array(chunk.byteLength);
        chunk.copyTo(bytes);
        this.sendAudio(bytes, capture.timestamp, capture.silent);
      },
      error: () => this.fail(),
    });
    this.encoder.configure({ ...opusConfig, bitrate: 32_000 });
    this.resetDecoder();
  }

  private resetDecoder(): void {
    if (this.codec !== "opus") return;
    if (this.decoder?.state !== "closed") this.decoder?.close();
    const epoch = ++this.decodeEpoch;
    this.decoder = new codecs.AudioDecoder!({
      output: (audio) => {
        try {
          if (this.closed || epoch !== this.decodeEpoch || this.awaitingClear) return;
          const samples = new Float32Array(audio.numberOfFrames);
          audio.copyTo(samples, { planeIndex: 0, format: "f32-planar" });
          this.play(samples, audio.sampleRate);
        } finally {
          audio.close();
        }
      },
      error: () => this.fail(),
    });
    this.decoder.configure(opusConfig);
  }

  async startCapture(stream: MediaStream): Promise<void> {
    if (!this.isOpen) throw new Error("Voice WebSocket is not ready");
    await this.context.audioWorklet.addModule(captureWorkletUrl);
    if (!this.isOpen) throw new DOMException("Aborted", "AbortError");
    this.source = this.context.createMediaStreamSource(stream);
    this.capture = new AudioWorkletNode(this.context, "learngraph-voice-capture");
    this.capture.port.onmessage = (event: MessageEvent<{ audio: Float32Array; capturedAt: number }>) => {
      if (!this.isOpen) return;
      if ((this.encoder?.encodeQueueSize ?? 0) > 4 || this.captureClock.length > 8) { this.fail(); return; }
      // Like a disabled WebRTC track, continue silence so server VAD/ASR can
      // finish the current turn when the microphone is muted mid-sentence.
      const input = this.muted ? new Float32Array(event.data.audio.length) : event.data.audio;
      const age = Math.max(0, this.context.currentTime * 1000 - event.data.capturedAt);
      if (age > 200) return;
      const rate = this.codec === "opus" ? 48_000 : 16_000;
      const samples = new Float32Array(rate / 50);
      for (let i = 0; i < samples.length; i++) {
        const at = i * input.length / samples.length;
        const left = Math.floor(at);
        const right = Math.min(left + 1, input.length - 1);
        samples[i] = input[left] + (input[right] - input[left]) * (at - left);
      }
      const timestamp = (performance.now() - age) >>> 0;
      if (this.codec === "opus" && this.encoder) {
        const audioTimestamp = this.captureSequence++ * 20_000;
        this.captureClock.push({ timestamp, silent: this.muted });
        const data = new codecs.AudioData!({
          format: "f32-planar", sampleRate: rate, numberOfFrames: samples.length,
          numberOfChannels: 1, timestamp: audioTimestamp, data: samples,
        });
        try { this.encoder.encode(data); } finally { data.close(); }
      } else {
        const pcm = new ArrayBuffer(samples.length * 2);
        const view = new DataView(pcm);
        for (let i = 0; i < samples.length; i++) {
          const sample = Math.max(-1, Math.min(1, samples[i]));
          view.setInt16(i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
        }
        this.sendAudio(new Uint8Array(pcm), timestamp, this.muted);
      }
    };
    this.source.connect(this.capture);
    this.capture.connect(this.context.destination);
  }

  private sendAudio(audio: Uint8Array, timestamp: number, silent: boolean): void {
    if (!this.isOpen || (this.muted && !silent)) return;
    const socket = this.socket!;
    // Do not accumulate a delayed conversation in the TCP send queue.
    if (socket.bufferedAmount > (this.codec === "opus" ? 2048 : 8192)) { this.fail(); return; }
    socket.send(packVoiceAudio(this.codec, this.sequence++, timestamp, 0, audio));
  }

  private receiveAudio(buffer: ArrayBuffer): void {
    if (buffer.byteLength <= 16 || buffer.byteLength > 16_384) throw new Error("Invalid voice audio size");
    const view = new DataView(buffer);
    if (view.getUint16(0) !== 0x4c47 || view.getUint8(2) !== 1 || view.getUint8(3) !== (this.codec === "opus" ? 1 : 2)) throw new Error("Invalid voice audio header");
    if (view.getUint32(12) !== this.generation || this.awaitingClear) return;
    this.receivedFrames++;
    if (this.codec === "opus" && this.decoder) {
      if (this.decoder.decodeQueueSize > 8) { this.clearPlayback(); this.resetDecoder(); return; }
      for (let offset = 16; offset < buffer.byteLength;) {
        if (offset + 2 > buffer.byteLength) throw new Error("Invalid Opus framing");
        const size = view.getUint16(offset);
        offset += 2;
        if (!size || size > 1275 || offset + size > buffer.byteLength) throw new Error("Invalid Opus packet");
        this.decoder.decode(new codecs.EncodedAudioChunk!({
          type: "key", timestamp: view.getUint32(4) * 20_000,
          data: new Uint8Array(buffer, offset, size),
        }));
        offset += size;
      }
    } else {
      if ((buffer.byteLength - 16) % 2) throw new Error("Invalid PCM framing");
      const audio = new Float32Array((buffer.byteLength - 16) / 2);
      for (let i = 0; i < audio.length; i++) audio[i] = view.getInt16(16 + i * 2, true) / 32768;
      this.play(audio, 24_000);
    }
  }

  private play(samples: Float32Array, rate: number): void {
    if (this.closed) return;
    const now = this.context.currentTime;
    if (this.nextPlaybackAt - now > 0.3) this.clearPlayback();
    const audio = this.context.createBuffer(1, samples.length, rate);
    audio.copyToChannel(new Float32Array(samples), 0);
    const node = this.context.createBufferSource();
    node.buffer = audio;
    node.connect(this.destination);
    node.onended = () => { this.nodes.delete(node); node.disconnect(); };
    this.nodes.add(node);
    const when = Math.max(now + 0.04, this.nextPlaybackAt);
    node.start(when);
    this.nextPlaybackAt = when + audio.duration;
  }

  send(message: string): boolean {
    if (!this.isOpen || message.length > 16_384) return false;
    this.socket!.send(message);
    return true;
  }

  setMuted(muted: boolean): void { this.muted = muted; }

  clearPlayback(waitForServer = false): void {
    if (waitForServer) this.awaitingClear = true;
    for (const node of this.nodes) { node.onended = null; node.stop(); node.disconnect(); }
    this.nodes.clear();
    this.nextPlaybackAt = 0;
  }

  async resume(): Promise<boolean> {
    // Some browsers leave resume() pending until another user gesture.
    // Let the existing playback-unlock UI surface instead of hanging connect().
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => resolve(false), 1000);
      this.context.resume().then(
        () => { window.clearTimeout(timer); resolve(this.context.state === "running"); },
        () => { window.clearTimeout(timer); resolve(false); },
      );
    });
  }

  private fail(): void {
    if (this.closed) return;
    this.close();
    this.options.onClose();
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.ready = false;
    if (this.abort) this.options.signal.removeEventListener("abort", this.abort);
    if (this.heartbeat !== null) clearInterval(this.heartbeat);
    this.capture?.port.close();
    this.capture?.disconnect();
    this.source?.disconnect();
    for (const coder of [this.encoder, this.decoder]) if (coder && coder.state !== "closed") coder.close();
    this.captureClock.length = 0;
    this.clearPlayback();
    this.destination.disconnect();
    this.playbackStream.getTracks().forEach((track) => track.stop());
    if (this.socket) {
      this.socket.onopen = this.socket.onmessage = this.socket.onerror = this.socket.onclose = null;
      this.socket.close();
    }
    void this.context.close();
  }
}

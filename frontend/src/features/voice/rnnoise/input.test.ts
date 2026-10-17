import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createVoiceNoiseInput, type VoiceNoiseStatus } from './input';

const result = { meanMs: 0.2, p95Ms: 0.4, maxMs: 0.8, frames: 160, memoryBytes: 16_777_216 };
let measured = result;
let raw: MediaStream;
let track: { applyConstraints: ReturnType<typeof vi.fn> };
let outputTrack: { stop: ReturnType<typeof vi.fn>; enabled: boolean };
let reportedNs: boolean | undefined;
class BackgroundWorker {
  static instances: BackgroundWorker[] = [];
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  terminate = vi.fn();
  constructor(readonly url?: URL) { BackgroundWorker.instances.push(this); }
  postMessage(data?: {type?:string}) {
    if (this.url?.pathname.includes('dsp.worker')) {
      if(data?.type==='start')queueMicrotask(()=>this.onmessage?.({data:{type:'ready'}}));
    } else queueMicrotask(() => this.onmessage?.({ data: measured }));
  }
}
class Worklet {
  static instances: Worklet[] = [];
  onprocessorerror: (() => void) | null = null;
  port = { onmessage: null as ((event: { data: unknown }) => void) | null,
    postMessage: vi.fn(), close: vi.fn() };
  disconnect = vi.fn();
  connect = vi.fn().mockReturnThis();
  constructor() {
    Worklet.instances.push(this);
    queueMicrotask(() => this.port.onmessage?.({ data: { type: 'ready' } }));
  }
}
class Context {
  static instances: Context[] = [];
  sampleRate = 48000;
  state = 'running';
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  source = { connect: vi.fn().mockImplementation((node: unknown) => node), disconnect: vi.fn() };
  destination = { stream: { getTracks: () => [outputTrack] } as unknown as MediaStream };
  close = vi.fn().mockImplementation(() => { this.state = 'closed'; return Promise.resolve(); });
  resume = vi.fn().mockResolvedValue(undefined);
  constructor() { Context.instances.push(this); }
  createMediaStreamSource() { return this.source; }
  createMediaStreamDestination() { return this.destination; }
}
beforeEach(() => {
  measured = result;
  BackgroundWorker.instances = []; Worklet.instances = []; Context.instances = [];
  track = { applyConstraints: vi.fn().mockResolvedValue(undefined) };
  reportedNs = undefined;
  Object.assign(track, { getSettings: () => ({ noiseSuppression: reportedNs }) });
  outputTrack = { stop: vi.fn(), enabled: false };
  raw = { getAudioTracks: () => [track] } as unknown as MediaStream;
  vi.stubGlobal('Worker', BackgroundWorker); vi.stubGlobal('AudioWorkletNode', Worklet);
  vi.stubGlobal('AudioContext', Context);
});
afterEach(() => vi.unstubAllGlobals());

describe('RNNoise capture graph', () => {
  it('sends only the processed stream, replaces browser NS and releases its resources', async () => {
    const status: VoiceNoiseStatus[] = [];
    const input = await createVoiceNoiseInput(raw, { onStatus: next => status.push(next) });
    expect(input.stream).toBe(Context.instances[0].destination.stream);
    expect(input.stream).not.toBe(raw);
    expect(track.applyConstraints).toHaveBeenCalledWith({ noiseSuppression: false });
    expect(status.at(-1)?.mode).toBe('rnnoise');
    expect(BackgroundWorker.instances[0].terminate).toHaveBeenCalledOnce();
    input.close(); input.close();
    expect(outputTrack.stop).toHaveBeenCalledOnce();
    expect(Context.instances[0].close).toHaveBeenCalledOnce();
  });
  it('keeps browser NS and avoids building a graph on a slow CPU', async () => {
    measured = { ...result, p95Ms: 9 };
    const input = await createVoiceNoiseInput(raw);
    expect(input.stream).toBe(raw);
    expect(track.applyConstraints).toHaveBeenCalledWith({ noiseSuppression: true });
    expect(Context.instances).toHaveLength(0);
    input.close();
  });
  it('recovers a failed processor through the same output track without unmuting it', async () => {
    const statuses: VoiceNoiseStatus[] = [];
    const input = await createVoiceNoiseInput(raw, { onStatus: status => statuses.push(status) });
    Worklet.instances[0].onprocessorerror?.();
    await Promise.resolve(); await Promise.resolve();
    expect(Context.instances[0].source.connect).toHaveBeenLastCalledWith(Context.instances[0].destination);
    expect(outputTrack.enabled).toBe(false);
    expect(statuses.at(-1)?.mode).toBe('browser');
    expect(track.applyConstraints).toHaveBeenLastCalledWith({ noiseSuppression: true });
    input.close();
  });
  it('reports actual browser NS when the device ignores the disable request', async () => {
    reportedNs = true;
    const onStatus = vi.fn();
    const input = await createVoiceNoiseInput(raw, { onStatus });
    expect(onStatus.mock.calls.at(-1)?.[0]).toMatchObject({ mode: 'rnnoise', browserNsEnabled: true });
    input.close();
  });
  it('continues RNNoise when the browser cannot toggle capture NS', async () => {
    track.applyConstraints.mockRejectedValue(new Error('Cannot satisfy constraints'));
    const onStatus = vi.fn();
    const input = await createVoiceNoiseInput(raw, { onStatus });
    expect(input.stream).not.toBe(raw);
    expect(onStatus.mock.calls.at(-1)?.[0]).toMatchObject({ mode: 'rnnoise', browserNsEnabled: null });
    input.close();
  });
  it('keeps one recurrent stream in a DSP Worker and cleans it up', async () => {
    measured={...result,meanMs:2.8,p95Ms:4};
    const onStatus=vi.fn();
    const input=await createVoiceNoiseInput(raw,{onStatus});
    expect(onStatus.mock.calls.at(-1)?.[0]).toMatchObject({mode:'rnnoise',execution:'worker',bufferMs:20});
    expect(BackgroundWorker.instances).toHaveLength(3);
    input.close();
    expect(BackgroundWorker.instances[2].terminate).toHaveBeenCalledOnce();
  });
  it('delivers model speech evidence only while the RNNoise graph owns capture',async()=>{
    const onSpeech=vi.fn();
    const input=await createVoiceNoiseInput(raw,{onSpeech});
    const evidence={type:'speech',probability:.95,rms:.02,durationMs:10};
    Worklet.instances[0].port.onmessage?.({data:evidence});
    expect(onSpeech).toHaveBeenCalledWith(evidence);
    Worklet.instances[0].onprocessorerror?.();
    await Promise.resolve();await Promise.resolve();
    Worklet.instances[0].port.onmessage?.({data:evidence});
    expect(onSpeech).toHaveBeenCalledTimes(1);
    input.close();
    Worklet.instances[0].port.onmessage?.({data:evidence});
    expect(onSpeech).toHaveBeenCalledTimes(1);
  });
  it('aborts calibration and never publishes into a replacement session', async () => {
    const controller = new AbortController();
    const onStatus = vi.fn();
    const promise = createVoiceNoiseInput(raw, { signal: controller.signal, onStatus });
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
    expect(BackgroundWorker.instances[0].terminate).toHaveBeenCalled();
    expect(Context.instances).toHaveLength(0);
    expect(onStatus).toHaveBeenCalledTimes(1);
  });
});

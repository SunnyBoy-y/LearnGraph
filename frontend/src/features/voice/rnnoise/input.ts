import processorUrl from './processor.ts?worker&url';
import { canRunRnnoise, rnnoiseExecution, rnnoiseWorkerBufferMs, reportedCpuCores, type RnnoiseCalibration } from './policy';
import type { VoiceSpeechEvidence } from '../voice-barge-in';

export interface VoiceNoiseStatus {
  mode: 'idle' | 'calibrating' | 'rnnoise' | 'browser';
  cores: number | null;
  calibration: RnnoiseCalibration | null;
  bufferMs: number | null;
  processedFrames: number;
  slowFrames: number;
  reason: string | null;
  browserNsEnabled: boolean | null;
  execution: 'audio-worklet' | 'worker' | null;
}
export const idleNoiseStatus: VoiceNoiseStatus = { mode: 'idle', cores: null,
  calibration: null, bufferMs: null, processedFrames: 0, slowFrames: 0, reason: null,
  browserNsEnabled: null, execution: null };

export interface VoiceNoiseInput {
  stream: MediaStream;
  audioContext?: AudioContext;
  close(): void;
}

export function calibrateRnnoise(signal?: AbortSignal, model: 'standard' | 'lightweight' = 'standard'): Promise<RnnoiseCalibration> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./calibration.worker.ts', import.meta.url), { type: 'module' });
    const finish = (error?: unknown, data?: RnnoiseCalibration) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', aborted);
      worker.terminate();
      if (error) reject(error); else resolve(data!);
    };
    const aborted = () => finish(new DOMException('Connection superseded', 'AbortError'));
    const timer = setTimeout(() => finish(new Error('RNNoise calibration timed out')), 4000);
    signal?.addEventListener('abort', aborted, { once: true });
    worker.onerror = () => finish(new Error('RNNoise calibration worker failed'));
    worker.onmessage = (event) => event.data?.error
      ? finish(new Error(event.data.error)) : finish(undefined, event.data);
    if (signal?.aborted) aborted(); else worker.postMessage({ frames: 160, model });
  });
}

/** Capture AEC remains upstream; RNNoise replaces browser NS when healthy. */
export async function createVoiceNoiseInput(raw: MediaStream, options: {
  signal?: AbortSignal;
  onStatus?: (status: VoiceNoiseStatus) => void;
  onSpeech?: (evidence: VoiceSpeechEvidence) => void;
} = {}): Promise<VoiceNoiseInput> {
  const { signal, onStatus } = options;
  let status: VoiceNoiseStatus = { ...idleNoiseStatus,
    cores: reportedCpuCores(navigator.hardwareConcurrency) };
  let context: AudioContext | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let node: AudioWorkletNode | null = null;
  let destination: MediaStreamAudioDestinationNode | null = null;
  let dspWorker: Worker | null = null;
  let closed = false;
  let fallingBack = false;
  let fallbackPromise: Promise<void> | null = null;
  const publish = (patch: Partial<VoiceNoiseStatus>) => {
    status = { ...status, ...patch };
    if (!closed) onStatus?.(status);
  };
  const close = () => {
    if (closed) return;
    closed = true;
    signal?.removeEventListener('abort', close);
    node?.port.postMessage({ type: 'stop' });
    node?.port.close();
    dspWorker?.terminate();
    node?.disconnect();
    source?.disconnect();
    destination?.stream.getTracks().forEach((track) => track.stop());
    if (context && context.state !== 'closed') void context.close().catch(() => undefined);
  };
  const checkCurrent = () => {
    if (closed || signal?.aborted) throw new DOMException('Connection superseded', 'AbortError');
  };
  const fallback = (reason: string) => {
    if (fallbackPromise) return fallbackPromise;
    fallingBack = true;
    fallbackPromise = (async () => {
      // Preserve the destination's track, including its existing mute state.
      source?.disconnect(); node?.disconnect();
      if (source && destination) source.connect(destination);
      node?.port.postMessage({ type: 'stop' });
      dspWorker?.terminate();
      if (context?.state === 'suspended') await context.resume();
      const track = raw.getAudioTracks()[0];
      let fallbackReason = reason;
      try { await track?.applyConstraints({ noiseSuppression: true }); }
      catch { fallbackReason += '; browser noise suppression unavailable'; }
      const browserNsEnabled = track?.getSettings?.().noiseSuppression ?? null;
      if (browserNsEnabled === false) fallbackReason += '; browser did not enable noise suppression';
      publish({ mode: 'browser', reason: fallbackReason, bufferMs: 0, browserNsEnabled, execution: null });
    })();
    return fallbackPromise;
  };
  signal?.addEventListener('abort', close, { once: true });
  try {
    checkCurrent();
    if (typeof AudioContext === 'undefined' || typeof AudioWorkletNode === 'undefined'
      || typeof Worker === 'undefined') throw new Error('AudioWorklet/Worker unavailable');
    publish({ mode: 'calibrating' });
    let calibration = await calibrateRnnoise(signal);
    if (!canRunRnnoise(calibration)) {
      try {
        const lightweight = await calibrateRnnoise(signal, 'lightweight');
        if (canRunRnnoise(lightweight)) calibration = lightweight;
      } catch (error) { if (signal?.aborted) throw error; }
    }
    checkCurrent();
    publish({ calibration });
    const profile = rnnoiseExecution(calibration);
    // The experimental local speech detector needs dependable delivery even
    // under UI/recorder load. Keep recurrent DSP off the short audio callback.
    const speechWorker = Boolean(options.onSpeech) && (status.cores ?? 1)>1 && profile!=='browser';
    const execution = speechWorker ? 'worker' : profile;
    const bufferMs = execution === 'worker' ? speechWorker ? 30 : rnnoiseWorkerBufferMs(calibration) : 10;
    if (execution === 'browser') throw new Error('RNNoise exceeds safe audio processing budget');
    context = new AudioContext({ sampleRate: 48_000, latencyHint: 'interactive' });
    if (!context.audioWorklet || context.sampleRate !== 48_000) throw new Error('48 kHz AudioWorklet unavailable');
    await context.audioWorklet.addModule(processorUrl);
    checkCurrent();
    source = context.createMediaStreamSource(raw);
    destination = context.createMediaStreamDestination();
    node = new AudioWorkletNode(context, 'learngraph-rnnoise', {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      channelCount: 1, channelCountMode: 'explicit', channelInterpretation: 'speakers',
      processorOptions: { workerMode: execution === 'worker', bufferMs, module: calibration.module, speechEvents: Boolean(options.onSpeech) },
    });
    await new Promise<void>((resolve, reject) => {
      const abort = () => settle(new DOMException('Connection superseded', 'AbortError'));
      const settle = (error?: Error) => {
        clearTimeout(timer); signal?.removeEventListener('abort', abort);
        if (error) reject(error); else resolve();
      };
      const timer = setTimeout(() => settle(new Error('RNNoise initialization timed out')), 4000);
      signal?.addEventListener('abort', abort, { once: true });
      node!.onprocessorerror = () => {
        settle(new Error('RNNoise processor failed'));
        if (!closed) void fallback('RNNoise processor failed');
      };
      node!.port.onmessage = (event) => {
        if (closed) return;
        const data = event.data;
        if (data?.type === 'speech' && !fallingBack) options.onSpeech?.(data);
        if (data?.type === 'ready') settle();
        if (data?.type === 'failure') {
          settle(new Error(String(data.reason)));
          void fallback(String(data.reason));
        }
        if (data?.type === 'stats' && !fallingBack) publish({ processedFrames: data.frames,
          slowFrames: data.slowFrames });
      };
      if (execution === 'worker') {
        dspWorker = new Worker(new URL('./dsp.worker.ts', import.meta.url), { type: 'module' });
        dspWorker.onerror = () => {
          settle(new Error('RNNoise DSP Worker failed'));
          if (!closed) void fallback('RNNoise DSP Worker failed');
        };
        dspWorker.onmessage = (event) => {
          if (closed) return;
          if (event.data?.type === 'ready') {
            const channel = new MessageChannel();
            dspWorker!.postMessage({ type: 'port', port: channel.port2 }, [channel.port2]);
            node!.port.postMessage({ type: 'worker-port', port: channel.port1 }, [channel.port1]);
          }
          if (event.data?.type === 'failure') {
            settle(new Error(String(event.data.reason)));
            void fallback(String(event.data.reason));
          }
        };
        dspWorker.postMessage({ type: 'start', module: calibration.module });
      }
    });
    checkCurrent();
    if (fallingBack) throw new Error('RNNoise failed during initialization');
    source.connect(node).connect(destination);
    await context.resume();
    checkCurrent();
    // Begin with browser NS enabled, so calibration never leaves capture bare.
    // A browser that cannot toggle NS can still run RNNoise. In particular,
    // WebAudio-generated tracks do not accept capture-device constraints.
    let constraintNotice: string | null = null;
    try { await raw.getAudioTracks()[0]?.applyConstraints({ noiseSuppression: false }); }
    catch { constraintNotice = '浏览器未接受内置降噪开关请求'; }
    checkCurrent();
    if (fallingBack) throw new Error('RNNoise failed while enabling capture');
    publish({ mode: 'rnnoise', execution, bufferMs, reason: constraintNotice,
      browserNsEnabled: raw.getAudioTracks()[0]?.getSettings?.().noiseSuppression ?? null });
    return { stream: destination.stream, audioContext: context, close };
  } catch (error) {
    if (closed || signal?.aborted) { close(); throw error; }
    await fallback(error instanceof Error ? error.message : String(error));
    checkCurrent();
    return { stream: destination?.stream ?? raw, audioContext: context ?? undefined, close };
  }
}

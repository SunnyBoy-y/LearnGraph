import { RnnoiseEngine, RNNOISE_FRAME, RNNOISE_RATE } from './engine';
import { FastRnnoiseEngine } from './fast-engine';
import { RNNOISE_CALLBACK_BUDGET_MS } from './policy';

// AudioWorklet globals are absent from TypeScript's window DOM library.
declare const sampleRate: number;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(name: string, processor: typeof AudioWorkletProcessor): void;

class RnnoiseProcessor extends AudioWorkletProcessor {
  private engine: RnnoiseEngine | FastRnnoiseEngine | null = null;
  private input = new Float32Array(RNNOISE_FRAME);
  private output = new Float32Array(RNNOISE_FRAME * 4);
  private inputSize = 0;
  private read = 0;
  private write = RNNOISE_FRAME;
  private frames = 0;
  private slowFrames = 0;
  private windowMs = 0;
  private stopped = false;
  private workerMode = false;
  private workerPort: MessagePort | null = null;
  private sample = 0;
  private sentFrames = 0;
  private workerOutput = new Float32Array(RNNOISE_FRAME * 32);
  private rawDelay = new Float32Array(RNNOISE_FRAME * 32);
  private readySlots = new Int32Array(32).fill(-1);
  private lateFrames = 0;
  private lastMissedFrame = -1;
  private workerDelay = 2 * RNNOISE_FRAME;
  private speechEvents = false;

  constructor(options?: { processorOptions?: { workerMode?: boolean; bufferMs?: number; module?: WebAssembly.Module; speechEvents?: boolean } }) {
    super();
    this.workerMode = options?.processorOptions?.workerMode === true;
    this.speechEvents = options?.processorOptions?.speechEvents === true;
    this.workerDelay = options?.processorOptions?.bufferMs === 30 ? 3 * RNNOISE_FRAME : 2 * RNNOISE_FRAME;
    this.port.onmessage = (event) => {
      if (event.data?.type === 'stop') {
        this.stopped = true;
        this.engine?.destroy();
        this.engine = null;
        this.workerPort?.close();
      }
      if (event.data?.type === 'worker-port') {
        this.workerPort = event.data.port;
        this.workerPort!.onmessage = (message) => {
          const { id, frame } = message.data;
          if (this.stopped || !Number.isInteger(id) || frame?.length !== RNNOISE_FRAME) return;
          const slot = id % 32;
          this.workerOutput.set(frame, slot * RNNOISE_FRAME);
          this.readySlots[slot] = id;
          if(this.speechEvents)this.port.postMessage({type:'speech',probability:message.data.probability,rms:message.data.rms,durationMs:10});
        };
        this.workerPort!.start();
        this.port.postMessage({ type: 'ready', bufferMs: this.workerDelay / 48 });
      }
    };
    try {
      if (sampleRate !== RNNOISE_RATE) throw new Error(`Unexpected sample rate: ${sampleRate}`);
      if (this.workerMode) return;
      this.engine = options?.processorOptions?.module ? new FastRnnoiseEngine(options.processorOptions.module) : new RnnoiseEngine();
      // Fixed 480-sample FIFO delay bridges arbitrary audio rendering blocks.
      this.port.postMessage({ type: 'ready', bufferMs: 10, memoryBytes: this.engine.memoryBytes });
    } catch (error) {
      this.port.postMessage({ type: 'failure', reason: String(error) });
    }
  }

  process(inputs: Float32Array[][], outputs: Float32Array[][]) {
    if (this.stopped) return false;
    const output = outputs[0]?.[0];
    if (!output) return true;
    const input = inputs[0]?.[0];
    if (this.workerMode) {
      // Some engines render an unconnected node during asynchronous worker
      // initialization. Do not drop frames or count false missed deadlines
      // before the port exists (the microphone graph is connected at ready).
      if(!this.workerPort){output.fill(0);return true;}
      for (let i = 0; i < output.length; i++) {
        const value = input?.[i] ?? 0;
        this.rawDelay[this.sample % this.rawDelay.length] = value;
        const played = this.sample - this.workerDelay;
        const id = Math.floor(played / RNNOISE_FRAME);
        const slot = id >= 0 ? id % 32 : 0;
        const ready = id >= 0 && this.readySlots[slot] === id;
        // On a missed deadline, aligned capture is preferable to an empty
        // buffer; sustained misses switch the stable output track to browser NS.
        const rawIndex = this.sample - this.workerDelay - 2 * RNNOISE_FRAME;
        output[i] = ready ? this.workerOutput[slot * RNNOISE_FRAME + played % RNNOISE_FRAME]
          : rawIndex >= 0 ? this.rawDelay[rawIndex % this.rawDelay.length] : 0;
        if (id >= 0 && !ready && id !== this.lastMissedFrame) {
          this.lastMissedFrame = id; this.lateFrames++;
        }
        this.sample++;
        this.input[this.inputSize++] = value;
        if (this.inputSize === RNNOISE_FRAME) {
          const frame = this.input;
          this.workerPort?.postMessage({ id: this.sentFrames++, frame }, [frame.buffer]);
          this.input = new Float32Array(RNNOISE_FRAME); this.inputSize = 0;
          this.frames++;
          if (this.frames % 100 === 0) {
            this.port.postMessage({ type: 'stats', frames: this.frames, slowFrames: this.lateFrames, bufferMs: this.workerDelay / 48 });
            if (this.frames > 100 && this.lateFrames >= 3) this.port.postMessage({ type: 'failure', reason: 'RNNoise DSP Worker missed audio deadlines' });
            this.lateFrames = 0;
          }
        }
      }
      return true;
    }
    for (let i = 0; i < output.length; i++) {
      // Read first: even on an RNNoise boundary the FIFO delay stays 480 samples.
      output[i] = this.output[this.read];
      this.read = (this.read + 1) % this.output.length;
      this.input[this.inputSize++] = input?.[i] ?? 0;
      if (this.inputSize !== RNNOISE_FRAME) continue;
      const start = Date.now();
      let sum = 0;
      if(this.speechEvents)for(const value of this.input)sum+=value*value;
      let probability = 0;
      try { probability = this.engine?.process(this.input) ?? 0; }
      catch (error) {
        this.engine?.destroy();
        this.engine = null;
        this.port.postMessage({ type: 'failure', reason: String(error) });
      }
      const elapsed = Date.now() - start;
      if(this.speechEvents)this.port.postMessage({type:'speech',probability,rms:Math.sqrt(sum/RNNOISE_FRAME),durationMs:10});
      for (let j = 0; j < RNNOISE_FRAME; j++) {
        const value = this.input[j];
        this.output[this.write] = Number.isFinite(value) ? value : 0;
        this.write = (this.write + 1) % this.output.length;
      }
      this.inputSize = 0;
      this.frames++;
      this.windowMs += elapsed;
      if (elapsed > RNNOISE_CALLBACK_BUDGET_MS) this.slowFrames++;
      if (this.frames % 100 === 0) {
        this.port.postMessage({ type: 'stats', frames: this.frames,
          meanMs: this.windowMs / 100, slowFrames: this.slowFrames,
          // Date.now() is coarse here; precise timings come from calibration.
          timingResolution: 'Date.now', bufferMs: 10 });
        if (this.frames > 200 && this.slowFrames >= 10) {
          this.port.postMessage({ type: 'failure', reason: 'RNNoise audio thread overloaded' });
        }
        this.slowFrames = this.windowMs = 0;
      }
    }
    return true;
  }
}

registerProcessor('learngraph-rnnoise', RnnoiseProcessor);

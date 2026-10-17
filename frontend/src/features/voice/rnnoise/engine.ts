import createModule from '@jitsi/rnnoise-wasm/dist/rnnoise-sync.js';

export const RNNOISE_RATE = 48_000;
export const RNNOISE_FRAME = 480;

/** One state per microphone; adjacent frames must keep their original order. */
export class RnnoiseEngine {
  private readonly module = createModule();
  private state = this.module._rnnoise_create(0);
  private pointer = this.module._malloc(RNNOISE_FRAME * 4);

  constructor() {
    if (!this.state || !this.pointer) {
      this.destroy();
      throw new Error('RNNoise allocation failed');
    }
  }

  get memoryBytes() { return this.module.HEAPF32.buffer.byteLength; }

  process(frame: Float32Array) {
    if (!this.state || frame.length !== RNNOISE_FRAME) throw new Error('Invalid RNNoise frame');
    const heap = this.module.HEAPF32;
    const offset = this.pointer >>> 2;
    for (let i = 0; i < RNNOISE_FRAME; i++) heap[offset + i] = frame[i] * 32768;
    const probability = this.module._rnnoise_process_frame(this.state, this.pointer, this.pointer);
    for (let i = 0; i < RNNOISE_FRAME; i++) frame[i] = heap[offset + i] / 32768;
    return probability;
  }

  destroy() {
    if (this.state) this.module._rnnoise_destroy(this.state);
    if (this.pointer) this.module._free(this.pointer);
    this.state = this.pointer = 0;
  }
}

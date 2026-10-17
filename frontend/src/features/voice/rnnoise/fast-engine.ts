import scalarUrl from '@sapphi-red/web-noise-suppressor/rnnoise.wasm?url';
import simdUrl from '@sapphi-red/web-noise-suppressor/rnnoise_simd.wasm?url';

interface FastExports extends WebAssembly.Exports {
  memory: WebAssembly.Memory;
  emscripten_stack_init(): void;
  __wasm_call_ctors(): void;
  malloc(size: number): number;
  free(pointer: number): void;
  rnnoise_create(model: number): number;
  rnnoise_destroy(state: number): void;
  rnnoise_process_frame(state: number, output: number, input: number): number;
}

export async function loadFastRnnoise() {
  const simd = WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,5,1,96,0,1,123,3,2,1,0,10,10,1,8,0,65,0,253,15,253,98,11]));
  const response = await fetch(simd ? simdUrl : scalarUrl);
  if (!response.ok) throw new Error('RNNoise lightweight weights unavailable');
  return { module: await WebAssembly.compile(await response.arrayBuffer()), simd };
}

/** Pretrained Shiguredo 2022.2.0 RNNoise build, shipped by sapphi-red 0.4.1. */
export class FastRnnoiseEngine {
  private exports: FastExports;
  private heap: Float32Array;
  private state = 0;
  private pointer = 0;
  constructor(module: WebAssembly.Module) {
    const instance = new WebAssembly.Instance(module, { env: {
      emscripten_memcpy_big: (dest: number, src: number, bytes: number) => {
        new Uint8Array(this.exports.memory.buffer).copyWithin(dest, src, src + bytes); return dest;
      },
      emscripten_resize_heap: (bytes: number) => {
        try {
          this.exports.memory.grow(Math.ceil((bytes - this.exports.memory.buffer.byteLength) / 65536));
          return 1;
        } catch { return 0; }
      },
      __assert_fail: () => { throw new Error('RNNoise lightweight runtime assertion'); },
    } });
    this.exports = instance.exports as FastExports;
    this.exports.emscripten_stack_init(); this.exports.__wasm_call_ctors();
    this.state = this.exports.rnnoise_create(0);
    this.pointer = this.exports.malloc(480 * 4);
    this.heap = new Float32Array(this.exports.memory.buffer);
    if (!this.state || !this.pointer) { this.destroy(); throw new Error('RNNoise allocation failed'); }
  }
  get memoryBytes() { return this.exports.memory.buffer.byteLength; }
  process(frame: Float32Array) {
    if (!this.state || frame.length !== 480) throw new Error('Invalid RNNoise frame');
    if (this.heap.buffer !== this.exports.memory.buffer) this.heap = new Float32Array(this.exports.memory.buffer);
    const offset = this.pointer >>> 2;
    for (let i = 0; i < 480; i++) this.heap[offset + i] = frame[i] * 32768;
    const probability = this.exports.rnnoise_process_frame(this.state, this.pointer, this.pointer);
    for (let i = 0; i < 480; i++) frame[i] = this.heap[offset + i] / 32768;
    return probability;
  }
  destroy() {
    if (this.state) this.exports.rnnoise_destroy(this.state);
    if (this.pointer) this.exports.free(this.pointer);
    this.state = this.pointer = 0;
  }
}

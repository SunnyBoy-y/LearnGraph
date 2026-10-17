import { RnnoiseEngine, RNNOISE_FRAME } from './engine';
import { FastRnnoiseEngine, loadFastRnnoise } from './fast-engine';

// This worker is temporary. Audio stays on the AudioWorklet rendering thread.
self.onmessage = async (event: MessageEvent<{ frames?: number; model?: 'standard' | 'lightweight' }>) => {
  let engine: FastRnnoiseEngine | RnnoiseEngine | null = null;
  try {
    const loaded = event.data.model === 'lightweight' ? await loadFastRnnoise() : null;
    engine = loaded ? new FastRnnoiseEngine(loaded.module) : new RnnoiseEngine();
    const frames = Math.max(64, Math.min(10_000, event.data.frames ?? 160));
    const input = new Float32Array(RNNOISE_FRAME);
    const times: number[] = [];
    let seed = 123456789;
    for (let frame = -40; frame < frames; frame++) {
      for (let i = 0; i < input.length; i++) {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        input[i] = (seed / 4294967296 - 0.5) * 0.12
          + 0.08 * Math.sin((frame * RNNOISE_FRAME + i) * 0.04);
      }
      const start = performance.now();
      engine.process(input);
      if (frame >= 0) times.push(performance.now() - start);
    }
    times.sort((a, b) => a - b);
    self.postMessage({ meanMs: times.reduce((a, b) => a + b, 0) / times.length,
      p95Ms: times[Math.floor(times.length * 0.95)], maxMs: times.at(-1),
      memoryBytes: engine.memoryBytes, frames, model: loaded ? 'lightweight' : 'standard',
      simd: loaded?.simd ?? false, ...(loaded ? { module: loaded.module } : {}) });
  } catch (error) {
    self.postMessage({ error: String(error) });
  } finally {
    engine?.destroy();
  }
};

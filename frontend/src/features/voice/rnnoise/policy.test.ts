import { describe, expect, it } from 'vitest';
import { canRunRnnoise, rnnoiseExecution, reportedCpuCores, type RnnoiseCalibration } from './policy';

const fast: RnnoiseCalibration = { meanMs: 0.3, p95Ms: 0.6, maxMs: 2, frames: 160, memoryBytes: 16_777_216 };
describe('RNNoise audio deadline policy', () => {
  it('uses measured callback headroom, not the number of advertised cores', () => {
    expect(canRunRnnoise(fast)).toBe(true);
    expect(canRunRnnoise({ ...fast, p95Ms: 2.4 })).toBe(false);
    expect(canRunRnnoise({ ...fast, meanMs: 1.9 })).toBe(false);
  });
  it('rejects failed or insufficient calibration', () => {
    for (const patch of [{ meanMs: NaN }, { p95Ms: Infinity }, { p95Ms: -1 }, { frames: 10 }]) {
      expect(canRunRnnoise({ ...fast, ...patch })).toBe(false);
    }
  });
  it('treats reported logical cores as an optional hint', () => {
    expect(reportedCpuCores(32)).toBe(32);
    expect(reportedCpuCores(undefined)).toBe(null);
    expect(reportedCpuCores(0)).toBe(null);
    expect(reportedCpuCores(Infinity)).toBe(null);
  });
  it('uses a sequential DSP Worker when one frame fits 10 ms but not the audio callback', () => {
    expect(rnnoiseExecution(fast)).toBe('audio-worklet');
    expect(rnnoiseExecution({ ...fast, meanMs: 2.8, p95Ms: 4.1 })).toBe('worker');
    expect(rnnoiseExecution({ ...fast, meanMs: 6, p95Ms: 9 })).toBe('browser');
    expect(rnnoiseExecution({ ...fast, meanMs: NaN })).toBe('browser');
  });
});

export interface RnnoiseCalibration {
  meanMs: number;
  p95Ms: number;
  maxMs: number;
  memoryBytes: number;
  frames: number;
  model?: 'standard' | 'lightweight';
  simd?: boolean;
  module?: WebAssembly.Module;
}

// RNNoise's 10 ms frame is computed in one 128-sample audio render callback.
// Leave headroom inside that 2.67 ms callback for the rest of the audio graph.
export const RNNOISE_CALLBACK_BUDGET_MS = 128 / 48;
export const RNNOISE_SAFE_P95_MS = 1.8;

export function canRunRnnoise(calibration: RnnoiseCalibration) {
  return Number.isFinite(calibration.meanMs) && calibration.meanMs >= 0
    && Number.isFinite(calibration.p95Ms) && calibration.p95Ms >= 0
    && calibration.frames >= 64 && calibration.p95Ms <= RNNOISE_SAFE_P95_MS
    && calibration.meanMs <= 1.5;
}

export function rnnoiseExecution(calibration: RnnoiseCalibration): 'audio-worklet' | 'worker' | 'browser' {
  if (canRunRnnoise(calibration)) return 'audio-worklet';
  // A full recurrent stream can run on one DSP Worker without blocking the
  // 128-sample audio callback. Keep mean compute below 60% of real time.
  if (Number.isFinite(calibration.meanMs) && calibration.meanMs >= 0 && calibration.meanMs <= 6
    && Number.isFinite(calibration.p95Ms) && calibration.p95Ms >= 0 && calibration.p95Ms <= 8
    && calibration.frames >= 64) return 'worker';
  return 'browser';
}

export function rnnoiseWorkerBufferMs(calibration: RnnoiseCalibration): 20 | 30 {
  return calibration.meanMs <= 4 && calibration.p95Ms <= 6 ? 20 : 30;
}

export function reportedCpuCores(value: number | undefined): number | null {
  return value && Number.isFinite(value) && value >= 1 ? Math.floor(value) : null;
}

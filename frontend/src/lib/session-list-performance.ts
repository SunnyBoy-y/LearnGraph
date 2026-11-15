/** Bounded local diagnostics. No titles, IDs, message content or telemetry upload. */
export type SessionListMetric = { kind: 'network' | 'render' | 'first-visible' | 'page-longtask'; duration: number; count?: number }
const samples: SessionListMetric[] = []
export function recordSessionListMetric(sample: SessionListMetric) {
  samples.push(sample)
  if (samples.length > 128) samples.splice(0, samples.length - 128)
}
export function readSessionListPerformance() {
  const percentile = (values: number[], ratio: number) => values.length ? values.sort((a, b) => a - b)[Math.min(values.length - 1, Math.floor(values.length * ratio))] : null
  return {
    samples: [...samples],
    networkP95: percentile(samples.filter(item => item.kind === 'network').map(item => item.duration), .95),
    renderP95: percentile(samples.filter(item => item.kind === 'render').map(item => item.duration), .95),
    pageLongTasks: samples.filter(item => item.kind === 'page-longtask').length,
  }
}

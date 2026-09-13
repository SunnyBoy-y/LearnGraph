// Client-only append provenance, without putting renderer state in API data.
// Weak keys allow old parts and their generations to be collected normally.
const epochs = new WeakMap<object, object>();

export function markStreamTextUpdate<T extends object>(
  next: T, previous: object | undefined, replacement: boolean,
): T {
  epochs.set(next, (!replacement && previous && epochs.get(previous)) || {});
  return next;
}

export function streamTextEpoch(part: object): object | undefined {
  return epochs.get(part);
}

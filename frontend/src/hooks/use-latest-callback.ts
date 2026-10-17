import { useCallback, useLayoutEffect, useRef } from "react";

/** Stable event identity for memoized children, using the latest committed handler. */
export function useLatestCallback<Args extends unknown[], Result>(
  callback: (...args: Args) => Result,
) {
  const latest = useRef(callback);
  useLayoutEffect(() => {
    latest.current = callback;
  }, [callback]);
  return useCallback((...args: Args) => latest.current(...args), []);
}

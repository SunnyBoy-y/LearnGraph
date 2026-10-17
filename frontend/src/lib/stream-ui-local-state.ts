import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";
import type { Dispatch, SetStateAction } from "react";

export const StreamUIStateScope = createContext("");

// Final-answer artifact placement can move a block between React parents.
// Keep local choices by durable Part ID across that remount, without writing
// them to the server or browser storage. Bound the cache for long conversations.
const blocks = new Map<string, Map<string, unknown>>();
const MAX_BLOCKS = 128;

function stateFor(scope: string) {
  const state = blocks.get(scope) ?? new Map<string, unknown>();
  blocks.delete(scope);
  blocks.set(scope, state);
  if (blocks.size > MAX_BLOCKS) blocks.delete(blocks.keys().next().value!);
  return state;
}

export function useStreamUIState<T>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const scope = useContext(StreamUIStateScope);
  const initialValue = useRef(initial);
  const [value, setValue] = useState<T>(() => {
    const state = stateFor(scope);
    if (!state.has(key)) state.set(key, initialValue.current);
    return state.get(key) as T;
  });
  const update = useCallback<Dispatch<SetStateAction<T>>>(
    (action) => {
      const state = stateFor(scope);
      const previous = state.has(key)
        ? (state.get(key) as T)
        : initialValue.current;
      const next =
        typeof action === "function"
          ? (action as (value: T) => T)(previous)
          : action;
      state.set(key, next);
      setValue(next);
    },
    [scope, key],
  );
  return [value, update];
}

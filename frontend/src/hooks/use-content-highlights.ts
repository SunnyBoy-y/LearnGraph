import { useEffect, useRef, useState } from 'react'

const empty = new Set<string>()

export function changedContentKeys(previous: ReadonlyMap<string, string>, current: ReadonlyMap<string, string>) {
  return new Set([...current].filter(([key, text]) => previous.get(key) !== text).map(([key]) => key))
}

/** Highlight actual changes after the first snapshot, never navigation or polling. */
export function useContentHighlights(
  scope: string,
  content: ReadonlyMap<string, string>,
  enabled = true,
  duration = 1100,
  matchByContent = false,
) {
  const previous = useRef<{ scope: string; content: ReadonlyMap<string, string> } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [highlight, setHighlight] = useState({ scope, keys: empty })

  useEffect(() => {
    const before = previous.current
    previous.current = enabled ? { scope, content } : null
    if (!enabled || !before || before.scope !== scope) {
      clearTimeout(timer.current)
      setHighlight(current => current.keys.size ? { scope, keys: empty } : current)
      return
    }
    const changed = changedContentKeys(before.content, content)
    // Generated report paragraph IDs may be positional. Reordering identical
    // prose is not a correction; graph relations keep identity-based matching.
    if (matchByContent) {
      const known = new Set(before.content.values())
      for (const key of changed) if (known.has(content.get(key)!)) changed.delete(key)
    }
    if (!changed.size) return
    clearTimeout(timer.current)
    setHighlight(current => ({
      scope,
      keys: new Set([...(current.scope === scope ? current.keys : empty), ...changed].filter(key => content.has(key))),
    }))
    timer.current = setTimeout(() => setHighlight({ scope, keys: empty }), duration)
  }, [scope, content, enabled, duration, matchByContent])

  useEffect(() => () => clearTimeout(timer.current), [])
  return enabled && highlight.scope === scope ? highlight.keys : empty
}

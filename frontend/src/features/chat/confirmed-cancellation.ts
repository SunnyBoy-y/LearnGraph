export const STOP_TERMINAL_STATUSES = ['completed', 'cancelled', 'failed', 'interrupted'] as const
export type StopState = { phase: 'idle' | 'requesting' | 'unconfirmed'; messageId?: string; detail?: string }

/** An accepted POST is not a terminal state. Never claim cancellation from an aborted HTTP stream. */
export async function requestConfirmedStop<T extends { status: string }>(options: {
  cancel: () => Promise<void>
  snapshot: () => Promise<T>
  wait?: (milliseconds: number) => Promise<void>
}): Promise<T> {
  let requestError: unknown
  try { await options.cancel() } catch (error) { requestError = error }
  const wait = options.wait ?? (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)))
  for (const delay of [0, 150, 350, 750, 1500]) {
    if (delay) await wait(delay)
    try {
      const snapshot = await options.snapshot()
      if ((STOP_TERMINAL_STATUSES as readonly string[]).includes(snapshot.status)) return snapshot
    } catch { /* The next bounded authoritative read may still succeed. */ }
    if (requestError) break
  }
  throw requestError instanceof Error ? requestError : new Error('停止尚未得到服务端确认；任务可能仍在运行。')
}

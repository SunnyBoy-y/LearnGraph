// Lightweight Node 24 smoke test for queue state; no Vite worker pool required.
import assert from 'node:assert/strict'
import { setTimeout as delay } from 'node:timers/promises'
import { toast as raw } from 'sonner'
import { toast, mountNotificationSurface, setNotificationFocus } from '../src/lib/notifications.ts'
globalThis.requestAnimationFrame = callback => setTimeout(() => callback(performance.now()), 0)
const unmount = mountNotificationSurface()
try {
  const a = toast.success('已保存', { scope: 'a' })
  const b = toast.warning('需要留意', { scope: 'b' })
  assert.equal(toast.success('已保存', { scope: 'a' }), a)
  const c = toast.error('暂不可用', { scope: 'c' })
  assert.deepEqual(raw.getToasts().map(t => t.id), [a, b])
  setNotificationFocus(true)
  assert.ok(raw.getToasts().every(t => t.duration === Infinity))
  setNotificationFocus(false)
  assert.equal(raw.getToasts().find(t => t.id === a).duration, 2800)
  toast.dismiss(a)
  await delay(250)
  assert.deepEqual(raw.getToasts().map(t => t.id), [b, c])
  toast.dismiss()
  await delay(250)
  let handled = false
  const actionable = toast.error('未能保存', { scope: 'model-a', context: '模型 A', description: '更改尚未保存', action: { label: '重试', onClick: () => { handled = true } } })
  const item = raw.getToasts().find(t => t.id === actionable)
  assert.equal(item.duration, Infinity)
  assert.equal(item.description, '模型 A\n更改尚未保存')
  item.action.onClick({ defaultPrevented: false })
  assert.equal(handled, true)
  console.log('PASS: queue limit, FIFO release, duplicate scope, focus pause, durations, context, persistent action')
} finally {
  unmount()
}

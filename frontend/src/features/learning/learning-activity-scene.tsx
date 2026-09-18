import { useEffect, useMemo, useRef, useState } from 'react'
import { sandboxedHtmlPreviewDocument } from '@/lib/sandboxed-html-preview'
import type { ActivitySpec, ActivityState } from './node-learning-api'

/** Official subapp SDK, with a deliberately narrow host capability: one
 * server-validated action. No chat, files, network relay or score writes. */
export function LearningActivityScene({spec, state, busy, onAction}: {
  spec: ActivitySpec; state: ActivityState; busy: boolean
  onAction: (id: string) => Promise<unknown>
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const current = useRef({state, busy, onAction})
  current.current = {state, busy, onAction}
  const version = useRef(0)
  const [loaded, setLoaded] = useState(false)
  const doc = useMemo(() => sandboxedHtmlPreviewDocument(spec.html ?? '', {offline:true, subappClient:true}), [spec.html])
  useEffect(() => {
    const acknowledgements = new Map<string, {status:string; error_code?:string}>()
    const pending = new Set<string>()
    let inFlight = false
    const send = (client_event_id: string, result: {status:string; error_code?:string}) => frame.current?.contentWindow?.postMessage({event_type:'component.event.ack',payload:{client_event_id,...result}}, '*')
    const receive = async (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.event_type !== 'component.event') return
      const payload = event.data.payload
      if (!payload || typeof payload.client_event_id !== 'string' || payload.client_event_id.length > 80) return
      const id = payload.client_event_id
      const cached = acknowledgements.get(id)
      if (cached) {send(id,cached);return}
      if (pending.has(id)) return
      let result: {status:string;error_code?:string}
      if (payload.type !== 'learning.action' || !spec.actions.some(a=>a.id===payload.action_id)) result={status:'rejected',error_code:'action_not_allowed'}
      else if (current.current.busy || inFlight) result={status:'rejected',error_code:'please_wait'}
      else {
        pending.add(id); inFlight=true
        try {await current.current.onAction(payload.action_id);result={status:'persisted'}}
        catch {result={status:'rejected',error_code:'action_not_persisted'}}
        finally {pending.delete(id);inFlight=false}
      }
      if (acknowledgements.size >= 200) acknowledgements.delete(acknowledgements.keys().next().value!)
      acknowledgements.set(id,result); send(id,result)
    }
    window.addEventListener('message',receive)
    return ()=>window.removeEventListener('message',receive)
  },[spec.actions])
  useEffect(()=>{
    if (!loaded) return
    frame.current?.contentWindow?.postMessage({event_type:'renderer.state',payload:{state:{...state,busy},version:++version.current}},'*')
  },[state,busy,loaded])
  return <iframe ref={frame} className="learning-demo learning-scene" title={`${spec.title} · 互动小剧场`} srcDoc={doc} sandbox="allow-scripts" referrerPolicy="no-referrer" onLoad={()=>{
    frame.current?.contentWindow?.postMessage({event_type:'renderer.unlock',payload:{token:'learning-actions-only'}},'*')
    frame.current?.contentWindow?.postMessage({event_type:'renderer.state',payload:{state:{...current.current.state,busy:current.current.busy},version:++version.current}},'*')
    setLoaded(true)
  }} />
}

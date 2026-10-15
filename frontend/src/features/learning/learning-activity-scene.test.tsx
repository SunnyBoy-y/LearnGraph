import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { LearningActivityScene } from './learning-activity-scene'
import { sandboxedHtmlPreviewDocument } from '@/lib/sandboxed-html-preview'

const spec = {title:'烹饪小剧场',instructions:'先热锅',html:'<h1>锅具实验</h1>',variables:[{id:'heat',label:'火力'}],actions:[{id:'heat',label:'热锅'}]}
const state = {values:{heat:0},history:[]}
function message(source: Window | null, id: string, action='heat', type='learning.action') {
  window.dispatchEvent(new MessageEvent('message',{source,data:{event_type:'component.event',payload:{client_event_id:id,type,action_id:action}}}))
}
describe('learning subapp host',()=>{
  it('uses an opaque offline frame with the official SDK and host-owned CSP',()=>{
    const html=sandboxedHtmlPreviewDocument('<meta http-equiv="Content-Security-Policy" content="default-src *"><script src="https://invalid.example/code.js"></script>',{offline:true,subappClient:true})
    const doc=new DOMParser().parseFromString(html,'text/html')
    const csp=doc.querySelector('meta[http-equiv]')!.getAttribute('content')!
    expect(csp).toContain("connect-src 'none'")
    expect(csp).not.toContain('https:')
    expect(csp).not.toContain('unsafe-eval')
    expect(html).toContain('window.__lgSubapp')
    render(<LearningActivityScene spec={spec} state={state} busy={false} onAction={vi.fn()} />)
    expect(screen.getByTitle('烹饪小剧场 · 互动小剧场')).toHaveAttribute('sandbox','allow-scripts')
  })
  it('rejects unrelated frames, unknown actions and arbitrary host capabilities',async()=>{
    const action=vi.fn().mockResolvedValue({})
    render(<LearningActivityScene spec={spec} state={state} busy={false} onAction={action} />)
    const frame=screen.getByTitle('烹饪小剧场 · 互动小剧场') as HTMLIFrameElement
    await act(async()=>{
      message(window,'foreign')
      message(frame.contentWindow,'unknown','award-gold')
      message(frame.contentWindow,'capability','heat','analysis.requested')
    })
    expect(action).not.toHaveBeenCalled()
  })
  it('deduplicates a replay and acknowledges only after server persistence',async()=>{
    let finish!:()=>void
    const action=vi.fn().mockImplementation(()=>new Promise<void>(resolve=>{finish=resolve}))
    render(<LearningActivityScene spec={spec} state={state} busy={false} onAction={action} />)
    const frame=screen.getByTitle('烹饪小剧场 · 互动小剧场') as HTMLIFrameElement
    const post=vi.spyOn(frame.contentWindow!,'postMessage')
    fireEvent.load(frame)
    await act(async()=>{message(frame.contentWindow,'event-1');message(frame.contentWindow,'event-1')})
    expect(action).toHaveBeenCalledTimes(1)
    expect(post.mock.calls.some(([data])=>data.event_type==='component.event.ack')).toBe(false)
    await act(async()=>finish())
    await waitFor(()=>expect(post).toHaveBeenCalledWith({event_type:'component.event.ack',payload:{client_event_id:'event-1',status:'persisted'}},'*'))
    await act(async()=>message(frame.contentWindow,'event-1'))
    expect(action).toHaveBeenCalledTimes(1)
  })
  it('does not let a completed or saving exam mutate state',async()=>{
    const action=vi.fn()
    render(<LearningActivityScene spec={spec} state={state} busy onAction={action} />)
    const frame=screen.getByTitle('烹饪小剧场 · 互动小剧场') as HTMLIFrameElement
    await act(async()=>message(frame.contentWindow,'busy'))
    expect(action).not.toHaveBeenCalled()
  })
})

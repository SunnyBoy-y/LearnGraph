import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NodeLearningPage } from './node-learning-page'
import type { Attempt, Build, Enrollment, LearningPageData } from './node-learning-api'

const api=vi.hoisted(()=>({page:vi.fn(),build:vi.fn(),start:vi.fn(),progress:vi.fn(),startAttempt:vi.fn(),attempt:vi.fn(),save:vi.fn(),submit:vi.fn(),action:vi.fn(),policy:vi.fn(),builds:vi.fn(),setPolicy:vi.fn(),control:vi.fn()}))
vi.mock('./node-learning-api',()=>({learningApi:api}))
vi.mock('@/components/ai-elements/incremental-markdown',()=>({IncrementalMarkdown:({text}:{text:string})=><p>{text}</p>}))
const page:LearningPageData={node:{id:'n',label:'热锅与火候',description:'观察食材变化',node_type:'concept',graph_id:'g'},stale:false,build:null,achievement:null,package:{id:'pkg',created_at:'2026-09-17',manifest:{schema_version:1,blueprint:{title:'热锅',objectives:['判断火候'],estimated_minutes:15},lesson:{sections:[{id:'s1',title:'为何热锅',body:'先了解原理，再观察热锅对食材的影响。',takeaway:'观察状态再行动'}],svg:'<svg viewBox="0 0 10 10"></svg>',caption:'锅具图解',html:''},activity:{title:'烹饪小剧场',instructions:'先热锅，再下菜',variables:[{id:'heat',label:'火力'}],actions:[{id:'heat',label:'热锅'}]},exam:{title:'火候测评',pass_score:80,practical_points:0,questions:[{id:'q1',kind:'single_choice',prompt:'应该先做哪一步？',options:['热锅','下菜'],points:100,critical:false}]},notes:[],provenance:'模型生成教材'}}}
const enrollment:Enrollment={id:'en',package_id:'pkg',revision:0,progress:{sections:[],activity:{values:{heat:0},history:[],completed:false}}}
const attempt:Attempt={id:'at',node_id:'n',package_id:'pkg',status:'active',revision:0,answers:{},activity:{values:{heat:0},history:[]},activity_spec:null,paper:page.package!.manifest.exam,result:null}
function mount(query='') {
  const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/w/w/learn/nodes/n${query}`]}><Routes><Route path="/w/:workspaceId/learn/nodes/:nodeId" element={<NodeLearningPage/>}/></Routes></MemoryRouter></QueryClientProvider>)
}
beforeEach(()=>{api.page.mockResolvedValue(structuredClone(page));api.start.mockResolvedValue(structuredClone(enrollment));api.attempt.mockResolvedValue(structuredClone(attempt))})
describe('node learning experience',()=>{
  const running: Build = {id:'build1', node_id:'n', status:'running', stage:'图文教材', completed_stages:1, total_stages:7, notes:[]}
  it('shows generation progress and allows cancellation on the node page',async()=>{
    api.page.mockResolvedValue({...page,package:null,build:running})
    api.control.mockImplementation(async()=>{
      const cancelled={...running,status:'cancelled'}
      api.page.mockResolvedValue({...page,package:null,build:cancelled})
      return cancelled
    })
    mount()
    expect(await screen.findByText('正在生成图文教材')).toBeVisible()
    expect(screen.queryByRole('button',{name:'创建学习页'})).not.toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('value','1')
    fireEvent.click(screen.getByRole('button',{name:'取消生成'}))
    expect(await screen.findByText('准备已取消')).toBeVisible()
    expect(api.control).toHaveBeenCalledWith('build1','cancel')
    expect(screen.getByRole('button',{name:'重新创建学习页'})).toBeEnabled()
  })
  it('retries the failed stage instead of creating another build',async()=>{
    api.page.mockResolvedValue({...page,package:null,build:{...running,status:'failed',error:'模型响应超时'}})
    api.control.mockImplementation(async()=>{
      const queued={...running,status:'queued'}
      api.page.mockResolvedValue({...page,package:null,build:queued})
      return queued
    })
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('模型响应超时')
    fireEvent.click(screen.getByRole('button',{name:'重试当前阶段'}))
    expect(await screen.findByText('等待后台处理')).toBeVisible()
    expect(api.control).toHaveBeenCalledWith('build1','retry')
    expect(api.build).not.toHaveBeenCalled()
  })
  it('uses the accepted build immediately even if the next status request fails',async()=>{
    api.page.mockResolvedValue({...page,package:null,build:null})
    api.build.mockImplementation(async()=>{
      api.page.mockRejectedValue(new Error('状态连接失败'))
      return {...running,status:'queued'}
    })
    mount()
    fireEvent.click(await screen.findByRole('button',{name:'创建学习页'}))
    expect(await screen.findByText('等待后台处理')).toBeVisible()
    expect(await screen.findByRole('alert')).toHaveTextContent('状态连接失败')
    expect(screen.getByRole('button',{name:'取消生成'})).toBeEnabled()
    expect(screen.queryByRole('button',{name:'创建学习页'})).not.toBeInTheDocument()
  })
  it('keeps existing material accessible while a new package is being built',async()=>{
    api.page.mockResolvedValue({...page,build:running})
    mount()
    expect(await screen.findByText('正在生成图文教材')).toBeVisible()
    expect(screen.getByText('为何热锅',{selector:'h2'})).toBeVisible()
  })
  it('reads prepared content without silently creating a build or a learning record',async()=>{
    mount()
    expect(await screen.findByText('为何热锅',{selector:'h2'})).toBeVisible()
    expect(api.build).not.toHaveBeenCalled()
    expect(api.start).not.toHaveBeenCalled()
    expect(screen.getByRole('button',{name:'标记本节已读'})).toBeDisabled()
    fireEvent.click(screen.getByRole('button',{name:'开始 / 恢复学习'}))
    await waitFor(()=>expect(screen.getByRole('button',{name:'标记本节已读'})).toBeEnabled())
    api.progress.mockResolvedValue({...enrollment,revision:1,progress:{...enrollment.progress,sections:['s1']}})
    fireEvent.click(screen.getByRole('button',{name:'标记本节已读'}))
    expect(await screen.findByRole('button',{name:'本节已读'})).toBeDisabled()
    expect(screen.queryByText(/已验证/)).not.toBeInTheDocument()
  })
  it('restores saved training state and persists interactions',async()=>{
    api.page.mockResolvedValue({...page,enrollment})
    mount('?tab=lab')
    const heat=await screen.findByRole('button',{name:'热锅'})
    api.progress.mockResolvedValue({...enrollment,revision:1,progress:{sections:[],activity:{values:{heat:1},history:['heat'],completed:true,feedback:'目标达成'}}})
    fireEvent.click(heat)
    expect(await screen.findByText('目标已完成')).toBeVisible()
    expect(api.progress).toHaveBeenCalledWith('en',{expected_revision:0,action_id:'heat'})
    expect(screen.queryByText(/已验证/)).not.toBeInTheDocument()
  })
  it('autosaves before enabling submission and renders the server result',async()=>{
    api.save.mockImplementation((_id,revision,answers)=>Promise.resolve({...attempt,revision:revision+1,answers}))
    api.submit.mockResolvedValue({...attempt,status:'failed',answers:{q1:'1'},result:{score:0,message:'请回到原理章节补练'}})
    mount('?attempt=at')
    fireEvent.click(await screen.findByRole('radio',{name:'下菜'}))
    expect(screen.getByRole('button',{name:'交卷评分'})).toBeDisabled()
    await waitFor(()=>expect(api.save).toHaveBeenCalledWith('at',0,{q1:'1'}),{timeout:3000})
    await waitFor(()=>expect(screen.getByRole('button',{name:'交卷评分'})).toBeEnabled())
    fireEvent.click(screen.getByRole('button',{name:'交卷评分'}))
    expect(await screen.findByText('请回到原理章节补练')).toBeVisible()
    expect(screen.queryByText(/已验证/)).not.toBeInTheDocument()
  })

  it('opens a prepared node in the shared learning conversation once',async()=>{
    const open = vi.fn()
    window.addEventListener('learngraph:open-learning-project', open)
    try {
      mount()
      expect(await screen.findByText('为何热锅',{selector:'h2'})).toBeVisible()
      await waitFor(()=>expect(open).toHaveBeenCalledTimes(1))
      const detail = open.mock.calls[0][0].detail
      expect(detail).toMatchObject({graphId:'g',nodeId:'n',nodeLabel:'热锅与火候',learningPackage:true})
    } finally {
      window.removeEventListener('learngraph:open-learning-project', open)
    }
  })

  it('keeps the assessment route independent and preserves the chat return link',async()=>{
    const open = vi.fn()
    window.addEventListener('learngraph:open-learning-project', open)
    try {
      mount('?tab=exam&returnSession=session-1')
      expect(await screen.findByText('火候测评',{selector:'h2'})).toBeVisible()
      expect(open).not.toHaveBeenCalled()
      expect(screen.getAllByRole('link',{name:/返回学习对话/}).some((link) => link.getAttribute('href') === '/w/w/chat/session-1')).toBe(true)
    } finally {
      window.removeEventListener('learngraph:open-learning-project', open)
    }
  })
})

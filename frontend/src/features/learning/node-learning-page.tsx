import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, BookOpen, Check, FlaskConical, LoaderCircle, Medal, MessageCircle, Settings2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { apiClient } from '@/api/client'
import { sandboxedHtmlPreviewDocument } from '@/lib/sandboxed-html-preview'
import { createUuid } from '@/lib/uuid'
import { workspaceQueryKey } from '@/lib/query-keys'
import { learningApi, type ActivitySpec, type ActivityState, type Attempt, type Build, type Enrollment, type LearningPageData, type Policy } from './node-learning-api'
import './node-learning.css'
import { IncrementalMarkdown } from '@/components/ai-elements/incremental-markdown'
import { LearningActivityScene } from './learning-activity-scene'
import { LearningBuildProgress } from './learning-build-progress'
import { LearningModelSelect } from './learning-model-select'

function InlinePreview({html, title}: {html: string; title: string}) {
  const doc = useMemo(() => sandboxedHtmlPreviewDocument(html, {offline: true}), [html])
  return <iframe className="learning-demo" title={title} srcDoc={doc} sandbox="allow-scripts" referrerPolicy="no-referrer" />
}

function LessonImage({fileId, alt}: {fileId: string; alt: string}) {
  const [url, setUrl] = useState('')
  useEffect(() => { let active = true; let objectUrl = ''; void apiClient.getBlob(`/files/${fileId}/content`).then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) } }).catch(() => {}); return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) } }, [fileId])
  return url ? <img className="learning-image" src={url} alt={alt} /> : <p className="learning-muted">插图暂不可用，仍可阅读下方图解。</p>
}

export function LearningBuildSettings({graphId}: {graphId: string}) {
  const {workspaceId = ''} = useParams()
  const [open, setOpen] = useState(false)
  const key = workspaceQueryKey(workspaceId, 'learning-policy', graphId)
  const policy = useQuery({queryKey: key, queryFn: () => learningApi.policy(graphId), enabled: open})
  const builds = useQuery({queryKey: workspaceQueryKey(workspaceId, 'learning-builds', graphId), queryFn: () => learningApi.builds(graphId), enabled: open, refetchInterval: open ? 5000 : false})
  const client = useQueryClient()
  const save = useMutation({mutationFn: (value: Policy) => learningApi.setPolicy(graphId, value), onSuccess: value => {client.setQueryData(key, value); void builds.refetch()}, onError: (e: Error) => {toast.error(e.message); void policy.refetch()}})
  const control = useMutation({mutationFn: ({id, action}: {id: string; action: 'cancel' | 'retry'}) => learningApi.control(id, action), onSuccess: row => {void builds.refetch(); void client.invalidateQueries({queryKey: workspaceQueryKey(workspaceId, 'learning-page', row.node_id)})}, onError: (e: Error) => toast.error(e.message)})
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}><Settings2 className="size-4" />内容准备</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="learning-settings"><DialogHeader><DialogTitle>提前准备节点学习页</DialogTitle><DialogDescription>开启后，仅为新增且尚未提问的节点准备教材、实验与测评。已准备内容会保留；生成使用下面选择的模型。</DialogDescription></DialogHeader>{policy.data ? <div className="learning-settings-fields"><label><input type="checkbox" checked={policy.data.enabled} disabled={save.isPending} onChange={e => save.mutate({...policy.data!, enabled: e.target.checked})} /> 自动准备新节点</label><label>准备范围<select value={policy.data.mode} disabled={save.isPending} onChange={e => save.mutate({...policy.data!, mode: e.target.value as Policy['mode']})}><option value="path">优先准备接下来的 3 个节点</option><option value="all">全部符合条件的新节点</option></select></label><label><input type="checkbox" checked={policy.data.image_enabled} disabled={save.isPending} onChange={e => save.mutate({...policy.data!, image_enabled: e.target.checked})} /> 必要时生成插画（增加模型费用）</label><label className="learning-settings-model">生成模型<LearningModelSelect workspaceId={workspaceId} ariaLabel="教学包生成模型（内容准备）" /><span className="learning-muted">教材、互动实验、小剧场与闯关测评共用；未配置时跟随对话模型。</span></label></div> : <p>{policy.error?.message ?? '读取设置…'}</p>}<div className="learning-build-list">{builds.data?.length === 0 && <p className="learning-muted">暂无构建任务。开启前的旧节点不会自动补建。</p>}{builds.data?.map(b => <div key={b.id}><Link to={`/w/${workspaceId}/learn/nodes/${b.node_id}`}>查看节点</Link><span>{b.stage} · {({queued:'排队中',running:'构建中',ready:'已准备',failed:'失败',cancelled:'已取消',skipped:'已跳过'} as Record<string,string>)[b.status] ?? b.status}</span>{b.error && <p role="status">{b.error}</p>}{b.status === 'failed' && <Button size="xs" variant="outline" disabled={control.isPending} onClick={() => control.mutate({id:b.id,action:'retry'})}>重试失败阶段</Button>}{['queued','running'].includes(b.status) && <Button size="xs" variant="ghost" disabled={control.isPending} onClick={() => control.mutate({id:b.id,action:'cancel'})}>取消</Button>}</div>)}</div></DialogContent></Dialog></>
}

export function ActivityCard({spec, state, onAction, onReset, busy, exam = false}: {
  spec: ActivitySpec; state: ActivityState; onAction: (id: string) => Promise<unknown>
  onReset: () => void; busy: boolean; exam?: boolean
}) {
  return <section className="learning-activity">
    <div className="learning-section-heading"><FlaskConical className="size-5" /><h2>{spec.title}</h2>{!exam && state.completed && <span className="learning-success">目标已完成</span>}</div>
    <p>{spec.instructions}</p>
    {spec.html && <LearningActivityScene spec={spec} state={state} busy={busy} onAction={onAction} />}
    <div className="learning-state" aria-label="实验当前状态">{spec.variables.map(v => <div key={v.id}><span>{v.label}</span><strong>{v.states?.[String(state.values[v.id])] ?? state.values[v.id] ?? '—'}{v.unit && <small> {v.unit}</small>}</strong></div>)}</div>
    <div className="learning-actions">{spec.actions.map(a => <Button key={a.id} disabled={busy} variant="outline" onClick={() => {void onAction(a.id).catch(()=>{})}}>{a.label}</Button>)}</div>
    <p aria-live="polite">{state.feedback ?? '观察当前状态，选择操作。'}</p>
    <details><summary>操作记录 · {state.history.length} 步</summary><ol>{state.history.map((id,i) => <li key={`${id}-${i}`}>{spec.actions.find(a => a.id === id)?.label ?? id}</li>)}</ol></details>
    <Button className="mt-3" size="sm" variant="ghost" onClick={onReset} disabled={busy}>重新实验</Button>
  </section>
}

function ExamView({attemptId, onRestart}: {attemptId: string; onRestart: () => void}) {
  const {workspaceId = ''} = useParams()
  const query = useQuery({queryKey: workspaceQueryKey(workspaceId, 'learning-attempt', attemptId), queryFn: () => learningApi.attempt(attemptId), refetchInterval: q => q.state.data?.status === 'grading' ? 2000 : false})
  const client = useQueryClient()
  const attempt = query.data
  useEffect(()=>{if(attempt && ['passed','failed','needs_review'].includes(attempt.status)){void client.invalidateQueries({queryKey:workspaceQueryKey(workspaceId,'learning-page',attempt.node_id)});void client.invalidateQueries({queryKey:workspaceQueryKey(workspaceId,'graph')})}},[attempt,client,workspaceId])
  const draftKey = `lg:exam-draft:${workspaceId}:${attemptId}`
  const [answers, setAnswers] = useState<Attempt['answers'] | null>(null)
  const [dirty, setDirty] = useState(false)
  const latest = useRef<Attempt['answers']>({})
  const update = (row: Attempt) => client.setQueryData(workspaceQueryKey(workspaceId, 'learning-attempt', attemptId), row)
  const save = useMutation({mutationFn: ({revision, value}: {revision: number; value: Attempt['answers']}) => learningApi.save(attemptId, revision, value), onSuccess: (row, sent) => {update(row); if (JSON.stringify(latest.current) === JSON.stringify(sent.value)) {setDirty(false); try {localStorage.removeItem(draftKey)} catch { /* storage unavailable */ }}}, onError: (e: Error) => {toast.error(`答案尚未同步：${e.message}`); void query.refetch()}})
  useEffect(() => { if (attempt && answers === null) { let initial = attempt.answers; try { const saved = localStorage.getItem(draftKey); if (saved && attempt.status === 'active') {initial = JSON.parse(saved); setDirty(true)} } catch { /* use durable draft */ } setAnswers(initial); latest.current = initial } }, [attempt, answers, draftKey])
  useEffect(() => { if (!dirty || !attempt || attempt.status !== 'active' || save.isPending || save.isError) return; const timer = setTimeout(() => save.mutate({revision: attempt.revision, value: latest.current}), 800); return () => clearTimeout(timer) }, [dirty, answers, attempt, save.isPending, save.isError]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{if(attempt && attempt.status !== 'active'){setAnswers(attempt.answers);latest.current=attempt.answers;setDirty(false)}},[attempt])
  const edit = (id: string, value: string | string[]) => { const next = {...latest.current, [id]: value}; latest.current = next; setAnswers(next); setDirty(true); if (save.isError) save.reset(); try {localStorage.setItem(draftKey, JSON.stringify(next))} catch { /* server autosave remains available */ } }
  const action = useMutation({mutationFn: ({id, reset}: {id?: string; reset?: boolean}) => learningApi.action(attemptId, attempt!.revision, id, reset), onSuccess: update, onError: (e: Error) => toast.error(e.message)})
  const submit = useMutation({mutationFn: () => learningApi.submit(attemptId), onSuccess: row => {update(row); void client.invalidateQueries({queryKey: workspaceQueryKey(workspaceId, 'graphs')})}, onError: (e: Error) => toast.error(e.message)})
  if (!attempt) return <p role="status">{query.error?.message ?? '读取试卷…'}</p>
  const active = attempt.status === 'active'
  const busy = save.isPending || action.isPending || submit.isPending
  return <section className="learning-exam"><header><span>节点关卡 · 满分 100</span><h2>{attempt.paper.title}</h2><p>{attempt.paper.pass_score} 分通过{attempt.paper.practical_points > 0 ? '，且必须独立通过实践卡' : ''} · 试卷版本已固定</p><p aria-live="polite">{active ? dirty ? save.isPending ? '正在保存…' : '有尚未同步的答案' : '答案已保存' : attempt.status === 'grading' ? '正在评分，离开页面后仍会继续' : '本次测评已结束'}</p></header><div className="learning-answer-index">{attempt.paper.questions.map((q,i) => <a href={`#exam-${q.id}`} key={q.id} aria-label={`第${i+1}题`}>{i+1}{answers?.[q.id]?.length ? ' ✓' : ''}</a>)}</div>{attempt.paper.questions.map((q,i) => <fieldset disabled={!active || submit.isPending || action.isPending} key={q.id} id={`exam-${q.id}`}><legend>{i+1}. {q.prompt} <small>{q.points} 分{q.critical ? ' · 关键题' : ''}</small></legend>{q.options.length > 0 ? q.options.map((option,j) => {const value = answers?.[q.id]; return <label key={j}><input type={q.kind === 'multiple_choice' ? 'checkbox' : 'radio'} name={q.id} checked={Array.isArray(value) ? value.includes(String(j)) : value === String(j)} onChange={e => edit(q.id, q.kind === 'multiple_choice' ? e.target.checked ? [...(Array.isArray(value)?value:[]),String(j)] : (Array.isArray(value)?value:[]).filter(x=>x!==String(j)) : String(j))} />{option}</label>}) : q.kind === 'fill_blank' ? <input aria-label={`第${i+1}题答案`} maxLength={2000} value={String(answers?.[q.id] ?? '')} onChange={e=>edit(q.id,e.target.value)} /> : <textarea aria-label={`第${i+1}题答案`} rows={6} maxLength={8000} value={String(answers?.[q.id] ?? '')} onChange={e=>edit(q.id,e.target.value)} placeholder="写出判断、理由与关键步骤…" />}</fieldset>)}{attempt.activity_spec && attempt.paper.practical_points > 0 && <ActivityCard spec={attempt.activity_spec} state={attempt.activity} exam busy={!active || busy || dirty} onAction={id=>action.mutateAsync({id})} onReset={()=>action.mutate({reset:true})} />}{active && <div className="learning-actions">{dirty && <Button variant="outline" disabled={busy} onClick={()=>save.mutate({revision:attempt.revision,value:latest.current})}>保存答案</Button>}<Button disabled={dirty || busy} onClick={()=>{const missing=attempt.paper.questions.filter(q=>!answers?.[q.id]?.length).length;if(!missing || window.confirm(`还有 ${missing} 道题未答，确定交卷？`))submit.mutate()}}>交卷评分</Button></div>}{attempt.result && <div className={attempt.status==='passed'?'learning-award':'learning-feedback'} aria-live="polite">{attempt.status==='passed' && <Medal className="size-8" />}<h2>{attempt.result.score !== undefined ? `${attempt.result.score} / 100` : '待复核'}</h2><p>{attempt.result.message}</p>{attempt.result.practical_passed === false && <p>实践必过项尚未通过。</p>}{attempt.result.questions?.map(q=><p key={q.id}><strong>{q.points}/{q.max_points}</strong> · {q.feedback}</p>)}<Button variant="outline" onClick={onRestart}>再挑战一次</Button></div>}</section>
}

export function NodeLearningPage() {
  const {workspaceId = '', nodeId = ''} = useParams()
  const [params, setParams] = useSearchParams()
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [tab, setTab] = useState('lesson')
  const [openError, setOpenError] = useState<string | null>(null)
  // Opening a package is an asynchronous shell operation. Keep the guard
  // outside the query data so polling build status cannot create another chat
  // session, and so React StrictMode's effect replay is harmless.
  const openLearningRef = useRef<string | null>(null)
  const client = useQueryClient()
  const page = useQuery({queryKey: workspaceQueryKey(workspaceId, 'learning-page', nodeId), queryFn: ({signal}) => learningApi.page(nodeId, signal), retry: false, refetchInterval: q => ['queued','running'].includes(q.state.data?.build?.status ?? '') ? 3000 : false})
  const data = page.data
  const updateBuild = (row: Build) => {
    client.setQueryData<LearningPageData>(workspaceQueryKey(workspaceId, 'learning-page', row.node_id), current => current ? {...current, build: row} : current)
    void page.refetch()
    void client.invalidateQueries({queryKey: workspaceQueryKey(workspaceId, 'learning-builds')})
  }
  const build = useMutation({mutationFn: () => learningApi.build(nodeId), onSuccess: updateBuild, onError: (e: Error) => {toast.error(e.message); void page.refetch()}})
  const control = useMutation({mutationFn: (action: 'cancel' | 'retry') => learningApi.control(data!.build!.id, action), onSuccess: updateBuild, onError: (e: Error) => {toast.error(e.message); void page.refetch()}})
  const start = useMutation({mutationFn: () => learningApi.start(nodeId), onSuccess: setEnrollment, onError: (e: Error) => toast.error(e.message)})
  const progress = useMutation({mutationFn: (payload: {section_id?: string; action_id?: string; reset_activity?: boolean}) => learningApi.progress(enrollment!.id, {expected_revision:enrollment!.revision,...payload}), onSuccess: setEnrollment, onError: (e: Error) => {toast.error(e.message); start.mutate()}})
  const beginExam = useMutation({mutationFn: () => learningApi.startAttempt(nodeId, createUuid()), onSuccess: row=>{const next: Record<string, string> = {attempt: row.id}; const returnSession = params.get('returnSession'); if (returnSession) next.returnSession = returnSession; setParams(next); setTab('exam')}, onError:(e:Error)=>toast.error(e.message)})
  useEffect(()=>{setEnrollment(null);setTab('lesson')},[nodeId])
  useEffect(()=>{if(data?.enrollment)setEnrollment(data.enrollment)},[data?.enrollment])
  useEffect(()=>{if(!params.get('attempt')&&!params.get('tab'))setTab(data?.node.node_type==='assessment'?'exam':data?.node.node_type==='practice'?'lab':'lesson')},[data?.node.node_type,nodeId,params])
  useEffect(()=>{if(params.get('attempt'))setTab('exam');else if(params.get('tab'))setTab(params.get('tab')!)},[params])
  useEffect(()=>()=>{void client.invalidateQueries({queryKey: workspaceQueryKey(workspaceId,'graphs')});void client.invalidateQueries({queryKey: workspaceQueryKey(workspaceId,'graph')})},[client,workspaceId])
  const examRoute = Boolean(params.get('attempt') || params.get('tab') === 'exam')
  const examViewActive = examRoute || tab === 'exam'
  const openInChat = () => {
    if (!data?.node.graph_id || !nodeId || examViewActive) return
    const key = `${workspaceId}:${nodeId}`
    openLearningRef.current = key
    setOpenError(null)
    window.dispatchEvent(new CustomEvent('learngraph:open-learning-project', {detail: {
      graphId: data.node.graph_id,
      title: data.node.graph_title ?? data.node.label,
      nodeId,
      nodeLabel: data.node.label,
      learningPackage: true,
      onError: (message: string) => {
        if (openLearningRef.current !== key) return
        openLearningRef.current = null
        setOpenError(message || '无法打开学习对话。')
      },
    }}))
  }
  useEffect(() => {
    // The assessment remains a deliberately separate route. Every other
    // visit hands the prepared package to the shared conversation canvas,
    // where the package can stream independently from the chat messages.
    if (!data || examRoute || openLearningRef.current === `${workspaceId}:${nodeId}`) return
    openInChat()
    // `data` is intentionally the readiness boundary; the ref prevents a
    // refetch/poll from dispatching another create-conversation request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, examRoute, nodeId, workspaceId])
  if (!data) return <div className="node-learning-page"><p role="status">{page.error?.message ?? '正在读取节点学习页…'}</p><Button variant="outline" onClick={()=>void page.refetch()}>重试</Button></div>
  const manifest = data.package?.manifest
  const returnSession = params.get('returnSession')
  const returnHref = returnSession ? `/w/${workspaceId}/chat/${encodeURIComponent(returnSession)}` : `/w/${workspaceId}/graphs/${data.node.graph_id}?node=${nodeId}`
  const returnLabel = returnSession ? '返回学习对话' : '返回图谱'
  return <main className="node-learning-page"><header className="learning-page-header"><Link to={returnHref}><ArrowLeft className="size-4" />{returnLabel}</Link><LearningBuildSettings graphId={data.node.graph_id} /></header>{openError && <section className="learning-feedback" role="alert"><p>学习对话打开失败：{openError}</p><Button size="sm" variant="outline" onClick={openInChat}>重试打开对话</Button></section>}<div className="learning-title"><span>{data.node.node_type==='practice'?'交互实验':data.node.node_type==='assessment'?'测评关卡':'知识教材'}</span><h1>{data.node.label}</h1><p>{data.node.description}</p>{data.achievement && <span className="learning-gold"><Medal className="size-4" />{data.achievement.outdated?'历史通关（定义已更新）':'已验证'} · 最佳 {data.achievement.score} 分</span>}</div>{!manifest ? <section className="learning-empty"><BookOpen className="size-9" /><h2>为这个节点准备一份完整学习页</h2><p>{data.node.graph_status==='candidate'?'请先回到图谱审核并发布，再创建学习页。':'教材、图解、实验和试卷会在后台生成，离开页面也能继续。'}</p>{data.build && <LearningBuildProgress build={data.build} busy={control.isPending} refreshing={page.isFetching} error={control.error?.message ?? page.error?.message} onRefresh={()=>void page.refetch()} onControl={action=>control.mutate(action)} />}{build.error && <p role="alert">{build.error.message}</p>}{(!data.build || ['cancelled','skipped','ready'].includes(data.build.status)) && <Button disabled={data.node.graph_status==='candidate' || build.isPending} onClick={()=>build.mutate()}>{build.isPending && <LoaderCircle className="size-4 animate-spin"/>}{build.isPending ? '正在提交任务…' : data.build ? '重新创建学习页' : '创建学习页'}</Button>}</section> : <>{data.build && ['queued','running','failed'].includes(data.build.status) && <LearningBuildProgress build={data.build} busy={control.isPending} refreshing={page.isFetching} error={control.error?.message ?? page.error?.message} onRefresh={()=>void page.refetch()} onControl={action=>control.mutate(action)} />}{data.stale && <p className="learning-feedback">节点定义已更新，当前教材保留原版本。已有进度和试卷不会被覆盖。</p>}<nav className="learning-tabs" aria-label="学习内容"><Button variant={tab==='lesson'?'default':'ghost'} onClick={()=>setTab('lesson')}>教材与任务</Button>{manifest.activity && <Button variant={tab==='lab'?'default':'ghost'} onClick={()=>setTab('lab')}>互动实验</Button>}<Button variant={tab==='exam'?'default':'ghost'} onClick={()=>setTab('exam')}>闯关测评</Button></nav>{tab==='lesson' && <div className="learning-reading"><aside><h3>本节目标</h3><ul>{manifest.blueprint.objectives.map((o,i)=><li key={i}>{o}</li>)}</ul><p>预计 {manifest.blueprint.estimated_minutes} 分钟</p><nav aria-label="章节目录">{manifest.lesson.sections.map((s,i)=><a key={s.id} href={`#lesson-${s.id}`}>{enrollment?.progress.sections.includes(s.id)?'✓':String(i+1).padStart(2,'0')} {s.title}</a>)}</nav>{!enrollment && <Button disabled={start.isPending} onClick={()=>start.mutate()}>开始 / 恢复学习</Button>}</aside><article>{manifest.image && <LessonImage fileId={manifest.image.file_id} alt={manifest.image.alt}/>}<figure><img alt={manifest.lesson.caption} src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(manifest.lesson.svg)}`} /><figcaption>{manifest.lesson.caption}</figcaption></figure>{manifest.lesson.sections.map(s=><section id={`lesson-${s.id}`} key={s.id}><h2>{s.title}</h2><div className="learning-prose"><IncrementalMarkdown text={s.body} codeHighlight="plain" /></div><p className="learning-takeaway">{s.takeaway}</p><Button size="sm" variant="outline" disabled={!enrollment || progress.isPending || enrollment.progress.sections.includes(s.id)} onClick={()=>progress.mutate({section_id:s.id})}><Check className="size-4" />{enrollment?.progress.sections.includes(s.id)?'本节已读':'标记本节已读'}</Button></section>)}{manifest.lesson.html && <InlinePreview html={manifest.lesson.html} title="本节交互演示"/>}<p className="learning-muted">{manifest.provenance}</p>{manifest.notes.map((n,i)=><p className="learning-muted" key={i}>{n}</p>)}</article></div>}{tab==='lab' && manifest.activity && (enrollment ? <ActivityCard spec={manifest.activity} state={enrollment.progress.activity} busy={progress.isPending} onAction={action_id=>progress.mutateAsync({action_id})} onReset={()=>progress.mutate({reset_activity:true})} /> : <section className="learning-empty"><h2>{manifest.activity.title}</h2><p>{manifest.activity.instructions}</p><Button disabled={start.isPending} onClick={()=>start.mutate()}>开始 / 恢复实验</Button></section>)}{tab==='exam' && (params.get('attempt') ? <ExamView key={params.get('attempt')} attemptId={params.get('attempt')!} onRestart={()=>beginExam.mutate()}/> : <section className="learning-empty"><Medal className="size-9"/><h2>{manifest.exam.title}</h2><p>{manifest.exam.questions.length} 道笔试题{manifest.exam.practical_points>0?' + 独立实践卡':''} · 满分100 · {manifest.exam.pass_score}分通过</p><p>交卷后统一反馈。正式挑战不沿用训练完成状态。</p>{data.latest_attempt && <Button variant="outline" onClick={()=>setParams({attempt:data.latest_attempt!.id, ...(returnSession ? {returnSession} : {})})}>{["active","grading"].includes(data.latest_attempt.status)?"继续上次测评":"查看上次成绩"}</Button>}<Button disabled={beginExam.isPending} onClick={()=>beginExam.mutate()}>开始 / 继续测评</Button></section>)}</>}<footer className="learning-page-footer">{returnSession ? <Button size="sm" variant="outline" asChild><Link to={returnHref}><MessageCircle className="size-4"/>返回学习对话</Link></Button> : <Button size="sm" variant="outline" onClick={openInChat}><MessageCircle className="size-4"/>打开学习对话</Button>}<Button size="sm" variant="ghost" onClick={()=>window.dispatchEvent(new CustomEvent("learngraph:compose",{detail:{content:`请继续讲解“${data.node.label}”，结合我刚才的学习内容给一个例子。`}}))}>追问当前节点</Button><span>学习进度与正式成绩分别记录，读完教材不会自动获得通关标记。</span></footer></main>
}

import { apiClient } from '@/api/client'

export interface Build { id: string; node_id: string; status: string; stage: string; completed_stages: number; total_stages: number; error?: string; notes: string[]; updated_at?: string; failed_stage?: string | null; failed_item?: string | null; attempts?: number; failure_category?: string; advice?: string | null }
export interface Policy { enabled: boolean; mode: 'path' | 'all'; image_enabled: boolean; revision: number }
export interface ActivitySpec { title: string; instructions: string; html?: string; variables: {id: string; label: string; minimum?:number; maximum?:number; unit?:string; states?:Record<string,string>}[]; actions: {id: string; label: string}[] }
export interface ActivityState { values: Record<string, number>; history: string[]; completed?: boolean; feedback?: string }
export interface Question { id: string; kind: string; prompt: string; options: string[]; points: number; critical: boolean }
export interface Paper { title: string; pass_score: number; practical_points: number; questions: Question[] }
export interface Manifest { schema_version: number; blueprint: {title: string; objectives: string[]; estimated_minutes: number}; lesson: {sections: {id: string; title: string; body: string; takeaway: string}[]; svg: string; caption: string; html: string}; activity: ActivitySpec | null; exam: Paper; image?: {file_id: string; alt: string} | null; notes: string[]; provenance: string }
/**
 * Learner-safe projection of the newest build checkpoint.
 *
 * Unlike a published Manifest this object is intentionally partial: the API
 * exposes it after every completed build stage so the chat canvas can reveal
 * the lesson progressively. It must never be used for grading (the private
 * answer key is only present in the published package / attempt endpoint).
 */
export interface LearningBuildPreview {
  schema_version?: number;
  blueprint?: Manifest['blueprint'] | null;
  lesson?: Pick<Manifest['lesson'], 'sections' | 'svg' | 'caption' | 'html'> | null;
  activity?: ActivitySpec | null;
  exam?: Paper | null;
  image?: { file_id: string; alt: string } | null;
  notes: string[];
  provenance: string;
  available?: {
    blueprint?: boolean;
    lesson?: boolean;
    activity?: boolean;
    exam?: boolean;
    image?: boolean;
  };
  training_ready?: boolean;
  draft_package_id?: string;
  status?: string;
}
export interface LearningPageData { node: {id: string; label: string; description: string; graph_id: string; graph_title?:string; graph_status?:string; node_type: string}; package: {id: string; manifest: Manifest; created_at: string} | null; preview?: LearningBuildPreview | null; stale: boolean; enrollment?: Enrollment | null; latest_attempt?: {id:string;status:string} | null; build: Build | null; achievement: {score: number; attempt_id: string; outdated?:boolean} | null }
export interface Enrollment { id: string; package_id: string; revision: number; progress: {sections: string[]; activity: ActivityState} }
export interface Attempt { id: string; node_id: string; package_id: string; status: string; revision: number; answers: Record<string, string | string[]>; activity: ActivityState; activity_spec: ActivitySpec | null; paper: Paper; result: {score?: number; pass_score?: number; message: string; critical_passed?: boolean; practical_passed?: boolean; questions?: {id: string; points: number; max_points: number; feedback: string}[]} | null }
const url = '/learning-pages'
// These endpoints only enqueue work or read status; model generation is never
// part of their HTTP response. A lost connection must not freeze the controls.
async function statusRequest<T>(request: (signal: AbortSignal) => Promise<T>, parent?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  const onAbort = () => controller.abort()
  if (parent?.aborted) controller.abort()
  parent?.addEventListener('abort', onAbort, {once: true})
  let timedOut = false
  const timer = setTimeout(() => {timedOut = true; controller.abort()}, 20_000)
  try {
    return await request(controller.signal)
  } catch (error) {
    if (timedOut) throw new Error('连接超时，暂时无法确认任务状态。后台任务可能仍在进行，请刷新状态。')
    throw error
  } finally {
    clearTimeout(timer)
    parent?.removeEventListener('abort', onAbort)
  }
}
export const learningApi = {
  page: (id: string, signal?: AbortSignal) => statusRequest(s => apiClient.get<LearningPageData>(`${url}/nodes/${id}`, {signal: s}), signal),
  policy: (id: string) => apiClient.get<Policy>(`${url}/graphs/${id}/policy`),
  setPolicy: (id: string, value: Policy) => apiClient.patch<Policy>(`${url}/graphs/${id}/policy`, {...value, expected_revision: value.revision, revision: undefined}),
  builds: (id: string) => statusRequest(s => apiClient.get<Build[]>(`${url}/graphs/${id}/builds`, {signal: s})),
  build: (id: string) => statusRequest(s => apiClient.post<Build>(`${url}/nodes/${id}/build`, {trigger: 'on_demand'}, {signal: s})),
  control: (id: string, action: 'cancel' | 'retry') => statusRequest(s => apiClient.post<Build>(`${url}/builds/${id}/${action}`, undefined, {signal: s})),
  start: (id: string) => apiClient.post<Enrollment>(`${url}/nodes/${id}/start`),
  progress: (id: string, payload: {expected_revision: number; section_id?: string; action_id?: string; reset_activity?: boolean}) => apiClient.patch<Enrollment>(`${url}/enrollments/${id}`, payload),
  startAttempt: (id: string, key: string) => apiClient.post<Attempt>(`${url}/nodes/${id}/attempts`, {request_key: key}),
  attempt: (id: string) => apiClient.get<Attempt>(`${url}/attempts/${id}`),
  save: (id: string, revision: number, answers: Attempt['answers']) => apiClient.patch<Attempt>(`${url}/attempts/${id}/answers`, {expected_revision: revision, answers}),
  action: (id: string, revision: number, action_id?: string, reset_activity = false) => apiClient.patch<Attempt>(`${url}/attempts/${id}/activity`, {expected_revision: revision, action_id, reset_activity}),
  submit: (id: string) => apiClient.post<Attempt>(`${url}/attempts/${id}/submit`),
}

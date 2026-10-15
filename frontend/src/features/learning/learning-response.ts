import { z } from 'zod'
import type { Attempt, Enrollment, LearningPageData } from './node-learning-api'

const activity = z.object({
  title: z.string(), instructions: z.string(), html: z.string().optional(),
  variables: z.array(z.object({id: z.string(), label: z.string(), states: z.record(z.string(), z.string()).optional()}).passthrough()),
  actions: z.array(z.object({id: z.string(), label: z.string()}).passthrough()),
}).passthrough()
const activityState = z.object({values: z.record(z.string(), z.number()), history: z.array(z.string())}).passthrough()
const blueprint = z.object({title: z.string(), objectives: z.array(z.string()), estimated_minutes: z.number()}).passthrough()
const lesson = z.object({
  sections: z.array(z.object({id: z.string(), title: z.string(), body: z.string(), takeaway: z.string()}).passthrough()),
  svg: z.string(), caption: z.string(), html: z.string(),
}).passthrough()
const paper = z.object({
  title: z.string(), pass_score: z.number(), practical_points: z.number(),
  questions: z.array(z.object({id: z.string(), kind: z.string(), prompt: z.string(), options: z.array(z.string()), points: z.number(), critical: z.boolean()}).passthrough()),
}).passthrough()
const image = z.object({file_id: z.string(), alt: z.string()}).passthrough().nullish()
const manifest = z.object({blueprint, lesson, activity: activity.nullable(), exam: paper, image, notes: z.array(z.string()), provenance: z.string()}).passthrough()
const preview = z.object({
  blueprint: blueprint.nullish(), lesson: lesson.nullish(), activity: activity.nullish(), exam: paper.nullish(), image,
  notes: z.array(z.string()), provenance: z.string(),
}).passthrough()
const enrollment = z.object({
  id: z.string(), package_id: z.string(), revision: z.number(),
  progress: z.object({sections: z.array(z.string()), activity: activityState}).passthrough(),
}).passthrough()
const page = z.object({
  node: z.object({id: z.string(), label: z.string(), description: z.string(), graph_id: z.string(), node_type: z.string()}).passthrough(),
  package: z.object({id: z.string(), manifest}).passthrough().nullable(),
  preview: preview.nullish(), enrollment: enrollment.nullish(),
  build: z.object({id: z.string(), status: z.string()}).passthrough().nullable(),
}).passthrough()
const attempt = z.object({
  id: z.string(), node_id: z.string(), package_id: z.string(), revision: z.number(), status: z.string(),
  answers: z.record(z.string(), z.union([z.string(), z.array(z.string())])),
  activity: activityState, activity_spec: activity.nullable(), paper,
  result: z.object({message: z.string(), questions: z.array(z.object({id: z.string(), points: z.number(), max_points: z.number(), feedback: z.string()}).passthrough()).optional()}).passthrough().nullable(),
}).passthrough()
function checked<T>(schema: z.ZodType, value: unknown, label: string): T {
  const parsed = schema.safeParse(value)
  if (!parsed.success) throw new Error(`${label}返回的数据格式不完整，请重新读取；若仍失败，请联系管理员检查该内容。`)
  return parsed.data as T
}
export const validateLearningPage = (value: unknown): LearningPageData => checked(page, value, '学习包')
export const validateEnrollment = (value: unknown): Enrollment => checked(enrollment, value, '学习进度')
export const validateAttempt = (value: unknown): Attempt => checked(attempt, value, '测评')

import type { MemoryEntry, MemoryProfileDimension } from '@/types/memory'

/**
 * 人类可读的展示层映射：把记忆系统内部的工程词汇（memory_type / dimension key /
 * lifecycle 状态）转换成学习者能直接读懂的语言。
 *
 * 这里只做「翻译」，不产生任何新事实：每个标签都能回指后端已经返回的真实字段。
 */

/** backend/app/domain/memory_types.py · MEMORY_TYPE_REGISTRY 的用户语言映射。 */
const MEMORY_TYPE_LABELS: Record<string, string> = {
  learning_preference: '学习偏好',
  teacher_focus: '学习目标',
  goal_constraint: '学习目标',
  misconception: '常见误区',
  strategy_effectiveness: '有效学习策略',
  decision: '重要决定',
  ai_observation: '当前状态',
  event_summary: '学习记录',
  semantic_memory: '稳定偏好',
}

export function memoryCategoryLabel(item: {
  record_kind?: string | null
  atom_kind?: string | null
}): string {
  const kind = (item.record_kind || item.atom_kind || '').trim()
  return MEMORY_TYPE_LABELS[kind] ?? '其他记忆'
}

/** 全部记忆页的分类筛选项：只保留真实出现过的分类，按固定语义顺序排列。 */
const CATEGORY_ORDER = [
  '学习偏好',
  '学习目标',
  '当前状态',
  '常见误区',
  '有效学习策略',
  '重要决定',
  '学习记录',
  '稳定偏好',
  '其他记忆',
]

export function memoryCategoryOptions(items: MemoryEntry[]): string[] {
  const present = new Set(items.map((item) => memoryCategoryLabel(item)))
  return CATEGORY_ORDER.filter((label) => present.has(label))
}

export function formatMemoryTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

/** “今天更新 / 3 天前更新 / 2026年9月12日 更新”这类低干扰时间提示。 */
export function memoryUpdatedHint(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60_000))
  if (elapsedMinutes < 5) return '刚刚更新'
  if (elapsedMinutes < 60) return `${elapsedMinutes} 分钟前更新`
  const hours = Math.floor(elapsedMinutes / 60)
  if (hours < 24) return `${hours} 小时前更新`
  const days = Math.floor(hours / 24)
  if (days === 1) return '昨天更新'
  if (days < 14) return `${days} 天前更新`
  return `${date.toLocaleDateString('zh-CN')} 更新`
}

/** “刚刚 / 2 小时前 / 昨天 / 3 天前 / 2026年9月12日”：用于来源与时间说明。 */
export function memoryRelativeTime(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60_000))
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days === 1) return '昨天'
  if (days < 14) return `${days} 天前`
  return date.toLocaleDateString('zh-CN')
}

/**
 * 报告「上次刷新失败」的人话版本。
 *
 * 后端在重写失败时把原因写进 profile.stale_reason（`refresh_failed|原因|时间`），
 * 这里只做翻译，不改变任何判定；不是失败态时返回 null，页面照旧显示原横幅。
 */
export function memoryRefreshFailure(
  reason: string | null | undefined,
): { message: string; at: string | null } | null {
  const value = (reason ?? '').trim()
  if (!value.startsWith('refresh_failed|')) return null
  const [, detail = '', at = ''] = value.split('|')
  const text = detail.toLowerCase()
  let message = '模型这次没能完成整理'
  if (/timed out|timeout/.test(text)) message = '模型响应超时'
  else if (/unavailable/.test(text)) message = '记忆模型当前不可用'
  else if (/unconfigured/.test(text)) message = '还没有配置记忆模型'
  else if (/entailment|unsupported claims/.test(text)) message = '生成的段落没有通过事实校验'
  else if (/invalid/.test(text)) message = '模型返回的内容不符合格式要求'
  return { message, at: at ? memoryRelativeTime(at) : null }
}

/**
 * 报告章节 ↔ 后端稳定 dimension key 的映射。
 *
 * 内容完全来自后端 profile（每个段落都由 atom_ids 支撑），前端只负责重新组织
 * 阅读顺序与标题，不生成任何新的判断。没有数据支撑的章节直接不渲染。
 */
export const MEMORY_REPORT_SECTIONS: Array<{
  id: string
  title: string
  description?: string
  keys: string[]
}> = [
  {
    id: 'stage-review',
    title: '本阶段复盘',
    description: 'AI 导师基于当前证据对最近阶段的回顾',
    keys: ['current_state'],
  },
  {
    id: 'direction',
    title: '你正在走向哪里',
    description: '已经稳定下来的长期方向',
    keys: ['long_term_direction'],
  },
  {
    id: 'how-you-learn',
    title: '你是怎样学习的',
    description: '学习方式与协作偏好',
    keys: ['learning_style', 'output_collaboration'],
  },
  {
    id: 'strengths',
    title: '你的优势与特点',
    description: '已经有稳定证据支撑的特点',
    keys: ['stable_technical_preference'],
  },
  {
    id: 'growth',
    title: '当前值得突破的地方',
    description: '下一步更值得投入的方向',
    keys: ['long_term_limit'],
  },
  {
    id: 'environment',
    title: '常用学习环境',
    description: '你通常在哪里、用什么方式学习',
    keys: ['common_environment'],
  },
  {
    id: 'recent-state',
    title: '近期学习状态',
    description: '最近这一段时间的状态变化',
    keys: ['recent_state', 'learning_progress'],
  },
]

export type MemoryReportSection = {
  id: string
  title: string
  description?: string
  dimensions: MemoryProfileDimension[]
}

/**
 * 把后端 dimensions 重新组织成报告章节。
 *
 * - 命中的稳定 key 归入对应章节；
 * - 未命中的 key（模型自行新增的维度）保留原始 title 作为补充章节；
 * - 空章节不返回（不渲染空壳卡片）。
 */
export function buildMemoryReportSections(
  dimensions: MemoryProfileDimension[],
): MemoryReportSection[] {
  const remaining = new Map<string, MemoryProfileDimension>()
  dimensions.forEach((dimension) => {
    const key = (dimension.key || '').trim()
    if (!key) return
    if (!remaining.has(key)) remaining.set(key, dimension)
  })

  const sections: MemoryReportSection[] = []
  MEMORY_REPORT_SECTIONS.forEach((section) => {
    const matched: MemoryProfileDimension[] = []
    section.keys.forEach((key) => {
      const dimension = remaining.get(key)
      if (!dimension) return
      remaining.delete(key)
      if (dimension.paragraphs.length) matched.push(dimension)
    })
    if (matched.length) {
      sections.push({
        id: section.id,
        title: section.title,
        description: section.description,
        dimensions: matched,
      })
    }
  })

  // 模型输出的、不在稳定词表里的维度按原始顺序兜底展示，标题沿用后端 title。
  remaining.forEach((dimension, key) => {
    if (!dimension.paragraphs.length) return
    sections.push({
      id: `extra-${key}`,
      title: dimension.title || key,
      dimensions: [dimension],
    })
  })

  return sections
}

import type { MemoryProfile, MemoryProfileDimension } from '@/types/memory'

type Paragraph = MemoryProfileDimension['paragraphs'][number]

export function memoryParagraphKey(section: string, paragraph: Paragraph, index: number) {
  const identity = paragraph.id || [...paragraph.atom_ids].sort().join(',') || String(index)
  return `${section}:${identity}`
}

export function memoryReportContent(profile: MemoryProfile | undefined) {
  const content = new Map<string, string>()
  if (!profile) return content
  const add = (key: string, text: string) => {
    if (text.trim()) content.set(key, text.replace(/\s+/g, ' ').trim())
  }
  add('overview', profile.overview)
  for (const dimension of profile.dimensions) {
    dimension.paragraphs.forEach((paragraph, index) => add(memoryParagraphKey(dimension.key, paragraph, index), paragraph.text))
  }
  for (const section of profile.structured_sections) {
    section.paragraphs.forEach((paragraph, index) => add(memoryParagraphKey(section.heading, paragraph, index), paragraph.text))
  }
  if (!profile.dimensions.length && !profile.structured_sections.length) add('markdown', profile.markdown)
  return content
}

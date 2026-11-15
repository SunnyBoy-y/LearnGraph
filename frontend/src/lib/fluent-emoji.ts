import data from './fluent-emoji-data.json';

export interface FluentEmojiEntry {
  name: string;
  code: string;
  glyph: string;
  label: string;
}

const entries = data.entries.map(([name, code, glyph, label]) => ({ name, code, glyph, label }));
const byName = new Map(entries.map(entry => [entry.name, entry]));
const byCode = new Map(entries.map(entry => [entry.code.replaceAll('_', '-'), entry]));
const normalizeGlyph = (glyph: string) => glyph.replace(/\ufe0f/g, '');
const byGlyph = new Map(entries.map(entry => [normalizeGlyph(entry.glyph), entry]));
// Common Microsoft/CLDR names retained alongside the current Unicode names.
const aliases: Record<string, string> = {
  'direct-hit': 'bullseye',
  'tada': 'party-popper',
  'bulb': 'light-bulb',
  'heart': 'red-heart',
  'thumbsup': 'thumbs-up',
  'thumbs-up-sign': 'thumbs-up',
  'white-check-mark': 'check-mark-button',
  'heavy-check-mark': 'check-mark',
  'memo': 'memo',
  'thinking': 'thinking-face',
  'fireworks': 'fireworks',
};

export const fluentEmojiCount = entries.length;
export const fluentEmojiPrefix = `${import.meta.env.BASE_URL}fluent-emoji/${data.version}/`;
export const fluentEmojiNamePattern = /^fluent:[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function getFluentEmoji(name: string | null | undefined): FluentEmojiEntry | undefined {
  if (!name || name.length > 100 || !fluentEmojiNamePattern.test(name)) return undefined;
  const slug = name.slice(7);
  return byName.get(aliases[slug] ?? slug) ?? byCode.get(slug);
}

export function fluentEmojiGlyph(name: string | null | undefined): string | undefined {
  return getFluentEmoji(name)?.glyph;
}

export function fluentEmojiSource(entry: FluentEmojiEntry): string {
  return `${fluentEmojiPrefix}${entry.code}.webp`;
}

/** Only exact trusted local assets may bypass Markdown's normal image renderer. */
export function fluentEmojiFromSource(source: unknown): FluentEmojiEntry | undefined {
  if (typeof source !== 'string' || !source.startsWith(fluentEmojiPrefix)) return undefined;
  const filename = source.slice(fluentEmojiPrefix.length);
  if (!/^[0-9a-f]+(?:[-_][0-9a-f]+)*\.webp$/.test(filename)) return undefined;
  return byCode.get(filename.slice(0, -5).replaceAll('_', '-'));
}

let segmenter: Intl.Segmenter | undefined;
export type FluentEmojiTextPart = string | FluentEmojiEntry;

/** Grapheme boundaries keep skin tones, flags and ZWJ families together. */
export function splitFluentEmoji(text: string): FluentEmojiTextPart[] {
  if (!/[\p{Extended_Pictographic}\p{Regional_Indicator}\u20e3]/u.test(text) || typeof Intl.Segmenter !== 'function') return [text];
  segmenter ??= new Intl.Segmenter('en', { granularity: 'grapheme' });
  const result: FluentEmojiTextPart[] = [];
  let start = 0;
  for (const { segment, index } of segmenter.segment(text)) {
    // Preserve explicit text presentation and bare text-style symbols (©, ™).
    if (segment.includes('\ufe0e') || !/[\p{Emoji_Presentation}\ufe0f\u20e3]/u.test(segment)) continue;
    const emoji = byGlyph.get(normalizeGlyph(segment));
    if (!emoji) continue;
    if (index > start) result.push(text.slice(start, index));
    result.push(emoji);
    start = index + segment.length;
  }
  if (start < text.length) result.push(text.slice(start));
  return result.length ? result : [text];
}

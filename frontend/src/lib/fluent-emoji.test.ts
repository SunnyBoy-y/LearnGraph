import { describe, expect, it } from 'vitest';
import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import type { Element, Root } from 'hast';
import data from './fluent-emoji-data.json';
import { fluentEmojiCount, fluentEmojiFromSource, fluentEmojiGlyph, fluentEmojiSource, getFluentEmoji, splitFluentEmoji } from './fluent-emoji';
import { rehypeFluentEmoji } from './rehype-fluent-emoji';

describe('local Fluent Emoji registry', () => {
  it('resolves the complete pinned image distribution with unique filenames', () => {
    const directory = path.resolve(process.cwd(), 'node_modules/@lobehub/fluent-emoji-3d/assets');
    const files = readdirSync(directory).filter(name => name.endsWith('.webp'));
    expect(fluentEmojiCount).toBe(3391);
    expect(data.entries).toHaveLength(files.length);
    expect(new Set(data.entries.map(entry => entry[0])).size).toBe(data.entries.length);
    for (const [name, code, glyph] of data.entries) {
      expect(existsSync(path.join(directory, `${code}.webp`)), code).toBe(true);
      expect(getFluentEmoji(`fluent:${code.replaceAll('_', '-')}`)?.glyph, code).toBe(glyph);
      expect(fluentEmojiFromSource(fluentEmojiSource(getFluentEmoji(`fluent:${name}`)!))?.code).toBe(code);
      expect(getFluentEmoji(`fluent:${name}`)?.code, name).toBe(code);
    }
  });
  it('accepts model-friendly names, aliases and exact local sources', () => {
    expect(fluentEmojiGlyph('fluent:direct-hit')).toBe('🎯');
    expect(fluentEmojiGlyph('fluent:rocket')).toBe('🚀');
    expect(fluentEmojiGlyph('fluent:light-bulb')).toBe('💡');
    const entry = getFluentEmoji('fluent:brain')!;
    expect(fluentEmojiSource(entry)).toMatch(/^\/fluent-emoji\/1\.1\.0\/1f9e0\.webp$/);
    expect(fluentEmojiFromSource(fluentEmojiSource(entry))).toBe(entry);
  });
  it.each(['fluent:../../secret', 'https://evil.test/a.webp', 'fluent:rocket?x=1', 'fluent:rocket#x', 'fluent:__proto__', 'fluent:Rocket', 'fluent:-rocket', 'fluent:rocket--test', 'fluent:missing-icon', 'fluent:' + 'a'.repeat(94)])('does not resolve untrusted or unknown names: %s', name => {
    expect(getFluentEmoji(name)).toBeUndefined();
  });
  it.each(['https://evil.test/fluent-emoji/1.1.0/1f680.webp', '/fluent-emoji/1.1.0/../../../secret', '/fluent-emoji/1.1.0/1f680.webp?x=1', '/fluent-emoji/1.1.0/1f680.webp#x', '/fluent-emoji/1.1.0/does-not-exist.webp'])('does not treat arbitrary source as a trusted asset: %s', source => {
    expect(fluentEmojiFromSource(source)).toBeUndefined();
  });
});

describe('prose emoji parsing', () => {
  const glyphs = (text: string) => splitFluentEmoji(text).filter(part => typeof part !== 'string').map(part => part.glyph);
  it('preserves entire family, skin tone and ZWJ sequences', () => {
    const parts = splitFluentEmoji('学习 🚀 + 👩🏽‍💻 + 👨‍👩‍👧‍👦 完成');
    expect(parts.filter(part => typeof part !== 'string')).toHaveLength(3);
    expect(parts.map(part => typeof part === 'string' ? part : part.glyph).join('')).toBe('学习 🚀 + 👩🏽‍💻 + 👨‍👩‍👧‍👦 完成');
  });
  it('maps tagged subdivision flags without collapsing them into a black flag', () => {
    const england = String.fromCodePoint(0x1f3f4, 0xe0067, 0xe0062, 0xe0065, 0xe006e, 0xe0067, 0xe007f);
    expect(fluentEmojiGlyph('fluent:flag-england')).toBe(england);
    const [part] = splitFluentEmoji(england);
    expect(typeof part).toBe('object');
    expect(typeof part !== 'string' && part.glyph).toBe(england);
    expect(typeof part !== 'string' && fluentEmojiSource(part)).toContain('1f3f4-e0067_e0062_e0065_e006e_e0067.webp');
  });
  it('leaves plain text, unknown emoji and explicit text presentation unchanged', () => {
    expect(splitFluentEmoji('普通文字 © ™ ♥︎')).toEqual(['普通文字 © ™ ♥︎']);
    expect(splitFluentEmoji('\u{1FAFF}')).toEqual(['\u{1FAFF}']);
    expect(glyphs('⚠️ 警告')).toEqual(['⚠️']);
  });
  it('never turns a flag sequence into a different flag or drops trailing text', () => {
    for (const text of ['🇨🇳', '🇺🇸', '🏳️‍🌈', '👩', '👩‍', '👩‍💻 后续']) {
      expect(splitFluentEmoji(text).map(part => typeof part === 'string' ? part : part.glyph).join('')).toBe(text);
    }
  });
  it('does not modify code, math, URLs, alt attributes, or existing emoji wrappers', () => {
    const element = (tagName: string, text: string, properties: Element['properties'] = {}): Element => ({
      type: 'element', tagName, properties, children: [{ type: 'text', value: text }],
    });
    const tree: Root = { type: 'root', children: [
      element('p', '🚀'), element('a', '🧠', { href: 'https://example.com/🚀' }),
      element('code', '🚀'), element('pre', '🧠'), element('math', '🚀'),
      element('span', '🧠', { className: ['katex'] }), element('img', '', { alt: '🚀', src: '/x.png' }),
      element('span', '🚀', { dataFluentEmoji: '1f680' }),
    ] };
    rehypeFluentEmoji()(tree);
    const json = JSON.stringify(tree);
    expect(json.match(/"dataFluentEmoji"/g)).toHaveLength(3);
    expect(json).toContain('https://example.com/🚀');
    expect(json).toContain('"alt":"🚀"');
  });
});

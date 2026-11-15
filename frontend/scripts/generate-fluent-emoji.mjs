import { createHash } from 'node:crypto';
import { readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Explicit maintenance command only. Builds and runtime never fetch metadata.
const source = 'https://unicode.org/Public/emoji/15.1/emoji-test.txt';
const response = await fetch(source);
if (!response.ok) throw new Error(`Unicode metadata: HTTP ${response.status}`);
const text = await response.text();
const directory = new URL('../node_modules/@lobehub/fluent-emoji-3d/assets/', import.meta.url);
const files = (await readdir(directory)).filter(name => name.endsWith('.webp')).sort();
const normalize = code => code.split(/[-_]/).map(part => parseInt(part, 16).toString(16)).filter(part => part !== 'fe0f').join('-').replace(/-e007f$/, '');
const byCode = new Map(files.map(name => [normalize(name.slice(0, -5)), name.slice(0, -5)]));
const records = new Map();
const slugify = name => name.replace(/#/g, ' number sign ').replace(/\*/g, ' asterisk ').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
for (const line of text.split('\n')) {
  const match = /^([A-F0-9 ]+)\s*;\s*fully-qualified\s*#\s*(\S+)\s+E[\d.]+\s+(.+)$/.exec(line);
  if (!match) continue;
  const code = match[1].trim().toLowerCase().split(/\s+/).map(part => part.padStart(4, '0')).join('-');
  const asset = byCode.get(normalize(code));
  if (!asset) continue;
  const glyph = match[2];
  const label = match[3].trim();
  records.set(asset, [slugify(label), asset, glyph, label]);
}
// Assets introduced after this Unicode snapshot remain available by codepoint slug.
for (const file of files) {
  const code = file.slice(0, -5);
  if (!records.has(code)) records.set(code, [code.replaceAll('_', '-'), code, String.fromCodePoint(...code.split(/[-_]/).map(part => parseInt(part, 16))), code]);
}
if (new Set([...records.values()].map(entry => entry[0])).size !== records.size) throw new Error('Duplicate Fluent Emoji names');
const data = {
  version: '1.1.0',
  unicodeSource: source,
  unicodeSha256: createHash('sha256').update(text).digest('hex'),
  entries: [...records.values()].sort((a, b) => a[0].localeCompare(b[0], 'en')),
};
const target = fileURLToPath(new URL('../src/lib/fluent-emoji-data.json', import.meta.url));
await writeFile(target, JSON.stringify(data) + '\n');
console.log(`Generated ${data.entries.length} local Fluent Emoji entries (${records.size} unique assets).`);

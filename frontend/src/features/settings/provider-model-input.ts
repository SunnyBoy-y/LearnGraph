export function parseModelIds(text: string, existing: readonly string[] = []): string[] {
  const ids = [...new Set(text.split(/[\s,;，；]+/u).map((value) => value.trim()).filter(Boolean))];
  if (ids.some((id) => id.length > 160)) throw new Error('单个模型ID不能超过160个字符');
  if (ids.length > 200) throw new Error('一次最多添加200个模型');
  const current = new Set(existing);
  return ids.filter((id) => !current.has(id));
}

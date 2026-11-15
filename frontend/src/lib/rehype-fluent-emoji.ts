import type { Element, Root, RootContent } from 'hast';
import { splitFluentEmoji } from './fluent-emoji';

const literalElements = new Set(['pre', 'code', 'script', 'style', 'svg', 'math']);

/** Replace prose emoji only; code, math, attributes and URLs remain untouched. */
export function rehypeFluentEmoji() {
  return (tree: Root) => {
    const visit = (parent: Root | Element) => {
      if (parent.type === 'element' && (literalElements.has(parent.tagName) ||
        parent.properties.dataFluentEmoji ||
        /(?:^|\s)(?:katex|math-inline|math-display)(?:\s|$)/.test(String(parent.properties.className ?? '').replaceAll(',', ' ')))) return;
      const children: RootContent[] = [];
      for (const child of parent.children) {
        if (child.type === 'element') visit(child);
        if (child.type !== 'text') { children.push(child); continue; }
        for (const part of splitFluentEmoji(child.value)) {
          children.push(typeof part === 'string' ? { type: 'text', value: part } : {
            type: 'element', tagName: 'span', properties: { dataFluentEmoji: part.code.replaceAll('_', '-') },
            children: [{ type: 'text', value: part.glyph }],
          });
        }
      }
      parent.children = children as Element['children'];
    };
    visit(tree);
  };
}

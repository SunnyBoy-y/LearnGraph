import { memo, useState } from 'react';
import {
  Zap, BrainCircuit, Search, Layers3, ShieldCheck, Lightbulb, CircleDot,
  CheckCheck, Blocks, SlidersHorizontal, FileText, Database, ChartNoAxesCombined,
  BookOpen, Target, Code2,
} from 'lucide-react';
import { fluentEmojiSource, getFluentEmoji, type FluentEmojiEntry } from '@/lib/fluent-emoji';
import './answer-icon.css';

const legacyIcons = {
  zap: Zap, 'brain-circuit': BrainCircuit, search: Search, layers: Layers3,
  shield: ShieldCheck, lightbulb: Lightbulb, 'circle-dot': CircleDot,
  'check-check': CheckCheck, blocks: Blocks, sliders: SlidersHorizontal,
  'file-text': FileText, database: Database, chart: ChartNoAxesCombined,
  'book-open': BookOpen, target: Target, code: Code2,
};

type Size = 'inline' | 'small' | 'large';

export const FluentEmoji = memo(function FluentEmoji({ emoji, label, size = 'inline', className = '' }: {
  emoji: FluentEmojiEntry; label?: string; size?: Size; className?: string;
}) {
  const src = fluentEmojiSource(emoji);
  const [failedSource, setFailedSource] = useState('');
  const decorative = label === '';
  const classes = `answer-icon answer-icon--${size} ${className}`;
  if (failedSource === src) return <span className={classes} role={decorative ? undefined : 'img'}
    aria-hidden={decorative || undefined} aria-label={decorative ? undefined : label || emoji.label}>{emoji.glyph}</span>;
  return <img className={classes} src={src} alt={decorative ? '' : label || emoji.glyph}
    aria-label={label || undefined} aria-hidden={decorative || undefined}
    width={size === 'large' ? 48 : 24} height={size === 'large' ? 48 : 24}
    loading="lazy" decoding="async" draggable={false} onError={() => setFailedSource(src)} />;
});

export const AnswerIcon = memo(function AnswerIcon({ name, label = '', size = 'inline', className = '' }: {
  name: string | null | undefined; label?: string; size?: Size; className?: string;
}) {
  if (!name) return null;
  const emoji = getFluentEmoji(name);
  const classes = size === 'inline' ? `answer-icon--leading ${className}` : className;
  if (emoji) return <FluentEmoji emoji={emoji} label={label} size={size} className={classes} />;
  const Icon = legacyIcons[name as keyof typeof legacyIcons] ?? CircleDot;
  return <span className={`answer-icon answer-icon--${size} ${classes}`} role={label ? 'img' : undefined}
    aria-label={label || undefined} aria-hidden={!label || undefined}><Icon width="100%" height="100%" strokeWidth={1.8} aria-hidden="true" /></span>;
});

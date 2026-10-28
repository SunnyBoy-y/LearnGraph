import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Smile, Search } from 'lucide-react';
import { Link } from 'react-router-dom';
import { getStickerPipeline, searchStickers, type Sticker } from '@/api/stickers';
import { useAuth } from '@/features/auth/auth-context-value';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { StickerImage } from './sticker-image';
import { StickerAnalysisDialog } from './sticker-analysis-dialog';

export function StickerPicker({ onSelect, disabled = false }: { onSelect: (sticker: Sticker) => void; disabled?: boolean }) {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [analyzing, setAnalyzing] = useState<Sticker | null>(null);
  const result = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'search', query], queryFn: () => searchStickers(query), enabled: open, staleTime: 5000 });
  const pipeline = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'pipeline'], queryFn: getStickerPipeline, enabled: open, refetchInterval: open ? 3000 : false });
  return <><Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><Button aria-label="选择表情包" className="size-8 shrink-0" disabled={disabled} size="icon" type="button" variant="ghost"><Smile className="size-[18px]" /></Button></PopoverTrigger>
    <PopoverContent align="start" className="w-[min(22rem,calc(100vw-2rem))] p-3" side="top">
      <div className="mb-3 flex items-center justify-between gap-2"><span className="text-sm font-medium">表情包</span><div className="flex gap-3"><Link className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(false)} to={`/w/${auth.workspaceId}/settings/conversation#sticker-market`}>表情市场</Link><Link className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(false)} to={`/w/${auth.workspaceId}/settings/conversation#stickers`}>管理与导入</Link></div></div>
      <div className="relative"><Search aria-hidden="true" className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" /><Input aria-label="搜索表情包" className="pl-9" onChange={event => setQuery(event.target.value)} placeholder="搜索心情、配字或场景" value={query} /></div>
      <div className="mt-3 grid max-h-64 grid-cols-4 gap-1 overflow-y-auto">
        {result.data?.candidates.map(sticker => { const job = pipeline.data?.assets?.find(entry => entry.sticker_id === sticker.id); return <button aria-label={`${sticker.needs_description ? '分析表情' : '添加表情'}：${sticker.name}`} className="flex min-h-20 min-w-0 flex-col items-center rounded-lg p-2 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" key={sticker.id} onClick={() => { setOpen(false); if (sticker.needs_description) setAnalyzing(sticker); else onSelect(sticker); }} title={sticker.needs_description ? '点击分析画面、配字和使用场景' : sticker.semantics.description} type="button"><StickerImage alt={sticker.name} className="size-12" id={sticker.id} poster version={sticker.content_hash} /><span className="mt-1 max-w-full truncate text-[11px] text-muted-foreground">{sticker.name}</span>{sticker.needs_description ? <span className="mt-0.5 text-[10px] text-primary">{job?.status === 'queued' || job?.status === 'leased' ? '分析中…' : job?.status === 'failed' ? '失败 · 点击重试' : '待分析 · 点击识别'}</span> : null}</button>; })}
      </div>
      {result.isPending ? <p className="py-4 text-center text-xs text-muted-foreground">正在加载表情…</p> : result.error ? <p className="py-4 text-xs text-destructive" role="alert">表情加载失败，请重新打开重试。</p> : result.data?.candidates.length === 0 ? <p className="py-4 text-center text-xs text-muted-foreground">没有找到合适的表情，试试其他关键词。</p> : null}
      <Link className="mt-2 block text-[10px] text-muted-foreground underline" onClick={() => setOpen(false)} to={`/w/${auth.workspaceId}/settings/conversation#sticker-market`}>素材来源、署名与许可</Link>
    </PopoverContent>
  </Popover><StickerAnalysisDialog onClose={() => setAnalyzing(null)} onSelect={onSelect} sticker={analyzing} /></>;
}

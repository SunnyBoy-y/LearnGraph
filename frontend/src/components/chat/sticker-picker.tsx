import { useRef, useState } from 'react';
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

const emoji = [
  ['开心', '😀'], ['微笑', '😊'], ['大笑', '😂'], ['喜欢', '😍'], ['思考', '🤔'], ['惊讶', '😮'],
  ['难过', '🥺'], ['哭泣', '😢'], ['庆祝', '🎉'], ['赞', '👍'], ['鼓掌', '👏'], ['感谢', '🙏'],
  ['支持', '❤️'], ['加油', '💪'], ['灵感', '💡'], ['完成', '✅'], ['挥手', '👋'], ['闪亮', '✨'],
] as const;
const kaomoji = [
  ['开心', '(＾▽＾)'], ['微笑', '(｡･ω･｡)'], ['可爱', '(๑•̀ㅂ•́)و✧'], ['谢谢', '(人´∀`)'],
  ['加油', '٩(ˊᗜˋ*)و'], ['鼓掌', '(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧'], ['害羞', '(⁄ ⁄•⁄ω⁄•⁄ ⁄)'], ['思考', '(・・?)'],
  ['惊讶', '(⊙_⊙)'], ['难过', '(｡•́︿•̀｡)'], ['哭泣', '(╥﹏╥)'], ['拥抱', '(づ｡◕‿‿◕｡)づ'],
  ['挥手', '(￣▽￣)ノ'], ['得意', '(￣▽￣)✧'], ['无奈', '¯\\_(ツ)_/¯'], ['睡觉', '(－_－) zzZ'],
] as const;

export function StickerPicker({ onSelect, onInsertText, disabled = false }: { onSelect: (sticker: Sticker) => void; onInsertText: (text: string) => void; disabled?: boolean }) {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const selectionPendingRef = useRef(false);
  const openRef = useRef(open);
  openRef.current = open;
  const [tab, setTab] = useState<'stickers' | 'emoji' | 'kaomoji'>('stickers');
  const [query, setQuery] = useState('');
  const [analyzing, setAnalyzing] = useState<Sticker | null>(null);
  const result = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'search', query], queryFn: () => searchStickers(query), enabled: open && tab === 'stickers', staleTime: 5000 });
  const pipeline = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'pipeline'], queryFn: getStickerPipeline, enabled: open && tab === 'stickers', refetchInterval: open && tab === 'stickers' ? 3000 : false });
  const items = tab === 'emoji' ? emoji : kaomoji;
  return <><Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><Button ref={triggerRef} aria-label="选择表情包" className="size-8 shrink-0" disabled={disabled} size="icon" type="button" variant="ghost"><Smile className="size-[18px]" /></Button></PopoverTrigger>
    <PopoverContent align="start" className="w-[min(22rem,calc(100vw-2rem))] p-3" side="top" onCloseAutoFocus={event => {
      if (!selectionPendingRef.current) return;
      selectionPendingRef.current = false;
      event.preventDefault();
      if (openRef.current) return;
      triggerRef.current?.closest('form')?.querySelector<HTMLTextAreaElement>('textarea')?.focus({ preventScroll: true });
    }}>
      <div className="mb-3 flex items-center justify-between gap-2"><span className="text-sm font-medium">表情</span><div className="flex gap-3"><Link className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(false)} to={`/w/${auth.workspaceId}/settings/conversation#sticker-market`}>表情市场</Link><Link className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(false)} to={`/w/${auth.workspaceId}/settings/conversation#stickers`}>管理与导入</Link></div></div>
      <div aria-label="表情分类" className="mb-3 flex gap-1" role="group">{([['stickers', '表情包'], ['emoji', '小表情'], ['kaomoji', '颜文字']] as const).map(([value, label]) => <Button aria-pressed={tab === value} className="h-8" key={value} onClick={() => { setTab(value); setQuery(''); }} size="sm" type="button" variant={tab === value ? 'secondary' : 'ghost'}>{label}</Button>)}</div>
      <div className="relative"><Search aria-hidden="true" className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" /><Input aria-label={tab === 'stickers' ? '搜索表情包' : '搜索表情'} className="pl-9" onChange={event => setQuery(event.target.value)} placeholder="搜索心情、配字或场景" value={query} /></div>
      {tab === 'stickers' ? <>
        <div className="mt-3 grid max-h-64 grid-cols-4 gap-1 overflow-y-auto">
          {result.data?.candidates.map(sticker => { const job = pipeline.data?.assets?.find(entry => entry.sticker_id === sticker.id); return <button aria-label={`${sticker.needs_description ? '分析表情' : '添加表情'}：${sticker.name}`} className="flex min-h-20 min-w-0 flex-col items-center rounded-lg p-2 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" key={sticker.id} onClick={() => { selectionPendingRef.current = !sticker.needs_description; setOpen(false); if (sticker.needs_description) setAnalyzing(sticker); else onSelect(sticker); }} title={sticker.needs_description ? '点击分析画面、配字和使用场景' : sticker.semantics.description} type="button"><StickerImage alt={sticker.name} className="size-12" id={sticker.id} poster version={sticker.content_hash} /><span className="mt-1 max-w-full truncate text-[11px] text-muted-foreground">{sticker.name}</span>{sticker.needs_description ? <span className="mt-0.5 text-[10px] text-primary">{job?.status === 'queued' || job?.status === 'leased' ? '分析中…' : job?.status === 'failed' ? '失败 · 点击重试' : '待分析 · 点击识别'}</span> : null}</button>; })}
        </div>
        {result.isPending ? <p className="py-4 text-center text-xs text-muted-foreground">正在加载表情…</p> : result.error ? <p className="py-4 text-xs text-destructive" role="alert">表情加载失败，请重新打开重试。</p> : result.data?.candidates.length === 0 ? <p className="py-4 text-center text-xs text-muted-foreground">没有找到合适的表情，试试其他关键词。</p> : null}
        <Link className="mt-2 block text-[10px] text-muted-foreground underline" onClick={() => setOpen(false)} to={`/w/${auth.workspaceId}/settings/conversation#sticker-market`}>素材来源、署名与许可</Link>
      </> : <div className={`mt-3 grid max-h-64 gap-1 overflow-y-auto ${tab === 'emoji' ? 'grid-cols-6' : 'grid-cols-2'}`}>
        {items.filter(([name, text]) => `${name} ${text}`.toLowerCase().includes(query.trim().toLowerCase())).map(([name, text]) => <button aria-label={`插入${tab === 'emoji' ? '小表情' : '颜文字'}：${name}`} className={`min-h-12 rounded-lg p-2 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${tab === 'emoji' ? 'text-2xl' : 'text-sm'}`} key={text} onClick={() => { setOpen(false); onInsertText(text); }} title={name} type="button">{text}</button>)}
      </div>}
    </PopoverContent>
  </Popover><StickerAnalysisDialog onClose={() => setAnalyzing(null)} onSelect={onSelect} sticker={analyzing} /></>;
}

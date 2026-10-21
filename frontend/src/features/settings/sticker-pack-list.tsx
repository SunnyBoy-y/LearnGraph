import { useRef, useState } from 'react';
import { ArrowDownToLine, GripVertical } from 'lucide-react';
import type { StickerStatus } from '@/api/stickers';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { AnalyzeStickerPackButton } from './sticker-market';

type Pack = StickerStatus['packs'][number];

export function StickerPackList({ packs, busy, downloading, onReorder, onToggle, onDownload }: {
  packs: Pack[];
  busy: boolean;
  downloading: boolean;
  onReorder: (ids: string[]) => void;
  onToggle: (id: string, enabled: boolean) => void;
  onDownload: (pack: Pack) => void;
}) {
  const drag = useRef<{ id: string; target: string; startY: number; moved: boolean } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const move = (source: string, destination: string) => {
    if (busy || source === destination) return;
    const ids = packs.map(pack => pack.id);
    const from = ids.indexOf(source);
    const to = ids.indexOf(destination);
    if (from < 0 || to < 0) return;
    ids.splice(from, 1);
    ids.splice(to, 0, source);
    onReorder(ids);
  };
  const clearDrag = () => { drag.current = null; setDragging(null); setTarget(null); };
  return <div className="space-y-2 border-t pt-4">
    <p className="text-xs text-muted-foreground" id="sticker-pack-sort-help">拖动调整表情包顺序，按顺序启用前 100 张。也可聚焦手柄后按 ↑ / ↓ 排序。</p>
    <div aria-label="表情包排序与启停" className="space-y-2" role="list">
      {packs.map((pack, index) => <div className={`grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-2 rounded-xl border p-3 ${dragging === pack.id ? 'opacity-60' : ''} ${target === pack.id && dragging !== pack.id ? 'border-primary bg-primary/5' : ''}`} data-sticker-pack-id={pack.id} key={pack.id} role="listitem">
        <button aria-describedby="sticker-pack-sort-help" aria-label={`拖动排序：${pack.name}`} className="row-span-2 touch-none self-start rounded-md p-1 text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-40" disabled={busy} onKeyDown={event => {
          const destination = event.key === 'ArrowUp' ? packs[index - 1] : event.key === 'ArrowDown' ? packs[index + 1] : undefined;
          if (destination) { event.preventDefault(); move(pack.id, destination.id); }
        }} onPointerCancel={clearDrag} onPointerDown={event => {
          if (event.button !== 0 || busy) return;
          event.preventDefault();
          event.currentTarget.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { id: pack.id, target: pack.id, startY: event.clientY, moved: false };
          setDragging(pack.id);
        }} onPointerMove={event => {
          const current = drag.current;
          if (!current) return;
          if (Math.abs(event.clientY - current.startY) > 4) current.moved = true;
          const destination = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-sticker-pack-id]')?.getAttribute('data-sticker-pack-id');
          if (destination && packs.some(candidate => candidate.id === destination)) { current.target = destination; setTarget(destination); }
          else { current.target = current.id; setTarget(null); }
        }} onPointerUp={event => {
          const current = drag.current;
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          clearDrag();
          if (current?.moved) move(current.id, current.target);
        }} title="拖动排序，也可按上下方向键" type="button"><GripVertical className="size-4 cursor-grab active:cursor-grabbing" /></button>
        <div className="min-w-0"><p className="break-words text-xs font-medium">{pack.name}<span className="ml-2 font-normal text-muted-foreground">{pack.builtin ? '内置' : '个人表情库 · 私有'}</span></p><p aria-live="polite" className="mt-1 text-[11px] text-muted-foreground">{!pack.enabled ? `已停用 · 共 ${pack.count ?? 0} 张` : pack.active_count === 0 ? `等待名额 · 共 ${pack.count ?? 0} 张` : `已启用 ${pack.active_count ?? pack.count ?? 0} / ${pack.count ?? 0} 张`}</p><p className="mt-1 line-clamp-2 whitespace-pre-line text-[10px] leading-4 text-muted-foreground" title={pack.license}>{pack.license}</p></div>
        <div className="col-start-2 flex flex-wrap items-center gap-2"><Button aria-label={`下载已安装的 ${pack.name}`} disabled={downloading} onClick={() => onDownload(pack)} size="sm" variant="ghost"><ArrowDownToLine className="size-3.5" /></Button>{!pack.builtin ? <AnalyzeStickerPackButton disabled={!pack.active_count} id={pack.id} /> : null}<Switch aria-label={`启用表情包：${pack.name}`} checked={pack.enabled} disabled={busy} onCheckedChange={enabled => onToggle(pack.id, enabled)} /></div>
      </div>)}
    </div>
    {busy ? <p aria-live="polite" className="text-xs text-muted-foreground">正在保存表情包顺序与启用状态…</p> : null}
  </div>;
}

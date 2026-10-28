import { X } from 'lucide-react';
import type { Sticker } from '@/api/stickers';
import { Button } from '@/components/ui/button';
import { StickerImage } from './sticker-image';

export function ComposerStickers({ stickers, onRemove }: {
  stickers: Sticker[];
  onRemove: (id: string) => void;
}) {
  if (!stickers.length) return null;
  return <ul aria-label="待发送表情" className="flex shrink-0 gap-2 px-1 pb-2">
    {stickers.map(sticker => <li className="relative flex h-20 w-20 shrink-0 flex-col items-center justify-center gap-1 rounded-lg border bg-card p-2" key={sticker.id}>
      <StickerImage alt={sticker.name} className="size-12" id={sticker.id} poster version={sticker.content_hash} />
      <span className="max-w-full truncate text-[10px] text-muted-foreground" title={sticker.name}>{sticker.name}</span>
      <Button aria-label={`移除表情：${sticker.name}`} className="absolute right-0 top-0 size-6 bg-card/90" onClick={() => onRemove(sticker.id)} size="icon" title={`移除表情：${sticker.name}`} type="button" variant="ghost"><X className="size-3" /></Button>
    </li>)}
  </ul>;
}

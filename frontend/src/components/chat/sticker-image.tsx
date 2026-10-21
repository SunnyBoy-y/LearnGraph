import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { authStore } from '@/api/auth-store';
import { stickerBlob } from '@/api/stickers';
import { cn } from '@/lib/utils';

export function StickerImage({ id, version, alt, className, poster = false }: { id: string; version?: string; alt: string; className?: string; poster?: boolean }) {
  const [url, setUrl] = useState('');
  const query = useQuery({
    queryKey: ['stickers', authStore.getWorkspaceId(), authStore.getSession()?.userId, 'image', id, version, poster],
    queryFn: () => stickerBlob(id, poster || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true),
    enabled: Boolean(id),
    staleTime: 60_000,
    retry: false,
  });
  useEffect(() => {
    if (!query.data) return;
    const nextUrl = URL.createObjectURL(query.data);
    setUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [query.data]);
  if (query.error) return <span className="text-xs text-muted-foreground" role="status">[表情不可用：{alt}]</span>;
  return url ? <img alt={alt} className={cn('object-contain', className)} decoding="async" src={url} /> : <span aria-label={`正在加载表情：${alt}`} className={cn('inline-block animate-pulse rounded-xl bg-muted', className)} />;
}

export function StickerPart({ data }: { data?: Record<string, unknown> }) {
  const id = typeof data?.sticker_id === 'string' ? data.sticker_id : '';
  const alt = typeof data?.alt === 'string' ? data.alt : '表情包';
  return <figure className="my-2 inline-flex max-w-full flex-col gap-1" data-sticker-id={id}>
    <StickerImage alt={alt} className="size-36 max-w-full" id={id} version={typeof data?.asset_version === 'string' ? data.asset_version : undefined} />
    {typeof data?.attribution === 'string' ? <figcaption className="max-w-52 text-[10px] leading-4 text-muted-foreground">{data.license_url === 'https://creativecommons.org/licenses/by-sa/4.0/' ? <a href={data.license_url} rel="noreferrer" target="_blank">{data.attribution}</a> : data.attribution}</figcaption> : null}
    <figcaption className="sr-only">{alt}</figcaption>
  </figure>;
}

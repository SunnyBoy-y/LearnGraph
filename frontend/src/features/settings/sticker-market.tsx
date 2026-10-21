import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDownToLine, Check, ChevronRight, PackagePlus, Search, ShieldCheck, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { analyzeStickerPack, configureStickerPipeline, getStickerMarket, getStickerPipeline, installMarketPack, marketStickerBlob, type StickerMarketPack } from '@/api/stickers';
import { useAuth } from '@/features/auth/auth-context-value';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { saveStickerArchive } from '@/lib/sticker-download';

function MarketImage({ pack, index, alt }: { pack: string; index: number; alt: string }) {
  const auth = useAuth();
  const [url, setUrl] = useState('');
  const image = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'market-image', pack, index], queryFn: () => marketStickerBlob(pack, index), staleTime: 3600_000, retry: false });
  useEffect(() => {
    if (!image.data) return;
    const next = URL.createObjectURL(image.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [image.data]);
  return url ? <img alt={alt} className="size-14 object-contain sm:size-16" decoding="async" loading="lazy" src={url} /> : <span aria-label={alt} className="flex size-14 items-center justify-center rounded-xl bg-muted text-[10px] text-muted-foreground sm:size-16">{image.error ? '预览不可用' : '加载中'}</span>;
}

export function StickerMarketPanel({ onOpenLibrary }: { onOpenLibrary: (id: string) => void }) {
  const auth = useAuth();
  const client = useQueryClient();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('全部');
  const [source, setSource] = useState('all');
  const [detail, setDetail] = useState<StickerMarketPack | null>(null);
  const market = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'market', query], queryFn: () => getStickerMarket(query), refetchInterval: 8000 });
  const install = useMutation({ mutationFn: installMarketPack, onSuccess: data => { void client.invalidateQueries({ queryKey: ['stickers', auth.workspaceId] }); toast.success(data.imported ? `已添加 ${data.imported} 张表情，语义卡与关键词已就绪` : '这套表情已在你的表情库'); }, onError: (error: Error) => toast.error(error.message) });
  const download = useMutation({ mutationFn: (pack: StickerMarketPack) => saveStickerArchive(pack.id, pack.name, true), onSuccess: () => toast.success('表情包已交给浏览器保存，含完整语义清单与许可证'), onError: (error: Error) => toast.error(error.message) });
  const packs = market.data?.packs.filter(pack => (category === '全部' || pack.category === category) && (source === 'all' || pack.source_id === source)) ?? [];
  const categories = ['全部', '日常', '陪伴', '趣味'];
  const actions = (pack: StickerMarketPack) => <div className="flex flex-wrap gap-2"><Button disabled={install.isPending} onClick={() => pack.installed_pack_id ? onOpenLibrary(pack.installed_pack_id) : install.mutate(pack.id)} size="sm" variant={pack.installed_pack_id ? 'secondary' : 'default'}>{pack.installed_pack_id ? <Check className="mr-1.5 size-3.5" /> : <PackagePlus className="mr-1.5 size-3.5" />}{pack.installed_pack_id ? pack.enabled ? '已添加 · 查看' : '已停用 · 管理' : install.isPending && install.variables === pack.id ? '正在添加…' : '添加到表情库'}</Button><Button aria-label={`下载 ${pack.name}`} disabled={download.isPending} onClick={() => download.mutate(pack)} size="sm" variant="outline"><ArrowDownToLine className="mr-1.5 size-3.5" />下载 ZIP</Button></div>;
  return <div className="space-y-5 pt-2">
    <div className="rounded-2xl border bg-gradient-to-br from-primary/10 via-background to-muted/40 p-5"><div className="mb-2 flex items-center gap-2 text-xs font-medium text-primary"><ShieldCheck className="size-4" />开放许可 · 原图保留 · 离线可用</div><h3 className="text-xl font-semibold tracking-tight">让回应，多一点表情。</h3><p className="mt-2 max-w-xl text-xs leading-6 text-muted-foreground">精选 Microsoft Fluent、Google Noto 与 OpenMoji 开放许可素材，每套附带中文语义卡。添加后即可搜索、发送，也能让 AI 理解情绪与使用场景。</p><div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-muted-foreground"><span>{market.data?.total_packs ?? market.data?.packs.length ?? '…'} 套精选包</span><span>{market.data?.total_assets ?? market.data?.packs.reduce((total, pack) => total + pack.count, 0) ?? '…'} 张不同表情</span><span>无需 API Key 或接入审批</span><span>下载包含图片 + 语义 + 许可</span></div></div>
    <div className="relative"><Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input aria-label="搜索表情市场" className="pl-9" maxLength={200} onChange={event => setQuery(event.target.value)} placeholder="搜索表情包、情绪或关键词" value={query} /></div>
    <label className="flex items-center gap-2 text-xs text-muted-foreground">素材来源<select aria-label="素材来源" className="h-8 rounded-md border bg-background px-2 text-foreground" onChange={event => setSource(event.target.value)} value={source}><option value="all">全部开放许可来源</option><option value="fluent">Microsoft Fluent · MIT</option><option value="noto">Google Noto · Apache-2.0</option><option value="openmoji">OpenMoji · CC BY-SA 4.0</option></select></label>
    <div aria-label="市场分类" className="flex flex-wrap gap-2">{categories.map(value => <Button aria-pressed={category === value} key={value} onClick={() => setCategory(value)} size="sm" variant={category === value ? 'secondary' : 'ghost'}>{value}</Button>)}</div>
    {market.isPending ? <p className="py-8 text-center text-sm text-muted-foreground">正在打开表情市场…</p> : market.error ? <p className="text-sm text-destructive" role="alert">{market.error.message}<Button onClick={() => void market.refetch()} size="sm" variant="ghost">重试</Button></p> : null}
    <div className="grid gap-4 xl:grid-cols-2">{packs.map(pack => <article className="min-w-0 space-y-4 rounded-2xl border bg-card p-4" key={pack.id}><button aria-label={`预览 ${pack.name}`} className="flex w-full flex-col gap-4 text-left" onClick={() => setDetail(pack)} type="button"><div className="flex w-full flex-wrap justify-center gap-2 rounded-xl bg-muted/35 px-2 py-4">{pack.previews.slice(0, 4).map(preview => <MarketImage alt={preview.name} index={preview.index} key={preview.index} pack={pack.id} />)}</div><div className="w-full"><div className="flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">{pack.name}</h4><ChevronRight className="size-4 shrink-0 text-muted-foreground" /></div><p className="mt-1 text-xs leading-5 text-muted-foreground">{pack.description}</p></div></button><p className="text-[10px] text-muted-foreground">{pack.author} · {pack.license_id} · {pack.count} 张 · {Math.ceil(pack.bytes / 1024)} KiB · v{pack.version}</p>{actions(pack)}</article>)}</div>
    {!market.isPending && !market.error && !packs.length ? <p className="py-8 text-center text-sm text-muted-foreground">没有找到匹配的表情包，试试其他关键词。</p> : null}
    <div className="space-y-2 rounded-xl bg-muted/30 p-4 text-[11px] leading-5 text-muted-foreground"><p>{market.data?.policy ?? '仅提供已核实许可的精选素材，私有导入不会公开。'}</p><p>这是精选资源目录，不是完整上游商店。新增来源须逐包核验素材许可。微信、Signal 等第三方商店不会被自动抓取或重新托管。</p><p>语义卡 → 关键词索引 → 可选 Embedding → AI 检索与发送；不依赖指定模型。</p></div>
    <Dialog open={Boolean(detail)} onOpenChange={open => { if (!open) setDetail(null); }}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg"><DialogHeader><DialogTitle>{detail?.name}</DialogTitle><DialogDescription>{detail?.description}</DialogDescription></DialogHeader>{detail ? <><div className="grid grid-cols-3 gap-4 sm:grid-cols-4">{detail.previews.map(preview => <div className="flex flex-col items-center gap-1 text-center" key={preview.index} title={preview.description}><MarketImage alt={preview.name} index={preview.index} pack={detail.id} /><span className="text-[11px]">{preview.name}</span></div>)}</div><p className="text-xs leading-6 text-muted-foreground">{detail.license}</p><a className="text-xs text-primary underline" href={detail.source} rel="noreferrer" target="_blank">查看固定版本的官方来源与许可证</a><p className="text-[11px] leading-5 text-muted-foreground">{detail.attribution} · 无需 API 接入审批，但仍须遵守许可。完整许可及逐张作者署名包含在 ZIP 的 LICENSE.txt / NOTICE.txt 中。</p>{detail.license_url ? <a className="text-xs text-primary underline" href={detail.license_url} rel="noreferrer" target="_blank">阅读素材许可证</a> : null}{actions(market.data?.packs.find(pack => pack.id === detail.id) ?? detail)}</> : null}</DialogContent></Dialog>
  </div>;
}

export function StickerPipelinePanel() {
  const auth = useAuth();
  const client = useQueryClient();
  const [consent, setConsent] = useState(false);
  const pipeline = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'pipeline'], queryFn: getStickerPipeline, refetchInterval: 4000 });
  const completed = pipeline.data?.completed;
  const queued = pipeline.data?.queued;
  const failed = pipeline.data?.failed;
  useEffect(() => {
    if (completed === undefined) return;
    void client.invalidateQueries({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'search'] });
  }, [client, auth.workspaceId, auth.userId, completed, queued, failed]);
  const configure = useMutation({ mutationFn: configureStickerPipeline, onSuccess: () => { setConsent(false); void client.invalidateQueries({ queryKey: ['stickers', auth.workspaceId] }); }, onError: (error: Error) => toast.error(error.message) });
  return <div className="space-y-3 rounded-xl border p-4"><div className="flex items-start justify-between gap-3"><div><p className="flex items-center gap-2 text-xs font-medium"><Sparkles className="size-4 text-primary" />AI 可读语义管线</p><p className="mt-2 text-xs leading-5 text-muted-foreground">市场表情自带语义卡。私有导入可用视觉模型识别配字、画面、情绪与适用场景；人工描述不会被自动覆盖。</p></div><Switch aria-label="自动分析我的新导入表情" checked={pipeline.data?.auto_analyze ?? false} disabled={!pipeline.data || configure.isPending} onCheckedChange={checked => checked ? setConsent(true) : configure.mutate(false)} /></div><p aria-live="polite" className="text-[11px] text-muted-foreground">{pipeline.data?.available ? `视觉模型：${pipeline.data.model_id}` : '未配置视觉模型：保留清单 / 文件名关键词，可手动补充描述'}{pipeline.data ? ` · 等待 ${pipeline.data.queued} · 完成 ${pipeline.data.completed} · 失败 ${pipeline.data.failed}` : ''}</p>{pipeline.error ? <p className="text-xs text-destructive" role="alert">{pipeline.error.message}</p> : null}<Button asChild size="sm" variant="ghost"><Link to={`/w/${auth.workspaceId}/settings/models?tab=tasks`}>配置平台视觉模型</Link></Button><Dialog open={consent} onOpenChange={setConsent}><DialogContent><DialogHeader><DialogTitle>允许自动分析私有表情？</DialogTitle><DialogDescription>启用后，尚无描述及后续导入的表情将提取最多三张关键帧，发送给平台所配置的视觉 Provider，可能产生费用。请确认你有权处理这些图片。可随时关闭；关闭会取消自动排队任务，已发送的请求无法撤回。</DialogDescription></DialogHeader><Button disabled={configure.isPending} onClick={() => configure.mutate(true)}>同意并启用自动分析</Button></DialogContent></Dialog></div>;
}

export function AnalyzeStickerPackButton({ id, disabled = false }: { id: string; disabled?: boolean }) {
  const auth = useAuth();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const pipeline = useQuery({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'pipeline'], queryFn: getStickerPipeline });
  const progress = pipeline.data?.packs.find(pack => pack.pack_id === id);
  const analyze = useMutation({ mutationFn: () => analyzeStickerPack(id), onSuccess: data => { setOpen(false); toast.success(data.queued ? `已排队分析 ${data.queued} 张表情` : '无需新任务：已有描述或正在分析'); void client.invalidateQueries({ queryKey: ['stickers', auth.workspaceId] }); }, onError: (error: Error) => toast.error(error.message) });
  return <><Button disabled={disabled || Boolean(progress?.queued)} onClick={() => setOpen(true)} size="sm" variant="ghost"><Sparkles className="mr-1.5 size-3.5" />{progress?.queued ? `分析中（${progress.queued}）` : progress?.failed ? `重试语义（${progress.failed}）` : '补全语义'}</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>用视觉模型补全描述</DialogTitle><DialogDescription>仅处理这套表情中缺少描述的图片。最多三张关键帧会发送给当前视觉 Provider，可能产生费用。人工描述不会被覆盖；失败后可在此重试。</DialogDescription></DialogHeader><Button disabled={analyze.isPending} onClick={() => analyze.mutate()}>{analyze.isPending ? '正在排队…' : '同意发送并分析'}</Button></DialogContent></Dialog></>;
}

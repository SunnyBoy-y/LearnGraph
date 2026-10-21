import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Upload, RefreshCw, Search, Pencil, ImagePlus, ArrowDownToLine } from 'lucide-react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { listProviders } from '@/api';
import { configureStickers, getStickerStatus, importStickerPack, rebuildStickerIndex, searchStickers, setStickerPackEnabled, updateSticker, type Sticker } from '@/api/stickers';
import { useAuth } from '@/features/auth/auth-context-value';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { StickerImage } from '@/components/chat/sticker-image';
import { SettingsSection, SettingRow } from './settings-primitives';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { AnalyzeStickerPackButton, StickerMarketPanel, StickerPipelinePanel } from './sticker-market';
import { saveStickerArchive } from '@/lib/sticker-download';
import { StickerAnalysisDialog } from '@/components/chat/sticker-analysis-dialog';

const labels: Record<string, string> = { keyword: '关键词检索已就绪', queued: '等待构建语义索引', building: '正在构建语义索引', ready: '语义索引已就绪', failed: '语义索引暂不可用', superseded: '已切换到新的索引任务' };

export function StickerSettingsPanel() {
  const auth = useAuth();
  const client = useQueryClient();
  const prefix = ['stickers', auth.workspaceId, auth.userId];
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState(window.location.hash === '#sticker-market' ? 'market' : 'library');
  const [packFilter, setPackFilter] = useState('all');
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<Sticker | null>(null);
  const [analyzing, setAnalyzing] = useState<Sticker | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const status = useQuery({ queryKey: [...prefix, 'status'], queryFn: getStickerStatus, refetchInterval: 4000 });
  const result = useQuery({ queryKey: [...prefix, 'search', query, packFilter, page], queryFn: () => searchStickers(query, false, { packId: packFilter === 'all' ? undefined : packFilter, offset: page * 24 }), staleTime: 5000 });
  const providers = useQuery({ queryKey: ['providers', auth.workspaceId], queryFn: () => listProviders() });
  const refresh = () => client.invalidateQueries({ queryKey: ['stickers', auth.workspaceId] });
  const failure = (error: Error) => toast.error(error.message);
  const configure = useMutation({ mutationFn: configureStickers, onSuccess: refresh, onError: failure });
  const rebuild = useMutation({ mutationFn: rebuildStickerIndex, onSuccess: refresh, onError: failure });
  const togglePack = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => setStickerPackEnabled(id, enabled), onSuccess: refresh, onError: failure });
  const upload = useMutation({ mutationFn: (files: File[]) => importStickerPack(files, ''), onSuccess: data => { toast.success(data.imported ? `已导入 ${data.imported} 张表情，关键词搜索已可用` : '这套表情已经导入'); void refresh(); }, onError: failure });
  const save = useMutation({ mutationFn: updateSticker, onSuccess: () => { setEditing(null); void refresh(); toast.success('表情描述已更新'); }, onError: failure });
  const download = useMutation({ mutationFn: ({ id, name }: { id: string; name: string }) => saveStickerArchive(id, name), onError: failure });
  const data = status.data;
  const canManage = auth.effectivePermissions?.includes('workspace.manage') ?? false;
  const filtered = result.data?.candidates.filter(sticker => packFilter === 'all' || sticker.pack_id === packFilter) ?? [];
  return <SettingsSection id="stickers" title="表情包" description="用一张恰当的表情回应。内置日常表情，也可以添加自己的收藏。">
    <Tabs onValueChange={setTab} value={tab}><TabsList className="mb-4"><TabsTrigger value="library">我的表情</TabsTrigger><TabsTrigger value="market">表情市场</TabsTrigger></TabsList><TabsContent value="market"><StickerMarketPanel onOpenLibrary={id => { setPackFilter(id); setPage(0); setTab('library'); }} /></TabsContent><TabsContent value="library">
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted/40 px-4 py-3">
        <div className="min-w-0"><p className="flex items-center gap-2 text-sm font-medium"><Check className="size-4 text-primary" />关键词检索始终可用</p><p aria-live="polite" className="mt-1 text-xs text-muted-foreground">{data ? labels[data.index.status] ?? data.index.status : '正在读取表情库…'}{data?.index.model_id ? ` · ${data.index.model_id}` : ' · 配置检索模型后自动增强'}</p></div>
        <Button disabled={upload.isPending} onClick={() => input.current?.click()} size="sm" variant="outline"><Upload className="mr-2 size-4" />{upload.isPending ? '正在导入…' : '导入表情包'}</Button>
        <input accept="image/png,image/jpeg,image/webp,image/gif,.apng,.zip" aria-label="选择要导入的表情文件" className="sr-only" multiple onChange={event => { const files = Array.from(event.target.files ?? []); if (files.length) upload.mutate(files); event.target.value = ''; }} ref={input} type="file" />
      </div>
      {data && ['queued', 'building'].includes(data.index.status) ? <div aria-live="polite" className="space-y-2"><div className="flex justify-between text-xs text-muted-foreground"><span>后台构建期间仍可关键词搜索</span><span>{data.index.completed} / {data.index.total}</span></div><progress aria-label="语义索引构建进度" className="h-1.5 w-full accent-primary" max={Math.max(1, data.index.total)} value={data.index.completed} /></div> : null}
      {data?.index.error ? <p className="text-xs text-destructive" role="alert">{data.index.error}。关键词检索不受影响。</p> : null}
      {status.error ? <p className="text-xs text-destructive" role="alert">{status.error.message}</p> : null}
      <StickerPipelinePanel />
      <SettingRow title="AI 主动使用表情包" description="由对话语境决定是否使用；严肃任务优先完整文字。"><Switch aria-label="AI 主动使用表情包" checked={data?.ai_enabled ?? true} disabled={!data || !canManage || configure.isPending} onCheckedChange={ai_enabled => configure.mutate({ ai_enabled })} /></SettingRow>
      <div className="flex flex-wrap items-center gap-2"><label className="text-xs text-muted-foreground" htmlFor="sticker-retrieval-mode">检索方式</label><select className="h-8 max-w-full rounded-md border bg-background px-2 text-xs" disabled={!data || !canManage || configure.isPending} id="sticker-retrieval-mode" onChange={event => configure.mutate({ mode: event.target.value as 'follow' | 'independent' | 'keyword' })} value={data?.mode ?? 'follow'}><option value="follow">跟随平台 Embedding 配置</option><option value="independent">使用独立检索模型</option><option value="keyword">仅关键词检索</option></select><Button asChild size="sm" variant="ghost"><Link to={`/w/${auth.workspaceId}/settings/models?tab=tasks`}>配置检索模型</Link></Button>{data?.index.model_id && canManage ? <Button disabled={rebuild.isPending} onClick={() => rebuild.mutate()} size="sm" variant="ghost"><RefreshCw className="mr-1.5 size-3.5" />重建索引</Button> : null}</div>
      {data?.mode === 'independent' ? <div className="grid gap-2 sm:grid-cols-2"><select aria-label="表情包 Embedding Provider" className="h-9 min-w-0 rounded-md border bg-background px-2 text-sm" disabled={!canManage} onChange={event => configure.mutate({ provider_id: event.target.value })} value={data.provider_id}><option value="">选择已有 Provider</option>{providers.data?.filter(provider => provider.enabled).map(provider => <option key={provider.id} value={provider.id}>{provider.display_name}</option>)}</select><Input aria-label="表情包 Embedding 模型" defaultValue={data.model_id} disabled={!canManage} key={data.provider_id} onBlur={event => { if (event.target.value.trim() !== data.model_id) configure.mutate({ model_id: event.target.value.trim() }); }} placeholder="输入 Embedding 模型 ID" /></div> : null}
      <div className="border-t pt-4"><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div className="relative min-w-0 flex-1"><Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" /><Input aria-label="搜索表情素材" className="pl-9" onChange={event => { setQuery(event.target.value); setPage(0); }} placeholder="搜索心情、名称、配字或使用场景" value={query} /></div><select aria-label="筛选表情包" className="h-9 max-w-full rounded-md border bg-background px-2 text-xs" onChange={event => { setPackFilter(event.target.value); setPage(0); }} value={packFilter}><option value="all">全部表情包</option>{data?.packs.map(pack => <option key={pack.id} value={pack.id}>{pack.name}</option>)}</select></div>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">{filtered.map(sticker => <button className="group flex min-w-0 flex-col items-center rounded-xl border border-transparent px-2 py-3 hover:border-border hover:bg-muted/30 focus-visible:outline-2 focus-visible:outline-ring" key={sticker.id} onClick={() => sticker.needs_description ? setAnalyzing(sticker) : setEditing(sticker)} title={sticker.semantics.description} type="button"><StickerImage alt={sticker.name} className="size-16" id={sticker.id} poster version={sticker.content_hash} /><span className="mt-2 max-w-full truncate text-xs">{sticker.name}</span><span className="mt-1 text-[10px] text-muted-foreground">{sticker.needs_description ? '待分析 · 点击识别' : sticker.semantics.intent[0] ?? '日常回应'}</span></button>)}</div>
        {result.isPending ? <p className="py-8 text-center text-sm text-muted-foreground">正在加载表情…</p> : result.error ? <p className="py-6 text-sm text-destructive" role="alert">{result.error.message}</p> : !filtered.length ? <div className="py-8 text-center text-sm text-muted-foreground"><ImagePlus className="mx-auto mb-2 size-6" />没有匹配的表情，换个关键词或导入自己的收藏。</div> : null}
        {(result.data?.total ?? 0) > 24 ? <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground"><Button disabled={page === 0} onClick={() => setPage(value => value - 1)} size="sm" variant="ghost">上一页</Button><span>第 {page + 1} 页 · 共 {result.data?.total} 张</span><Button disabled={(page + 1) * 24 >= (result.data?.total ?? 0)} onClick={() => setPage(value => value + 1)} size="sm" variant="ghost">下一页</Button></div> : null}
      </div>
      <div className="space-y-2 border-t pt-3">{data?.packs.map(pack => <div className="flex items-start justify-between gap-3 py-1" key={pack.id}><div className="min-w-0"><p className="text-xs font-medium">{pack.name}<span className="ml-2 font-normal text-muted-foreground">{pack.builtin ? '内置' : '个人表情库 · 私有'}</span></p><p className="mt-1 line-clamp-3 whitespace-pre-line text-[10px] leading-4 text-muted-foreground" title={pack.license}>{pack.license}</p></div><div className="flex shrink-0 flex-wrap items-center justify-end gap-1"><Button aria-label={`下载已安装的 ${pack.name}`} disabled={download.isPending} onClick={() => download.mutate({ id: pack.id, name: pack.name })} size="sm" variant="ghost"><ArrowDownToLine className="size-3.5" /></Button>{!pack.builtin ? <AnalyzeStickerPackButton disabled={!pack.enabled} id={pack.id} /> : null}<Switch aria-label={`启用表情包：${pack.name}`} checked={pack.enabled} disabled={togglePack.isPending || (pack.builtin && !canManage)} onCheckedChange={enabled => togglePack.mutate({ id: pack.id, enabled })} /></div></div>)}</div>
      <p className="text-[11px] leading-5 text-muted-foreground">支持多张图片或 ZIP，最多 100 张、总计 24 MiB。无需视觉模型即可导入；点击表情可补充描述和关键词。你的导入仅自己可见；启用远程语义检索时，描述会发送给所选 Provider。</p>
    </div>
    </TabsContent></Tabs>
    <StickerAnalysisDialog onClose={() => setAnalyzing(null)} sticker={analyzing} />
    <Dialog open={editing !== null} onOpenChange={open => { if (!open) setEditing(null); }}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md"><DialogHeader><DialogTitle>表情详情</DialogTitle></DialogHeader>{editing ? <StickerEditor key={editing.id} onSave={value => save.mutate(value)} pending={save.isPending} readOnly={data?.packs.find(pack => pack.id === editing.pack_id)?.builtin ?? true} sticker={editing} /> : null}</DialogContent></Dialog>
  </SettingsSection>;
}

function StickerEditor({ sticker, readOnly, pending, onSave }: { sticker: Sticker; readOnly: boolean; pending: boolean; onSave: (value: Sticker) => void }) {
  const [draft, setDraft] = useState(sticker);
  const [tagsText, setTagsText] = useState(sticker.semantics.tags.join('，'));
  const [avoidText, setAvoidText] = useState(sticker.semantics.avoid_when.join('，'));
  return <form className="space-y-4" onSubmit={event => { event.preventDefault(); onSave({ ...draft, semantics: { ...draft.semantics, tags: tagsText.split(/[,，]/).map(value => value.trim()).filter(Boolean), avoid_when: avoidText.split(/[,，]/).map(value => value.trim()).filter(Boolean) } }); }}><StickerImage alt={draft.name} className="mx-auto size-32" id={draft.id} version={draft.content_hash} /><label className="block space-y-1 text-xs">名称<Input disabled={readOnly} maxLength={120} onChange={event => setDraft({ ...draft, name: event.target.value })} required value={draft.name} /></label><label className="block space-y-1 text-xs">含义与适用场景<Textarea disabled={readOnly} maxLength={1200} onChange={event => setDraft({ ...draft, semantics: { ...draft.semantics, description: event.target.value } })} placeholder="例如：表达小得意，适合熟人间友好调侃" value={draft.semantics.description} /></label><label className="block space-y-1 text-xs">图片配字（OCR）<Textarea disabled={readOnly} maxLength={1200} onChange={event => setDraft({ ...draft, semantics: { ...draft.semantics, ocr: event.target.value } })} value={draft.semantics.ocr} /></label><div className="space-y-1 rounded-lg bg-muted/40 p-3 text-xs leading-5"><p>情绪：{draft.semantics.emotion.join("、") || "未标注"}</p><p>表达意图：{draft.semantics.intent.join("、") || "未标注"}</p><p>适用场景：{draft.semantics.use_when.join("、") || "请结合描述与上下文判断"}</p></div><label className="block space-y-1 text-xs">关键词（用逗号分隔）<Input disabled={readOnly} onChange={event => setTagsText(event.target.value)} value={tagsText} /></label><label className="block space-y-1 text-xs">不适用场景（用逗号分隔）<Input disabled={readOnly} onChange={event => setAvoidText(event.target.value)} value={avoidText} /></label>{readOnly ? <p className="text-xs text-muted-foreground">内置表情及语义随应用维护。</p> : <Button className="w-full" disabled={pending} type="submit"><Pencil className="mr-2 size-4" />{pending ? '保存中…' : '保存描述'}</Button>}</form>;
}

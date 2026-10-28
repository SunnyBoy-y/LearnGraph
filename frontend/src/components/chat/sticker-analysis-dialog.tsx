import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import { analyzeSticker, getSticker, getStickerPipeline, updateSticker, type Sticker } from '@/api/stickers';
import { useAuth } from '@/features/auth/auth-context-value';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { StickerImage } from './sticker-image';

export function StickerAnalysisDialog({ sticker, onClose, onSelect }: { sticker: Sticker | null; onClose: () => void; onSelect?: (value: Sticker) => void }) {
  const auth = useAuth();
  const client = useQueryClient();
  const prefix = ['stickers', auth.workspaceId, auth.userId];
  const [manual, setManual] = useState(false);
  const [description, setDescription] = useState('');
  useEffect(() => { setManual(false); setDescription(''); }, [sticker?.id]);
  const current = useQuery({ queryKey: [...prefix, 'detail', sticker?.id], queryFn: () => getSticker(sticker!.id), enabled: Boolean(sticker), refetchInterval: sticker ? 1500 : false });
  const pipeline = useQuery({ queryKey: [...prefix, 'pipeline'], queryFn: getStickerPipeline, enabled: Boolean(sticker), refetchInterval: sticker ? 1500 : false });
  const value = current.data ?? sticker;
  const job = pipeline.data?.assets?.find(entry => entry.sticker_id === sticker?.id);
  const busy = job?.status === 'queued' || job?.status === 'leased';
  const done = Boolean(value && !value.needs_description);
  const analyze = useMutation({ mutationFn: (id: string) => analyzeSticker(id), onSuccess: () => { void client.invalidateQueries({ queryKey: prefix }); } });
  const save = useMutation({ mutationFn: updateSticker, onSuccess: () => { setManual(false); void client.invalidateQueries({ queryKey: prefix }); } });
  useEffect(() => {
    if (done) void client.invalidateQueries({ queryKey: ['stickers', auth.workspaceId, auth.userId, 'search'] });
  }, [client, auth.workspaceId, auth.userId, done]);
  return <Dialog open={Boolean(sticker)} onOpenChange={open => { if (!open) onClose(); }}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md"><DialogHeader><DialogTitle>{done ? '表情分析完成' : '分析这张表情'}</DialogTitle><DialogDescription>仅分析当前图片，不处理整包。最多三张关键帧会发送给配置的视觉 Provider，可能产生费用；已有人工描述不会覆盖。</DialogDescription></DialogHeader>{value ? <div className="space-y-4"><StickerImage alt={value.name} className="mx-auto size-32" id={value.id} version={value.content_hash} /><p className="text-center text-sm font-medium">{value.name}</p>{done ? <div className="space-y-2 rounded-xl bg-muted/40 p-3 text-xs leading-5"><p>{value.semantics.description}</p>{value.semantics.ocr ? <p>配字：{value.semantics.ocr}</p> : null}<p>情绪 / 意图：{[...value.semantics.emotion, ...value.semantics.intent].join('、') || '未标注'}</p><p>适用：{value.semantics.use_when.join('、') || '结合上下文判断'}</p><p>不适用：{value.semantics.avoid_when.join('、') || '未标注'}</p></div> : <p aria-live="polite" className="text-xs leading-5 text-muted-foreground">{busy ? '正在后台分析，完成后这里会自动显示语义结果。' : job?.status === 'failed' ? '上次分析失败，原图仍然可用。请检查视觉模型配置后重试。' : pipeline.data?.available ? `将使用 ${pipeline.data.model_id} 识别画面、配字、情绪与使用场景。` : '尚未配置可用的视觉模型，可以先配置模型，或手工填写语义描述。'}</p>}{manual ? <label className="block space-y-2 text-xs">手工语义描述<Textarea maxLength={1200} onChange={event => setDescription(event.target.value)} placeholder="描述画面、实际含义和使用场景" value={description} /><Button disabled={!description.trim() || save.isPending} onClick={() => save.mutate({ ...value, semantics: { ...value.semantics, description: description.trim() } })} size="sm">保存语义描述</Button></label> : null}{current.error || pipeline.error || analyze.error || save.error ? <p className="text-xs text-destructive" role="alert">{(current.error ?? pipeline.error ?? analyze.error ?? save.error)?.message}</p> : null}<div className="flex flex-wrap gap-2">{!done ? <Button disabled={!pipeline.data?.available || busy || analyze.isPending || current.isPending || Boolean(current.error)} onClick={() => analyze.mutate(value.id)}><Sparkles className="mr-1.5 size-4" />{busy ? '分析中…' : analyze.isPending ? '正在排队…' : job?.status === 'failed' ? '同意发送并重试' : '同意发送并分析'}</Button> : null}{onSelect ? <Button onClick={() => { onSelect(value); onClose(); }} variant={done ? 'default' : 'outline'}>{done ? '添加到消息' : '添加原图到消息'}</Button> : null}<Button onClick={() => { setManual(true); setDescription(value.semantics.description); }} variant="ghost">手工填写描述</Button>{!pipeline.data?.available ? <Button asChild variant="ghost"><Link onClick={onClose} to={`/w/${auth.workspaceId}/settings/models?tab=tasks`}>配置视觉模型</Link></Button> : null}</div></div> : null}</DialogContent></Dialog>;
}

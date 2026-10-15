import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { commitConfigPreview, getConfigSources, previewConfigSources } from '@/api/provider-automation';

export function ProviderConfigImport() {
  const client = useQueryClient();
  const sources = useQuery({ queryKey: ['provider-config-sources'], queryFn: getConfigSources, retry: false });
  const [selected, setSelected] = useState<string[]>([]);
  const [sourceSelection, setSourceSelection] = useState<string[] | null>(null);
  const available = sources.data?.sources.filter((source) => source.status === 'available') ?? [];
  const sourceIds = sourceSelection ?? available.map((source) => source.id);
  const scan = useMutation({
    mutationFn: () => previewConfigSources(sourceIds),
    onSuccess: (preview) => setSelected(preview.candidates.filter((item) => !item.duplicate).map((item) => item.id)),
  });
  const commit = useMutation({
    mutationFn: () => {
      if (!scan.data) throw new Error('请先扫描配置');
      return commitConfigPreview(scan.data.preview_id, selected);
    },
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['providers'] }); },
  });
  const busy = scan.isPending || commit.isPending;
  return <div className="space-y-4">
    <p className="text-sm">扫描部署管理员授权并只读挂载的配置。支持 CC Switch 数据库和 Codex++ 配置；不是扫描浏览器所在电脑。</p>
    <p className="text-xs text-muted-foreground">预览不请求供应商、不返回完整Key。确认后加密保存，并为每条新供应商登记一次后台余额验证；模型连接仍需验证后启用。</p>
    {sources.isPending ? <p className="text-sm">正在读取授权来源…</p> : null}
    {sources.isError ? <div role="alert" className="text-sm text-destructive">{sources.error.message}<Button size="xs" type="button" variant="ghost" onClick={() => void sources.refetch()}>重试</Button></div> : null}
    {sources.data?.sources.length === 0 ? <div className="rounded-lg border p-4 text-sm">
      <p className="font-medium">尚未接入部署机器配置</p>
      <p className="mt-2 text-muted-foreground">请让部署管理员配置 LEARNGRAPH_PROVIDER_IMPORT_SOURCES、允许的工作区和只读挂载目录。仅启动现有服务代理Bridge不等于授权读取配置。</p>
      <Button type="button" size="xs" variant="outline" className="mt-2" onClick={() => void sources.refetch()}>重新检查接入状态</Button>
    </div> : null}
    <div className="space-y-2">{sources.data?.sources.map((source) => <label key={source.id} className="flex items-start gap-2 rounded-lg border p-3 text-sm">
      <input type="checkbox" checked={sourceIds.includes(source.id)} disabled={busy || source.status !== 'available'} onChange={(event) => setSourceSelection(event.target.checked ? [...sourceIds, source.id] : sourceIds.filter((id) => id !== source.id))} />
      <span><span className="font-medium">{source.label}</span><span className="block text-xs text-muted-foreground">{source.message}</span></span>
    </label>)}</div>
    <Button type="button" disabled={busy || sourceIds.length === 0} onClick={() => { commit.reset(); scan.reset(); setSelected([]); scan.mutate(); }}>
      {scan.isPending ? '只读扫描中…' : '扫描并预览配置'}
    </Button>
    {scan.isError ? <p role="alert" className="text-sm text-destructive">{scan.error.message}</p> : null}
    {scan.data?.warnings.map((warning, index) => <p key={index} className="text-xs text-amber-600">{warning}</p>)}
    {scan.data && scan.data.candidates.length === 0 ? <p className="text-sm">本次没有可导入项，请查看来源状态和格式提示；这不代表机器上没有模型。</p> : null}
    {scan.data?.candidates.map((item) => <label className="flex items-start gap-2 rounded-lg border p-3" key={item.id}>
      <input type="checkbox" disabled={busy || item.duplicate || commit.isSuccess} checked={selected.includes(item.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} />
      <span className="min-w-0 flex-1 space-y-1 text-xs">
        <span className="block text-sm font-medium">{item.name}{item.duplicate ? ' · 相同连接已存在' : ''}</span>
        <span className="block break-all font-mono">{item.base_url}</span>
        <span className="block text-muted-foreground">{item.source_label} · {item.provider_type} · {item.has_key ? '已找到Key（不回显）' : '未找到Key'}</span>
        <span className="block text-muted-foreground">{item.model_ids.length ? `配置内模型：${item.model_ids.join('、')}` : '配置未指定模型，导入后可选择或手动添加'}</span>
      </span>
    </label>)}
    {scan.data?.candidates.length ? <Button type="button" disabled={busy || selected.length === 0 || commit.isSuccess} onClick={() => commit.mutate()}>
      {commit.isPending ? '正在确认并保存…' : commit.isSuccess ? '本次导入已完成' : `确认导入 ${selected.length} 条连接`}
    </Button> : null}
    {commit.isError ? <p role="alert" className="text-sm text-destructive">{commit.error.message}</p> : null}
    {commit.data ? <div className="space-y-2 rounded-lg border p-3 text-sm" aria-live="polite">
      <p className="font-medium">导入结果</p>
      {commit.data.created.map((item) => <p key={item.provider_id}>{item.name}：{item.enabled ? '已启用' : '已保存，待模型连接验证'}</p>)}
      {commit.data.skipped.map((item, index) => <p key={`skip-${index}`}>{item.name}：跳过 · {item.reason}</p>)}
      {commit.data.failed.map((item, index) => <p key={`fail-${index}`} className="text-destructive">{item.name}：失败 · {item.reason}</p>)}
      <p className="text-xs text-muted-foreground">关闭后可在对应供应商的「管理模型」中刷新模型列表、选择默认模型并启用。</p>
    </div> : null}
  </div>;
}

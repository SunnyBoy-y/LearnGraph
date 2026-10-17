import { useEffect, useId, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Check, ChevronRight, FileUp, Loader2, RefreshCw, Search, Sparkles, X } from 'lucide-react';
import { toast } from 'sonner';

import { commitConfigPreview, getConfigSources, previewConfigSources, type ConfigCandidate, type ConfigPreview, type ConfigSource } from '@/api/provider-automation';
import { discoverProviderModels, importCcSwitchProviders } from '@/api/providers';
import { currentWorkspaceQueryKey } from '@/lib/query-keys';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Popover, PopoverClose, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { parseConnectionFile, type FileConnection } from './provider-import-file';
import './provider-import.css';

type Source = 'ccswitch' | 'cherry' | 'file';
type Connection = ConfigCandidate | FileConnection;
const sourceLabels: Record<Source, string> = { ccswitch: 'CC Switch', cherry: 'Cherry Studio', file: '配置文件' };
const sourceMatches = (source: ConfigSource, kind: Source) => kind === 'cherry'
  ? ['cherry', 'cherrystudio', 'cherry-studio'].includes(source.kind)
  : source.kind === kind;
const unavailableReason = (item: Connection) => 'config' in item ? item.reason
  : item.duplicate ? '相同连接已存在' : !item.base_url ? '缺少 Base URL' : !item.has_key ? '缺少可用的 API Key' : undefined;

function SourceMark({ source }: { source: Source }) {
  return <span className={`provider-import-mark provider-import-mark--${source}`} aria-hidden="true">
    {source === 'ccswitch' ? <Sparkles className="size-5" /> : source === 'cherry' ? <span>✿</span> : <FileUp className="size-4" />}
  </span>;
}

export function ImportProviderPopover() {
  const client = useQueryClient();
  const titleId = useId();
  const descriptionId = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const sourcesList = useRef<HTMLDivElement>(null);
  const scanVersion = useRef(0);
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [sources, setSources] = useState<ConfigSource[]>([]);
  const [preview, setPreview] = useState<ConfigPreview | null>(null);
  const [files, setFiles] = useState<FileConnection[]>([]);
  const [fileName, setFileName] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [onlyAvailable, setOnlyAvailable] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [readingFile, setReadingFile] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    if (source) searchInput.current?.focus();
    else sourcesList.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  }, [source, open]);

  async function scan() {
    const version = ++scanVersion.current;
    setScanning(true);
    setError('');
    setPreview(null);
    setSources([]);
    setSelected([]);
    try {
      const result = await getConfigSources();
      if (version !== scanVersion.current) return;
      setSources(result.sources);
      const ids = result.sources.filter(item => item.status === 'available' && (sourceMatches(item, 'ccswitch') || sourceMatches(item, 'cherry'))).map(item => item.id);
      if (ids.length) {
        const next = await previewConfigSources(ids);
        if (version === scanVersion.current) setPreview(next);
      }
    } catch {
      if (version === scanVersion.current) setError('暂时无法读取配置来源，请重新扫描或选择配置文件。');
    } finally {
      if (version === scanVersion.current) setScanning(false);
    }
  }

  function changeOpen(next: boolean) {
    setOpen(next);
    if (!next) {
      ++scanVersion.current;
      setScanning(false);
      setFiles([]); // Drop plaintext credentials when the menu closes.
      setSelected([]);
      setError('');
    } else {
      setSource(null);
      setSearch('');
      setOnlyAvailable(false);
      void scan();
    }
  }

  const candidatesFor = (kind: Source) => preview?.candidates.filter(item => sources.some(s => s.id === item.source_id && sourceMatches(s, kind))) ?? [];
  const candidates: Connection[] = source === 'file' ? files : source ? candidatesFor(source) : [];
  const visible = candidates.filter(item => (!onlyAvailable || !unavailableReason(item)) && `${item.name} ${item.base_url}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const selectable = visible.filter(item => !unavailableReason(item));
  const allVisibleSelected = selectable.length > 0 && selectable.every(item => selected.includes(item.id));

  const commit = useMutation({
    mutationFn: async () => {
      if (source === 'file') {
        const result = await importCcSwitchProviders({ config_json: JSON.stringify({ providers: files.filter(item => selected.includes(item.id) && !item.reason).map(item => item.config) }) });
        return {
          created: result.created,
          skipped: result.skipped.filter(item => item.reason === 'duplicate'),
          failed: result.skipped.filter(item => item.reason !== 'duplicate'),
        };
      }
      if (!preview) throw new Error('请重新扫描后再导入');
      return commitConfigPreview(preview.preview_id, selected);
    },
    onSuccess: result => {
      const providersKey = currentWorkspaceQueryKey('providers');
      void client.invalidateQueries({ queryKey: providersKey });
      if (result.failed.length) {
        // Completed previews cannot be resubmitted with a different selection.
        // Scan again to mark saved items as duplicates before retrying failures.
        if (source === 'file') {
          const failedNames = new Set(result.failed.map(item => item.name));
          setFiles(current => current.filter(item => failedNames.has(item.name)));
          setSelected([]);
        } else void scan();
        setError(`已导入 ${result.created.length} 项，${result.failed.length} 项未完成，请重新选择后重试。`);
      } else changeOpen(false);
      if (result.created.length) {
        toast.success(`成功导入 ${result.created.length} 项连接${result.skipped.length ? `，跳过 ${result.skipped.length} 项配置` : ''}`, {
          description: source === 'file' ? '已尝试获取模型列表，未完成的连接可在「管理模型」中完善。' : '正在获取模型列表，未完成的连接可在「管理模型」中完善。',
        });
        // The legacy file-import endpoint already discovers models. Authorized
        // source commits only save connections, so discover those after closing.
        if (source !== 'file') void Promise.allSettled(result.created.map(item => discoverProviderModels(item.provider_id))).then(results => {
          void client.invalidateQueries({ queryKey: providersKey });
          if (results.some(item => item.status === 'rejected')) toast.info('部分连接的模型列表待完善，可在「管理模型」中重试。');
        });
      } else if (result.skipped.length) toast.info(`已跳过 ${result.skipped.length} 项已有或不支持的连接`);
    },
    onError: () => setError('导入未完成，请重新扫描或选择文件后重试。已保存的连接会保留。'),
  });
  const busy = commit.isPending || readingFile;

  async function readFile(file: File) {
    const version = ++scanVersion.current;
    setScanning(false);
    setReadingFile(true);
    setError('');
    setFiles([]);
    setSelected([]);
    try {
      if (file.size > 4_000_000) throw new Error('请选择小于 4 MB 的 JSON 配置文件');
      const items = parseConnectionFile(await file.text());
      if (version !== scanVersion.current) return;
      setFiles(items);
      setFileName(file.name);
      setSource('file');
      setSearch('');
      setOnlyAvailable(false);
    } catch (err) {
      if (version === scanVersion.current) setError(err instanceof Error ? err.message : '无法读取配置文件');
    } finally { setReadingFile(false); }
  }

  function chooseSource(kind: Source | null) {
    setSource(kind);
    setSelected([]);
    setSearch('');
    setOnlyAvailable(false);
    setError('');
    if (!kind) setFiles([]);
  }

  return <Popover open={open} onOpenChange={next => { if (!commit.isPending) changeOpen(next); }}>
    <PopoverTrigger asChild><Button size="sm" variant="outline" className="gap-1.5 shadow-sm"><Sparkles className="size-3.5" />导入配置</Button></PopoverTrigger>
    <PopoverContent align="end" sideOffset={8} collisionPadding={12} aria-labelledby={titleId} aria-describedby={descriptionId}
      className={`provider-import-popover ${source ? 'provider-import-popover--connections' : ''}`}
      onEscapeKeyDown={event => { event.stopPropagation(); if (commit.isPending) event.preventDefault(); }}>
      <input ref={fileInput} type="file" accept=".json,application/json" className="sr-only" aria-label="选择配置文件" tabIndex={-1}
        onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void readFile(file); }} />
      <header className="provider-import-header">
        {source ? <Button variant="ghost" size="icon-sm" aria-label="返回配置来源" disabled={busy} onClick={() => chooseSource(null)}><ArrowLeft className="size-4" /></Button> : null}
        <div className="min-w-0 flex-1">
          <h3 id={titleId}>{source ? source === 'file' ? '从配置文件导入' : `从 ${sourceLabels[source]} 导入` : '导入连接'}</h3>
          <p id={descriptionId}>{source ? `发现 ${candidates.length} 项配置 · 选择要导入的连接` : '选择配置来源，再勾选要导入的连接'}</p>
        </div>
        <PopoverClose asChild><Button variant="ghost" size="icon-sm" disabled={commit.isPending} aria-label="关闭导入菜单"><X className="size-4" /></Button></PopoverClose>
      </header>
      {!source ? <div ref={sourcesList} className="provider-import-sources">
        {(['ccswitch', 'cherry'] as const).map(kind => {
          const count = candidatesFor(kind).length;
          const available = sources.some(item => sourceMatches(item, kind) && item.status === 'available');
          return <button key={kind} type="button" className="provider-import-source" disabled={scanning || busy || !count} onClick={() => chooseSource(kind)}>
            <SourceMark source={kind} /><span className="min-w-0 flex-1"><strong>{sourceLabels[kind]}</strong><span>{scanning ? '正在查找配置…' : count ? `发现 ${count} 项配置` : available ? '未发现可导入配置' : '未连接配置来源，可从文件导入'}</span></span><ChevronRight className="size-4" />
          </button>;
        })}
        <button type="button" className="provider-import-source provider-import-source--file" disabled={busy} onClick={() => fileInput.current?.click()}><SourceMark source="file" /><span className="flex-1">{readingFile ? '正在读取文件…' : '从配置文件导入'}</span><ChevronRight className="size-4" /></button>
        <p className="provider-import-source-note">自动发现仅使用已授权给此服务的配置。远程部署时，请选择电脑上导出的 JSON 文件。</p>
      </div> : <>
        <div className="provider-import-tools">
          <div className="relative"><Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" /><Input ref={searchInput} aria-label="搜索连接" placeholder="搜索名称或地址" className="h-9 pl-9" value={search} onChange={event => setSearch(event.target.value)} /></div>
          <div className="mt-2.5 flex items-center justify-between gap-2 text-xs text-muted-foreground"><label className="flex items-center gap-2"><input type="checkbox" checked={onlyAvailable} onChange={event => setOnlyAvailable(event.target.checked)} />仅显示可导入</label><button type="button" className="hover:text-foreground disabled:opacity-50" disabled={busy || !selectable.length} onClick={() => setSelected(current => allVisibleSelected ? current.filter(id => !selectable.some(item => item.id === id)) : [...new Set([...current, ...selectable.map(item => item.id)])])}>{allVisibleSelected ? '取消全选' : '全选'}</button></div>
        </div>
        <div className="provider-import-list" aria-label="可导入连接" aria-busy={scanning}>
          {scanning ? <p className="provider-import-empty"><Loader2 className="size-4 animate-spin" />正在查找连接…</p> : visible.length ? visible.map(item => {
            const reason = unavailableReason(item);
            return <label key={item.id} className={`provider-import-connection ${reason ? 'is-unavailable' : ''}`} title={reason}>
              <input type="checkbox" aria-label={`选择 ${item.name}`} disabled={busy || Boolean(reason)} checked={selected.includes(item.id)} onChange={event => setSelected(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} />
              <span className="min-w-0 flex-1"><strong>{item.name}</strong><span title={reason ?? item.base_url}>{reason ?? item.base_url}</span></span>
              {selected.includes(item.id) ? <Check className="size-3.5 shrink-0" aria-hidden="true" /> : null}
            </label>;
          }) : <p className="provider-import-empty">{search || onlyAvailable ? '没有匹配的连接' : '没有可导入的连接，请重新扫描或选择文件。'}</p>}
        </div>
        {source === 'file' ? <div className="provider-import-filename"><span className="truncate" title={fileName}>{fileName}</span><button type="button" disabled={busy} onClick={() => fileInput.current?.click()}>更换文件</button></div> : null}
      </>}
      {error ? <p role="alert" className="provider-import-error">{error}</p> : null}
      {source && preview?.warnings.length && source !== 'file' ? <p className="provider-import-source-note px-4 pb-3">部分配置未能读取，可改用导出的 JSON 文件。</p> : null}
      <footer className="provider-import-footer">
        {source ? <><span className="mr-auto text-xs text-muted-foreground" aria-live="polite">已选择 {selected.length} 项</span><Button variant="ghost" size="sm" disabled={busy || !selected.length} onClick={() => setSelected([])}>清空</Button><Button size="sm" disabled={busy || scanning || !selected.length} onClick={() => { setError(''); commit.mutate(); }}>{commit.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}{commit.isPending ? '正在导入…' : `导入 ${selected.length} 项`}</Button></> : <Button variant="ghost" size="sm" className="ml-auto gap-1.5" disabled={scanning || busy} onClick={() => void scan()}><RefreshCw className={`size-3.5 ${scanning ? 'animate-spin' : ''}`} />重新扫描</Button>}
      </footer>
    </PopoverContent>
  </Popover>;
}

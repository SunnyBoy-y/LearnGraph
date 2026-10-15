import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { getBalanceStatus, rediscoverBalance } from '@/api/provider-automation';
import { Button } from '@/components/ui/button';

const LABELS: Record<string, string> = {
  not_configured: '尚未验证余额方法', pending: '等待后台验证', running: '正在验证余额方法',
  ready: '余额方法已验证', unsupported: '未找到可用余额接口', auth_error: '需检查账务凭据',
  transient_error: '本次验证失败，已停止自动尝试', interrupted: '查询中断，已停止自动尝试',
  paused: '自动刷新已暂停', invalidated: '配置已变化，需重新验证',
};

export function ProviderBalanceStatus({ providerId }: { providerId: string }) {
  const client = useQueryClient();
  const { workspaceId, userId, effectivePermissions } = useAuth();
  const canManage = effectivePermissions.includes("workspace.manage");
  const key = workspaceQueryKey(workspaceId, "provider-balance-status", providerId, userId);
  const status = useQuery({
    queryKey: key, queryFn: ({ signal }) => getBalanceStatus(providerId, { signal, headers: { "X-Workspace-ID": workspaceId } }), retry: false,
    refetchInterval: (query) => ['pending', 'running'].includes(query.state.data?.state ?? '') ? 5000 : 60000,
    refetchOnWindowFocus: false,
  });
  const discover = useMutation({
    mutationFn: () => rediscoverBalance(providerId, { headers: { "X-Workspace-ID": workspaceId } }),
    onSuccess: (value) => { client.setQueryData(key, value); toast.success('已登记一次余额验证；失败后不会自动重试'); },
    onError: (error) => toast.error(error.message),
  });
  const value = status.data;
  const busy = discover.isPending || value?.state === 'pending' || value?.state === 'running';
  return <div className="space-y-2 rounded-lg border p-3 text-xs">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span>{status.isPending ? '读取余额状态…' : value ? LABELS[value.state] ?? value.state : '无法读取余额状态'}</span>
      {canManage ? <Button type="button" size="xs" variant="outline" disabled={busy || status.isPending} onClick={() => discover.mutate()}>
        {busy ? '验证中…' : '重新探测余额方法'}
      </Button> : null}
    </div>
    {status.isError ? <p role="alert" className="text-destructive">{status.error.message}</p> : null}
    {value?.last_error ? <p className="text-muted-foreground">{value.last_error}</p> : null}
    {value?.last_result ? <div>
      <p>{value.last_result.balance_infos.map((info) => `${info.currency} ${info.total_balance}`).join(' · ') || value.last_result.notice || '已取得账户用量'}</p>
      <p className="text-muted-foreground">上次成功：{new Date(value.last_result.queried_at).toLocaleString()}{value.state !== 'ready' ? '（历史结果，非实时余额）' : ''}</p>
    </div> : null}
    <p className="text-muted-foreground">{value?.auto_refresh_enabled ? '后台按已验证方法低频刷新；失败即暂停。' : '没有验证成功的方法时，不会自动请求供应商。'} 本页刷新只读取服务器缓存。供应商账户余额和订阅用量不是工作区预算余额。</p>
  </div>;
}

import { currentWorkspaceQueryKey } from '@/lib/query-keys';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, RefreshCw, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { exportMemoryEventManifest, getMemoryArchitectureStatus, getMemoryEnhancement, replayValidateMemory } from '@/api';
import { ErrorState, LoadingState, SectionHeading, Surface, } from '@/components/shared/page-elements';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { MemoryEnhancement } from '@/types/memory';
import type { MemoryArchitectureStatus } from '@/types/memory-events';
export function MemorySettingsExportPanel() {
    const queryClient = useQueryClient();
    const enhancement = useQuery({ queryKey: currentWorkspaceQueryKey('memory-enhancement'), queryFn: getMemoryEnhancement });
    const architecture = useQuery<MemoryArchitectureStatus>({
        queryKey: currentWorkspaceQueryKey('memory-architecture-status'),
        queryFn: getMemoryArchitectureStatus,
    });
    const replayValidate = useMutation({
        mutationFn: () => replayValidateMemory(),
        onSuccess: async (result) => {
            const gaps = (result as Record<string, unknown>).gaps;
            toast.success(`重放校验完成${typeof gaps === 'number' ? `（${gaps} 处 gap）` : ''}`);
            await queryClient.invalidateQueries({ queryKey: currentWorkspaceQueryKey('memory-architecture-status') });
        },
        onError: (error) => toast.error(error.message),
    });
    const exportEvents = useMutation({
        mutationFn: () => exportMemoryEventManifest(),
        onSuccess: (result) => {
            const manifest = result as Record<string, unknown>;
            const count = typeof manifest.event_count === 'number' ? manifest.event_count : '?';
            toast.success(`事件清单已导出（${count} 条事件）`);
        },
        onError: (error) => toast.error(error.message),
    });
    const enhancementData = enhancement.data as MemoryEnhancement | undefined;
    return (<div className="space-y-4">
      <Surface className="p-5">
        <SectionHeading description="用户可见的 Audience 决定谁能看到这条记忆；内部 Context Scope 决定检索时按哪个作用域匹配。两者分开建模，不会互相覆盖。" title="受众与作用域"/>
        <div className="mt-4 grid gap-2 border-t pt-4 sm:grid-cols-2">
          <div className="rounded-lg border bg-muted/20 p-3">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <ShieldCheck className="size-3.5"/>Audience（受众）
            </p>
            <p className="mt-1 text-xs text-muted-foreground">tenant / user / workspace / task</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              控制记忆对其他用户或子 Agent 的可见性，由治理动作修改。
            </p>
          </div>
          <div className="rounded-lg border bg-muted/20 p-3">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <RefreshCw className="size-3"/>Context Scope（检索作用域）
            </p>
            <p className="mt-1 text-xs text-muted-foreground">workspace / goal / node / session</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              检索时用于作用域硬过滤，与受众是独立维度。
            </p>
          </div>
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading description="本地 Embedding 用于语义重排；模型变更后需要重新索引。" title="Embedding 与索引"/>
        <div className="mt-4 space-y-3 border-t pt-4">
          {enhancement.isPending ? (<LoadingState />) : enhancement.isError ? (<ErrorState message={enhancement.error.message}/>) : enhancementData ? (<>
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge variant="outline">活跃记忆 {enhancementData.active_memories}</Badge>
                <Badge variant="outline">已索引 {enhancementData.indexed_memories}</Badge>
                <Badge variant={enhancementData.embedding.enabled ? 'default' : 'secondary'}>
                  Embedding {enhancementData.embedding.enabled ? '开启' : '关闭'}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">模型选择统一在模型与服务的任务模型分配中维护。</p>

            </>) : null}
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading description="事件溯源架构状态、重放校验与不可变事件清单导出。需要工作区管理权限。" title="架构状态与导出"/>
        <div className="mt-4 space-y-4 border-t pt-4">
          {architecture.isPending ? (<LoadingState />) : architecture.isError ? (<ErrorState message={architecture.error.message}/>) : architecture.data ? (<div className="flex flex-wrap gap-2 text-xs">
              <Badge variant="outline">写模式 {architecture.data.write_mode}</Badge>
              <Badge variant="outline">读模式 {architecture.data.read_mode}</Badge>
              <Badge variant={architecture.data.context_builder_v2 ? 'default' : 'secondary'}>
                Context Builder v2 {architecture.data.context_builder_v2 ? '开' : '关'}
              </Badge>
              <Badge variant={architecture.data.task_episode_enabled ? 'default' : 'secondary'}>
                Task/Episode {architecture.data.task_episode_enabled ? '开' : '关'}
              </Badge>
              <Badge variant={architecture.data.file_revision_invalidation_enabled ? 'default' : 'secondary'}>
                文件失效 {architecture.data.file_revision_invalidation_enabled ? '开' : '关'}
              </Badge>
            </div>) : null}
          <div className="flex flex-wrap gap-2">
            <Button disabled={replayValidate.isPending} onClick={() => replayValidate.mutate()} size="sm" variant="outline">
              <RefreshCw className={replayValidate.isPending ? 'size-4 animate-spin' : 'size-4'}/>
              运行重放校验
            </Button>
            <Button disabled={exportEvents.isPending} onClick={() => exportEvents.mutate()} size="sm" variant="outline">
              <Download className="size-4"/>导出事件清单
            </Button>
          </div>
        </div>
      </Surface>
    </div>);
}

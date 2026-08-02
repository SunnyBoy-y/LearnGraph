import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, RefreshCw, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'

import {
  exportMemoryEventManifest,
  getMemoryArchitectureStatus,
  getMemoryEnhancement,
  getMemoryPolicy,
  replayValidateMemory,
  updateMemoryEnhancement,
  updateMemoryPolicy,
} from '@/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  ErrorState,
  LoadingState,
  SectionHeading,
  Surface,
} from '@/components/shared/page-elements'
import type { MemoryArchitectureStatus } from '@/types/memory-events'
import type { MemoryEnhancement, MemoryPolicy } from '@/types/memory'

function PolicyRow({
  label,
  description,
  checked,
  disabled,
  onToggle,
}: {
  label: string
  description: string
  checked: boolean
  disabled?: boolean
  onToggle: (value: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border bg-background/60 p-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onToggle} />
    </div>
  )
}

export function MemorySettingsExportPanel() {
  const queryClient = useQueryClient()
  const policy = useQuery({ queryKey: ['memory-policy'], queryFn: () => getMemoryPolicy() })
  const enhancement = useQuery({ queryKey: ['memory-enhancement'], queryFn: getMemoryEnhancement })
  const architecture = useQuery<MemoryArchitectureStatus>({
    queryKey: ['memory-architecture-status'],
    queryFn: getMemoryArchitectureStatus,
  })

  const [embeddingModel, setEmbeddingModel] = useState('')

  const updatePolicy = useMutation({
    mutationFn: (payload: Parameters<typeof updateMemoryPolicy>[0]) => updateMemoryPolicy(payload),
    onSuccess: async () => {
      toast.success('记忆策略已更新')
      await queryClient.invalidateQueries({ queryKey: ['memory-policy'] })
    },
    onError: (error) => toast.error(error.message),
  })

  const updateEnhancement = useMutation({
    mutationFn: (payload: Parameters<typeof updateMemoryEnhancement>[0]) =>
      updateMemoryEnhancement(payload),
    onSuccess: async () => {
      toast.success('Embedding 设置已更新')
      await queryClient.invalidateQueries({ queryKey: ['memory-enhancement'] })
    },
    onError: (error) => toast.error(error.message),
  })

  const replayValidate = useMutation({
    mutationFn: () => replayValidateMemory(),
    onSuccess: async (result) => {
      const gaps = (result as Record<string, unknown>).gaps
      toast.success(`重放校验完成${typeof gaps === 'number' ? `（${gaps} 处 gap）` : ''}`)
      await queryClient.invalidateQueries({ queryKey: ['memory-architecture-status'] })
    },
    onError: (error) => toast.error(error.message),
  })

  const exportEvents = useMutation({
    mutationFn: () => exportMemoryEventManifest(),
    onSuccess: (result) => {
      const manifest = result as Record<string, unknown>
      const count = typeof manifest.event_count === 'number' ? manifest.event_count : '?'
      toast.success(`事件清单已导出（${count} 条事件）`)
    },
    onError: (error) => toast.error(error.message),
  })

  const policyData = policy.data as MemoryPolicy | undefined
  const enhancementData = enhancement.data as MemoryEnhancement | undefined

  return (
    <div className="space-y-4">
      <Surface className="p-5">
        <SectionHeading
          description="控制自动召回与学习状态投影。关闭后旧记忆仍保留，只是不再注入对话。"
          title="召回与学习开关"
        />
        <div className="mt-4 grid gap-2 border-t pt-4">
          {policy.isPending ? (
            <LoadingState />
          ) : policy.isError ? (
            <ErrorState message={policy.error.message} />
          ) : policyData ? (
            <>
              <PolicyRow
                checked={policyData.effective_recall_enabled ?? false}
                description="工作区级：是否在对话中自动召回长期记忆。"
                disabled={updatePolicy.isPending}
                label="工作区自动召回"
                onToggle={(value) =>
                  updatePolicy.mutate({ workspace_recall_enabled: value })
                }
              />
              <PolicyRow
                checked={policyData.effective_learning_enabled ?? false}
                description="工作区级：是否根据证据更新知识节点掌握度。"
                disabled={updatePolicy.isPending}
                label="学习状态投影"
                onToggle={(value) =>
                  updatePolicy.mutate({ workspace_learning_enabled: value })
                }
              />
              <PolicyRow
                checked={!policyData.effective_recall_enabled
                  ? false
                  : !(policyData.workspace_recall_enabled === false)}
                description="仅作展示：当前会话是否继承工作区召回开关。关闭工作区召回时此项不生效。"
                disabled
                label="当前会话继承召回"
                onToggle={() => undefined}
              />
            </>
          ) : null}
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading
          description="用户可见的 Audience 决定谁能看到这条记忆；内部 Context Scope 决定检索时按哪个作用域匹配。两者分开建模，不会互相覆盖。"
          title="受众与作用域"
        />
        <div className="mt-4 grid gap-2 border-t pt-4 sm:grid-cols-2">
          <div className="rounded-lg border bg-muted/20 p-3">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <ShieldCheck className="size-3.5" />Audience（受众）
            </p>
            <p className="mt-1 text-xs text-muted-foreground">tenant / user / workspace / task</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              控制记忆对其他用户或子 Agent 的可见性，由治理动作修改。
            </p>
          </div>
          <div className="rounded-lg border bg-muted/20 p-3">
            <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <RefreshCw className="size-3" />Context Scope（检索作用域）
            </p>
            <p className="mt-1 text-xs text-muted-foreground">workspace / goal / node / session</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              检索时用于作用域硬过滤，与受众是独立维度。
            </p>
          </div>
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading
          description="本地 Embedding 用于语义重排；模型变更后需要重新索引。"
          title="Embedding 与索引"
        />
        <div className="mt-4 space-y-3 border-t pt-4">
          {enhancement.isPending ? (
            <LoadingState />
          ) : enhancement.isError ? (
            <ErrorState message={enhancement.error.message} />
          ) : enhancementData ? (
            <>
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge variant="outline">活跃记忆 {enhancementData.active_memories}</Badge>
                <Badge variant="outline">已索引 {enhancementData.indexed_memories}</Badge>
                <Badge variant={enhancementData.embedding.enabled ? 'default' : 'secondary'}>
                  Embedding {enhancementData.embedding.enabled ? '开启' : '关闭'}
                </Badge>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <div className="flex-1 space-y-2">
                  <Label htmlFor="embedding-model">Embedding 模型 ID</Label>
                  <Input
                    id="embedding-model"
                    onChange={(event) => setEmbeddingModel(event.target.value)}
                    placeholder={enhancementData.embedding.model_id || '输入模型 ID'}
                    value={embeddingModel || enhancementData.embedding.model_id}
                  />
                </div>
                <Button
                  disabled={updateEnhancement.isPending || !embeddingModel.trim()}
                  onClick={() =>
                    updateEnhancement.mutate({ embedding: { model_id: embeddingModel.trim() } })
                  }
                  size="sm"
                  variant="outline"
                >
                  保存模型
                </Button>
              </div>
            </>
          ) : null}
        </div>
      </Surface>

      <Surface className="p-5">
        <SectionHeading
          description="事件溯源架构状态、重放校验与不可变事件清单导出。需要工作区管理权限。"
          title="架构状态与导出"
        />
        <div className="mt-4 space-y-4 border-t pt-4">
          {architecture.isPending ? (
            <LoadingState />
          ) : architecture.isError ? (
            <ErrorState message={architecture.error.message} />
          ) : architecture.data ? (
            <div className="flex flex-wrap gap-2 text-xs">
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
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={replayValidate.isPending}
              onClick={() => replayValidate.mutate()}
              size="sm"
              variant="outline"
            >
              <RefreshCw className={replayValidate.isPending ? 'size-4 animate-spin' : 'size-4'} />
              运行重放校验
            </Button>
            <Button
              disabled={exportEvents.isPending}
              onClick={() => exportEvents.mutate()}
              size="sm"
              variant="outline"
            >
              <Download className="size-4" />导出事件清单
            </Button>
          </div>
        </div>
      </Surface>
    </div>
  )
}

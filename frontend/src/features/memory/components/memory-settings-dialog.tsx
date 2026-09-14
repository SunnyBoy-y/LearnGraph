import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Download, Settings2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { getMemoryEnhancement, getMemoryPolicy, updateMemoryEnhancement, updateMemoryPolicy } from '@/api'
import { ErrorState, LoadingState, SectionHeading, Surface } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { MemorySettingsExportPanel } from './memory-settings-export-panel'

function SettingRow({
  checked,
  description,
  disabled,
  label,
  onToggle,
}: {
  checked: boolean
  description: string
  disabled?: boolean
  label: string
  onToggle: (value: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3.5">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onToggle} />
    </div>
  )
}

/**
 * 记忆设置：普通用户只看到「自动记忆 / 使用记忆 / 数据」三组开关，
 * 架构状态、Audience、Context Scope、Embedding、Event Manifest 等收进「高级记忆设置」。
 */
export function MemorySettingsDialog({
  exportBusy,
  open,
  onExport,
  onOpenGovernance,
  onOpenTrash,
  onOpenChange,
}: {
  exportBusy: boolean
  open: boolean
  onExport: () => void
  onOpenGovernance: () => void
  onOpenTrash: () => void
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const policy = useQuery({ queryKey: ['memory-policy'], queryFn: () => getMemoryPolicy() })
  const enhancement = useQuery({ queryKey: ['memory-enhancement'], queryFn: getMemoryEnhancement })

  const updatePolicy = useMutation({
    mutationFn: (payload: Parameters<typeof updateMemoryPolicy>[0]) => updateMemoryPolicy(payload),
    onSuccess: async () => {
      toast.success('记忆设置已更新')
      await queryClient.invalidateQueries({ queryKey: ['memory-policy'] })
    },
    onError: (error) => toast.error(error.message),
  })

  const updateEnhancement = useMutation({
    mutationFn: (payload: Parameters<typeof updateMemoryEnhancement>[0]) =>
      updateMemoryEnhancement(payload),
    onSuccess: async () => {
      toast.success('自动记忆设置已更新')
      await queryClient.invalidateQueries({ queryKey: ['memory-enhancement'] })
    },
    onError: (error) => toast.error(error.message),
  })

  const policyData = policy.data
  const enhancementData = enhancement.data
  const loading = policy.isPending || enhancement.isPending
  const errored = policy.error ?? enhancement.error

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>记忆设置</DialogTitle>
          <DialogDescription>
            决定 AI 导师记什么、什么时候使用你记住的内容。关闭后旧记忆仍然保留，只是不再参与对话。
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <LoadingState label="正在读取记忆设置…" />
        ) : errored ? (
          <ErrorState message={errored.message} />
        ) : (
          <div className="space-y-3 py-1">
            <Surface className="overflow-hidden p-0">
              <div className="border-b px-4 py-3">
                <p className="text-sm font-semibold">自动记忆</p>
              </div>
              <SettingRow
                checked={enhancementData?.extraction.enabled ?? false}
                description="从对话与学习记录中发现值得长期记住的信息，先整理成待确认的理解。"
                disabled={updateEnhancement.isPending || !enhancementData}
                label="自动发现值得记住的信息"
                onToggle={(value) => updateEnhancement.mutate({ extraction: { enabled: value } })}
              />
            </Surface>

            <Surface className="overflow-hidden p-0">
              <div className="border-b px-4 py-3">
                <p className="text-sm font-semibold">使用记忆</p>
              </div>
              <div className="divide-y">
                <SettingRow
                  checked={policyData?.effective_recall_enabled ?? false}
                  description="在回答中使用我的长期记忆，避免重复解释已经学过的内容。"
                  disabled={updatePolicy.isPending || !policyData}
                  label="在回答中使用长期记忆"
                  onToggle={(value) => updatePolicy.mutate({ workspace_recall_enabled: value })}
                />
                <SettingRow
                  checked={policyData?.effective_learning_enabled ?? false}
                  description="根据学习证据更新我对知识点的掌握程度，用来调整练习与讲解重点。"
                  disabled={updatePolicy.isPending || !policyData}
                  label="使用记忆调整学习内容"
                  onToggle={(value) => updatePolicy.mutate({ workspace_learning_enabled: value })}
                />
              </div>
            </Surface>

            <Surface className="overflow-hidden p-0">
              <div className="border-b px-4 py-3">
                <p className="text-sm font-semibold">数据</p>
              </div>
              <div className="flex flex-wrap gap-1.5 px-4 py-3.5">
                <Button disabled={exportBusy} onClick={onExport} size="sm" variant="outline">
                  <Download className="size-4" />导出记忆
                </Button>
                <Button
                  onClick={() => {
                    onOpenChange(false)
                    onOpenTrash()
                  }}
                  size="sm"
                  variant="outline"
                >
                  <Trash2 className="size-4" />查看回收站
                </Button>
              </div>
            </Surface>

            <Surface className="overflow-hidden p-0">
              <Collapsible onOpenChange={setAdvancedOpen} open={advancedOpen}>
                <CollapsibleTrigger asChild>
                  <button
                    className="flex w-full items-center justify-between gap-3 px-4 py-3.5 text-left hover:bg-muted/40"
                    type="button"
                  >
                    <span className="flex items-center gap-2 text-sm font-medium">
                      <Settings2 className="size-4 text-muted-foreground" />
                      高级记忆设置
                    </span>
                    <ChevronRight
                      className={
                        advancedOpen
                          ? 'size-4 rotate-90 text-muted-foreground transition-transform'
                          : 'size-4 text-muted-foreground transition-transform'
                      }
                    />
                  </button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="space-y-3 border-t p-4">
                    <p className="text-xs leading-5 text-muted-foreground">
                      受众与作用域、Embedding 与索引、架构状态、事件清单与重放校验，以及完整的记忆治理入口。
                    </p>
                    <MemorySettingsExportPanel showPolicy={false} />
                    <Button onClick={onOpenGovernance} size="sm" variant="outline">
                      <Settings2 className="size-4" />前往工作区记忆治理
                    </Button>
                  </div>
                </CollapsibleContent>
              </Collapsible>
            </Surface>

            <div className="pt-1">
              <SectionHeading
                description="更多工程细节（Provider Binding、Context Builder、Replay Validate）保留在工作区设置的记忆治理页。"
                title="需要更细的控制？"
              />
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

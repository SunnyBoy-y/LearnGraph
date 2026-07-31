import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ShieldCheck, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'

import {
  forgetMemory,
  recordMemoryFeedback,
} from '@/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { MemoryEntry } from '@/types/memory'

export function MemoryGovernancePanel({ memory }: { memory: MemoryEntry }) {
  const queryClient = useQueryClient()
  const [confirmation, setConfirmation] = useState('')
  const feedback = useMutation({
    mutationFn: (feedbackType: 'stale' | 'wrong' | 'project_only' | 'deny_child' | 'suppress_auto_recall') =>
      recordMemoryFeedback(memory.id, { feedback_type: feedbackType }),
    onSuccess: async () => {
      toast.success('记忆治理策略已更新')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['memory'] }),
        queryClient.invalidateQueries({ queryKey: ['memory-detail', memory.id] }),
      ])
    },
    onError: (error) => toast.error(error.message),
  })
  const forget = useMutation({
    mutationFn: () => forgetMemory(memory.id, { confirmation }),
    onSuccess: async () => {
      toast.success('已永久忘记；外部投影将在后台持续清理')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['memory'] }),
        queryClient.invalidateQueries({ queryKey: ['memory-detail', memory.id] }),
      ])
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <div className="space-y-3 rounded-xl border border-border/70 bg-muted/20 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldCheck className="size-4" />
        <strong className="text-sm">记忆治理</strong>
        <Badge variant="outline">{memory.audience_type ?? 'workspace'}</Badge>
        <Badge variant="outline">{memory.sensitivity ?? 'normal'}</Badge>
        <Badge variant="outline">{memory.lifecycle_status ?? 'active'}</Badge>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('stale')}>已过时</Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('wrong')}>记错了</Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('project_only')}>只在本项目</Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('deny_child')}>不给子 Agent</Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('suppress_auto_recall')}>停止自动召回</Button>
      </div>
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
        <p className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <TriangleAlert className="size-4 text-destructive" />
          永久忘记会销毁事件密钥并清除检索、向量、关系与上下文投影，无法恢复。请输入“{memory.title}”确认。
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="永久忘记确认标题"
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder={memory.title}
            value={confirmation}
          />
          <Button
            size="sm"
            variant="destructive"
            disabled={forget.isPending || confirmation.trim() !== memory.title.trim()}
            onClick={() => forget.mutate()}
          >
            永久忘记
          </Button>
        </div>
      </div>
    </div>
  )
}

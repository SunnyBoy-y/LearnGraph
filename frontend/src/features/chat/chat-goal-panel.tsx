import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, Pause, Play, Target, X } from 'lucide-react'
import { controlChatGoal, readChatGoal, type ChatGoal, type ChatGoalAction } from '@/api/chat-goals'
import { Button } from '@/components/ui/button'
import { toast } from '@/lib/notifications'
import { workspaceQueryKey } from '@/lib/query-keys'

const labels: Record<ChatGoal['status'], string> = {
  queued: '排队中', running: '执行中', pausing: '本轮结束后暂停', clearing: '本轮结束后清除',
  paused: '已暂停', completed: '已完成', blocked: '待补充', failed: '未完成', budget_exhausted: '轮次已用尽',
}

export function chatGoalIsActive(goal?: ChatGoal | null) {
  return goal != null && (goal.status === 'queued' || goal.status === 'running'
    || goal.status === 'pausing' || goal.status === 'clearing')
}

export const chatGoalKey = (workspaceId: string, sessionId: string) => workspaceQueryKey(workspaceId, 'chat-goal', sessionId)

export function useChatGoal(workspaceId: string, sessionId: string) {
  return useQuery({
    queryKey: chatGoalKey(workspaceId, sessionId),
    queryFn: ({ signal }) => readChatGoal(sessionId, signal),
    enabled: sessionId !== 'new',
    refetchInterval: (query) => chatGoalIsActive(query.state.data) ? 2000 : false,
  })
}

export function ChatGoalPanel({ workspaceId, sessionId, goal, readOnly }: {
  workspaceId: string; sessionId: string; goal: ChatGoal; readOnly: boolean;
}) {
  const queryClient = useQueryClient()
  const control = useMutation({
    mutationFn: (action: ChatGoalAction) => controlChatGoal(sessionId, goal.id, action),
    onSuccess: (value) => {
      queryClient.setQueryData(chatGoalKey(workspaceId, sessionId), value)
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'messages', sessionId) })
    },
    onError: (error) => {
      toast.error(error.message)
      void queryClient.invalidateQueries({ queryKey: chatGoalKey(workspaceId, sessionId) })
    },
  })
  const canPause = goal.status === 'running' || goal.status === 'queued'
  const canResume = ['paused', 'blocked', 'failed'].includes(goal.status) && goal.rounds_used < goal.max_rounds
  return (
    <section aria-label="长程任务" className="min-w-0 self-stretch rounded-lg border border-border bg-background px-3 py-2 text-sm">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Target aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
        <strong className="shrink-0 font-medium">长程任务</strong>
        <span role="status" className="flex min-w-0 flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          {chatGoalIsActive(goal) ? <LoaderCircle aria-hidden="true" className="size-3 motion-safe:animate-spin" /> : null}
          {labels[goal.status]} <span>{goal.rounds_used}/{goal.max_rounds} 轮</span>
        </span>
        {!readOnly && goal.status !== 'clearing' ? <div className="ml-auto flex shrink-0 items-center gap-1">
          {canPause ? <Button type="button" size="icon-xs" variant="ghost" aria-label="暂停长程任务" title="本轮结束后暂停"
            disabled={control.isPending} onClick={() => control.mutate('pause')}><Pause aria-hidden="true" className="size-3.5" /></Button> : null}
          {canResume ? <Button type="button" size="icon-xs" variant="ghost" aria-label="继续长程任务" title="继续长程任务"
            disabled={control.isPending} onClick={() => control.mutate('resume')}><Play aria-hidden="true" className="size-3.5" /></Button> : null}
          <Button type="button" size="icon-xs" variant="ghost" aria-label="清除长程任务" title="清除任务，保留聊天记录"
            disabled={control.isPending} onClick={() => control.mutate('clear')}><X aria-hidden="true" className="size-3.5" /></Button>
        </div> : null}
      </div>
      <p className="mt-1 line-clamp-2 break-words" title={goal.objective}>{goal.objective}</p>
      {goal.summary ? <p className="mt-1 max-h-20 overflow-y-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">{goal.summary}</p> : null}
    </section>
  )
}

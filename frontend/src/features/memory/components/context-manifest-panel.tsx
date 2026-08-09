import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { listContextManifests } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ContextManifestReceipt } from '@/types/context-builds'

export function ContextManifestPanel() {
  const [sessionId, setSessionId] = useState('')
  const [appliedSessionId, setAppliedSessionId] = useState('')
  const receipts = useQuery({
    queryKey: ['memory-context-manifests', appliedSessionId],
    queryFn: () =>
      listContextManifests({
        session_id: appliedSessionId || undefined,
      }),
    enabled: Boolean(appliedSessionId),
  })

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input
          value={sessionId}
          onChange={(event) => setSessionId(event.target.value)}
          placeholder="Session ID（留空查全部）"
        />
        <Button
          disabled={receipts.isFetching}
          onClick={() => setAppliedSessionId(sessionId.trim())}
        >
          读取回执
        </Button>
      </div>
      {receipts.isError ? (
        <p className="text-sm text-red-600">无法读取记忆回执。</p>
      ) : null}
      {receipts.data?.length ? (
        <div className="space-y-2">
          {receipts.data.map((receipt: ContextManifestReceipt) => (
            <div
              className="rounded-lg border p-3 text-sm"
              key={receipt.context_build_id}
            >
              <div className="flex flex-wrap gap-4 text-muted-foreground">
                <span className="font-mono text-xs">{receipt.context_build_id}</span>
                <span>状态 {receipt.status}</span>
                <span>
                  注入 {receipt.injected_ids.length} / 选择 {receipt.selected_ids.length} / 检索{' '}
                  {receipt.retrieved_ids.length}
                </span>
                <span>{receipt.injected_tokens} tokens</span>
              </div>
              <div className="mt-2 grid gap-1 text-xs sm:grid-cols-2">
                <span>候选 {receipt.candidate_ids.join(', ') || '无'}</span>
                <span>排除 {receipt.excluded_ids.join(', ') || '无'}</span>
                <span>截断 {receipt.truncated_ids.join(', ') || '无'}</span>
                <span>原因 {JSON.stringify(receipt.reason_codes)}</span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          {receipts.isFetched ? '暂无历史回执。' : '输入 Session ID 后读取真实 Manifest 回执。'}
        </p>
      )}
    </div>
  )
}

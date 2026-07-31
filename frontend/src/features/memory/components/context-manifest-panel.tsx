import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { buildMemoryContext } from '@/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { ContextBuildView } from '@/types/context-builds'

export function ContextManifestPanel() {
  const [query, setQuery] = useState('继续当前任务')
  const build = useMutation({
    mutationFn: () => buildMemoryContext({ query, debug_manifest: true }),
  })
  const result: ContextBuildView | undefined = build.data

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input value={query} onChange={(event) => setQuery(event.target.value)} />
        <Button disabled={!query.trim() || build.isPending} onClick={() => build.mutate()}>预览 AI 上下文</Button>
      </div>
      {result && (
        <div className="space-y-2 rounded-xl border p-4 text-sm">
          <div className="flex flex-wrap gap-4 text-muted-foreground">
            <span>Build {result.context_build_id}</span>
            <span>{result.total_tokens} tokens</span>
            <span>{result.memories.length} 条记忆</span>
          </div>
          {result.context_manifest.map((item, index) => (
            <pre key={index} className="overflow-auto rounded-lg bg-muted p-3 text-xs">
              {JSON.stringify(item, null, 2)}
            </pre>
          ))}
          {result.degraded_modes.length > 0 && (
            <p className="text-xs text-amber-600">降级：{result.degraded_modes.join(', ')}</p>
          )}
        </div>
      )}
    </div>
  )
}

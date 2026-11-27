import { Component, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { isChunkLoadError } from '@/lib/lazy-with-retry'

type RenderBoundaryProps = {
  children: ReactNode
  fallbackHref: string
  onRetry: () => Promise<unknown>
  resetKey: string
}

type RenderBoundaryState = {
  failed: boolean
  chunkError: boolean
  retrying: boolean
  resetKey: string
  errorDetail: string
}

class RenderBoundary extends Component<RenderBoundaryProps, RenderBoundaryState> {
  state: RenderBoundaryState = { failed: false, chunkError: false, retrying: false, resetKey: '', errorDetail: '' }

  static getDerivedStateFromProps(
    props: RenderBoundaryProps,
    state: RenderBoundaryState,
  ): Partial<RenderBoundaryState> | null {
    // Reset only the error state when the route changes. Remounting this
    // boundary with React's `key` also remounted WorkspaceShell and its
    // settings dialog, which made every settings navigation flash.
    if (state.resetKey !== props.resetKey) {
      return { failed: false, chunkError: false, retrying: false, resetKey: props.resetKey, errorDetail: '' }
    }
    return null
  }

  static getDerivedStateFromError(error: unknown) {
    return {
      failed: true,
      chunkError: isChunkLoadError(error),
      retrying: false,
      errorDetail: error instanceof Error ? error.stack || error.message : '未知渲染错误',
    }
  }

  retry = async () => {
    if (this.state.retrying) return
    if (this.state.chunkError) {
      window.location.reload()
      return
    }
    this.setState({ retrying: true })
    try {
      await this.props.onRetry()
      this.setState({ failed: false, chunkError: false, retrying: false, errorDetail: '' })
    } catch {
      this.setState({ retrying: false })
    }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <main className="grid min-h-svh place-items-center p-6">
        <section role="alert" className="max-w-lg space-y-4">
          <h1 className="text-xl font-semibold">
            {this.state.chunkError ? '页面模块加载失败' : '页面暂时无法显示'}
          </h1>
          <p>
            {this.state.chunkError
              ? '当前网络较慢或应用版本刚更新，页面脚本未能完整下载。已保存的数据不会被清空，请点击重新加载。'
              : '内容格式或加载状态异常。已保存的数据不会被清空，你可以重新读取，或返回图谱书架。'}
          </p>
          <div className="flex gap-3">
            <Button disabled={this.state.retrying} onClick={() => void this.retry()}>
              {this.state.retrying ? '重新读取中…' : this.state.chunkError ? '重新加载页面' : '重新读取页面'}
            </Button>
            <Button asChild variant="outline"><Link to={this.props.fallbackHref}>返回图谱书架</Link></Button>
          </div>
          <details className="text-sm text-muted-foreground">
            <summary className="cursor-pointer">查看错误详情</summary>
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded border p-3 text-xs">{this.state.errorDetail}</pre>
          </details>
        </section>
      </main>
    )
  }
}

export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation()
  const params = useParams()
  const client = useQueryClient()
  const workspaceId = params.workspaceId ?? location.pathname.match(/^\/w\/([^/]+)/)?.[1]
  return <RenderBoundary resetKey={location.key} fallbackHref={workspaceId ? `/w/${workspaceId}/graphs` : '/'} onRetry={() => client.invalidateQueries()}>{children}</RenderBoundary>
}

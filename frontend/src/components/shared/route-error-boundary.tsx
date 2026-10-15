import { Component, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'

type RenderBoundaryProps = {
  children: ReactNode
  fallbackHref: string
  onRetry: () => Promise<unknown>
  resetKey: string
}

type RenderBoundaryState = {
  failed: boolean
  retrying: boolean
  resetKey: string
}

class RenderBoundary extends Component<RenderBoundaryProps, RenderBoundaryState> {
  state: RenderBoundaryState = { failed: false, retrying: false, resetKey: '' }

  static getDerivedStateFromProps(
    props: RenderBoundaryProps,
    state: RenderBoundaryState,
  ): Partial<RenderBoundaryState> | null {
    // Reset only the error state when the route changes. Remounting this
    // boundary with React's `key` also remounted WorkspaceShell and its
    // settings dialog, which made every settings navigation flash.
    if (state.resetKey !== props.resetKey) {
      return { failed: false, retrying: false, resetKey: props.resetKey }
    }
    return null
  }

  static getDerivedStateFromError() { return {failed: true, retrying: false} }
  retry = async () => {
    if (this.state.retrying) return
    this.setState({retrying: true})
    try { await this.props.onRetry(); this.setState({failed: false, retrying: false}) }
    catch { this.setState({retrying: false}) }
  }
  render() {
    if (!this.state.failed) return this.props.children
    return <main className="grid min-h-svh place-items-center p-6"><section role="alert" className="max-w-lg space-y-4"><h1 className="text-xl font-semibold">页面暂时无法显示</h1><p>内容格式或加载状态异常。已保存的数据不会被清空，你可以重新读取，或返回图谱书架。</p><div className="flex gap-3"><Button disabled={this.state.retrying} onClick={() => void this.retry()}>{this.state.retrying ? '重新读取中…' : '重新读取页面'}</Button><Button asChild variant="outline"><Link to={this.props.fallbackHref}>返回图谱书架</Link></Button></div></section></main>
  }
}

export function RouteErrorBoundary({children}: {children: ReactNode}) {
  const location = useLocation()
  const params = useParams()
  const client = useQueryClient()
  const workspaceId = params.workspaceId ?? location.pathname.match(/^\/w\/([^/]+)/)?.[1]
  return <RenderBoundary resetKey={location.key} fallbackHref={workspaceId ? `/w/${workspaceId}/graphs` : '/'} onRetry={() => client.invalidateQueries()}>{children}</RenderBoundary>
}

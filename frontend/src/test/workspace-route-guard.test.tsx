import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Link, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AuthContext, type AuthContextValue } from '@/features/auth/auth-context-value'
import { WorkspaceRouteGuard } from '@/features/auth/workspace-route-guard'

/**
 * 切换账户白屏回归：登出后 state.from 仍指向上一个账户的工作区，新账户登录会
 * 先落到 `/w/<别的账户工作区>/...`，那里必然 select 失败(403)。守卫必须能从这个
 * 失败里走出来渲染当前账户的工作区，绝不能停在「重定向到当前 URL」的空页面。
 */
const ACTIVE_WORKSPACE = 'mine'
const OTHER_WORKSPACE = 'other'

function authValue(setWorkspaceId: AuthContextValue['setWorkspaceId']): AuthContextValue {
  return {
    authenticated: true,
    username: 'Alice',
    avatarUrl: null,
    updateProfile: () => undefined,
    workspaceId: ACTIVE_WORKSPACE,
    workspaceName: '我的工作区',
    mustChangePassword: false,
    setWorkspaceId,
    login: async () => ({ workspaceId: ACTIVE_WORKSPACE, mustChangePassword: false }),
    demoLogin: async () => ({ workspaceId: ACTIVE_WORKSPACE, mustChangePassword: false }),
    register: async () => ({ workspaceId: ACTIVE_WORKSPACE, mustChangePassword: false }),
    changePassword: async () => undefined,
    deleteAccount: async () => undefined,
    logout: async () => undefined,
  }
}

/** 手工控制的 setWorkspaceId：每次调用都挂起，由用例决定何时失败。 */
function pendingSelection() {
  const calls: Array<(reason?: unknown) => void> = []
  const setWorkspaceId = vi.fn(
    () =>
      new Promise<void>((_resolve, reject) => {
        calls.push(reject)
      }),
  )
  return { setWorkspaceId, calls }
}

function guardTree(value: AuthContextValue, entry: string) {
  return (
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            element={
              <WorkspaceRouteGuard>
                <p>工作区内容</p>
              </WorkspaceRouteGuard>
            }
            path="/w/:workspaceId/*"
          />
        </Routes>
        <Link to={`/w/${ACTIVE_WORKSPACE}/home`}>跳到我的工作区</Link>
      </MemoryRouter>
    </AuthContext.Provider>
  )
}

describe('WorkspaceRouteGuard', () => {
  it('无法选择的工作区回落到当前账户工作区并渲染内容', async () => {
    const selection = pendingSelection()
    render(guardTree(authValue(selection.setWorkspaceId), `/w/${OTHER_WORKSPACE}/artifacts`))

    await waitFor(() => expect(selection.calls).toHaveLength(1))
    await act(async () => {
      selection.calls[0](new Error('403 workspace_forbidden'))
    })

    expect(await screen.findByText('工作区内容')).toBeInTheDocument()
  })

  it('失败晚于回落时必须仍然渲染当前工作区，而不是空页面', async () => {
    const selection = pendingSelection()
    render(guardTree(authValue(selection.setWorkspaceId), `/w/${OTHER_WORKSPACE}/artifacts`))
    await waitFor(() => expect(selection.calls).toHaveLength(1))

    // 用户在失败返回前就已经回到自己的工作区（切换账户 / 点导航都会走到这里）。
    fireEvent.click(screen.getByRole('link', { name: '跳到我的工作区' }))
    expect(await screen.findByText('工作区内容')).toBeInTheDocument()

    // 迟到的 403：旧实现会在这里把整页变成空白，且之后没有任何依赖变化来解锁。
    await act(async () => {
      selection.calls[0](new Error('403 workspace_forbidden'))
    })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })

    expect(screen.getByText('工作区内容')).toBeInTheDocument()
  })

  it('身份抖动时同一个工作区只发起一次选择请求', async () => {
    const first = pendingSelection()
    const { rerender } = render(
      guardTree(authValue(first.setWorkspaceId), `/w/${OTHER_WORKSPACE}/artifacts`),
    )
    await waitFor(() => expect(first.setWorkspaceId).toHaveBeenCalledTimes(1))

    // 会话水合期间 session / workspaceName 先后更新，auth value 的引用会变，
    // 守卫的 effect 因此重跑；重复请求只会带来迟到的 403。
    const second = pendingSelection()
    rerender(guardTree(authValue(second.setWorkspaceId), `/w/${OTHER_WORKSPACE}/artifacts`))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })

    expect(first.setWorkspaceId).toHaveBeenCalledTimes(1)
    expect(second.setWorkspaceId).not.toHaveBeenCalled()
  })
})

import { describe, expect, it } from 'vitest'

import { postLoginPath } from './post-login-redirect'

describe('postLoginPath', () => {
  it('同一账户重新登录时回到原来的路径', () => {
    expect(postLoginPath('/w/ws-1/chat/abc?x=1', 'ws-1')).toBe('/w/ws-1/chat/abc?x=1')
  })

  it('切换账户时忽略上一个账户的工作区路径', () => {
    expect(postLoginPath('/w/ws-1/artifacts', 'ws-2')).toBe('/w/ws-2/chat/new')
  })

  it('工作区前缀相同但并非同一段时不算命中', () => {
    expect(postLoginPath('/w/ws-12/home', 'ws-1')).toBe('/w/ws-1/chat/new')
    expect(postLoginPath('/workspaces/ws-1', 'ws-1')).toBe('/w/ws-1/chat/new')
  })

  it('没有来源或来源是鉴权页时进入新对话', () => {
    expect(postLoginPath(undefined, 'ws-1')).toBe('/w/ws-1/chat/new')
    expect(postLoginPath(null, 'ws-1')).toBe('/w/ws-1/chat/new')
    expect(postLoginPath('/auth/change-password', 'ws-1')).toBe('/w/ws-1/chat/new')
  })
})

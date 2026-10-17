import { expect, it, vi } from 'vitest'

import { demoLogin } from './auth'
import { apiClient } from './client'

it('requests demo login without sending browser credentials', async () => {
  const response = {
    access_token: 'session-token', default_workspace_id: 'workspace', username: 'server-selected',
  }
  const post = vi.spyOn(apiClient, 'post').mockResolvedValue(response)
  expect(await demoLogin()).toEqual(response)
  expect(post).toHaveBeenCalledWith('/auth/demo-login', {}, { auth: false, workspace: false })
})

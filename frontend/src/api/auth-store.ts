import type { AuthSession, LoginResponse } from '@/types/auth'
import { createUuid } from '@/lib/uuid'

const TOKEN_KEY = 'learngraph.access_token'
const WORKSPACE_KEY = 'learngraph.workspace_id'
const USER_ID_KEY = 'learngraph.user_id'
const USERNAME_KEY = 'learngraph.username'
const DISPLAY_NAME_KEY = 'learngraph.display_name'
const SESSION_ID_KEY = 'learngraph.session_id'
const DEVICE_ID_KEY = 'learngraph.device_id'

type AuthState = {
  accessToken: string | null
  workspaceId: string | null
  userId: string | null
  username: string | null
  displayName: string | null
  sessionId: string | null
}

const memoryState: AuthState = {
  accessToken: null,
  workspaceId: null,
  userId: null,
  username: null,
  displayName: null,
  sessionId: null,
}
let memoryDeviceId: string | null = null

function browserStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function persistentStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function createDeviceId(): string {
  return createUuid()
}

function read(key: string, fallback: string | null): string | null {
  const storage = browserStorage()
  if (!storage) return fallback

  try {
    return storage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

function write(key: string, value: string | null): void {
  const storage = browserStorage()
  if (!storage) return

  try {
    if (value === null) storage.removeItem(key)
    else storage.setItem(key, value)
  } catch {
    // Private browsing and storage policies can reject writes. The in-memory
    // state remains usable for the current page lifetime.
  }
}

export const authStore = {
  getDeviceId(): string {
    const storage = persistentStorage()
    if (storage) {
      try {
        const existing = storage.getItem(DEVICE_ID_KEY)
        if (existing) return existing
      } catch {
        // Fall back to the in-memory device identity below.
      }
    }
    if (!memoryDeviceId) memoryDeviceId = createDeviceId()
    try {
      storage?.setItem(DEVICE_ID_KEY, memoryDeviceId)
    } catch {
      // Private browsing can reject localStorage; the current tab remains usable.
    }
    return memoryDeviceId
  },

  getAccessToken(): string | null {
    return read(TOKEN_KEY, memoryState.accessToken)
  },

  getWorkspaceId(): string | null {
    return read(WORKSPACE_KEY, memoryState.workspaceId)
  },

  getSession(): AuthSession | null {
    const accessToken = this.getAccessToken()
    const workspaceId = this.getWorkspaceId()
    if (!accessToken || !workspaceId) return null

    const userId = read(USER_ID_KEY, memoryState.userId)
    const username = read(USERNAME_KEY, memoryState.username)
    const displayName = read(DISPLAY_NAME_KEY, memoryState.displayName)
    const sessionId = read(SESSION_ID_KEY, memoryState.sessionId)
    return {
      accessToken,
      workspaceId,
      ...(userId ? { userId } : {}),
      ...(username ? { username } : {}),
      ...(displayName ? { displayName } : {}),
      ...(sessionId ? { sessionId } : {}),
    }
  },

  setAccessToken(accessToken: string | null): void {
    memoryState.accessToken = accessToken
    write(TOKEN_KEY, accessToken)
  },

  setWorkspaceId(workspaceId: string | null): void {
    memoryState.workspaceId = workspaceId
    write(WORKSPACE_KEY, workspaceId)
  },

  setSession(session: AuthSession): void {
    memoryState.accessToken = session.accessToken
    memoryState.workspaceId = session.workspaceId
    memoryState.userId = session.userId ?? null
    memoryState.username = session.username ?? null
    memoryState.displayName = session.displayName ?? null
    memoryState.sessionId = session.sessionId ?? null
    write(TOKEN_KEY, memoryState.accessToken)
    write(WORKSPACE_KEY, memoryState.workspaceId)
    write(USER_ID_KEY, memoryState.userId)
    write(USERNAME_KEY, memoryState.username)
    write(DISPLAY_NAME_KEY, memoryState.displayName)
    write(SESSION_ID_KEY, memoryState.sessionId)
  },

  setLoginResponse(response: LoginResponse): void {
    if (!response.default_workspace_id) {
      throw new Error('This account has no accessible workspace')
    }
    this.setSession({
      accessToken: response.access_token,
      workspaceId: response.default_workspace_id,
      userId: response.user_id,
      username: response.username,
      displayName: response.display_name,
      sessionId: response.session_id,
    })
  },

  clear(): void {
    memoryState.accessToken = null
    memoryState.workspaceId = null
    memoryState.userId = null
    memoryState.username = null
    memoryState.displayName = null
    memoryState.sessionId = null
    write(TOKEN_KEY, null)
    write(WORKSPACE_KEY, null)
    write(USER_ID_KEY, null)
    write(USERNAME_KEY, null)
    write(DISPLAY_NAME_KEY, null)
    write(SESSION_ID_KEY, null)
  },
}

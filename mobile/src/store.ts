/**
 * 全局状态：连接配置、认证、会话列表、导航。
 */

import { create } from 'zustand'
import * as api from './api/endpoints'
import { configureApi } from './api/http'
import { createUuid, storageGet, storageRemove, storageSet } from './storage'
import type { DeploymentProfile, Session } from './types'

export type Screen = 'connect' | 'webview' | 'login' | 'home' | 'chat'

const K = {
  baseUrl: 'lg.baseUrl',
  token: 'lg.token',
  workspaceId: 'lg.workspaceId',
  username: 'lg.username',
  displayName: 'lg.displayName',
  sessionId: 'lg.sessionId',
  expiresAt: 'lg.expiresAt',
  deviceId: 'lg.deviceId',
}

/** 把用户输入的地址归一化为 baseUrl（不含 /api/v1），'' 表示走同源（开发代理） */
export function normalizeBaseUrl(input: string): string {
  let value = input.trim()
  if (!value) return ''
  if (!/^https?:\/\//i.test(value)) value = `http://${value}`
  return value.replace(/\/+$/, '')
}

export function isNativePlatform(): boolean {
  return Boolean(
    typeof window !== 'undefined' &&
      (window as unknown as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor
        ?.isNativePlatform?.(),
  )
}

type ToastKind = 'info' | 'error' | 'success'

interface ToastState {
  toast: { kind: ToastKind; text: string } | null
  showToast: (kind: ToastKind, text: string) => void
  clearToast: () => void
}

export const useToastStore = create<ToastState>((set) => ({
  toast: null,
  showToast: (kind, text) => set({ toast: { kind, text } }),
  clearToast: () => set({ toast: null }),
}))

interface AppState {
  hydrated: boolean
  screen: Screen

  baseUrl: string
  profileInfo: DeploymentProfile | null
  connectionError: string | null

  token: string | null
  workspaceId: string | null
  username: string | null
  displayName: string | null
  sessionId: string | null
  expiresAt: string | null
  deviceId: string

  sessions: Session[]
  sessionsLoading: boolean
  sessionsError: string | null

  activeSessionId: string | null
  activeSession: Session | null

  hydrate: () => Promise<void>
  setConnection: (baseUrl: string) => Promise<{ ok: boolean; error?: string; info?: DeploymentProfile | null }>
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  loadSessions: () => Promise<void>
  createNewSession: () => Promise<Session | null>
  deleteSession: (sessionId: string) => Promise<boolean>
  openSession: (session: Session) => void
  clearActiveSession: () => void
  setActiveSessionMeta: (session: Session) => void
}

function applyAuthContext(s: {
  token: string | null
  workspaceId: string | null
  deviceId: string
}) {
  configureApi({
    getToken: () => s.token,
    getWorkspaceId: () => s.workspaceId,
    getDeviceId: () => s.deviceId,
    onUnauthorized: () => {
      useAppStore.getState().logout().catch(() => undefined)
    },
  })
}

async function persistAuth(state: Pick<AppState, 'token' | 'workspaceId' | 'username' | 'displayName' | 'sessionId' | 'expiresAt'>) {
  await storageSet(K.token, state.token ?? '')
  await storageSet(K.workspaceId, state.workspaceId ?? '')
  await storageSet(K.username, state.username ?? '')
  await storageSet(K.displayName, state.displayName ?? '')
  await storageSet(K.sessionId, state.sessionId ?? '')
  await storageSet(K.expiresAt, state.expiresAt ?? '')
}

export const useAppStore = create<AppState>((set, get) => ({
  hydrated: false,
  screen: 'connect',

  baseUrl: '',
  profileInfo: null,
  connectionError: null,

  token: null,
  workspaceId: null,
  username: null,
  displayName: null,
  sessionId: null,
  expiresAt: null,
  deviceId: '',

  sessions: [],
  sessionsLoading: false,
  sessionsError: null,

  activeSessionId: null,
  activeSession: null,

  async hydrate() {
    const [baseUrl, token, workspaceId, username, displayName, sessionId, expiresAt, deviceId] =
      await Promise.all([
        storageGet(K.baseUrl),
        storageGet(K.token),
        storageGet(K.workspaceId),
        storageGet(K.username),
        storageGet(K.displayName),
        storageGet(K.sessionId),
        storageGet(K.expiresAt),
        storageGet(K.deviceId),
      ])
    const devId = deviceId || createUuid()
    if (!deviceId) await storageSet(K.deviceId, devId)

    const next = {
      baseUrl: baseUrl ?? '',
      token: token || null,
      workspaceId: workspaceId || null,
      username: username || null,
      displayName: displayName || null,
      sessionId: sessionId || null,
      expiresAt: expiresAt || null,
      deviceId: devId,
    }
    if (next.baseUrl) configureApi({ baseUrl: next.baseUrl })
    applyAuthContext(next)
    set({
      ...next,
      hydrated: true,
      // 包裹器流程：有服务器地址 → 直接进入网页版（登录在网页版内完成）
      screen: next.baseUrl ? 'webview' : 'connect',
    })
    if (next.token && next.baseUrl) {
      void get().loadSessions()
    }
  },

  async setConnection(baseUrl) {
    const normalized = normalizeBaseUrl(baseUrl)
    set({ connectionError: null })
    try {
      if (!normalized) {
        // 浏览器开发态：留空走 Vite 代理
        configureApi({ baseUrl: '/api/v1' })
      } else {
        configureApi({ baseUrl: normalized })
      }
      const info = await api.deploymentProfile()
      set({ baseUrl: normalized, profileInfo: info })
      await storageSet(K.baseUrl, normalized)
      return { ok: true, info }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      set({ connectionError: message })
      return { ok: false, error: message }
    }
  },

  async login(username, password) {
    const response = await api.login(username, password)
    if (!response.default_workspace_id) {
      throw new Error('该账号没有可用的工作区')
    }
    const next = {
      token: response.access_token,
      workspaceId: response.default_workspace_id,
      username: response.username,
      displayName: response.display_name,
      sessionId: response.session_id,
      expiresAt: response.expires_at,
      deviceId: get().deviceId,
    }
    applyAuthContext(next)
    await persistAuth(next)
    set({ ...next, screen: 'home', connectionError: null })
    void get().loadSessions()
    if (response.must_change_password) {
      useToastStore.getState().showToast('info', '首次登录：建议尽快修改密码')
    }
  },

  async logout() {
    try {
      await api.logout()
    } catch {
      // 网络不可达时仍要本地登出
    }
    await Promise.all([
      storageRemove(K.token),
      storageRemove(K.workspaceId),
      storageRemove(K.username),
      storageRemove(K.displayName),
      storageRemove(K.sessionId),
      storageRemove(K.expiresAt),
    ])
    configureApi({ getToken: () => null, getWorkspaceId: () => null })
    set({
      token: null,
      workspaceId: null,
      username: null,
      displayName: null,
      sessionId: null,
      expiresAt: null,
      screen: get().baseUrl ? 'webview' : 'connect',
      sessions: [],
      activeSessionId: null,
      activeSession: null,
    })
  },

  async loadSessions() {
    const state = get()
    if (!state.token || !state.workspaceId) return
    set({ sessionsLoading: true, sessionsError: null })
    try {
      const sessions = await api.listSessions()
      set({ sessions, sessionsLoading: false })
      const active = state.activeSessionId
      if (active) {
        const found = sessions.find((s) => s.id === active)
        if (found) set({ activeSession: found })
      }
    } catch (error) {
      set({ sessionsLoading: false, sessionsError: error instanceof Error ? error.message : String(error) })
    }
  },

  async createNewSession() {
    const session = await api.createSession({})
    set((s) => ({ sessions: [session, ...s.sessions] }))
    return session
  },

  async deleteSession(sessionId) {
    try {
      const impact = await api.sessionDeleteImpact([sessionId])
      await api.sessionsBatchDelete([sessionId], impact.confirmation_text)
      set((s) => ({
        sessions: s.sessions.filter((x) => x.id !== sessionId),
        activeSessionId: s.activeSessionId === sessionId ? null : s.activeSessionId,
        activeSession: s.activeSessionId === sessionId ? null : s.activeSession,
      }))
      return true
    } catch (error) {
      useToastStore.getState().showToast(
        'error',
        error instanceof Error ? error.message : String(error),
      )
      return false
    }
  },

  openSession(session) {
    set({ screen: 'chat', activeSessionId: session.id, activeSession: session })
  },

  clearActiveSession() {
    set({ activeSessionId: null, activeSession: null, screen: 'home' })
  },

  setActiveSessionMeta(session) {
    set((s) => ({
      activeSession: session,
      sessions: s.sessions.map((x) => (x.id === session.id ? session : x)),
    }))
  },
}))

/** 相对时间展示 */
export function formatRelativeTime(iso: string): string {
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return ''
  const diff = Date.now() - time
  const minute = 60_000
  const hour = 3_600_000
  const day = 86_400_000
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < 2 * day) return '昨天'
  const d = new Date(time)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

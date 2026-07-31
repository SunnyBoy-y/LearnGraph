import { createContext, useContext } from 'react'

export type AuthContextValue = {
  authenticated: boolean
  username: string
  workspaceId: string
  workspaceName: string
  setWorkspaceId: (workspaceId: string) => Promise<void>
  login: (username: string, password: string) => Promise<{
    workspaceId: string
    mustChangePassword: boolean
  }>
  register: (payload: {
    username: string
    email?: string
    display_name: string
    password: string
  }) => Promise<{ workspaceId: string; mustChangePassword: boolean }>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  deleteAccount: (currentPassword: string, confirmation: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}

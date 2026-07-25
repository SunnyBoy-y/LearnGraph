import type {
  DemoLoginRequest,
  LoginResponse,
  RegisterRequest,
  Workspace,
} from '@/types/auth'

import { authStore } from './auth-store'
import { apiClient } from './client'

export type AccountDeletionImpact = {
  can_delete: boolean
  blockers: string[]
  active_session_count: number
  active_membership_count: number
  personal_workspace_count: number
  owned_organization_count: number
}

export async function login(payload: DemoLoginRequest): Promise<LoginResponse> {
  const response = await apiClient.post<LoginResponse, DemoLoginRequest>('/auth/login', payload, {
    auth: false,
    workspace: false,
  })
  authStore.setLoginResponse(response)
  return response
}

export async function register(payload: RegisterRequest): Promise<LoginResponse> {
  const response = await apiClient.post<LoginResponse, RegisterRequest>('/auth/register', payload, {
    auth: false,
    workspace: false,
  })
  authStore.setLoginResponse(response)
  return response
}

export function logout(): Promise<{ status: string }> {
  return apiClient.post('/auth/logout', undefined, { workspace: false })
}

export function changePassword(currentPassword: string, newPassword: string): Promise<{ status: string }> {
  return apiClient.post(
    '/auth/change-password',
    { current_password: currentPassword, new_password: newPassword },
    { workspace: false },
  )
}

export function getAccountDeletionImpact(): Promise<AccountDeletionImpact> {
  return apiClient.get<AccountDeletionImpact>('/auth/account/deletion-impact', {
    workspace: false,
  })
}

export function deleteAccount(
  currentPassword: string,
  confirmation: string,
): Promise<{ status: string }> {
  return apiClient.post(
    '/auth/delete-account',
    { current_password: currentPassword, confirmation },
    { workspace: false },
  )
}

export function listWorkspaces(): Promise<Workspace[]> {
  return apiClient.get<Workspace[]>('/workspaces', { workspace: false })
}

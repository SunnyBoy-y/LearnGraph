import type {
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  UserProfile,
  Workspace,
  WorkspaceSelectionResponse,
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

export async function login(payload: LoginRequest): Promise<LoginResponse> {
  const response = await apiClient.post<LoginResponse, LoginRequest>('/auth/login', payload, {
    auth: false,
    workspace: false,
  })
  authStore.setLoginResponse(response)
  return response
}

export async function demoLogin(): Promise<LoginResponse> {
  const response = await apiClient.post<LoginResponse>(
    '/auth/demo-login',
    {},
    { auth: false, workspace: false },
  )
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

export function updateProfile(displayName: string): Promise<UserProfile> {
  return apiClient.patch('/auth/profile', { display_name: displayName }, { workspace: false })
}

export function uploadAvatar(file: File): Promise<UserProfile> {
  const form = new FormData()
  form.append('file', file)
  return apiClient.upload('/auth/profile/avatar', form, { workspace: false })
}

export function generateDefaultAvatar(): Promise<UserProfile> {
  return apiClient.post('/auth/profile/avatar/default', undefined, { workspace: false })
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

export function selectWorkspace(workspaceId: string): Promise<WorkspaceSelectionResponse> {
  // Selecting a workspace changes the server-side session default. It must not
  // inherit X-Workspace-ID for the workspace that is about to be left.
  return apiClient.post<WorkspaceSelectionResponse>(`/workspaces/${workspaceId}/select`, undefined, {
    workspace: false,
  })
}

export function listWorkspaces(): Promise<Workspace[]> {
  return apiClient.get<Workspace[]>('/workspaces', { workspace: false })
}

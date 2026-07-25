import type { IsoDateTime } from './common'

export interface DemoLoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  email?: string
  display_name: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_at: IsoDateTime
  session_id: string
  user_id: string
  username: string
  display_name: string
  default_workspace_id: string | null
  must_change_password: boolean
  demo_only: boolean
}

export interface DemoLoginResponse extends LoginResponse {
  demo_only: boolean
}

export interface Workspace {
  id: string
  tenant_id: string
  owner_user_id: string
  organization_id: string | null
  workspace_kind: 'personal' | 'organization'
  name: string
  description: string
  created_at: IsoDateTime
}

export interface WorkspaceSelectionResponse {
  workspace: Workspace
  header_name: string
}

export interface AuthSession {
  accessToken: string
  workspaceId: string
  userId?: string
  username?: string
  displayName?: string
  sessionId?: string
}

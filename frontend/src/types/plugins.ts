export interface Plugin {
  id: string
  workspace_id: string
  plugin_key: string
  name: string
  version: string
  plugin_type: string
  status: string
  enabled: boolean
  permissions: string[]
  capabilities: string[]
}

export interface PluginToggleRequest {
  enabled: boolean
}


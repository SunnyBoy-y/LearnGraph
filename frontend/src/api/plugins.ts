import type { Plugin, PluginToggleRequest } from '@/types/plugins'

import { apiClient } from './client'

export function listPlugins(): Promise<Plugin[]> {
  return apiClient.get<Plugin[]>('/plugins')
}

export function togglePlugin(pluginId: string, payload: PluginToggleRequest): Promise<Plugin> {
  return apiClient.post<Plugin, PluginToggleRequest>(
    `/plugins/${encodeURIComponent(pluginId)}/toggle`,
    payload,
  )
}


import type { IsoDateTime } from './common'

export interface DashboardMetric {
  key: string
  label: string
  value: number | string
  status: string
}

export interface DashboardResponse {
  workspace_id: string
  metrics: DashboardMetric[]
  next_actions: Array<{ id: string; title: string; description: string; status: string; source: string; action_type: string; project_id: string | null; goal_id: string | null; graph_id: string | null; node_id: string | null; due_at: IsoDateTime | null; priority: number }>
  system_status: Record<string, string>
}

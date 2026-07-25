import type { IsoDateTime, UnknownRecord } from './common'

export interface Project { id: string; workspace_id: string; title: string; status: string; primary_goal_id: string | null; primary_graph_id: string | null; position: number; archived_at: IsoDateTime | null; created_at: IsoDateTime; updated_at: IsoDateTime }
export interface ProjectCreate { title: string; primary_goal_id?: string | null; primary_graph_id?: string | null }
export interface DeleteImpact { resource_type: string; resource_id: string; title: string; confirmation_text: string; impacts: Array<{ resource_type: string; count: number; action: string }> }
export interface SourceRecord { id: string; workspace_id: string; provider_id: string; source_url: string; final_url: string; title: string; content: string; content_hash: string; content_type: string; authorized_domain: string; cache_status: string; research_job_id: string | null; metadata_json: UnknownRecord; created_at: IsoDateTime }
export type SourceTargetType = 'project' | 'goal' | 'graph' | 'node'
export interface SourceLink { id: string; source_id: string; target_type: SourceTargetType; target_id: string; relation: string; created_at: IsoDateTime }
export interface ActionItem { id: string; title: string; description: string; status: string; source: string; action_type: string; project_id: string | null; goal_id: string | null; graph_id: string | null; node_id: string | null; roadmap_id: string | null; day_index: number; duration_minutes: number; due_at: IsoDateTime | null; priority: number; position: number; completed_at: IsoDateTime | null; metadata_json: UnknownRecord }
export interface CompositeDraft { id: string; target_message_id: string; source_version_ids: string[]; content: string; parts: UnknownRecord[]; status: string; confirmed_version_id: string | null; created_at: IsoDateTime; updated_at: IsoDateTime }
export interface Roadmap { id: string; goal_id: string; graph_id: string | null; graph_revision: number | null; title: string; version: number; status: string; rationale: string; planning_snapshot: UnknownRecord; published_at: IsoDateTime | null; items: ActionItem[]; created_at: IsoDateTime; updated_at: IsoDateTime }
export type RoadmapVersion = Omit<Roadmap, 'planning_snapshot' | 'items'>
export interface RoadmapItemRescheduleRequest { base_version: number; day_index: number; position: number; duration_minutes?: number; rationale: string }
export interface RoadmapRejectRequest { base_version: number; rationale: string }

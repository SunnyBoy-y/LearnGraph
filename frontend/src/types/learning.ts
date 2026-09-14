import type { IsoDateTime, UnknownRecord } from "./common";

export type EvidenceSourceType =
  "conversation" | "exercise" | "file" | "user_correction" | "artifact";

/**
 * @deprecated 旧版证据写入模型（POST /api/v1/evidence）。
 * 新证据链路统一使用 POST /api/v1/learning/evidence（事件溯源）；
 * 本类型仅保留给旧端点兼容期使用，前端不得再新增创建入口。
 */
export interface EvidenceCreateRequest {
  node_id: string;
  source_type: EvidenceSourceType;
  summary: string;
  confidence?: number;
  metadata?: UnknownRecord;
}

export interface EvidenceDecisionRequest {
  decision: "accepted" | "rejected";
  reason?: string;
}

export interface Evidence {
  id: string;
  workspace_id: string;
  node_id: string;
  source_type: string;
  summary: string;
  confidence: number;
  status: string;
  metadata_json: UnknownRecord;
  created_at: IsoDateTime;
}

export interface MasteryNode {
  node_id: string;
  label: string;
  mastery_stars: number;
  retrieval_state: string;
  evidence_state: string;
  attention_state: string;
  accepted_evidence_count: number;
  next_review_at?: IsoDateTime | null;
  exercise_attempt_count?: number;
  exercise_correct_count?: number;
}

export interface MasteryGoalOccurrence {
  goal_id: string;
  goal_title: string;
  graph_id: string;
  graph_title: string;
  graph_status: string;
}

export interface MasteryAlignment {
  node_id: string;
  label: string;
  external_concept_id: string | null;
  occurrences: MasteryGoalOccurrence[];
  explanation: string;
}

export interface CapabilityReport {
  workspace_id: string;
  generated_at: IsoDateTime;
  summary: {
    concept_count: number;
    accepted_evidence_count: number;
    mastered_concept_count: number;
    review_due_count: number;
    exercise_attempt_count?: number;
    exercise_correct_count?: number;
  };
  nodes: MasteryNode[];
}

export interface MasterySchedule {
  id: string;
  node_id: string;
  next_review_at: IsoDateTime | null;
  last_qualified_recall_at: IsoDateTime | null;
  pending_message_count: number;
  active_rule_version: string;
  updated_at: IsoDateTime;
}
export interface MasteryReviewJob {
  id: string;
  workspace_id: string;
  trigger: string;
  status: string;
  dedupe_key: string | null;
  attempt_count: number;
  started_at: IsoDateTime | null;
  completed_at: IsoDateTime | null;
  last_error: string;
  node_ids: string[];
  report: UnknownRecord;
  created_at: IsoDateTime;
}

export interface MasterySessionState {
  id: string;
  workspace_id: string;
  session_id: string;
  pending_message_count: number;
  pending_node_ids: string[];
  pending_node_counts: Record<string, number>;
  activity_version: number;
  processed_version: number;
  enqueued_version: number;
  last_message_id: string | null;
  last_activity_at: IsoDateTime;
  idle_due_at: IsoDateTime | null;
  last_processed_at: IsoDateTime | null;
  updated_at: IsoDateTime;
}

export interface MasterySchedulerTick {
  workspace_id: string;
  recovered_job_ids: string[];
  enqueued_job_ids: string[];
  completed_job_ids: string[];
  failed_job_ids: string[];
  threshold_session_ids: string[];
  idle_session_ids: string[];
  due_node_ids: string[];
}

export type ExerciseQuestionType =
  | "single_choice"
  | "multiple_choice"
  | "true_false"
  | "fill_blank"
  | "short_answer"
  | "mixed";

export type ExerciseDifficulty = "easy" | "medium" | "hard";

export interface ExerciseGenerateRequest {
  node_id: string;
  question_type?: ExerciseQuestionType;
  count?: number;
  difficulty?: ExerciseDifficulty;
  file_ids?: string[];
  collection_ids?: string[];
}

export interface ExerciseSourceRef {
  file_id?: string;
  chunk_id?: string;
  locator?: string;
  content_hash?: string;
  filename?: string;
}

export interface Exercise {
  id: string;
  workspace_id: string;
  node_id: string;
  question_type: string;
  prompt: string;
  options: string[];
  explanation: string;
  difficulty?: string;
  generation_batch_id?: string | null;
  source_refs?: ExerciseSourceRef[];
  created_at: IsoDateTime;
  attempt_count?: number;
  correct_count?: number;
  last_is_correct?: boolean | null;
}

export interface AnswerRequest {
  answer: string | string[];
}

export interface AnswerResult {
  answer_record_id: string;
  is_correct: boolean;
  feedback: string;
  evidence_signal_id: string;
  mastery_star_awarded?: boolean;
  /** 练习反馈面：部分正确评分与要点覆盖，来自后端真实评价结果。 */
  score_ratio?: number;
  covered_points?: string[];
  missing_points?: string[];
  error_type?: string | null;
  attempt_index?: number;
  hint_count?: number;
  node_id?: string | null;
  next_review_at?: IsoDateTime | null;
  schedule_reason?: string;
}

/**
 * 事件溯源学习状态（GET /api/v1/learning/nodes/{node_id}/state）。
 * 后端 LearningStateProjector 由证据投影出连续掌握分（0–1）、置信度与误区清单，
 * 取代旧版 MasteryNode 的 0–5 星标作为单一事实来源。
 */
export interface Misconception {
  evidence_id: string;
  summary: string;
}

export interface LearningNodeStateView {
  node_id: string;
  status: string;
  mastery_score: number;
  confidence: number;
  evidence_count: number;
  misconceptions: Misconception[];
  last_assessed_at: IsoDateTime | null;
  next_review_at: IsoDateTime | null;
  evidence_ids: string[];
  algorithm_version: string;
}

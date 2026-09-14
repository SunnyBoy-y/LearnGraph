import type { IsoDateTime } from "./common";

export type PracticeSessionMode =
  | "scheduled"
  | "custom"
  | "wrong_book"
  | "node"
  | "material";

export type PracticeSessionStatus = "planned" | "active" | "completed" | "abandoned";

export type PracticeItemState = "unanswered" | "correct" | "partial" | "incorrect";

export type PracticeWindow = "7d" | "30d" | "all";

export interface PracticePlanItem {
  node_id: string;
  label: string;
  graph_id?: string | null;
  reason_code: string;
  reason: string;
  detail: string;
  planned_questions: number;
  priority: number;
  available_exercises: number;
  due_in_days?: number | null;
  overdue_days?: number | null;
  last_practiced_at?: IsoDateTime | null;
  next_review_at?: IsoDateTime | null;
  retrieval_state: string;
  evidence_state: string;
  mastery_score?: number | null;
  confidence?: number | null;
  consecutive_wrong: number;
}

export interface PracticeTodayPlan {
  mode: "scheduled" | "maintenance" | "empty";
  message: string;
  question_count: number;
  estimated_minutes: number;
  node_count: number;
  node_ids: string[];
  question_types: string[];
  items: PracticePlanItem[];
  /** 缺少题库时能否现场出题（工作区有可用的远程结构化模型）。 */
  provider_available?: boolean;
  /** 计划被模型侧问题挡住：UI 给出直达「设置 → 功能模型」的出口。 */
  model_setting_required?: boolean;
  skipped_nodes: PracticePlanItem[];
}

export interface PracticeStats {
  pending_node_count: number;
  due_node_count: number;
  weak_node_count: number;
  attention_node_count: number;
  consolidated_node_count: number;
  estimated_minutes: number;
  answered_7d: number;
  answered_7d_previous: number;
  first_try_correct_7d: number;
  first_try_accuracy_7d?: number | null;
  first_try_accuracy_previous_7d?: number | null;
  first_try_accuracy_delta_7d?: number | null;
}

export interface PracticeTrendPoint {
  date: string;
  answered: number;
  sessions: number;
  minutes: number;
  first_try_accuracy?: number | null;
  final_accuracy?: number | null;
}

export interface PracticeSessionSummary {
  id: string;
  mode: string;
  status: string;
  title: string;
  started_at?: IsoDateTime | null;
  completed_at?: IsoDateTime | null;
  created_at: IsoDateTime;
  duration_seconds: number;
  planned_question_count: number;
  completed_question_count: number;
  total_attempt_count: number;
  first_try_correct_count: number;
  final_correct_count: number;
  first_try_accuracy?: number | null;
  final_accuracy?: number | null;
  node_count: number;
  node_labels: string[];
}

export interface PracticeSessionItem {
  position: number;
  exercise_id: string;
  node_id: string;
  node_label: string;
  graph_id?: string | null;
  question_type: string;
  prompt: string;
  options: string[];
  difficulty: string;
  explanation_available: boolean;
  source_refs: Array<Record<string, unknown>>;
  state: PracticeItemState;
  attempts: number;
  hint_count: number;
  hints: string[];
  last_answer?: string | null;
  last_feedback: string;
  score_ratio?: number | null;
  covered_points: string[];
  missing_points: string[];
  error_type?: string | null;
  answered_at?: IsoDateTime | null;
  duration_ms: number;
}

export interface PracticeSessionView {
  session: PracticeSessionSummary;
  items: PracticeSessionItem[];
  current_position: number;
  current_exercise_id?: string | null;
  remaining_count: number;
  report_available: boolean;
  plan?: PracticeTodayPlan | null;
  /** 组卷时遇到的问题（某个知识点出题失败等），必须展示，避免静默少题。 */
  warnings?: string[];
  /** 少题的原因在模型侧（换模型就能重试）：UI 给出直达设置的出口。 */
  model_setting_required?: boolean;
}

export interface PracticeSessionCreateRequest {
  mode: PracticeSessionMode;
  node_ids?: string[];
  question_type?: string;
  count?: number | null;
  difficulty?: "easy" | "medium" | "hard" | null;
  file_ids?: string[];
  collection_ids?: string[];
  generation_batch_id?: string | null;
}

export interface PracticeAnswerRequest {
  exercise_id: string;
  answer: string | string[];
  duration_ms?: number;
}

export interface PracticeAnswerResult {
  answer_record_id: string;
  exercise_id: string;
  node_id: string;
  node_label: string;
  graph_id?: string | null;
  is_correct: boolean;
  score_ratio: number;
  covered_points: string[];
  missing_points: string[];
  error_type?: string | null;
  feedback: string;
  explanation?: string | null;
  attempt_index: number;
  is_first_attempt: boolean;
  is_first_try_correct: boolean;
  hint_count: number;
  evidence_signal_id: string;
  mastery_star_awarded: boolean;
  next_review_at?: IsoDateTime | null;
  schedule_reason: string;
  retry_allowed: boolean;
  reveal_available: boolean;
  session_completed: boolean;
  remaining_count: number;
}

export interface PracticeHint {
  exercise_id: string;
  hint: string;
  hint_count: number;
  source: "generated" | "source_material" | "node_description";
}

export interface PracticeReveal {
  exercise_id: string;
  explanation: string;
  answer_display: string;
  covered_points: string[];
  missing_points: string[];
}

export interface PracticeReportNode {
  node_id: string;
  label: string;
  graph_id?: string | null;
  planned: number;
  first_try_correct: number;
  final_correct: number;
  attempts: number;
  requires_attention: boolean;
  reason: string;
  detail: string;
  next_review_at?: IsoDateTime | null;
  previous_next_review_at?: IsoDateTime | null;
  mastery_before?: number | null;
  mastery_after?: number | null;
  confidence_before?: number | null;
  confidence_after?: number | null;
  misconceptions: string[];
}

export interface PracticeSessionReport {
  session: PracticeSessionSummary;
  consolidated: PracticeReportNode[];
  attention: PracticeReportNode[];
  mastery_changes: Array<{
    node_id: string;
    node_label: string;
    graph_id?: string | null;
    mastery_before?: number | null;
    mastery_after?: number | null;
    stars_before?: number | null;
    stars_after?: number | null;
    changed: boolean;
  }>;
  misconceptions: Array<{
    node_id: string;
    node_label: string;
    graph_id?: string | null;
    summary: string;
    error_type?: string | null;
    exercise_id: string;
    count?: number;
  }>;
  next_review: {
    due_at?: string | null;
    node_count?: number;
    estimated_minutes?: number;
  };
  items: PracticeSessionItem[];
}

export interface WrongBookExercise {
  exercise_id: string;
  node_id: string;
  question_type: string;
  prompt: string;
  wrong_count: number;
  attempt_count: number;
  last_wrong_at?: IsoDateTime | null;
  last_feedback: string;
  error_type?: string | null;
}

export interface WrongBookNode {
  node_id: string;
  label: string;
  graph_id?: string | null;
  wrong_question_count: number;
  attempt_count: number;
  recent_results: boolean[];
  last_attempt_at?: IsoDateTime | null;
  repeated_patterns: string[];
  exercises: WrongBookExercise[];
}

export interface WrongBook {
  generated_at: IsoDateTime;
  total_wrong_questions: number;
  node_count: number;
  nodes: WrongBookNode[];
}

export interface PracticeNodePerformance {
  node_id: string;
  label: string;
  graph_id?: string | null;
  answered: number;
  first_try_accuracy?: number | null;
  final_accuracy?: number | null;
  recent_results: boolean[];
  status: string;
  status_label: string;
  misconceptions: string[];
  consecutive_wrong: number;
}

export interface PracticeDelayedRecall {
  available: boolean;
  reason: string;
  sample_size: number;
  recall_24h?: number | null;
  recall_7d?: number | null;
}

export interface PracticeLearningReport {
  window: string;
  window_start?: IsoDateTime | null;
  generated_at: IsoDateTime;
  answered: number;
  sessions: number;
  minutes: number;
  first_try_correct: number;
  final_correct: number;
  first_try_accuracy?: number | null;
  final_accuracy?: number | null;
  consolidated_node_count: number;
  attention_node_count: number;
  trend: PracticeTrendPoint[];
  nodes: PracticeNodePerformance[];
  delayed_recall: PracticeDelayedRecall;
  calendar: PracticeTrendPoint[];
}

export interface PracticeOverview {
  generated_at: IsoDateTime;
  stats: PracticeStats;
  today_plan: PracticeTodayPlan;
  focus_nodes: PracticePlanItem[];
  recent_session?: PracticeSessionSummary | null;
  active_session?: PracticeSessionSummary | null;
  trend: PracticeTrendPoint[];
}

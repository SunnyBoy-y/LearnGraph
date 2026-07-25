import type {
  AnswerRequest,
  AnswerResult,
  Exercise,
  ExerciseGenerateRequest,
} from "@/types/learning";

import { apiClient } from "./client";

export function listExercises(
  options: {
    wrongOnly?: boolean;
    nodeId?: string;
    questionType?: string;
    batchId?: string;
  } = {},
): Promise<Exercise[]> {
  const query: Record<string, string | boolean> = {};
  if (options.wrongOnly) query.wrong_only = true;
  if (options.nodeId) query.node_id = options.nodeId;
  if (options.questionType) query.question_type = options.questionType;
  if (options.batchId) query.batch_id = options.batchId;
  return apiClient.get<Exercise[]>("/exercises", {
    query: Object.keys(query).length ? query : undefined,
  });
}

export function generateExercises(
  payload: ExerciseGenerateRequest,
): Promise<Exercise[]> {
  return apiClient.post<Exercise[], ExerciseGenerateRequest>(
    "/exercises/generate",
    payload,
  );
}

export function answerExercise(
  exerciseId: string,
  payload: AnswerRequest,
): Promise<AnswerResult> {
  return apiClient.post<AnswerResult, AnswerRequest>(
    `/exercises/${encodeURIComponent(exerciseId)}/answer`,
    payload,
  );
}

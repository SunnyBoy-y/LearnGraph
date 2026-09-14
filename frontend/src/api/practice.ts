import type {
  PracticeAnswerRequest,
  PracticeAnswerResult,
  PracticeHint,
  PracticeLearningReport,
  PracticeOverview,
  PracticeReveal,
  PracticeSessionCreateRequest,
  PracticeSessionReport,
  PracticeSessionSummary,
  PracticeSessionView,
  PracticeWindow,
  WrongBook,
} from "@/types/practice";

import { apiClient } from "./client";

/** Minutes east of UTC for the current browser, so day buckets match the user. */
export function localTimezoneOffsetMinutes(): number {
  return -new Date().getTimezoneOffset();
}

export function getPracticeOverview(): Promise<PracticeOverview> {
  return apiClient.get<PracticeOverview>("/practice/overview", {
    query: { tz_offset_minutes: localTimezoneOffsetMinutes() },
  });
}

export function createPracticeSession(
  payload: PracticeSessionCreateRequest,
): Promise<PracticeSessionView> {
  return apiClient.post<PracticeSessionView, PracticeSessionCreateRequest>(
    "/practice/sessions",
    payload,
    { query: { tz_offset_minutes: localTimezoneOffsetMinutes() } },
  );
}

export function listPracticeSessions(options: {
  status?: string;
  limit?: number;
} = {}): Promise<PracticeSessionSummary[]> {
  return apiClient.get<PracticeSessionSummary[]>("/practice/sessions", {
    query: {
      status: options.status ?? "completed",
      limit: options.limit ?? 20,
    },
  });
}

export function getPracticeSession(
  sessionId: string,
): Promise<PracticeSessionView> {
  return apiClient.get<PracticeSessionView>(
    `/practice/sessions/${encodeURIComponent(sessionId)}`,
  );
}

export function answerPracticeQuestion(
  sessionId: string,
  payload: PracticeAnswerRequest,
): Promise<PracticeAnswerResult> {
  return apiClient.post<PracticeAnswerResult, PracticeAnswerRequest>(
    `/practice/sessions/${encodeURIComponent(sessionId)}/answer`,
    payload,
  );
}

export function requestPracticeHint(
  sessionId: string,
  exerciseId: string,
): Promise<PracticeHint> {
  return apiClient.post<PracticeHint, Record<string, never>>(
    `/practice/sessions/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(
      exerciseId,
    )}/hint`,
    {},
  );
}

export function revealPracticeItem(
  sessionId: string,
  exerciseId: string,
): Promise<PracticeReveal> {
  return apiClient.get<PracticeReveal>(
    `/practice/sessions/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(
      exerciseId,
    )}/review`,
  );
}

export function completePracticeSession(
  sessionId: string,
  abandon = false,
): Promise<PracticeSessionReport> {
  return apiClient.post<PracticeSessionReport, { abandon: boolean }>(
    `/practice/sessions/${encodeURIComponent(sessionId)}/complete`,
    { abandon },
  );
}

export function getPracticeSessionReport(
  sessionId: string,
): Promise<PracticeSessionReport> {
  return apiClient.get<PracticeSessionReport>(
    `/practice/sessions/${encodeURIComponent(sessionId)}/report`,
  );
}

export function getWrongBook(): Promise<WrongBook> {
  return apiClient.get<WrongBook>("/practice/wrong-book");
}

export function getPracticeLearningReport(
  window: PracticeWindow,
): Promise<PracticeLearningReport> {
  return apiClient.get<PracticeLearningReport>("/practice/report", {
    query: { window, tz_offset_minutes: localTimezoneOffsetMinutes() },
  });
}

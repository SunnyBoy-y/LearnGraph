/**
 * TJ-Sylva-style node metric helpers.
 *
 * LearnGraph stores local goal importance as `target_weight` (1–100).
 * TJ-Sylva uses discrete 1–3 metrics (importance / relevance / difficulty)
 * and folds importance+relevance into 1–3 recommend dots.
 * Card chrome also shows yellow ★ knowledge-importance stars (1–3)
 * from the same weight bands — distinct from green mastery stars (0–5).
 */

export type MetricLevel = 1 | 2 | 3;

export function clampMetric(value: unknown): MetricLevel {
  const number = Number(value);
  if (!Number.isFinite(number)) return 2;
  return Math.max(1, Math.min(3, Math.round(number))) as MetricLevel;
}

/** Map LearnGraph 1–100 weight → TJ-Sylva 1–3 importance. */
export function weightToImportance(weight: number | null | undefined): MetricLevel {
  const value = Number(weight);
  if (!Number.isFinite(value)) return 2;
  if (value >= 70) return 3;
  if (value >= 40) return 2;
  return 1;
}

/** Inverse: 1–3 importance → representative 1–100 weight for persistence. */
export function importanceToWeight(level: MetricLevel): number {
  if (level === 3) return 85;
  if (level === 1) return 25;
  return 50;
}

export function metricLabel(level: MetricLevel): string {
  return (["低", "中", "高"] as const)[level - 1];
}

export function recommendAdvice(score: MetricLevel): string {
  if (score === 3) return "强烈建议看";
  if (score === 2) return "可以看看";
  return "可以跳过";
}

/**
 * Fold importance (+ optional relevance) into 1–3 recommend dots.
 * Difficulty stays out of the score (TJ-Sylva design).
 */
export function recommendScore(
  importance: MetricLevel,
  relevance: MetricLevel = importance,
): MetricLevel {
  return clampMetric(Math.round((importance + relevance) / 2));
}

export function recommendFromWeight(
  weight: number | null | undefined,
  relevance?: MetricLevel,
): MetricLevel {
  const importance = weightToImportance(weight);
  return recommendScore(importance, relevance ?? importance);
}

/** Yellow ★ count shown on knowledge cards for importance (not mastery). */
export function importanceStars(weight: number | null | undefined): MetricLevel {
  return weightToImportance(weight);
}


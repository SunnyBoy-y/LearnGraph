export function StarRating({
  value,
  max = 5,
  tone = "mastery",
  label,
}: {
  value: number
  max?: number
  /** mastery = green growth stars; importance = yellow knowledge-importance stars */
  tone?: "mastery" | "importance"
  label?: string
}) {
  const safeMax = Math.max(1, Math.min(5, Math.round(max)))
  const filled = Math.max(0, Math.min(safeMax, Math.round(Number(value) || 0)))
  const aria =
    label ??
    (tone === "importance"
      ? `知识重要度 ${filled}/${safeMax}`
      : `成长星 ${filled}/${safeMax}`)
  return (
    <span
      aria-label={aria}
      className={tone === "importance" ? "stars stars--importance" : "stars"}
      data-tone={tone}
      title={aria}
    >
      {Array.from({ length: safeMax }, (_, index) =>
        index < filled ? "★" : "☆",
      ).join("")}
    </span>
  )
}

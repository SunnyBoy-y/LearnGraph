export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatDay(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const hours = `${date.getHours()}`.padStart(2, "0");
  const minutes = `${date.getMinutes()}`.padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日 ${hours}:${minutes}`;
}

export function formatMinutes(minutes: number | null | undefined): string {
  if (!minutes || minutes <= 0) return "—";
  return `${minutes} 分钟`;
}

export function dueLabel(value?: string | null): string {
  if (!value) return "尚未排期";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "尚未排期";
  const days = Math.round((date.getTime() - Date.now()) / 86_400_000);
  if (days < 0) return `已过期 ${Math.abs(days)} 天`;
  if (days === 0) return "今天到期";
  if (days === 1) return "明天到期";
  return `${days} 天后到期`;
}

/**
 * 判分错误类型 → 中文。必须与后端 `PracticeService._error_label` 一一对应，
 * 否则接口返回的内部码（如 wrong_judgement）会原样显示给用户。
 */
export function errorTypeLabel(errorType?: string | null): string {
  return (
    {
      incomplete_selection: "多选漏选",
      incorrect_selection: "多选错选",
      wrong_judgement: "判断错误",
      wrong_answer: "答案错误",
      missing_points: "要点遗漏",
      off_target: "偏离考点",
      no_attempt: "未作答",
      missing_answer_key: "缺少参考答案",
    }[errorType ?? ""] ?? "作答错误"
  );
}

// 图谱画布用的是同一套文案；接口里是内部码，不能直接展示。
const RETRIEVAL_STATE_LABELS: Record<string, string> = {
  unverified: "未学习",
  due: "待复习",
  due_soon: "即将复习",
  fresh: "掌握稳定",
  relearning: "重新学习",
};

const EVIDENCE_STATE_LABELS: Record<string, string> = {
  none: "暂无证据",
  single: "单条证据",
  multi: "多条证据",
  cross_time: "跨时段证据",
  robust: "证据充分",
  conflicted: "证据冲突",
  interest_only: "仅兴趣记录",
};

export function retrievalStateLabel(state?: string | null): string {
  return RETRIEVAL_STATE_LABELS[state ?? ""] ?? "未学习";
}

export function evidenceStateLabel(state?: string | null): string {
  return EVIDENCE_STATE_LABELS[state ?? ""] ?? "暂无证据";
}

/** 知识点 → 图谱节点深链：复用现有图谱页的 `?node=` 定位能力。 */
export function graphNodeHref(
  workspaceId: string,
  graphId?: string | null,
  nodeId?: string,
): string | null {
  if (!graphId || !nodeId) return null;
  return `/w/${workspaceId}/graphs/${graphId}?node=${encodeURIComponent(nodeId)}`;
}

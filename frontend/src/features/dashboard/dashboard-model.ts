import type { GraphSummary } from "@/types/graphs";
import type { PracticeSessionSummary } from "@/types/practice";
import type { ActionItem } from "@/types/workflow";
import type { SessionSummary } from "@/api/session-browser";

export const sessionScope = { roots_only: false, pinned_first: false } as const;
const resumeWindow = 14 * 24 * 60 * 60 * 1_000;

export function partitionPlan(actions: ActionItem[], today: Date) {
  const midnight = new Date(today);
  midnight.setHours(0, 0, 0, 0);
  const pending = actions.filter((item) => ["pending", "in_progress"].includes(item.status));
  const historical = pending.filter((item) => item.due_at && Date.parse(item.due_at) < +midnight);
  const historyIds = new Set(historical.map((item) => item.id));
  const current = pending.filter((item) => !historyIds.has(item.id)).sort((a, b) =>
    Number(b.status === "in_progress") - Number(a.status === "in_progress") ||
    b.priority - a.priority ||
    (a.due_at ? Date.parse(a.due_at) : Infinity) - (b.due_at ? Date.parse(b.due_at) : Infinity),
  );
  return { current, historical };
}

export function planPath(base: string, graphId: string) {
  return `${base}/graphs/${encodeURIComponent(graphId)}?panel=plan`;
}

export function shortDate(value: string, today: Date) {
  const date = new Date(value);
  if (!Number.isFinite(+date)) return "";
  if (date.toDateString() === today.toDateString()) return "今天";
  return new Intl.DateTimeFormat("zh-CN", {
    ...(date.getFullYear() !== today.getFullYear() && { year: "numeric" }),
    month: "numeric", day: "numeric",
  }).format(date);
}

type Recommendation = {
  tone: "study" | "practice" | "start";
  label: string;
  title: string;
  detail: string;
  context?: string;
  to: string;
  cta: string;
  graphTo?: string;
};

export function recommend({ base, now, graphs, sessions, practice, current }: {
  base: string;
  now: Date;
  graphs: GraphSummary[];
  sessions: SessionSummary[];
  practice: PracticeSessionSummary[];
  current: ActionItem[];
}): Recommendation {
  const graphById = new Map(graphs.filter((g) => g.status === "published").map((g) => [g.id, g]));
  const recent = (date?: string | null) => {
    const age = +now - Date.parse(date ?? "");
    return age >= 0 && age <= resumeWindow;
  };
  const session = sessions.find((item) => item.graph_id && graphById.has(item.graph_id) &&
    item.status !== "closed" && item.session_kind !== "side" && item.session_kind !== "agent" && recent(item.updated_at));
  const exercise = practice.find((item) => item.status === "active" && recent(item.started_at ?? item.created_at) &&
    item.completed_question_count < item.planned_question_count);

  if (exercise && (!session || Date.parse(exercise.started_at ?? exercise.created_at) >= Date.parse(session.updated_at))) {
    return {
      tone: "practice", label: "继续练习", title: exercise.title,
      detail: [exercise.node_labels.slice(0, 2).join(" · "), `已答 ${exercise.completed_question_count} / ${exercise.planned_question_count} 题`].filter(Boolean).join(" · "),
      to: `${base}/practice/session/${encodeURIComponent(exercise.id)}`, cta: "继续练习",
    };
  }
  if (session?.graph_id) {
    const graph = graphById.get(session.graph_id)!;
    return {
      tone: "study", label: "继续上次学习", title: graph.title, detail: session.title,
      context: `最近学习 · ${shortDate(session.updated_at, now)}`,
      to: `${base}/chat/${encodeURIComponent(session.id)}`, cta: "继续学习", graphTo: `${base}/graphs/${encodeURIComponent(graph.id)}`,
    };
  }
  const task = current.find((item) => item.graph_id && graphById.has(item.graph_id));
  const taskGraph = task?.graph_id ? graphById.get(task.graph_id) : undefined;
  if (task && taskGraph) {
    return {
      tone: "study", label: "计划中的下一步", title: task.title, detail: taskGraph.title,
      to: planPath(base, taskGraph.id), cta: "打开学习计划", graphTo: `${base}/graphs/${encodeURIComponent(taskGraph.id)}`,
    };
  }
  const graph = [...graphById.values()].sort((a, b) =>
    (Date.parse(b.last_studied_at ?? "") || 0) - (Date.parse(a.last_studied_at ?? "") || 0),
  )[0];
  if (graph) {
    return { tone: "study", label: "回到学习图谱", title: graph.title, detail: "从图谱选择一个知识点，接着学。", to: `${base}/graphs/${encodeURIComponent(graph.id)}`, cta: "打开学习图谱" };
  }
  if (graphs.length) {
    return { tone: "start", label: "学习图谱", title: "准备好你的学习路线", detail: "查看已有图谱，选择接下来想推进的目标。", to: `${base}/graphs`, cta: "查看学习图谱" };
  }
  return { tone: "start", label: "开始学习", title: "开启你的第一个学习目标", detail: "说说你想学什么，让学习助手帮你梳理知识路线。", to: `${base}/goals/new/clarify`, cta: "创建学习目标" };
}

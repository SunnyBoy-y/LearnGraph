/**
 * Graph node presentation layer.
 *
 * The backend can only express `root | concept | practice | assessment`
 * (see `backend/app/domain/schemas/goals.py`), and every field below is read
 * straight off `GraphNode` — this module never invents data and never talks to
 * the API. It only turns the existing fields into the learner-facing vocabulary
 * the Graph page renders (type icon + label, learning status, availability).
 *
 * Keeping the mapping in one place means the canvas, the legend, the filters
 * and the exported SVG all describe a node the same way.
 */
import {
  BookOpen,
  Box,
  Compass,
  FlaskConical,
  Lightbulb,
  Route,
  Target,
  Medal,
  type LucideIcon,
} from "lucide-react";

/** UI node-type vocabulary shown in the legend (superset of the backend enum). */
export type NodeTypeId =
  | "root"
  | "concept"
  | "principle"
  | "method"
  | "application"
  | "lab"
  | "assessment"
  | "open";

export type NodeTypeProfile = {
  id: NodeTypeId;
  label: string;
  icon: LucideIcon;
  /** CSS tone suffix: `knowledge-node__type--<tone>`. */
  tone: "root" | "concept" | "principle" | "method" | "application" | "lab" | "open";
  hint: string;
};

export const NODE_TYPE_PROFILES: Record<NodeTypeId, NodeTypeProfile> = {
  root: {
    id: "root",
    label: "目标",
    icon: Target,
    tone: "root",
    hint: "整张图谱的学习目标根节点",
  },
  concept: {
    id: "concept",
    label: "概念",
    icon: BookOpen,
    tone: "concept",
    hint: "需要理解与记忆的知识点",
  },
  principle: {
    id: "principle",
    label: "原理",
    icon: Compass,
    tone: "principle",
    hint: "解释“为什么”的机制与规律",
  },
  method: {
    id: "method",
    label: "方法",
    icon: Route,
    tone: "method",
    hint: "可以照着做的步骤、套路与技巧",
  },
  application: {
    id: "application",
    label: "应用",
    icon: Box,
    tone: "application",
    hint: "把知识用到真实场景里的能力点",
  },
  lab: {
    id: "lab",
    label: "交互实验",
    icon: FlaskConical,
    tone: "lab",
    hint: "通过动手操作理解知识",
  },
  assessment: { id: "assessment", label: "测评关卡", icon: Medal, tone: "lab", hint: "通过正式测评获得通关标记" },
  open: {
    id: "open",
    label: "开放性任务",
    icon: Lightbulb,
    tone: "open",
    hint: "没有唯一答案的开放任务",
  },
};

/** Legend / filter order (root first because it anchors the whole graph). */
export const NODE_TYPE_ORDER: NodeTypeId[] = [
  "root",
  "concept",
  "principle",
  "method",
  "application",
  "lab",
  "assessment",
  "open",
];

/**
 * Backend `node_type` (and forward-compatible aliases) → UI type.
 * Experiments and formal checkpoints have distinct purposes and entry labels.
 */
const NODE_TYPE_ALIASES: Record<string, NodeTypeId> = {
  root: "root",
  goal: "root",
  concept: "concept",
  knowledge: "concept",
  knowledge_point: "concept",
  point: "concept",
  principle: "principle",
  theory: "principle",
  mechanism: "principle",
  law: "principle",
  method: "method",
  procedure: "method",
  technique: "method",
  skill: "method",
  application: "application",
  apply: "application",
  case: "application",
  practice: "lab",
  exercise: "lab",
  assessment: "assessment",
  quiz: "assessment",
  test: "assessment",
  experiment: "lab",
  lab: "lab",
  open: "open",
  open_task: "open",
  open_ended: "open",
  challenge: "open",
  project: "open",
};

export function resolveNodeTypeProfile(
  nodeType: string | null | undefined,
  options: { root?: boolean } = {},
): NodeTypeProfile {
  const key = (nodeType ?? "").trim().toLowerCase();
  const resolved = NODE_TYPE_ALIASES[key];
  if (resolved) return NODE_TYPE_PROFILES[resolved];
  if (options.root) return NODE_TYPE_PROFILES.root;
  return NODE_TYPE_PROFILES.concept;
}

export function nodeTypeLabel(
  nodeType: string | null | undefined,
  options: { root?: boolean } = {},
) {
  return resolveNodeTypeProfile(nodeType, options).label;
}

/* ------------------------------------------------------------------ *
 * Learning status
 * ------------------------------------------------------------------ */

/**
 * Learner-facing states. `locked` is an *availability* state (missing
 * prerequisites) and is intentionally kept distinct from mastery, exactly like
 * the backend keeps `_prerequisite_satisfied` apart from `retrieval_state`.
 */
export type NodeLearningStatusId =
  | "unlearned"
  | "learning"
  | "mastered"
  | "due"
  | "locked";

export type NodeLearningStatus = {
  id: NodeLearningStatusId;
  label: string;
  tone: "neutral" | "progress" | "mastered" | "due" | "locked";
  /** Contextual CTA copy for this state. */
  ctaLabel: string;
};

export const NODE_STATUS_PROFILES: Record<NodeLearningStatusId, NodeLearningStatus> = {
  unlearned: { id: "unlearned", label: "未学习", tone: "neutral", ctaLabel: "学习此节点" },
  learning: { id: "learning", label: "学习中", tone: "progress", ctaLabel: "继续学习" },
  mastered: { id: "mastered", label: "已掌握", tone: "mastered", ctaLabel: "复习" },
  due: { id: "due", label: "待复习", tone: "due", ctaLabel: "开始复习" },
  locked: { id: "locked", label: "未解锁", tone: "locked", ctaLabel: "查看前置知识" },
};

export const NODE_STATUS_ORDER: NodeLearningStatusId[] = [
  "unlearned",
  "learning",
  "mastered",
  "due",
  "locked",
];

/** Mirrors `WorkflowService.PREREQUISITE_MIN_STARS` in the backend. */
export const PREREQUISITE_MIN_STARS = 1;

/**
 * Availability check, copied from the backend's prerequisite gate
 * (`backend/app/services/workflow.py::_prerequisite_satisfied`) so the graph
 * and the roadmap agree on what "前置未完成" means.
 */
export function isPrerequisiteSatisfied(node: {
  mastery_stars?: number | null;
  evidence_state?: string | null;
  retrieval_state?: string | null;
}): boolean {
  const stars = Math.max(0, Math.round(Number(node.mastery_stars) || 0));
  const evidence = (node.evidence_state ?? "").trim();
  return (
    stars >= PREREQUISITE_MIN_STARS &&
    evidence !== "none" &&
    evidence !== "interest_only" &&
    evidence !== "conflicted" &&
    node.retrieval_state !== "relearning"
  );
}

export type NodeStatusInput = {
  stars?: number | null;
  retrievalState?: string | null;
  attentionState?: string | null;
  /** True when this node has at least one unsatisfied prerequisite. */
  blockedByPrerequisite?: boolean;
};

export function resolveLearningStatus(
  input: NodeStatusInput,
): NodeLearningStatus {
  const stars = Math.max(0, Math.min(5, Math.round(Number(input.stars) || 0)));
  const retrieval = (input.retrievalState ?? "").trim();
  const attention = (input.attentionState ?? "").trim();

  if (retrieval === "due" || retrieval === "due_soon" || retrieval === "relearning") {
    return NODE_STATUS_PROFILES.due;
  }
  if (attention === "mastered" || stars >= 5) {
    return NODE_STATUS_PROFILES.mastered;
  }
  if (stars >= 1) return NODE_STATUS_PROFILES.learning;
  // Not started yet (0 stars): the only other distinct state is missing
  // prerequisites, which is availability rather than mastery.
  if (input.blockedByPrerequisite) return NODE_STATUS_PROFILES.locked;
  return NODE_STATUS_PROFILES.unlearned;
}

/** Weak metadata helper: depth → `L2` (never a “第 x / 5 步” path claim). */
export function graphLevelLabel(depth: number | null | undefined) {
  const value = Math.max(0, Math.round(Number(depth) || 0));
  return `L${value}`;
}

/**
 * 0–5 mastery ladder as words. Detail panels and the exported image may show
 * mastery; the canvas card deliberately does not (stars read as rating,
 * difficulty and progress at the same time).
 */
export const MASTERY_LEVEL_LABELS = [
  "未学习",
  "已学习",
  "初步理解",
  "能够应用",
  "熟练掌握",
  "掌握稳定",
] as const;

export function masteryLevelLabel(stars: number | null | undefined) {
  const level = Math.max(0, Math.min(5, Math.round(Number(stars) || 0)));
  return MASTERY_LEVEL_LABELS[level];
}

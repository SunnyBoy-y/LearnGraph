import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Pencil,
  RefreshCcw,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  clarifyGoal,
  confirmGoal,
  deleteGraphNode,
  generateCandidateGraph,
  getGraph,
  publishGoal,
  retryGraphNode,
  updateGraphNode,
} from "@/api";
import { ApiError } from "@/api/client";
import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import {
  OptionGroup,
  type OptionGroupSubmission,
} from "@/components/chat/option-group";
import { StatePill } from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Graph, GraphNode } from "@/types/graphs";
import type {
  ClarificationQuestion,
  Goal,
  GoalClarifyResponse,
  GoalConfirmRequest,
} from "@/types/goals";

/* oxlint-disable react/only-export-components -- this module exports the flow hook and its renderer as one contract. */

export type GoalSetupStage =
  | "capture"
  | "clarifying"
  | "goal_review"
  | "graph_building"
  | "graph_review"
  | "complete";

type GoalAnswer = {
  skipped: boolean;
  labels: string[];
  values: string[];
};

type GoalSetupOptions = {
  enabled: boolean;
  onPublished: (result: { goal: Goal; graph: Graph }) => Promise<void> | void;
  scopeKey: string;
};

function initialGoalDraft(goal: Goal): GoalConfirmRequest {
  return {
    title: goal.title,
    intent: goal.intent,
    time_limit: goal.time_limit,
    target_weight: goal.target_weight,
    deadline_at: goal.deadline_at,
    availability: goal.availability,
    preferences: goal.preferences,
    desired_outcome: goal.desired_outcome,
    constraints: goal.constraints,
    assumptions: goal.assumptions,
  };
}

function answerText(question: ClarificationQuestion, answer?: GoalAnswer) {
  if (!answer) return "";
  if (!answer.skipped) return answer.labels.join("、");
  return question.default_assumption || "按透明假设继续";
}

function goalDraftWithAnswers(
  result: GoalClarifyResponse,
  answers: Record<string, GoalAnswer>,
): GoalConfirmRequest {
  const base = initialGoalDraft(result.goal);
  const clarificationAnswers = result.questions.map((question) => {
    const answer = answers[question.key];
    return {
      key: question.key,
      question: question.prompt,
      answer: answerText(question, answer),
      skipped: answer?.skipped ?? true,
      graph_impact: question.graph_impact || "nodes",
    };
  });
  const fieldValues = new Map(
    clarificationAnswers
      .filter((item) => !item.skipped && item.answer)
      .map((item) => [item.key, item.answer]),
  );
  const skippedAssumptions = result.questions.flatMap((question) => {
    const answer = answers[question.key];
    if (!answer?.skipped) return [];
    return [
      {
        source: "goal_clarification",
        field: question.key,
        assumption:
          question.default_assumption || `用户跳过了「${question.prompt}」`,
      },
    ];
  });
  return {
    ...base,
    intent: fieldValues.get("intent") || base.intent,
    time_limit: fieldValues.get("time_limit") || base.time_limit,
    desired_outcome:
      fieldValues.get("desired_outcome") || base.desired_outcome,
    constraints: {
      ...base.constraints,
      clarification_answers: clarificationAnswers,
    },
    assumptions: [...(base.assumptions ?? []), ...skippedAssumptions],
  };
}

export function useGoalSetupFlow({
  enabled,
  onPublished,
  scopeKey,
}: GoalSetupOptions) {
  const queryClient = useQueryClient();
  const wasEnabled = useRef(enabled);
  const activeScopeKey = useRef(enabled ? scopeKey : "");
  const observedGraphRevision = useRef<number | undefined>(undefined);
  const [stage, setStage] = useState<GoalSetupStage>("capture");
  const [submittedPrompt, setSubmittedPrompt] = useState("");
  const [result, setResult] = useState<GoalClarifyResponse>();
  const [answers, setAnswers] = useState<Record<string, GoalAnswer>>({});
  const [questionIndex, setQuestionIndex] = useState(0);
  const [draft, setDraft] = useState<GoalConfirmRequest>();
  const [graphId, setGraphId] = useState("");
  const [acceptedNodeIds, setAcceptedNodeIds] = useState<Set<string>>(
    () => new Set(),
  );

  const clarify = useMutation({
    mutationFn: ({
      content,
      fileIds,
    }: {
      content: string;
      fileIds: string[];
    }) =>
      clarifyGoal({
        prompt: content,
        file_ids: fileIds,
        graph_context_ids: [],
      }),
  });
  const graph = useQuery({
    queryKey: ["graph", scopeKey, graphId],
    queryFn: () => getGraph(graphId),
    enabled: Boolean(graphId),
  });

  function currentGraphRevision() {
    const revision = graph.data?.revision;
    if (!revision) throw new Error("候选图谱 Revision 尚未载入，请稍后重试。");
    return revision;
  }

  function refreshGraphAfterConflict(error: Error) {
    if (
      error instanceof ApiError &&
      error.status === 409 &&
      error.code === "graph_revision_conflict"
    ) {
      setAcceptedNodeIds(new Set());
      toast.error("候选图谱已被更新，请重新审核后再发布。");
      void graph.refetch();
    }
  }
  const prepareGraph = useMutation({
    mutationFn: async () => {
      if (!result || !draft) throw new Error("Goal 草稿尚未准备完成。");
      const savedGoal = await confirmGoal(result.goal.id, draft);
      const graphSummary = await generateCandidateGraph(result.goal.id);
      return { graphSummary, savedGoal };
    },
    onSuccess: async ({ graphSummary, savedGoal }) => {
      setResult((current) =>
        current ? { ...current, goal: savedGoal } : current,
      );
      setGraphId(graphSummary.id);
      setAcceptedNodeIds(new Set());
      setStage("graph_review");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["goals"] }),
        queryClient.invalidateQueries({ queryKey: ["graphs"] }),
        queryClient.invalidateQueries({
          queryKey: ["graph", scopeKey, graphSummary.id],
        }),
      ]);
    },
    onError: () => setStage("goal_review"),
  });
  const updateNode = useMutation({
    mutationFn: ({ nodeId, body }: { nodeId: string; body: Partial<GraphNode> }) =>
      updateGraphNode(graphId, nodeId, {
        expected_revision: currentGraphRevision(),
        label: body.label,
        description: body.description,
        target_weight: body.target_weight,
      }),
    onSuccess: async (node) => {
      setAcceptedNodeIds((current) => {
        const next = new Set(current);
        next.delete(node.id);
        return next;
      });
      await queryClient.invalidateQueries({
        queryKey: ["graph", scopeKey, graphId],
      });
    },
    onError: refreshGraphAfterConflict,
  });
  const retryNode = useMutation({
    mutationFn: ({
      nodeId,
      instruction,
    }: {
      nodeId: string;
      instruction: string;
    }) =>
      retryGraphNode(
        graphId,
        nodeId,
        currentGraphRevision(),
        instruction.trim(),
      ),
    onSuccess: async (node) => {
      setAcceptedNodeIds((current) => {
        const next = new Set(current);
        next.delete(node.id);
        return next;
      });
      await queryClient.invalidateQueries({
        queryKey: ["graph", scopeKey, graphId],
      });
    },
    onError: refreshGraphAfterConflict,
  });
  const removeNode = useMutation({
    mutationFn: (nodeId: string) =>
      deleteGraphNode(graphId, nodeId, currentGraphRevision()),
    onSuccess: async (result) => {
      setAcceptedNodeIds((current) => {
        const next = new Set(current);
        next.delete(result.resource_id);
        return next;
      });
      await queryClient.invalidateQueries({
        queryKey: ["graph", scopeKey, graphId],
      });
    },
    onError: refreshGraphAfterConflict,
  });
  const publish = useMutation({
    mutationFn: async () => {
      if (!result || !graphId) throw new Error("候选图谱尚未准备完成。");
      const response = await publishGoal(result.goal.id, {
        graph_id: graphId,
        expected_revision: currentGraphRevision(),
      });
      const publishedGraph = await getGraph(response.graph_id);
      if (publishedGraph.status !== "published")
        throw new Error("服务端没有返回已发布图谱，请刷新后重试。");
      return { goal: response.goal, graph: publishedGraph };
    },
    onSuccess: async (published) => {
      setStage("complete");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["goals"] }),
        queryClient.invalidateQueries({ queryKey: ["graphs"] }),
        queryClient.invalidateQueries({
          queryKey: ["graph", scopeKey, graphId],
        }),
      ]);
      await onPublished(published);
    },
    onError: refreshGraphAfterConflict,
  });
  const resetClarify = clarify.reset;
  const resetPrepareGraph = prepareGraph.reset;
  const resetUpdateNode = updateNode.reset;
  const resetRetryNode = retryNode.reset;
  const resetRemoveNode = removeNode.reset;
  const resetPublish = publish.reset;

  useEffect(() => {
    const scopeChanged = activeScopeKey.current !== scopeKey;
    if (enabled && (!wasEnabled.current || scopeChanged)) {
      setStage("capture");
      setSubmittedPrompt("");
      setResult(undefined);
      setAnswers({});
      setQuestionIndex(0);
      setDraft(undefined);
      setGraphId("");
      setAcceptedNodeIds(new Set());
      observedGraphRevision.current = undefined;
      resetClarify();
      resetPrepareGraph();
      resetUpdateNode();
      resetRetryNode();
      resetRemoveNode();
      resetPublish();
    }
    if (enabled) activeScopeKey.current = scopeKey;
    wasEnabled.current = enabled;
  }, [
    enabled,
    resetClarify,
    resetPrepareGraph,
    resetPublish,
    resetRemoveNode,
    resetRetryNode,
    resetUpdateNode,
    scopeKey,
  ]);

  useEffect(() => {
    const revision = graph.data?.revision;
    if (!revision) return;
    if (
      observedGraphRevision.current !== undefined &&
      observedGraphRevision.current !== revision
    ) {
      setAcceptedNodeIds(new Set());
    }
    observedGraphRevision.current = revision;
  }, [graph.data?.revision]);

  useEffect(() => {
    if (!enabled) return;
    const currentGraph = graph.data;
    window.dispatchEvent(
      new CustomEvent("learngraph:goal-graph-preview", {
        detail: {
          submittedPrompt,
          title: result?.goal.title,
          answers: result?.questions
            .map((question) => answerText(question, answers[question.key]))
            .filter(Boolean),
          questionCount: result?.questions.length ?? 0,
          phase:
            stage === "capture"
              ? "draft"
              : clarify.isPending || stage === "graph_building"
                ? "building"
                : stage === "graph_review"
                  ? "reviewing"
                  : stage === "complete"
                    ? "approved"
                    : "clarifying",
          graphNodes: currentGraph?.nodes ?? [],
          graphEdges: currentGraph?.edges ?? [],
        },
      }),
    );
  }, [
    answers,
    clarify.isPending,
    enabled,
    graph.data,
    result,
    stage,
    submittedPrompt,
  ]);

  const allNodesAccepted = Boolean(graph.data?.nodes.length);
  const error =
    clarify.error ??
    prepareGraph.error ??
    graph.error ??
    updateNode.error ??
    retryNode.error ??
    removeNode.error ??
    publish.error;
  const busy =
    clarify.isPending ||
    prepareGraph.isPending ||
    updateNode.isPending ||
    retryNode.isPending ||
    removeNode.isPending ||
    publish.isPending;

  async function submit(content: string, fileIds: string[]) {
    if (stage !== "capture") return;
    const normalized = content.trim();
    if (normalized.length < 3) return;
    setSubmittedPrompt(normalized);
    setAnswers({});
    setQuestionIndex(0);
    const response = await clarify.mutateAsync({ content: normalized, fileIds });
    setResult(response);
    setDraft(initialGoalDraft(response.goal));
    setStage(response.questions.length ? "clarifying" : "goal_review");
    await queryClient.invalidateQueries({ queryKey: ["goals"] });
  }

  function recordAnswer(
    question: ClarificationQuestion,
    submission: OptionGroupSubmission,
  ) {
    const skipped = submission.values.length === 0;
    setAnswers((current) => ({
      ...current,
      [question.key]: {
        skipped,
        labels: skipped ? [] : submission.labels,
        values: skipped ? [] : submission.values,
      },
    }));
    if (questionIndex < (result?.questions.length ?? 1) - 1)
      setQuestionIndex((current) => current + 1);
  }

  function reviewGoal() {
    if (!result) return;
    setDraft(goalDraftWithAnswers(result, answers));
    setStage("goal_review");
  }

  function confirmAndGenerateGraph() {
    if (!result || !draft || prepareGraph.isPending) return;
    setStage("graph_building");
    prepareGraph.mutate();
  }

  function returnToQuestionnaire() {
    setStage("clarifying");
  }

  return {
    acceptedNodeIds,
    allNodesAccepted,
    answers,
    busy,
    confirmAndGenerateGraph,
    draft,
    error,
    graph: graph.data,
    graphLoading: graph.isPending && Boolean(graphId),
    graphRefetch: graph.refetch,
    clarifyPending: clarify.isPending,
    prepareGraphPending: prepareGraph.isPending,
    publish: () => publish.mutate(),
    publishPending: publish.isPending,
    questionIndex,
    recordAnswer,
    removeNode: (nodeId: string) => removeNode.mutate(nodeId),
    retryNode: (nodeId: string, instruction: string) =>
      retryNode.mutate({ nodeId, instruction }),
    result,
    returnToQuestionnaire,
    reviewGoal,
    setAcceptedNodeIds,
    setDraft,
    setQuestionIndex,
    stage,
    submit,
    submittedPrompt,
    updateNode: (nodeId: string, body: Partial<GraphNode>) =>
      updateNode.mutate({ nodeId, body }),
  };
}

export type GoalSetupController = ReturnType<typeof useGoalSetupFlow>;

function GoalQuestionnaire({ flow }: { flow: GoalSetupController }) {
  const questions = flow.result?.questions ?? [];
  const question = questions[flow.questionIndex];
  const sectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    sectionRef.current?.focus();
  }, [flow.questionIndex]);
  if (!question) return null;
  const completedCount = questions.filter((item) => flow.answers[item.key]).length;
  const currentAnswer = flow.answers[question.key];
  const remaining = questions.length - completedCount;
  return (
    <section
      aria-label="目标澄清问卷"
      className="goal-flow-questionnaire goal-quiz"
      ref={sectionRef}
      tabIndex={-1}
    >
      <div className="goal-quiz-head">
        <div>
          <strong>
            {flow.questionIndex + 1} / {questions.length}
          </strong>
          <span className="goal-quiz-sub">
            回答会影响图谱边界与顺序；也可跳过，系统会记下透明假设。
          </span>
        </div>
        <StatePill
          label={flow.result?.remote_model_used ? "远程模型" : flow.result?.provider}
          status={flow.result?.provider ?? "loading"}
        />
      </div>
      {completedCount > 0 ? (
        <div className="goal-quiz-stash" aria-label="已答进度">
          <span className="goal-quiz-stash-tag">已答 {completedCount} 题</span>
          <span className="goal-quiz-stash-text">
            {questions
              .filter((item) => flow.answers[item.key])
              .map((item) => answerText(item, flow.answers[item.key]).slice(0, 28))
              .join(" · ")}
          </span>
        </div>
      ) : null}
      <div className="goal-quiz-card background-question">
        {question.reason ? (
          <span className="background-question-hint">{question.reason}</span>
        ) : null}
        <OptionGroup
          allowCustom={question.allow_custom ?? true}
          allowSkip={question.allow_skip ?? true}
          description={undefined}
          key={question.key}
          mode={question.input_type === "multiple_choice" ? "multiple" : "single"}
          onSubmit={(submission) => flow.recordAnswer(question, submission)}
          options={question.options.map((option) => ({ id: option, label: option }))}
          submitLabel={
            flow.questionIndex < questions.length - 1
              ? remaining > 1
                ? `确认 · 还差 ${remaining - (currentAnswer ? 0 : 1)} 题`
                : "确认并继续"
              : "确认本题"
          }
          title={question.prompt}
          value={
            currentAnswer && !currentAnswer.skipped
              ? currentAnswer.values
              : undefined
          }
        />
      </div>
      <div className="goal-flow-questionnaire__footer goal-quiz-actions">
        <Button
          aria-label="上一个澄清问题"
          disabled={flow.questionIndex === 0}
          onClick={() => flow.setQuestionIndex(flow.questionIndex - 1)}
          size="icon-sm"
          variant="ghost"
        >
          <ChevronLeft className="size-4" />
        </Button>
        <span>
          {completedCount}/{questions.length} 已处理
        </span>
        <Button
          aria-label="下一个澄清问题"
          disabled={flow.questionIndex === questions.length - 1}
          onClick={() => flow.setQuestionIndex(flow.questionIndex + 1)}
          size="icon-sm"
          variant="ghost"
        >
          <ChevronRight className="size-4" />
        </Button>
        <Button
          disabled={completedCount !== questions.length}
          onClick={flow.reviewGoal}
          size="sm"
        >
          {completedCount === questions.length
            ? "查看目标摘要"
            : `还差 ${questions.length - completedCount} 题`}
          <ArrowRight className="size-4" />
        </Button>
      </div>
    </section>
  );
}

function GoalSummaryReview({ flow }: { flow: GoalSetupController }) {
  if (!flow.draft || !flow.result) return null;
  const answers = flow.result.questions.map((question) => ({
    key: question.key,
    prompt: question.prompt,
    value: answerText(question, flow.answers[question.key]),
  }));
  return (
    <section className="goal-flow-review topic-preview-panel" aria-label="Goal 摘要审核">
      <div className="topic-preview-head">
        <div>
          <strong>确认学习目标</strong>
          <span className="topic-preview-sub">
            看一下、改一下措辞，再生成候选图谱。跳过的问题会变成透明假设。
          </span>
        </div>
        <StatePill status="review" />
      </div>
      <div className="goal-flow-review__form">
        <label>
          <span>目标名称</span>
          <Input
            aria-label="目标名称"
            onChange={(event) =>
              flow.setDraft((current) =>
                current ? { ...current, title: event.target.value } : current,
              )
            }
            value={flow.draft.title}
          />
        </label>
        <label>
          <span>学习意图</span>
          <Input
            aria-label="学习意图"
            onChange={(event) =>
              flow.setDraft((current) =>
                current ? { ...current, intent: event.target.value } : current,
              )
            }
            value={flow.draft.intent}
          />
        </label>
        <label className="goal-flow-review__wide">
          <span>期望结果</span>
          <Textarea
            aria-label="期望结果"
            onChange={(event) =>
              flow.setDraft((current) =>
                current
                  ? { ...current, desired_outcome: event.target.value }
                  : current,
              )
            }
            value={flow.draft.desired_outcome}
          />
        </label>
        <label>
          <span>时间约束</span>
          <Input
            aria-label="时间约束"
            onChange={(event) =>
              flow.setDraft((current) =>
                current ? { ...current, time_limit: event.target.value } : current,
              )
            }
            value={flow.draft.time_limit}
          />
        </label>
      </div>
      <div className="topic-preview-list" aria-label="问卷答案摘要">
        {answers.map((answer) => (
          <article className="topic-preview-card" key={answer.key}>
            <div className="topic-preview-card-body">
              <strong>{answer.prompt}</strong>
              <span>{answer.value || "未提供"}</span>
            </div>
          </article>
        ))}
      </div>
      <div className="topic-preview-actions goal-flow-review__actions">
        <Button onClick={flow.returnToQuestionnaire} size="sm" variant="ghost">
          <ChevronLeft className="size-4" />
          返回问卷
        </Button>
        <Button
          disabled={!flow.draft.title.trim() || flow.prepareGraphPending}
          onClick={flow.confirmAndGenerateGraph}
          size="sm"
        >
          {flow.prepareGraphPending ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <ArrowRight className="size-4" />
          )}
          确认生成
        </Button>
      </div>
    </section>
  );
}

function GoalGraphNodeReview({
  busy,
  deletable,
  node,
  onDelete,
  onRetry,
  onSave,
}: {
  busy: boolean;
  deletable: boolean;
  node: GraphNode;
  onDelete: () => void;
  onRetry: (instruction: string) => void;
  onSave: (body: Partial<GraphNode>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [retryInstruction, setRetryInstruction] = useState("");
  const [draft, setDraft] = useState({
    description: node.description,
    label: node.label,
    target_weight: node.target_weight,
  });
  useEffect(() => {
    if (!editing)
      setDraft({
        description: node.description,
        label: node.label,
        target_weight: node.target_weight,
      });
  }, [editing, node]);
  return (
    <article className="goal-flow-node topic-preview-card is-accepted">
      <div className="goal-flow-node__head">
        <Badge variant="secondary">{node.node_type}</Badge>
        <span>权重 {draft.target_weight}</span>
      </div>
      {editing ? (
        <div className="goal-flow-node__editor">
          <Input
            aria-label={`${node.label} 节点名称`}
            onChange={(event) =>
              setDraft((current) => ({ ...current, label: event.target.value }))
            }
            value={draft.label}
          />
          <Textarea
            aria-label={`${node.label} 节点说明`}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                description: event.target.value,
              }))
            }
            value={draft.description}
          />
          <Input
            aria-label={`${node.label} 目标权重`}
            max={100}
            min={1}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                target_weight: Number(event.target.value),
              }))
            }
            type="number"
            value={draft.target_weight}
          />
        </div>
      ) : (
        <div className="goal-flow-node__content topic-preview-card-body">
          <h3>{node.label}</h3>
          <p>{node.description}</p>
        </div>
      )}
      {retrying ? (
        <div className="goal-flow-node__editor">
          <Textarea
            aria-label={`对 ${node.label} 的修改建议`}
            onChange={(event) => setRetryInstruction(event.target.value)}
            placeholder="说明希望如何改写这个知识点，例如强调边界、补充前置或更正定义…"
            value={retryInstruction}
          />
          <div className="goal-flow-node__actions">
            <Button
              onClick={() => {
                setRetrying(false);
                setRetryInstruction("");
              }}
              size="sm"
              variant="ghost"
            >
              取消
            </Button>
            <Button
              disabled={busy || retryInstruction.trim().length < 2}
              onClick={() => {
                onRetry(retryInstruction.trim());
                setRetrying(false);
                setRetryInstruction("");
              }}
              size="sm"
            >
              <RefreshCcw className="size-3.5" />
              提交重试
            </Button>
          </div>
        </div>
      ) : null}
      <div className="goal-flow-node__actions">
        {editing ? (
          <>
            <Button
              aria-label={`取消编辑 ${node.label}`}
              onClick={() => setEditing(false)}
              size="icon-sm"
              variant="ghost"
            >
              <X className="size-3.5" />
            </Button>
            <Button
              disabled={
                busy ||
                !draft.label.trim() ||
                draft.target_weight < 1 ||
                draft.target_weight > 100
              }
              onClick={() => {
                onSave(draft);
                setEditing(false);
              }}
              size="sm"
            >
              <Save className="size-3.5" />
              保存
            </Button>
          </>
        ) : (
          <>
            <Button
              aria-label={`编辑 ${node.label}`}
              disabled={busy || retrying}
              onClick={() => setEditing(true)}
              size="icon-sm"
              title="编辑节点"
              variant="ghost"
            >
              <Pencil className="size-3.5" />
            </Button>
            <Button
              aria-label={`重新生成 ${node.label}`}
              disabled={busy}
              onClick={() => setRetrying(true)}
              size="icon-sm"
              title="局部重试（需输入建议）"
              variant="ghost"
            >
              <RefreshCcw className="size-3.5" />
            </Button>
            <Button
              aria-label={`删除 ${node.label}`}
              disabled={busy || !deletable}
              onClick={onDelete}
              size="icon-sm"
              title={deletable ? "删除候选节点" : "根节点不能删除"}
              variant="ghost"
            >
              <Trash2 className="size-3.5" />
            </Button>
          </>
        )}
      </div>
    </article>
  );
}

function GoalGraphReview({ flow }: { flow: GoalSetupController }) {
  if (flow.graphLoading)
    return (
      <div className="goal-flow-thinking" role="status">
        <LoaderCircle className="size-4 animate-spin" />
        正在读取候选图谱…
      </div>
    );
  if (!flow.graph) return null;
  return (
    <section className="goal-flow-graph-review topic-preview-panel" aria-label="候选图谱审核">
      <div className="topic-preview-head">
        <div>
          <strong>审核初始图谱</strong>
          <span className="topic-preview-sub">
            未修改的节点视为已接受。需要改名/改权重时直接点卡片上的编辑；局部重试请先输入建议。
          </span>
        </div>
        <div className="goal-graph-review-tools">
          <StatePill status={flow.graph.status} />
        </div>
      </div>
      <p className="goal-flow-graph-review__summary">
        {flow.graph.nodes.length} 个节点 · {flow.graph.edges.length} 条关系 · 默认全部接受
      </p>
      <div className="goal-flow-node-grid topic-preview-list">
        {flow.graph.nodes.map((node) => (
          <GoalGraphNodeReview
            busy={flow.busy}
            deletable={node.node_type !== "root"}
            key={node.id}
            node={node}
            onDelete={() => flow.removeNode(node.id)}
            onRetry={(instruction) => flow.retryNode(node.id, instruction)}
            onSave={(body) => flow.updateNode(node.id, body)}
          />
        ))}
      </div>
      <div className="goal-flow-edge-review" aria-label="候选图谱关系">
        <div className="goal-flow-edge-review__head">
          <strong>节点关系</strong>
          <span>{flow.graph.edges.length} 条</span>
        </div>
        {flow.graph.edges.length ? (
          <ul>
            {flow.graph.edges.map((edge) => {
              const source = flow.graph?.nodes.find(
                (node) => node.id === edge.source_node_id,
              );
              const target = flow.graph?.nodes.find(
                (node) => node.id === edge.target_node_id,
              );
              return (
                <li key={edge.id}>
                  <span>{source?.label ?? "未知节点"}</span>
                  <ArrowRight aria-hidden="true" className="size-3.5" />
                  <span>{target?.label ?? "未知节点"}</span>
                  <Badge variant="outline">{edge.relation}</Badge>
                </li>
              );
            })}
          </ul>
        ) : (
          <p>当前候选图谱没有节点关系，发布前请确认这符合目标结构。</p>
        )}
      </div>
      <div className="topic-preview-actions goal-flow-review__actions">
        <Button
          disabled={!flow.allNodesAccepted || flow.publishPending}
          onClick={flow.publish}
          size="sm"
        >
          {flow.publishPending ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <ArrowRight className="size-4" />
          )}
          确认生成 ({flow.graph.nodes.length})
        </Button>
      </div>
    </section>
  );
}

export function GoalSetupConversation({
  flow,
  hasConversationMessages,
}: {
  flow: GoalSetupController;
  hasConversationMessages: boolean;
}) {
  const showPrompt = flow.stage === "capture" && !flow.submittedPrompt;
  return (
    <>
      {showPrompt ? (
        hasConversationMessages ? (
          <section className="goal-flow-assistant goal-flow-assistant--prompt">
            <p>参考完资料后，告诉我你想达到的学习结果。</p>
          </section>
        ) : (
          <ConversationEmptyState className="chat-empty-state goal-mode-empty">
            <div className="chat-empty-state__content">
              <p className="chat-empty-state__eyebrow">目标设定</p>
              <h2>你想学什么？</h2>
              <p>可以从结果、时间或已有基础说起。</p>
            </div>
          </ConversationEmptyState>
        )
      ) : null}
      {flow.submittedPrompt ? (
        <div className="goal-flow-user">
          <p>{flow.submittedPrompt}</p>
        </div>
      ) : null}
      {flow.submittedPrompt ? (
        <section className="goal-flow-assistant">
          {flow.result ? (
            <p>
              我先将学习意向整理为「{flow.result.goal.title}」。下面只确认会改变图谱范围与顺序的信息。
            </p>
          ) : flow.clarifyPending ? (
            <div className="goal-flow-thinking" role="status">
              <LoaderCircle className="size-4 animate-spin" />
              正在理解学习意向…
            </div>
          ) : null}
          {flow.result && flow.stage === "clarifying" ? (
            <GoalQuestionnaire flow={flow} />
          ) : null}
        </section>
      ) : null}
      {flow.stage === "goal_review" || flow.stage === "graph_building" ? (
        <GoalSummaryReview flow={flow} />
      ) : null}
      {flow.stage === "graph_building" ? (
        <div className="goal-flow-thinking" role="status">
          <LoaderCircle className="size-4 animate-spin" />
          正在生成候选图谱…
        </div>
      ) : null}
      {flow.stage === "graph_review" ? <GoalGraphReview flow={flow} /> : null}
      {flow.error ? (
        <div className="goal-flow-error" role="alert">
          <span>{flow.error.message}</span>
          {flow.stage === "graph_review" ? (
            <Button onClick={() => void flow.graphRefetch()} size="xs" variant="ghost">
              <RefreshCcw className="size-3.5" />
              重试读取
            </Button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

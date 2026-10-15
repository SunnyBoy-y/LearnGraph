from pathlib import Path

def patch(path, changes):
    p=Path(path); text=p.read_text(encoding='utf-8')
    for old,new in changes:
        if text.count(old)!=1: raise RuntimeError(f'{path}: bad anchor {old[:100]} count={text.count(old)}')
        text=text.replace(old,new,1)
    p.write_text(text,encoding='utf-8',newline=''); print(f'Patched {path}: {len(changes)} changes')

patch('frontend/src/features/goals/goal-chat-flow.tsx',[
('retryNode.mutate({ nodeId, instruction })','retryNode.mutateAsync({ nodeId, instruction })'),
('updateNode.mutate({ nodeId, body })','updateNode.mutateAsync({ nodeId, body })'),
('onRetry: (instruction: string) => void;', 'onRetry: (instruction: string) => Promise<unknown>;'),
('onSave: (body: Partial<GraphNode>) => void;', 'onSave: (body: Partial<GraphNode>) => Promise<unknown>;'),
('''              onClick={() => {
                onRetry(retryInstruction.trim());
                setRetrying(false);
                setRetryInstruction("");
              }}''','''              onClick={async () => {
                try {
                  await onRetry(retryInstruction.trim());
                  setRetrying(false);
                  setRetryInstruction("");
                } catch { /* mutation reports failure; retain the draft */ }
              }}'''),
('''              onClick={() => {
                onSave(draft);
                setEditing(false);
              }}''','''              onClick={async () => {
                try { await onSave(draft); setEditing(false); }
                catch { /* retain the user's editing state and values */ }
              }}'''),
('        let streamedGraphId = "";', '        let streamedGraphId = "";\n        let streamCompleted = false;'),
('''          } else if (sse.event === "graph.complete") {
            streamedGraphId''','''          } else if (sse.event === "graph.complete") {
            streamCompleted = true;
            streamedGraphId'''),
('''        return { graphId: streamedGraphId, savedGoal };''','''        if (!streamCompleted) throw new Error("生成连接提前结束，已保留收到的内容，但图谱尚未确认完整。请重试生成，或从图谱书架查看已保存的候选内容。");
        return { graphId: streamedGraphId, savedGoal };'''),
])

patch('frontend/src/features/goals/goal-pages.tsx',[
('retryNode.mutate({ nodeId: node.id, instruction })','retryNode.mutateAsync({ nodeId: node.id, instruction })'),
('updateNode.mutate({ nodeId: node.id, body })','updateNode.mutateAsync({ nodeId: node.id, body })'),
('onRetry: (instruction: string) => void;', 'onRetry: (instruction: string) => Promise<unknown>;'),
('''    target_weight: number;
  }) => void;''','''    target_weight: number;
  }) => Promise<unknown>;'''),
('''  function saveEdit() {
    if (!draft.label.trim()) return;
    onSave({
      label: draft.label.trim(),
      description: draft.description.trim(),
      target_weight: draft.targetWeight,
    });
    setEditing(false);
  }''','''  async function saveEdit() {
    if (!draft.label.trim()) return;
    try {
      await onSave({label: draft.label.trim(), description: draft.description.trim(), target_weight: draft.targetWeight});
      setEditing(false);
    } catch { /* retain the draft; the mutation renders the failure */ }
  }'''),
('''              onClick={() => {
                onRetry(retryInstruction.trim());
                setRetryOpen(false);
                setRetryInstruction("");
              }}''','''              onClick={async () => {
                try {
                  await onRetry(retryInstruction.trim());
                  setRetryOpen(false);
                  setRetryInstruction("");
                } catch { /* preserve the instruction for retry */ }
              }}'''),
])

patch('backend/app/services/graph_cover_management.py',[
('from sqlalchemy import select','from sqlalchemy import select, update'),
('from app.domain.models import Graph, GraphNode','from app.domain.models import DurableJob, Graph, GraphNode\nfrom app.domain.graph_cover_models import COVER_ACTIVE_STATUSES, GraphCoverJob'),
('''        graph.cover_svg = cover''','''        # The later manual choice supersedes every queued/running AI render.
        # Update job rows before the graph: this serializes against the runner's
        # guarded completion write, including cancellation during its final read.
        job_ids = select(GraphCoverJob.job_id).where(
            GraphCoverJob.workspace_id == self.workspace_id,
            GraphCoverJob.graph_id == graph.id,
            GraphCoverJob.status.in_(COVER_ACTIVE_STATUSES),
        )
        self.db.execute(update(DurableJob).where(
            DurableJob.id.in_(job_ids), DurableJob.status.in_(("queued", "leased")),
        ).values(status="cancelled", lease_expires_at=None))
        self.db.execute(update(GraphCoverJob).where(
            GraphCoverJob.workspace_id == self.workspace_id,
            GraphCoverJob.graph_id == graph.id,
            GraphCoverJob.status.in_(COVER_ACTIVE_STATUSES),
        ).values(status="cancelled", error="已使用手动保存的封面，旧生成结果不再应用。"))
        graph.cover_svg = cover'''),
])

patch('backend/app/services/graph_cover_ai.py',[
('''        graph.cover_svg = cover
        job.status = "ready"''','''        completed = db.execute(update(GraphCoverJob).where(
            GraphCoverJob.id == cover_job_id,
            GraphCoverJob.status == "running",
        ).values(status="ready"))
        if completed.rowcount != 1:
            db.rollback()
            return
        graph.cover_svg = cover
        job.status = "ready"'''),
])

patch('frontend/src/features/graph/graph-plan-model.ts',[
('fromSnapshot && typeof rawDeadline === "string" ? rawDeadline : (goal?.deadline_at ?? null)', '(rawDeadline === null || typeof rawDeadline === "string") ? rawDeadline : (goal?.deadline_at ?? null)'),
])

p=Path('frontend/src/features/graph/graph-plan.tsx'); s=p.read_text(encoding='utf-8')
for name in ['reschedule','insert']:
    a=s.index(f'  const {name} = useMutation('); b=s.index('\n  });',a)+6
    part=s[a:b]
    old='onError: (error: Error) => toast.error(error.message),'
    assert part.count(old)==1
    new='onError: async (error: Error) => { toast.error(error.message); await roadmapQuery.refetch(); },'
    s=s[:a]+part.replace(old,new)+s[b:]
old='''      await updateGoalPlanning(goalId as string, payload);
      return replanRoadmap(goalId as string);'''
assert s.count(old)==1
s=s.replace(old,'''      const updated = await updateGoalPlanning(goalId as string, payload);
      queryClient.setQueryData(workspaceQueryKey(workspaceId, "goal", goalId), updated);
      queryClient.setQueriesData<Goal[]>({queryKey: workspaceQueryKey(workspaceId, "goals")}, current =>
        Array.isArray(current) ? current.map(item => item.id === updated.id ? updated : item) : current);
      await queryClient.invalidateQueries({queryKey: workspaceQueryKey(workspaceId, "goals")});
      return replanRoadmap(goalId as string);''')
p.write_text(s,encoding='utf-8',newline=''); print('Patched plan recovery and goal cache')

from pathlib import Path

def patch(path, changes):
    p=Path(path); text=p.read_text(encoding='utf-8')
    for old,new in changes:
        if text.count(old)!=1: raise RuntimeError(f'{path}: bad anchor {old[:100]} count={text.count(old)}')
        text=text.replace(old,new,1)
    p.write_text(text,encoding='utf-8',newline=''); print(f'Patched {path}: {len(changes)} changes')

p=Path('frontend/src/features/graph/graph-pages.tsx'); s=p.read_text(encoding='utf-8')
a=s.index('  const maximumDepth = workbenchGraph'); b=s.index('\n  useEffect(',a)
depths='''  const maximumDepth = useMemo(() => nodeDepthById.size ? Math.max(...nodeDepthById.values()) : 0, [nodeDepthById]);
  const effectiveDepthLimit = Math.min(depthLimit, maximumDepth);
  const requestedNodeDepth = requestedNodeId ? nodeDepthById.get(requestedNodeId) ?? 0 : 0;
'''
a2=s.index('  const startSession = useMutation(',s.index('export function JointStudyPage')); b2=s.index('  const splitSessions =',a2)
start='''  const startSession = useMutation({
    mutationFn: async () => {
      const nodeIds = [...selected];
      if (!result?.related || nodeIds.length < 2) throw new Error("请先判断当前所选节点的关联。");
      const graphId = resolvedGraphId;
      const prompt = result.next_actions.join("；") || `请联合讲解：${graph.data?.nodes.filter(node => nodeIds.includes(node.id)).map(node => node.label).join("、")}`;
      const session = await createSession({title: `${graph.data?.title ?? "联合"}学习`, graph_id: graphId});
      return {session, graphId, nodeIds, prompt};
    },
    onSuccess: ({session, graphId, nodeIds, prompt}) => navigate(`/w/${workspaceId}/chat/${session.id}`, {
      state: {pendingPrompt: prompt, learningNode: {graphId, nodeIds}},
    }),
    onError: (error: Error) => toast.error(`无法进入联合讲解：${error.message}`),
  });
'''
patch(str(p),[
('entry.graphId ? onStartLearning(entry) : onStartGoal()', 'onStartLearning(entry)'),
(s[a:b],depths),
('''  function locateNode(nodeId: string, neighbors = true) {
    canvasApiRef''','''  function locateNode(nodeId: string, neighbors = true) {
    setDepthLimit(current => Math.max(current, nodeDepthById.get(nodeId) ?? 0));
    canvasApiRef'''),
('''      exploreLoading={selectedExplore.isPending}''','''      exploreLoading={selectedExplore.isLoading}
      exploreError={selectedExplore.error?.message}
      onRetryExplore={() => void selectedExplore.refetch()}'''),
('''onSelect={selectWorkbenchNode}''','''onSelect={selectWorkbenchNode}
                     onOpenExplore={(node) => { selectWorkbenchNode(node); setExplorePanelOpen(true); }}'''),
('''  exploreLoading = false,''','''  exploreLoading = false,
  exploreError,
  onRetryExplore,'''),
('''  exploreLoading?: boolean;''','''  exploreLoading?: boolean;
  exploreError?: string;
  onRetryExplore?: () => void;'''),
('''  const [tab, setTab] = useState<"core" | "relations" | "resources">("core");''','''  const [tab, setTab] = useState<"core" | "relations" | "resources">("core");
  useEffect(() => { if (exploreOpen) setTab("resources"); }, [exploreOpen, node?.id]);'''),
('''                      {exploreLoading
                        ? "加载中…"''','''                      {exploreError ? "读取失败" : exploreLoading
                        ? "加载中…"'''),
('''                {!exploreRounds.length && !exploreLoading ? (''','''                {exploreError && <div role="alert"><p>{exploreError}</p><Button size="xs" onClick={onRetryExplore} disabled={exploreLoading}>重试读取历史</Button></div>}
                {!exploreRounds.length && !exploreLoading && !exploreError ? ('''),
('''  const [result, setResult] = useState<MultiNodeStudyResponse | null>(null);
  const selectedKey = selected.join(",");''','''  const [storedResult, setResult] = useState<{key: string; data: MultiNodeStudyResponse} | null>(null);
  const selectedKey = selected.join(",");
  const selectionKey = JSON.stringify([resolvedGraphId, selected]);
  const result = storedResult?.key === selectionKey ? storedResult.data : null;'''),
('''    mutationFn: () =>
      studyMultipleNodes(resolvedGraphId, { node_ids: selected }),
    onSuccess: setResult,''','''    mutationFn: (snapshot: {graphId: string; nodeIds: string[]; key: string}) =>
      studyMultipleNodes(snapshot.graphId, { node_ids: snapshot.nodeIds }),
    onSuccess: (data, snapshot) => setResult({key: snapshot.key, data}),'''),
(s[a2:b2],start),
('''  if (graph.isPending)
    return (''','''  if (!resolvedGraphId) {
    if (graphs.isPending) return <PageFrame><LoadingState /></PageFrame>;
    if (graphs.isError) return <PageFrame><ErrorState message={graphs.error.message} onRetry={() => void graphs.refetch()} /></PageFrame>;
    return <PageFrame><p>还没有可用于联合学习的图谱。</p><Button onClick={() => navigate(`/w/${workspaceId}/graphs`)}>前往图谱书架</Button></PageFrame>;
  }
  if (graph.isPending)
    return ('''),
('''        <ErrorState message={graph.error.message} />''','''        <ErrorState message={graph.error.message} onRetry={() => void graph.refetch()} />'''),
('''            onClick={() => study.mutate()}''','''            onClick={() => study.mutate({graphId: resolvedGraphId, nodeIds: [...selected], key: selectionKey})}'''),
('''        <Select
          onValueChange={(value) => {
            setGraphId(value);''','''        <Select
          disabled={startSession.isPending || splitSessions.isPending}
          onValueChange={(value) => {
            setGraphId(value);'''),
('disabled={!checked && selected.length >= 8}', 'disabled={startSession.isPending || splitSessions.isPending || (!checked && selected.length >= 8)}'),
('disabled={!result || !result.related || startSession.isPending}', 'disabled={!result?.related || selectedNodes.length < 2 || startSession.isPending || splitSessions.isPending}'),
('''              进入联合讲解
''','''              {startSession.isPending ? "正在进入…" : "进入联合讲解"}
'''),
])

patch('frontend/src/components/graph/node-explore.tsx',[
('import { Link2, MessageCircle }', 'import { useState } from "react";\nimport { Link2, MessageCircle }'),
('disabled={!active}', 'disabled={!active || !onOpen}'),
('''  if (!rounds.length) return null;''','''  const [expandedId, setExpandedId] = useState<string | null>(null);
  if (!rounds.length) return null;'''),
('''{rounds.length} 轮探索 · 点击条目查看对应问答''','''{rounds.length} 轮探索 · {onJump ? "点击条目定位对应问答" : "点击条目展开历史问题"}'''),
('''                onClick={() => onJump?.(round, index)}''','''                aria-expanded={!onJump ? expandedId === round.id : undefined}
                onClick={() => onJump ? onJump(round, index) : setExpandedId(current => current === round.id ? null : round.id)}'''),
('''                <span className="chain-label">{label}</span>''','''                <span className="chain-label" style={!onJump && expandedId === round.id ? {whiteSpace: "pre-wrap", overflow: "visible", display: "block", WebkitLineClamp: "unset"} : undefined}>{!onJump && expandedId === round.id ? round.content : label}</span>'''),
])

patch('frontend/src/components/layout/workspace-shell.tsx',[
('''        onOpenExplore={(node) => openExploreHistory(node.id)}''','''        onOpenExplore={(node) => openExploreHistory(node.id)}
        onStudy={(node) => {
          setSelectedNodeId(node.id);
          rememberLastLearnedNode(graphId, node.id);
          publishLearningNodeContext({graphId, nodeId: node.id, nodeLabel: node.label});
          window.dispatchEvent(new CustomEvent("learngraph:open-learning-project", {detail: {
            graphId, title: graph.title || node.label, nodeId: node.id, nodeLabel: node.label, learningPackage: true,
          }}));
        }}'''),
])

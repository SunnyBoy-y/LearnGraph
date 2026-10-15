from pathlib import Path

def patch(path, changes):
    p=Path(path); text=p.read_text(encoding='utf-8')
    for old,new in changes:
        if text.count(old)!=1: raise RuntimeError(f'{path}: bad anchor {old[:100]} count={text.count(old)}')
        text=text.replace(old,new,1)
    p.write_text(text,encoding='utf-8',newline=''); print(f'Patched {path}: {len(changes)} changes')

patch('backend/app/services/graph_cover_ai.py',[(
'''        job.status = "running"
        db.commit()''',
'''        claimed = db.execute(update(GraphCoverJob).where(
            GraphCoverJob.id == cover_job_id, GraphCoverJob.status == "queued",
        ).values(status="running"))
        if claimed.rowcount != 1:
            db.rollback()
            return
        db.commit()''')])

patch('frontend/src/components/graph/knowledge-graph.tsx',[
('''    return Boolean(nodes.find((node) => node.id === focusId)?.data.root);
  }, [focusId, nodes, structureSkeleton.items]);''','''    return structureSkeleton.depths[focusId] === 0 || Boolean(nodes.find((node) => node.id === focusId)?.data.root);
  }, [focusId, nodes, structureSkeleton]);'''),
('''  const toggleCollapse = useCallback(''','''  const treePositionsRef = useRef(structuredTree.positions);
  useEffect(() => { treePositionsRef.current = structuredTree.positions; }, [structuredTree.positions]);
  const toggleCollapse = useCallback('''),
('''        const pos = structuredTree.positions[nodeId];''','''        const pos = treePositionsRef.current[nodeId];'''),
('''    [flowInstance, onCollapsedIdsChange, structuredTree],''','''    [flowInstance, onCollapsedIdsChange],'''),
])

patch('frontend/src/features/learning/learning-package-canvas.tsx',[
('''  onStart,
}: {''','''  onStart,
  startPending = false,
}: {'''),
('''  onStart?: () => void;
})''','''  onStart?: () => void;
  startPending?: boolean;
})'''),
('''          <Button size="sm" onClick={onStart}>''','''          <Button size="sm" disabled={startPending} onClick={onStart}>'''),
('''                onStart={onStartActivity}''','''                onStart={onStartActivity}
                startPending={startPending}'''),
('''    mutationFn: () => learningApi.start(nodeId!),
    onSuccess:''','''    mutationFn: () => learningApi.start(nodeId!),
    onMutate: () => client.cancelQueries({queryKey: workspaceQueryKey(workspaceId, "learning-page", nodeId ?? "")}),
    onSuccess:'''),
('''  const progress = useMutation({
    mutationFn:''','''  const progress = useMutation({
    onMutate: () => client.cancelQueries({queryKey: workspaceQueryKey(workspaceId, "learning-page", nodeId ?? "")}),
    mutationFn:'''),
])

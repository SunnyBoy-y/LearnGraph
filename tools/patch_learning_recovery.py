from pathlib import Path

def patch(path, changes):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    for old, new in changes:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f'{path}: expected one anchor, got {count}: {old[:100]}')
        text = text.replace(old, new, 1)
    p.write_text(text, encoding='utf-8', newline='')
    print(f'Patched {path}: {len(changes)} changes')

patch('frontend/src/features/learning/learning-package-canvas.tsx', [
    ('import { useMutation, useQuery }', 'import { useMutation, useQuery, useQueryClient }'),
    ('  LearningBuildPreview,\n  Enrollment,', '  LearningBuildPreview,\n  LearningPageData,\n  Enrollment,'),
    ('  const navigate = useNavigate();\n  const nodeId', '  const navigate = useNavigate();\n  const client = useQueryClient();\n  const nodeId'),
    ('''  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  useEffect(() => {
    setEnrollment(query.data?.enrollment ?? null);
  }, [query.data?.enrollment]);''', '''  const enrollment = query.data?.enrollment ?? null;
  const setEnrollment = (row: Enrollment) => {
    client.setQueryData<LearningPageData>(
      workspaceQueryKey(workspaceId, "learning-page", nodeId ?? ""),
      current => current ? {...current, enrollment: row} : current,
    );
  };'''),
    ('''    mutationFn: () => learningApi.build(nodeId!),
    onSuccess: () => void query.refetch(),''', '''    mutationFn: () => query.data?.build?.status === "failed"
      ? learningApi.control(query.data.build.id, "retry")
      : learningApi.build(nodeId!),
    onSuccess: (row) => {
      client.setQueryData<LearningPageData>(
        workspaceQueryKey(workspaceId, "learning-page", nodeId ?? ""),
        current => current ? {...current, build: row} : current,
      );
      void query.refetch();
    },'''),
    ('''  if (query.isError || !query.data) {
    return <p className="learning-canvas-error">学习包暂时无法读取，请稍后重试。</p>;
  }''', '''  if (!query.data) {
    return <div className="learning-canvas-error" role="alert"><p>{query.error?.message ?? "学习包暂时无法读取。"}</p><Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>重新读取学习包</Button></div>;
  }'''),
    ('    <LearningPackageCanvasView\n', '''    <>
    {query.isError && <div className="learning-canvas-error" role="alert"><p>更新失败，已保留上次读取的内容。{query.error?.message}</p><Button size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新状态</Button></div>}
    <LearningPackageCanvasView
'''),
    ('      buildStatus={data.package ? undefined : data.build?.status}', '      buildStatus={data.package ? undefined : data.build?.status}\n      buildError={data.build?.error}\n      startPending={start.isPending}'),
    ('      progressBusy={progress.isPending}', '      progressBusy={progress.isPending || start.isPending}'),
    ('    />\n  );\n}\n', '    />\n    </>\n  );\n}\n'),
    ('  buildStatus?: string;', '  buildStatus?: string;\n  buildError?: string;\n  startPending?: boolean;'),
    ('  buildStatus,\n  readSections', '  buildStatus,\n  buildError,\n  startPending = false,\n  readSections'),
    ('            {blueprint ? (', '''            {["failed", "cancelled", "skipped"].includes(buildStatus ?? "") && <p role="status">{buildError || (buildStatus === "cancelled" ? "生成已取消，可重新创建学习包。" : buildStatus === "skipped" ? "生成已跳过，请检查节点状态后重新创建。" : "生成失败，可重试失败阶段。")}</p>}
            {blueprint ? ('''),
    ('{buildStatus === "failed" && onBuild ? (', '{["failed", "cancelled", "skipped"].includes(buildStatus ?? "") && onBuild ? ('),
    ('                    重试学习包生成', '                    {buildStatus === "failed" ? "重试失败阶段" : "重新创建学习包"}'),
    ('<Button className="w-fit" size="sm" onClick={onStartLearning}>', '<Button className="w-fit" size="sm" disabled={startPending} onClick={onStartLearning}>'),
])

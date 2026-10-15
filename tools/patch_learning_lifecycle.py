from pathlib import Path

def patch(path, changes):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    for old, new in changes:
        if text.count(old) != 1:
            raise RuntimeError(f'{path}: nonunique anchor {old[:90]}')
        text = text.replace(old, new, 1)
    p.write_text(text, encoding='utf-8', newline='')
    print(f'Patched {path}: {len(changes)} changes')

p = Path('frontend/src/features/learning/learning-package-canvas.tsx')
s = p.read_text(encoding='utf-8')
a = s.index('function PreviewImage(')
b = s.index('function SvgIllustration(', a)
image = '''function PreviewImage({ fileId, alt }: { fileId: string; alt: string }) {
  const [retry, setRetry] = useState(0);
  const [image, setImage] = useState<{fileId: string; url: string; error: string}>({fileId: "", url: "", error: ""});
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setImage({fileId, url: "", error: fileId ? "" : "插图文件不存在。"});
    if (fileId) void apiClient.getBlob(`/files/${encodeURIComponent(fileId)}/content`)
      .then(blob => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({fileId, url: objectUrl, error: ""});
      })
      .catch((error: unknown) => {
        if (active) setImage({fileId, url: "", error: error instanceof Error ? error.message : "插图加载失败。"});
      });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [fileId, retry]);
  const current = image.fileId === fileId ? image : null;
  if (current?.error) return <div role="alert"><p>插图暂不可用：{current.error}</p><Button size="sm" variant="outline" onClick={() => setRetry(value => value + 1)}>重试插图</Button></div>;
  return current?.url ? <img className="learning-canvas-image" src={current.url} alt={alt} onError={() => setImage({fileId, url: "", error: "图片无法解码。"})} /> : <LoadingBlock label="插图" />;
}

'''
patch(str(p), [(s[a:b], image)])

patch('frontend/src/features/learning/node-learning-page.tsx', [
    ('export function NodeLearningPage() {', '''export function NodeLearningPage() {
  const {workspaceId = '', nodeId = ''} = useParams()
  return <NodeLearningPageContent key={`${workspaceId}:${nodeId}`} />
}

function NodeLearningPageContent() {'''),
    ('if (!data?.node.graph_id || !nodeId || examViewActive) return', 'if (!data?.node.graph_id || !nodeId) return'),
    ("  const examViewActive = examRoute || tab === 'exam'\n", ''),
    ("learningApi.action(attemptId, attempt!.revision, id, reset), onSuccess: update, onError: (e: Error) => toast.error(e.message)", "learningApi.action(attemptId, attempt!.revision, id, reset), onSuccess: update, onError: (e: Error) => {toast.error(e.message); void query.refetch()}"),
    ("  if (!attempt) return <p role=\"status\">{query.error?.message ?? '读取试卷…'}</p>", "  if (!attempt) return <div role=\"status\"><p>{query.error?.message ?? '读取试卷…'}</p>{query.isError && <Button variant=\"outline\" disabled={query.isFetching} onClick={() => void query.refetch()}>重新读取试卷</Button>}</div>"),
    ('  const busy = save.isPending || action.isPending || submit.isPending', '  const busy = save.isPending || action.isPending || submit.isPending || query.isFetching'),
    ('onClick={()=>window.dispatchEvent(new CustomEvent("learngraph:compose",{detail:{content:`请继续讲解“${data.node.label}”，结合我刚才的学习内容给一个例子。`}}))}', 'onClick={openInChat}'),
])

patch('frontend/src/features/learning/learning-pages.tsx', [
    ('export function ExerciseAnswerPage() {', '''export function ExerciseAnswerPage() {
  const { questionId = "", workspaceId = "", setId = "default" } = useParams();
  return <ExerciseAnswerContent key={`${workspaceId}:${setId}:${questionId}`} />;
}

function ExerciseAnswerContent() {'''),
])

patch('backend/app/services/learning_packages.py', [
    ('def transition(spec: dict, current: dict, action_id: str, *, feedback: bool = True) -> dict:', '''def complete_training_activity(spec: dict, current: dict | None) -> dict:
    """Supply late-built variables without resetting completed learner actions."""
    initial = initial_activity(spec or {})
    if not current:
        return initial
    state = deepcopy(current)
    state["values"] = {**initial["values"], **state.get("values", {})}
    state.setdefault("history", [])
    state.setdefault("completed", False)
    return state


def transition(spec: dict, current: dict, action_id: str, *, feedback: bool = True) -> dict:'''),
    ('''    @staticmethod
    def enrollment_view(row):
        return {"id": row.id, "package_id": row.package_id, "revision": row.revision, "progress": row.progress}''', '''    def enrollment_view(self, row):
        package = self.db.get(LearningPackage, row.package_id)
        progress = deepcopy(row.progress)
        progress["activity"] = complete_training_activity(
            package.private_activity if package else {}, progress.get("activity"))
        return {"id": row.id, "package_id": row.package_id, "revision": row.revision, "progress": progress}'''),
    ('        progress = deepcopy(row.progress)\n        if payload.section_id:', '        progress = deepcopy(row.progress)\n        progress["activity"] = complete_training_activity(package.private_activity, progress.get("activity"))\n        if payload.section_id:'),
])

from pathlib import Path

def patch(path, changes):
    p=Path(path); text=p.read_text(encoding='utf-8')
    for old,new in changes:
        if text.count(old)!=1: raise RuntimeError(f'{path}: bad anchor {old[:100]} count={text.count(old)}')
        text=text.replace(old,new,1)
    p.write_text(text,encoding='utf-8',newline=''); print(f'Patched {path}: {len(changes)} changes')

patch('frontend/src/App.tsx',[
("import { WorkspaceShell } from '@/components/layout/workspace-shell'", "import { WorkspaceShell } from '@/components/layout/workspace-shell'\nimport { RouteErrorBoundary } from '@/components/shared/route-error-boundary'"),
('    <Suspense fallback={<RouteLoading />}><Routes>', '    <RouteErrorBoundary><Suspense fallback={<RouteLoading />}><Routes>'),
('    </Routes></Suspense>', '    </Routes></Suspense></RouteErrorBoundary>'),
])

p=Path('frontend/src/features/learning/node-learning-api.ts'); s=p.read_text(encoding='utf-8')
s="import { validateLearningPage, validateEnrollment, validateAttempt } from './learning-response'\n"+s
validators={'page':'validateLearningPage','start':'validateEnrollment','progress':'validateEnrollment','startAttempt':'validateAttempt','attempt':'validateAttempt','save':'validateAttempt','action':'validateAttempt','submit':'validateAttempt'}
for method,validator in validators.items():
    lines=[line for line in s.splitlines() if line.startswith(f'  {method}:')]
    assert len(lines)==1 and lines[0].endswith(',')
    old=lines[0]; s=s.replace(old,old[:-1]+f'.then({validator}),',1)
p.write_text(s,encoding='utf-8',newline=''); print('Connected learning payload validation')

p=Path('frontend/src/features/chat/chat-pages.tsx'); s=p.read_text(encoding='utf-8')
assert s.count('reviewGraphChange.mutate(')==3
s=s.replace('reviewGraphChange.mutate(', 'submitGraphReview(')
anchor='  const handleComponentAction = useCallback('
assert s.count(anchor)==1
s=s.replace(anchor,'''  const graphReviewPendingRef = useRef(false);
  const submitGraphReview = useCallback((payload: Parameters<typeof reviewGraphChange.mutate>[0]) => {
    if (graphReviewPendingRef.current) { toast.message("图谱变更正在处理，请稍候。"); return; }
    graphReviewPendingRef.current = true;
    const notification = toast.loading("正在处理图谱变更…");
    void reviewGraphChange.mutateAsync(payload).catch(() => { /* mutation reports the error */ }).finally(() => {
      graphReviewPendingRef.current = false;
      toast.dismiss(notification);
    });
  }, [reviewGraphChange]);

'''+anchor,1)
s=s.replace('[reviewGraphChange, send],','[submitGraphReview, send],',1)
p.write_text(s,encoding='utf-8',newline=''); print('Guarded graph proposal requests and added pending feedback')

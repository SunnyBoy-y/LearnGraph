import { useEffect, useRef, useState } from "react";
import { Code2, Play, RotateCcw, Sparkles, Square, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { localLanguages, normalizeLanguage, sameOutput, trainerPrompt } from "@/lib/code-trainer";
import type { RunResult, TrainerProps } from "@/lib/code-trainer";
import { runCode } from "@/lib/code-trainer-runner";
import { useStreamUIState } from "@/lib/stream-ui-local-state";
import { CodeTrainerEditor } from "./code-trainer-editor";
import "./code-trainer.css";

const labels: Record<string, string> = { completed: "运行完成", accepted: "公开样例通过", wrong_answer: "输出不符", compile_error: "语法 / 转换错误", runtime_error: "运行错误", time_limit: "运行超时", memory_limit: "内存超限", output_limit: "输出超限", cancelled: "已停止", load_error: "加载失败" };
const examples: Record<string, string> = {
  python: "import sys\nnums = list(map(int, sys.stdin.read().split()))\nprint(sum(nums))\n",
  javascript: "const nums = stdin.trim().split(/\\s+/).filter(Boolean).map(Number);\nconsole.log(nums.reduce((sum, n) => sum + n, 0));\n",
  typescript: "const nums: number[] = stdin.trim().split(/\\s+/).filter(Boolean).map(Number);\nconsole.log(nums.reduce((sum: number, n: number) => sum + n, 0));\n",
};
type TestResult = RunResult & { name: string; input: string; expected: string };

export function CodeTrainer({ props, interactive, onAsk }: { props: TrainerProps; interactive: boolean; onAsk?: (prompt: string) => void | Promise<void> }) {
  const initialLanguage = normalizeLanguage(props.language);
  const [language, setLanguage] = useStreamUIState("language", initialLanguage);
  const [drafts, setDrafts] = useStreamUIState<Record<string, string>>("drafts", { [initialLanguage]: props.starter_code });
  const [stdin, setStdin] = useStreamUIState("stdin", props.stdin);
  const [result, setResult] = useStreamUIState<RunResult | null>("result", null);
  const [tests, setTests] = useStreamUIState<TestResult[]>("tests", []);
  const [split, setSplit] = useStreamUIState("editor.split", false);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
  useEffect(() => { if (!interactive) controller.current?.abort(); }, [interactive]);
  const code = drafts[language] ?? "";
  const local = localLanguages.has(language);
  const languages = [...new Set(["python", "javascript", "typescript", "java", "go", "cpp", "rust", initialLanguage])];
  const changeCode = (value: string) => { setDrafts(current => ({ ...current, [language]: value })); setResult(null); setTests([]); };
  async function execute(mode: "run" | "test") {
    if (controller.current || !interactive) return;
    const abort = new AbortController(); controller.current = abort;
    setBusy(true); setResult(null); setTests([]);
    try {
      if (mode === "run") {
        const value = await runCode({ language, code, stdin, timeLimitMs: props.time_limit_ms }, abort.signal);
        if (mounted.current) setResult(value);
      } else {
        const results: TestResult[] = [];
        for (const test of props.tests) {
          if (abort.signal.aborted) break;
          const value = await runCode({ language, code, stdin: test.stdin, timeLimitMs: props.time_limit_ms }, abort.signal);
          results.push({ ...value, status: value.status === "completed" ? sameOutput(value.stdout, test.expected_stdout) ? "accepted" : "wrong_answer" : value.status, name: test.name, input: test.stdin, expected: test.expected_stdout });
          if (!mounted.current) return;
          setTests([...results]);
          if (["cancelled", "load_error"].includes(value.status)) break;
        }
        if (mounted.current && abort.signal.aborted) setResult({ status: "cancelled", stdout: "", stderr: "样例测试已停止" });
      }
    } catch (error) {
      if (mounted.current) setResult({ status: "load_error", stdout: "", stderr: error instanceof Error ? error.message : "运行失败" });
    } finally { controller.current = null; if (mounted.current) setBusy(false); }
  }
  async function ask() {
    if (!onAsk || asking) return;
    setAsking(true); setAskError("");
    try { await onAsk(trainerPrompt(props, language, code, stdin, tests.length ? tests : result)); }
    catch (error) { if (mounted.current) setAskError(error instanceof Error ? error.message : "发送失败，请重试"); }
    finally { if (mounted.current) setAsking(false); }
  }
  return <section className="message-component code-trainer" aria-label="编程判题训练器">
    <header className="code-trainer__heading"><Code2 className="size-4" /><div><strong>{props.title}</strong><span>编程判题训练器 · {local ? "浏览器本地执行" : "AI 代码分析"}</span></div><span className="code-trainer__mode">{local ? "LOCAL" : "AI"}</span></header>
    {!split && props.description && <p className="code-trainer__problem">{props.description}</p>}
    <div className="code-trainer__toolbar"><Select value={language} disabled={busy || asking} onValueChange={value => { setLanguage(value); setResult(null); setTests([]); }}><SelectTrigger size="sm" aria-label="编程语言"><SelectValue /></SelectTrigger><SelectContent>{languages.map(value => <SelectItem value={value} key={value}>{({python:"Python",javascript:"JavaScript",typescript:"TypeScript",java:"Java",go:"Go",cpp:"C++",rust:"Rust"} as Record<string,string>)[value] ?? value}{localLanguages.has(value) ? " · 本地" : " · AI"}</SelectItem>)}</SelectContent></Select><span>{local ? `${props.time_limit_ms / 1000}s / 次 · 单文件标准输入输出` : "模型推演，不执行代码"}</span><Button size="xs" variant="ghost" disabled={busy} onClick={() => { changeCode(language === initialLanguage ? props.starter_code : ""); setStdin(props.stdin); }}><RotateCcw className="size-3" />重置</Button></div>
    <CodeTrainerEditor value={code} language={language} readOnly={busy} onChange={changeCode} split={split} onSplitChange={setSplit} problem={<>
      <span className="code-ide__problem-label">题目</span><h3>{props.title}</h3>
      <p>{props.description || "请根据当前对话中的题目编写代码。"}</p>
      {!!props.tests.length && <><h4>公开样例</h4>{props.tests.map((test,index)=><section key={index}><strong>{test.name}</strong><pre>输入：{test.stdin || "（空）"}{"\n"}期望输出：{test.expected_stdout || "（空）"}</pre></section>)}</>}
    </>} />
    {!code && local && <Button className="code-trainer__example" size="xs" variant="ghost" onClick={() => changeCode(examples[language])}>填入输入求和示例</Button>}
    <label className="code-trainer__label">标准输入<Textarea aria-label="标准输入" rows={2} value={stdin} maxLength={4000} disabled={busy} onChange={event => { setStdin(event.target.value); setResult(null); setTests([]); }} placeholder="输入传给程序的数据" /></label>
    {!split && !!props.tests.length && <details className="code-trainer__samples"><summary>{props.tests.length} 个公开样例</summary>{props.tests.map((test, i) => <div key={i}><strong>{test.name}</strong><div className="code-trainer__io"><pre>输入：{test.stdin || "（空）"}</pre><pre>期望：{test.expected_stdout || "（空）"}</pre></div></div>)}</details>}
    <div className="code-trainer__actions">{local && <><Button size="sm" disabled={busy || !code.trim() || !interactive} onClick={() => void execute("run")}><Play className="size-3.5" />运行</Button><Button size="sm" variant="outline" disabled={busy || !code.trim() || !props.tests.length || !interactive} onClick={() => void execute("test")}>测试公开样例</Button></>}{busy && <Button size="sm" variant="outline" onClick={() => controller.current?.abort()}><Square className="size-3.5" />停止</Button>}<Button size="sm" variant={local ? "ghost" : "default"} disabled={busy || asking || !interactive || !onAsk || !code.trim()} onClick={() => void ask()}><Sparkles className="size-3.5" />{asking ? "正在发送…" : local ? "请 AI 辅导" : "交给 AI 分析"}</Button>{busy && <span role="status">正在加载 / 执行…</span>}</div>
    {askError && <p role="alert">{askError}</p>}
    <details className="code-trainer__samples"><summary>AI 将读取当前完整代码（{code.length} 字符）</summary><p>点击辅导时，会将下方代码连同题目、当前标准输入、公开样例和已有运行结果发送到当前对话。</p><pre aria-label="AI 辅导代码预览">{code || "（代码为空）"}</pre></details>
    <div aria-live="polite" className="code-trainer__results">{result && <div><strong>{labels[result.status]}{result.durationMs != null && ` · ${Math.round(result.durationMs)} ms`}</strong>{result.stdout && <pre aria-label="标准输出">{result.stdout}</pre>}{result.stderr && <pre className="code-trainer__error" aria-label="错误输出">{result.stderr}</pre>}{result.status === "completed" && !result.stdout && <p>程序没有标准输出</p>}</div>}{tests.length > 0 && <><p>已通过 {tests.filter(test => test.status === "accepted").length} / {props.tests.length} 个公开样例</p>{tests.map((test, index) => <details key={index}><summary>{test.status === "accepted" ? <Check className="size-3.5" /> : <X className="size-3.5" />}{test.name} · {labels[test.status]}</summary><pre>输入：{test.input || "（空）"}{"\n"}期望：{test.expected || "（空）"}{"\n"}实际：{test.stdout || "（空）"}</pre>{test.stderr && <pre className="code-trainer__error">{test.stderr}</pre>}</details>)}</>}</div>
    <footer className="code-trainer__footnote">{local ? "本地练习结果，公开样例可见，不作为正式成绩。JS / TS 使用 stdin、readline()、console.log()；不支持 Node.js 模块。Python 首次加载较大，支持标准库，不安装第三方包。" : "代码、题目、输入和公开样例会发送给当前对话模型。AI 分析可能出错，不代表真实执行或判题结果。"}</footer>
  </section>;
}

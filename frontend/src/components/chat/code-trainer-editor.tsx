import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type * as Monaco from "monaco-editor/editor/editor.api";
import { Columns2, Maximize2, Minimize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useStreamUIState } from "@/lib/stream-ui-local-state";

type MonacoModule = typeof import("@/lib/code-trainer-monaco");
type Props = { value: string; language: string; readOnly: boolean; onChange: (code: string) => void; split: boolean; onSplitChange: (split: boolean) => void; problem: ReactNode };
export function CodeTrainerEditor({ value, language, readOnly, onChange, split, onSplitChange, problem }: Props) {
  const shell = useRef<HTMLDivElement>(null);
  const left = useRef<HTMLDivElement>(null);
  const module = useRef<MonacoModule | null>(null);
  const model = useRef<Monaco.editor.ITextModel | null>(null);
  const editors = useRef<Monaco.editor.IStandaloneCodeEditor[]>([]);
  const latest = useRef({ value, language, readOnly, onChange });
  latest.current = { value, language, readOnly, onChange };
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const [smart, setSmart] = useStreamUIState("editor.smart", true);
  const preferences = useRef({ smart }); preferences.current = { smart };

  useEffect(() => {
    let disposed = false;
    const cleanups: Array<() => void> = [];
    void import("@/lib/code-trainer-monaco").then(api => {
      if (disposed || !left.current) return;
      module.current = api;
      const { monaco } = api;
      const textModel = monaco.editor.createModel(latest.current.value, latest.current.language);
      model.current = textModel;
      if (shell.current) shell.current.dataset.modelUri = textModel.uri.toString();
      textModel.updateOptions({ tabSize: 2, insertSpaces: true });
      api.setSmartCompletion(textModel, preferences.current.smart);
      const editor = monaco.editor.create(left.current, {
        model: textModel, ariaLabel: "代码编辑器", readOnly: latest.current.readOnly,
        fontSize: 13, lineHeight: 22, fontFamily: "Consolas, 'Cascadia Code', monospace",
        minimap: { enabled: false }, scrollBeyondLastLine: false,
        automaticLayout: true, wordWrap: "off", lineNumbers: "on",
        scrollbar: { vertical: "visible", horizontal: "visible", verticalScrollbarSize: 12, horizontalScrollbarSize: 12, alwaysConsumeMouseWheel: false },
        padding: { top: 12, bottom: 12 }, bracketPairColorization: { enabled: true },
        fixedOverflowWidgets: false,
        editContext: false,
        ...api.completionOptions(preferences.current.smart),
      });
      editors.current = [editor];
      let reverting = false;
      const changes = textModel.onDidChangeContent(() => {
        if (reverting) return;
        const code = textModel.getValue();
        if (code.length > 16000) {
          reverting = true; editor.trigger("limit", "undo", null); reverting = false;
          setError("代码最多 16000 个字符，超出部分未保存。"); return;
        }
        setError(""); latest.current.onChange(code);
      });
      const theme = () => {
        const root = document.documentElement;
        monaco.editor.setTheme(root.classList.contains("dark") || root.dataset.theme === "dark" ? "vs-dark" : "vs");
      };
      theme();
      const observer = new MutationObserver(theme);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme"] });
      cleanups.push(() => observer.disconnect(), () => changes.dispose(), () => { editors.current.forEach(view => view.dispose()); editors.current = []; textModel.dispose(); model.current = null; });
      setReady(true);
    }).catch(reason => { if (!disposed) setError(reason instanceof Error ? reason.message : "代码编辑器加载失败，请刷新重试。"); });
    return () => { disposed = true; cleanups.forEach(cleanup => cleanup()); };
  }, []);

  useEffect(() => {
    if (!ready || !module.current || !model.current) return;
    const { monaco } = module.current;
    if (model.current.getLanguageId() !== language) monaco.editor.setModelLanguage(model.current, language);
    if (model.current.getValue() !== value) model.current.setValue(value);
  }, [value, language, ready]);
  useEffect(() => {
    if (!ready || !module.current || !model.current) return;
    const api = module.current;
    api.setSmartCompletion(model.current, smart);
    editors.current.forEach(editor => editor.updateOptions({ readOnly, ...api.completionOptions(smart) }));
  }, [readOnly, smart, ready]);
  useEffect(() => {
    if (!fullscreen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const workbench = shell.current;
    if (workbench && typeof workbench.showPopover === "function") {
      workbench.setAttribute("popover", "manual"); workbench.showPopover();
    }
    // Monaco handles Escape for suggestions first; the toolbar always offers an exit.
    const keydown = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.defaultPrevented) setFullscreen(false); };
    document.addEventListener("keydown", keydown);
    return () => { document.body.style.overflow = previous; document.removeEventListener("keydown", keydown); if (workbench?.hasAttribute("popover")) { workbench.hidePopover(); workbench.removeAttribute("popover"); } };
  }, [fullscreen]);

  return <div ref={shell} className={`code-ide${fullscreen ? " code-ide--fullscreen" : ""}`} aria-label="代码工作台">
    <div className="code-ide__toolbar"><span>{language} · {split ? "左题目 · 右代码" : "代码编辑"}</span>
      <label title="关闭智能补全后，语言关键词补全仍始终开启"><input type="checkbox" checked={smart} onChange={event => setSmart(event.target.checked)} />智能补全</label>
      <Button size="xs" variant="ghost" aria-pressed={split} onClick={() => onSplitChange(!split)}><Columns2 className="size-3.5" />{split ? "关闭分屏" : "左右分屏"}</Button>
      <Button size="xs" variant="ghost" aria-pressed={fullscreen} onClick={() => setFullscreen(current => !current)}>{fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}{fullscreen ? "退出全屏" : "全屏"}</Button>
    </div>
    <div className={`code-ide__panes${split ? " code-ide__panes--split" : ""}`}>
      <aside className="code-ide__problem" aria-label="题目与公开样例" hidden={!split}>{problem}</aside>
      <div ref={left} className="code-ide__pane" />
    </div>
    {!ready && !error && <p role="status" className="code-ide__loading">正在加载代码编辑器…</p>}
    {error && <p role="alert" className="code-ide__error">{error}</p>}
    <div className="code-ide__status"><span>Tab 缩进 / 接受补全 · Ctrl+Space 补全 · Ctrl+F 查找</span><span>关键词补全始终开启 · {value.length}/16000</span></div>
  </div>;
}

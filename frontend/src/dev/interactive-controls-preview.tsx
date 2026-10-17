import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MessageResponse } from "@/components/ai-elements/message";
import { MarkdownTaskCheckbox, MarkdownTaskListItem } from "@/components/chat/interactive-control-list";
import { controlMarkdown } from "./interactive-controls-fixture";
import "@/index.css";

function Preview() {
  const [after, setAfter] = useState(true);
  return <main className="mx-auto max-w-[1000px] p-5 md:p-12">
    <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
      <div><h1 className="text-xl font-semibold">可勾选清单</h1><p className="mt-2 text-sm text-muted-foreground">截图内容重建 · 修改后使用实际 Markdown 复选框组件 · 示例数据</p></div>
      <div className="flex gap-2"><button className="rounded-lg border px-3 py-2 text-sm" onClick={() => setAfter(!after)}>{after ? "查看修改前" : "查看修改后"}</button>
      <button className="rounded-lg border px-3 py-2 text-sm" onClick={() => document.documentElement.classList.toggle("dark")}>切换深浅色</button></div>
    </header>
    <section className="rounded-2xl border p-5 md:p-8" data-preview={after ? "after" : "before"}>
      <h2 className="mb-6 text-sm text-muted-foreground">{after ? "修改后 · 可自行勾选和取消勾选" : "修改前 · 复选框禁用，无法点击"}</h2>
      <MessageResponse key={String(after)} components={after ? { input: MarkdownTaskCheckbox, li: MarkdownTaskListItem } : undefined}>{controlMarkdown}</MessageResponse>
    </section>
  </main>;
}
const root: Root = import.meta.hot?.data.root ?? createRoot(document.getElementById("root")!);
if (import.meta.hot) import.meta.hot.dispose(data => { data.root = root; });
root.render(<Preview />);

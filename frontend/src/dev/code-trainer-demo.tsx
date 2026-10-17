import { useState } from "react";
import { createRoot } from "react-dom/client";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { Button } from "@/components/ui/button";
import "@/index.css";

export function Demo() {
  const [prompt, setPrompt] = useState("");
  return <main style={{ maxWidth: 800, margin: "36px auto", padding: "0 20px" }}>
    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 18 }}><h1>编程判题训练器</h1><Button variant="outline" size="sm" onClick={() => document.documentElement.classList.toggle("dark")}>切换主题</Button></div>
    <TrustedComponentRenderer fallbackId="code-trainer-demo-v1" data={{ component_type: "code_trainer", schema_version: "1.0", allowed_events: ["submit"], props: {
      title: "01 · 把输入变成答案", description: "读取一行整数，输出它们的和。尝试负数、零与单个数字，观察每个公开样例的输出。", language: "python", starter_code: "import sys\nnums = list(map(int, sys.stdin.read().split()))\nprint(sum(nums))\n", stdin: "2 3", tests: [{ name: "两个正整数", stdin: "2 3", expected_stdout: "5" }, { name: "负数和零", stdin: "-5 0 2", expected_stdout: "-3" }, { name: "单个数字", stdin: "42", expected_stdout: "42" }],
    } }} onAction={action => setPrompt(String(action.payload.prompt))} />
    {prompt && <section style={{ marginTop: 20 }}><h2>待发送给对话模型的 Prompt（本演示页不调用模型）</h2><pre style={{ whiteSpace: "pre-wrap", fontSize: 12, overflowWrap: "anywhere" }}>{prompt}</pre></section>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Demo />);

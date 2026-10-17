import { useState } from "react";
import { createRoot } from "react-dom/client";
import { StreamUI } from "@/components/chat/stream-ui";
import type { UISnapshot } from "@/lib/stream-ui";
import "@/index.css";

const graph: UISnapshot = {
  protocol: "suip/0.1", block_id: "cx_graph", component: "graph", revision: 1, state: "completed",
  props: {
    title: "时间复杂度增长层级关系图",
    description: "箭头指向「增长更快」的一级，这条链就是记忆用的顺序",
    source_note: "依据大 O 记号的定义给出的增长顺序关系，非实测数据",
    columns: [], chart_type: "line", unit: "",
  },
  rows: [], items: [],
  nodes: [
    { id: "constant", label: "O(1) 常数级", description: "输入增大时耗时保持稳定", group: "" },
    { id: "log", label: "O(log n) 对数级", description: "例如二分查找", group: "" },
    { id: "linear", label: "O(n) 线性级", description: "例如遍历数组", group: "" },
    { id: "nlog", label: "O(n log n) 线性对数级", description: "例如归并排序", group: "" },
    { id: "square", label: "O(n²) 平方级", description: "例如双重循环", group: "" },
    { id: "exp", label: "O(2ⁿ) 指数级", description: "例如穷举全部子集", group: "" },
  ],
  edges: ["constant", "log", "linear", "nlog", "square"].map((source, index) => ({
    id: `edge-${index}`, source, target: ["log", "linear", "nlog", "square", "exp"][index], label: "增长更快",
  })),
};

function Preview() {
  const [prompt, setPrompt] = useState("");
  return <main style={{ maxWidth: 1080, margin: "36px auto", padding: "0 20px" }}>
    <h1 style={{ fontSize: 24, fontWeight: 650 }}>“让 AI 解释”修复预览</h1>
    <p style={{ color: "var(--muted-foreground)", margin: "12px 0 24px" }}>
      截图场景重建：回答已中断，图表已完成。使用真实图表组件，按钮响应为本地演示，不调用模型。
    </p>
    <section aria-label="修复前" style={{ marginBottom: 28 }}>
      <h2 style={{ fontWeight: 600, marginBottom: 12 }}>修复前 · 点击后被整条回答的状态拒绝</h2>
      <StreamUI data={graph} partId="explain-before" onAction={async () => {
        throw new Error("Wait for completion or refresh the message");
      }} />
    </section>
    <section aria-label="修复后">
      <h2 style={{ fontWeight: 600, marginBottom: 12 }}>修复后 · 已完成的图表可以继续请求解释</h2>
      <StreamUI data={graph} partId="explain-after" onAction={async (action) => {
        const node = graph.nodes.find((item) => item.id === action.payload.node_id);
        setPrompt(node
          ? `请解释图表「${graph.props.title}」中的节点「${node.label}」及其关联。`
          : `请解释图表「${graph.props.title}」，分析其中的主要信息。`);
      }} />
      {prompt && <div role="status" style={{ padding: 16, marginTop: 12, border: "1px solid var(--border)", borderRadius: 12 }}>
        <strong>解释请求已就绪（演示）</strong><p style={{ marginTop: 8 }}>{prompt}</p>
      </div>}
    </section>
  </main>;
}

createRoot(document.getElementById("root")!).render(<Preview />);

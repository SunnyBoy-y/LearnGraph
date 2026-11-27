import { useState } from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "@/index.css";
import { WorkbenchGraph } from "@/components/graph/workbench-graph";
import { graphLayoutFixture } from "@/components/graph/workbench-layout-fixture";

const raw = Number(new URLSearchParams(location.search).get("nodes"));
const size = [7, 20, 50, 100].includes(raw) ? raw : 7;
const { nodes, edges } = graphLayoutFixture(size);
function Preview() {
  const [selected, setSelected] = useState("n1");
  return <main style={{ display: "grid", gridTemplateRows: "auto minmax(0, 1fr)", height: "100dvh", padding: "16px 20px", gap: 12, background: "#fbfcfa" }}>
    <header><h1 style={{ fontWeight: 700, fontSize: 21 }}>LearnGraph · 图谱布局验收</h1><p style={{ color: "#53645b" }}>合成图谱 · {size} 个节点</p></header>
    <div style={{ border: "1px solid #dce5de", borderRadius: 14, overflow: "hidden" }}>
      <WorkbenchGraph nodes={nodes} edges={edges} title={`合成图谱 · ${size} 个节点`} currentId="n1" selectedId={selected} onSelect={node => setSelected(node.id)} onStudy={() => undefined} onOpenExplore={() => undefined} />
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<Preview />);

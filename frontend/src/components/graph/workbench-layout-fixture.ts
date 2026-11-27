import type { Edge } from "@xyflow/react";
import type { KnowledgeNode } from "./knowledge-graph";

export function graphLayoutFixture(count: number) {
  const labels = ["AI 应用开发综合能力", "RAG 企业知识检索", "后端工程化技术栈", "多智能体工作流", "实时语音链路实验", "AI 辅助开发工作流", "综合能力自检"];
  const nodes: KnowledgeNode[] = Array.from({ length: count }, (_, index) => ({
    id: `n${index}`, type: "knowledge", position: { x: -9999, y: -9999 },
    data: {
      label: count === 7 ? labels[index] : index === 0 ? labels[0] : `${labels[1 + ((index - 1) % 6)]} · ${index}`,
      nodeType: index === 0 ? "root" : index % 7 === 6 ? "assessment" : index % 7 >= 4 ? "practice" : "concept",
      root: index === 0, blockedByPrerequisite: index % 7 >= 4,
      description: "理解核心方法，并在实际案例中验证掌握程度。",
    },
  }));
  const edges: Edge[] = [];
  const add = (source: number, target: number, relation: string) => edges.push({ id: `${source}-${target}-${relation}`, source: `n${source}`, target: `n${target}`, label: relation === "prerequisite" ? "前置" : relation === "contains" ? "包含" : "关联", data: { relation } });
  if (count === 7) {
    [1, 2, 3].forEach(target => add(0, target, "contains"));
    [[1, 4], [2, 5], [3, 5], [4, 6], [5, 6]].forEach(([source, target]) => add(source, target, "prerequisite"));
  } else {
    const breadth = Math.max(3, Math.round(Math.sqrt(count)));
    for (let i = 1; i < count - 1; i++) {
      if (i <= breadth) add(0, i, "contains");
      if (i > breadth) {
        add(i - breadth, i, "prerequisite");
        if (i % 4 === 0) add(i - breadth - 1, i, "prerequisite");
      }
      if (i > 2 && i % 5 === 0) add(i - 2, i, "related");
    }
    add(1, 2, "prerequisite");
    add(2, 1, "prerequisite");
    add(2, 2, "related");
  }
  return { nodes, edges };
}

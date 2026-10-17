import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { StreamUI } from "@/components/chat/stream-ui";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { Button } from "@/components/ui/button";
import type { UISnapshot } from "@/lib/stream-ui";
import "@/index.css";

const base = (
  block_id: string,
  component: UISnapshot["component"],
  title: string,
): UISnapshot => ({
  protocol: "suip/0.1",
  block_id,
  component,
  revision: 0,
  state: "streaming",
  props: {
    title,
    description: "",
    chart_type: "line",
    columns: [],
    unit: "",
    source_note: "虚构示例数据 · 仅用于演示交互",
  },
  rows: [],
  nodes: [],
  edges: [],
  items: [],
});
const chart = base("activity", "chart", "五个月，一步步看见变化");
chart.props.columns = [
  { key: "commits", label: "提交量" },
  { key: "issues", label: "Issue" },
];
chart.props.description = "点击图例切换指标，或切换为柱状图进行比较。";
const graph = base("people", "graph", "谁在推动项目？");
graph.props.description = "拖动节点调整位置，选择一个节点查看它的关联。";
const metrics = base("summary", "metric", "进展一览");
const timeline = base("milestones", "timeline", "从想法到上线");
const table = base("details", "table", "月度明细");
table.props.columns = chart.props.columns;
type DemoPart =
  | { type: "text"; id: string; content: string }
  | { type: "ui"; id: string; data: UISnapshot };
type Step = { text: string; id: string } | { data: UISnapshot };
const steps: Step[] = [
  {
    id: "intro",
    text: "我们先看项目活跃度，再把数据与参与者联系起来。下面的图表会随着回答逐步填充，你可以随时切换指标。",
  },
  { data: chart },
  ...[38, 55, 49, 74, 96].map((_, index) => ({
    data: {
      ...chart,
      revision: index + 1,
      rows: [38, 55, 49, 74, 96].slice(0, index + 1).map((value, i) => ({
        id: `m${i}`,
        label: `${i + 1}月`,
        values: { commits: value, issues: [8, 12, 9, 18, 21][i] },
      })),
    },
  })),
  {
    data: {
      ...chart,
      revision: 6,
      state: "completed",
      rows: [38, 55, 49, 74, 96].map((value, i) => ({
        id: `m${i}`,
        label: `${i + 1}月`,
        values: { commits: value, issues: [8, 12, 9, 18, 21][i] },
      })),
    },
  },
  {
    id: "insight",
    text: "五月提交量达到 96 次。三月出现短暂回落，随后连续两个月增长。点击下方人物节点，可以进一步查看谁参与了哪些工作。",
  },
  { data: graph },
  {
    data: {
      ...graph,
      revision: 1,
      nodes: [
        {
          id: "ming",
          label: "小明",
          description: "负责前端实现与图表交互。",
          group: "开发",
        },
        {
          id: "ui",
          label: "流式 UI",
          description: "声明式组件与增量数据。",
          group: "项目",
        },
      ],
    },
  },
  {
    data: {
      ...graph,
      revision: 2,
      state: "completed",
      nodes: [
        {
          id: "ming",
          label: "小明",
          description: "负责前端实现与图表交互。",
          group: "开发",
        },
        {
          id: "ui",
          label: "流式 UI",
          description: "声明式组件与增量数据。",
          group: "项目",
        },
        {
          id: "lin",
          label: "小林",
          description: "负责协议校验和后端事件流。",
          group: "开发",
        },
        {
          id: "release",
          label: "五月发布",
          description: "图表与关系图正式进入对话页面。",
          group: "事件",
        },
      ],
      edges: [
        { id: "a", source: "ming", target: "ui", label: "构建" },
        { id: "b", source: "lin", target: "ui", label: "支持" },
        { id: "c", source: "ui", target: "release", label: "交付" },
      ],
    },
  },
  {
    id: "summary-text",
    text: "把趋势、人物关系和阶段成果放在一起，我们就能从同一条回答里获得完整的项目视图。",
  },
  {
    data: {
      ...metrics,
      state: "completed",
      items: [
        {
          id: "total",
          label: "总提交量",
          value: 312,
          description: "覆盖 1 月至 5 月",
          date: "",
        },
        {
          id: "peak",
          label: "单月最高",
          value: 96,
          description: "5 月创下阶段新高",
          date: "",
        },
        {
          id: "team",
          label: "协作人数",
          value: 2,
          description: "前端与协议并行推进",
          date: "",
        },
      ],
    },
  },
  {
    data: {
      ...timeline,
      state: "completed",
      items: [
        {
          id: "idea",
          label: "确定组件协议",
          date: "1 月",
          description: "定义稳定 ID、数据结构和组件目录。",
          value: null,
        },
        {
          id: "build",
          label: "实现增量渲染",
          date: "3 月",
          description: "空骨架先出现，随后按记录逐步填充。",
          value: null,
        },
        {
          id: "ship",
          label: "发布交互图表",
          date: "5 月",
          description: "在同一条回复中交错显示文本与交互控件。",
          value: null,
        },
      ],
    },
  },
  {
    data: {
      ...table,
      state: "completed",
      rows: [38, 55, 49, 74, 96].map((value, i) => ({
        id: `m${i}`,
        label: `${i + 1}月`,
        values: { commits: value, issues: [8, 12, 9, 18, 21][i] },
      })),
    },
  },
];

const compatibilityMetrics = steps.flatMap((step) =>
  "data" in step && step.data.component === "metric" ? [step.data] : [],
)[0]!;

export function Demo() {
  const [position, setPosition] = useState(0);
  const [paused, setPaused] = useState(false);
  const [dark, setDark] = useState(false);
  const [action, setAction] = useState("");
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);
  useEffect(() => {
    if (paused || position >= steps.length) return;
    const timer = window.setTimeout(
      () => setPosition((p) => p + 1),
      position === 0 ? 300 : 650,
    );
    return () => window.clearTimeout(timer);
  }, [position, paused]);
  const parts: DemoPart[] = [];
  for (const step of steps.slice(0, position)) {
    if ("text" in step)
      parts.push({ type: "text", id: step.id, content: step.text });
    else {
      const current = parts.find((part) => part.id === step.data.block_id);
      if (current?.type === "ui") current.data = step.data;
      else parts.push({ type: "ui", id: step.data.block_id, data: step.data });
    }
  }
  return (
    <main
      style={{
        maxWidth: 780,
        margin: "0 auto",
        padding: "48px 24px 100px",
        fontFamily: "var(--font-sans)",
      }}
    >
      <nav
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 20,
          color: "var(--muted-foreground)",
          fontSize: 12,
        }}
      >
        <span>LearnGraph / 交互图表</span>
        <Button
          variant="ghost"
          size="sm"
          type="button"
          onClick={() => setDark((value) => !value)}
        >
          {dark ? "浅色外观" : "深色外观"}
        </Button>
      </nav>
      <h1
        style={{
          fontSize: 32,
          fontWeight: 600,
          letterSpacing: "-.04em",
          margin: "36px 0 12px",
        }}
      >
        让回答，成为可以探索的界面。
      </h1>
      <p style={{ color: "var(--muted-foreground)", lineHeight: 1.8 }}>
        文本、图表和结构化信息，按回答顺序逐步出现。这里使用本地虚构数据演示；解释按钮仅显示交互事件，正式对话会提交给
        AI。
      </p>
      <div
        style={{
          display: "flex",
          gap: 16,
          alignItems: "center",
          margin: "22px 0 36px",
          fontSize: 13,
        }}
      >
        <Button
          variant="outline"
          size="sm"
          type="button"
          onClick={() => {
            setPosition(0);
            setPaused(false);
            setAction("");
            setEpoch((e) => e + 1);
          }}
        >
          重新播放
        </Button>
        <Button
          variant="ghost"
          size="sm"
          type="button"
          onClick={() => setPaused((value) => !value)}
        >
          {paused ? "继续" : "暂停"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          type="button"
          onClick={() => setPosition(steps.length)}
        >
          查看完整回答
        </Button>
        <span style={{ marginLeft: "auto", color: "var(--muted-foreground)" }}>
          {position === steps.length ? "回答已完成" : "正在生成…"}
        </span>
      </div>
      <div
        style={{ borderTop: "1px solid var(--border)", paddingTop: 28 }}
        key={epoch}
      >
        {parts.map((part) =>
          part.type === "text" ? (
            <p key={part.id} style={{ lineHeight: 1.9, margin: "18px 0" }}>
              {part.content}
            </p>
          ) : (
            <StreamUI
              key={part.id}
              partId={part.id}
              data={part.data}
              onAction={(event) => setAction(JSON.stringify(event, null, 2))}
            />
          ),
        )}
      </div>
      <details style={{ marginTop: 32 }}>
        <summary style={{ cursor: "pointer", fontSize: 13 }}>
          与已有控件并排检查
        </summary>
        <p
          style={{
            color: "var(--muted-foreground)",
            fontSize: 12,
            marginTop: 12,
          }}
        >
          相同虚构数据，共用系统卡片、指标配色和操作按钮。下方输入框仍使用原有提交事件。
        </p>
        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "repeat(auto-fit, minmax(min(300px, 100%), 1fr))",
            gap: 14,
            alignItems: "start",
          }}
        >
          <TrustedComponentRenderer
            fallbackId="compat-legacy-metric"
            data={{
              component_type: "metric_card",
              schema_version: "1.0",
              props: {
                title: "已有指标卡",
                description: "相同的虚构示例数据",
                metrics: compatibilityMetrics.items
                  .slice(0, 2)
                  .map((item) => ({
                    id: item.id,
                    label: item.label,
                    value: item.value,
                    hint: item.description,
                  })),
              },
              allowed_events: [],
            }}
          />
          <StreamUI
            partId="compat-stream-metric"
            data={{
              ...compatibilityMetrics,
              props: {
                ...compatibilityMetrics.props,
                title: "流式指标卡",
                description: "相同的虚构示例数据",
              },
              items: compatibilityMetrics.items.slice(0, 2),
            }}
            onAction={(event) => setAction(JSON.stringify(event, null, 2))}
          />
        </div>
        <TrustedComponentRenderer
          fallbackId="compat-legacy-input"
          data={{
            component_type: "fill_blank",
            schema_version: "1.0",
            props: {
              title: "已有输入控件",
              placeholder: "输入一段文字检查旧控件",
              submit_label: "提交答案",
            },
            allowed_events: ["submit"],
          }}
          onAction={(event) => setAction(JSON.stringify(event, null, 2))}
        />
      </details>
      {action && (
        <aside
          role="status"
          style={{ background: "var(--muted)", padding: 20, borderRadius: 12 }}
        >
          <strong>已产生交互事件（本地演示）</strong>
          <pre style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>{action}</pre>
        </aside>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<Demo />);

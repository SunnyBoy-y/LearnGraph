// 独立图表组件：把 recharts 循环依赖子树与 chat 主渲染路径隔离。
// recharts 的 ESM 循环导入会被 rolldown 静态合并时求值顺序破坏（module-eval
// TDZ crash，同 streamdown 问题）；本组件经 message-part-renderer 动态 import，
// 使 recharts 只进入按需加载的独立 chunk。recharts 本体在 ChartPart 内部二次
// 动态 import——只有真正渲染图表时才求值 recharts 模块。
import { useEffect, useState } from "react";

export type PartData = Record<string, unknown> | undefined;

type RechartsModule = typeof import("recharts");

function EmptyPart({ children }: { children: string }) {
  return (
    <div className="message-part-empty" role="status">
      {children}
    </div>
  );
}

export function ChartPart({ data }: { data: PartData }) {
  const [recharts, setRecharts] = useState<RechartsModule | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("recharts")
      .then((module) => {
        if (!cancelled) setRecharts(module);
      })
      .catch(() => {
        if (!cancelled) setRecharts(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!recharts) {
    return <EmptyPart>图表组件加载中…</EmptyPart>;
  }

  const {
    Area,
    AreaChart,
    Bar,
    BarChart,
    Cell,
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip: ChartTooltip,
    XAxis,
    YAxis,
  } = recharts;

  const chartType =
    data?.chart_type === "pie" ||
    data?.chart_type === "line" ||
    data?.chart_type === "bar"
      ? data.chart_type
      : null;
  const labels = Array.isArray(data?.labels)
    ? data.labels.filter((item): item is string => typeof item === "string")
    : [];
  const series = Array.isArray(data?.series)
    ? data.series.flatMap((item, index) => {
        if (!item || typeof item !== "object" || Array.isArray(item)) return [];
        const record = item as Record<string, unknown>;
        const values = Array.isArray(record.values)
          ? record.values.filter(
              (value): value is number =>
                typeof value === "number" && Number.isFinite(value),
            )
          : [];
        if (
          typeof record.name !== "string" ||
          values.length !== labels.length
        )
          return [];
        return [
          {
            key: `series_${index}`,
            name: record.name,
            values,
            color:
              typeof record.color === "string"
                ? record.color
                : `var(--chart-${(index % 5) + 1})`,
          },
        ];
      })
    : [];
  const structuredPoints = labels.map((label, index) => ({
    label,
    ...Object.fromEntries(
      series.map((item) => [item.key, item.values[index]]),
    ),
  }));
  const structuredValid =
    chartType !== null && labels.length > 0 && series.length > 0;

  if (structuredValid) {
    const common = (
      <>
        <ChartTooltip
          contentStyle={{
            background: "var(--card)",
            borderColor: "var(--border)",
            borderRadius: 10,
            fontSize: 12,
          }}
        />
        {data?.show_legend !== false ? <Legend /> : null}
      </>
    );
    return (
      <section
        aria-label={
          typeof data?.title === "string" ? data.title : "数据图表"
        }
        className="message-chart"
      >
        <div className="message-chart__heading">
          <strong>
            {typeof data?.title === "string" ? data.title : "数据图表"}
          </strong>
          <span>{typeof data?.summary === "string" ? data.summary : `${labels.length} 个数据点`}</span>
        </div>
        <div className="message-chart__canvas">
          <ResponsiveContainer height="100%" width="100%">
            {chartType === "pie" ? (
              <PieChart>
                {common}
                <Pie
                  data={structuredPoints}
                  dataKey={series[0].key}
                  label={data?.show_values === true}
                  nameKey="label"
                >
                  {structuredPoints.map((point, index) => (
                    <Cell
                      fill={
                        index === 0
                          ? series[0].color
                          : `var(--chart-${(index % 5) + 1})`
                      }
                      key={`${String(point.label)}-${index}`}
                    />
                  ))}
                </Pie>
              </PieChart>
            ) : chartType === "bar" ? (
              <BarChart data={structuredPoints}>
                <CartesianGrid
                  stroke="var(--border)"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  axisLine={false}
                  dataKey="label"
                  fontSize={11}
                  tickLine={false}
                />
                <YAxis axisLine={false} fontSize={11} tickLine={false} width={36} />
                {common}
                {series.map((item) => (
                  <Bar
                    dataKey={item.key}
                    fill={item.color}
                    key={item.key}
                    name={item.name}
                  />
                ))}
              </BarChart>
            ) : (
              <LineChart data={structuredPoints}>
                <CartesianGrid
                  stroke="var(--border)"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  axisLine={false}
                  dataKey="label"
                  fontSize={11}
                  tickLine={false}
                />
                <YAxis axisLine={false} fontSize={11} tickLine={false} width={36} />
                {common}
                {series.map((item) => (
                  <Line
                    dataKey={item.key}
                    dot
                    key={item.key}
                    name={item.name}
                    stroke={item.color}
                    strokeWidth={2}
                    type="monotone"
                  />
                ))}
              </LineChart>
            )}
          </ResponsiveContainer>
        </div>
      </section>
    );
  }

  const points = Array.isArray(data?.points)
    ? data.points.filter(
        (item): item is Record<string, number | string> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
  const xKey = typeof data?.x_key === "string" ? data.x_key : "";
  const yKey = typeof data?.y_key === "string" ? data.y_key : "";
  const valid =
    points.length > 0 &&
    xKey &&
    yKey &&
    points.every((point) => typeof point[yKey] === "number" && xKey in point);

  if (!valid) return <EmptyPart>图表数据不完整，未生成虚构趋势。</EmptyPart>;
  return (
    <section className="message-chart" aria-label={typeof data?.title === "string" ? data.title : "数据图表"}>
      <div className="message-chart__heading">
        <strong>{typeof data?.title === "string" ? data.title : "数据图表"}</strong>
        <span>{points.length} 个真实数据点</span>
      </div>
      <div className="message-chart__canvas">
        <ResponsiveContainer height="100%" width="100%">
          <AreaChart data={points}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis axisLine={false} dataKey={xKey} fontSize={11} tickLine={false} />
            <YAxis axisLine={false} fontSize={11} tickLine={false} width={32} />
            <ChartTooltip
              contentStyle={{
                background: "var(--card)",
                borderColor: "var(--border)",
                borderRadius: 10,
                fontSize: 12,
              }}
            />
            <Area
              dataKey={yKey}
              fill="color-mix(in srgb, var(--primary) 18%, transparent)"
              stroke="var(--primary)"
              strokeWidth={2}
              type="monotone"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

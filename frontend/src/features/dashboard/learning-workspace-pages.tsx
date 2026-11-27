import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { ActivityHeatmap } from "@/components/shared/activity-heatmap";
import { PageFrame } from "@/components/shared/page-elements";
import "./dashboard.css";

export { LearningPlanPage } from "./learning-plan-page";

function HomeLink({ base }: { base: string }) {
  return <Link className="study-text-link self-start" to={`${base}/home`}><ArrowLeft aria-hidden="true" size={15} />我的学习空间</Link>;
}

export function LearningReportPage() {
  const { workspaceId = "" } = useParams();
  return <PageFrame className="study-home">
    <HomeLink base={`/w/${workspaceId}`} />
    <header><h1 data-page-title className="text-2xl font-semibold">学习报告</h1>
      <p className="mt-1 text-sm text-muted-foreground">活动记录包含创建会话、计划行动和完成行动，反映使用活跃度，不代表知识掌握程度。</p></header>
    <div className="study-card study-activity"><ActivityHeatmap variant="report" /></div>
  </PageFrame>;
}

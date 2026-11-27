import { lazy, Suspense } from "react";
import { Navigate, useParams, useSearchParams } from "react-router-dom";

const ActivityPage = lazy(() => import("./node-learning-page").then(({ NodeLearningPage }) => ({ default: NodeLearningPage })));

/** Keep old bookmarks useful without fetching or generating learning packages. */
export function NodeLearningPage() {
  const { workspaceId = "", nodeId = "" } = useParams();
  const [params] = useSearchParams();
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  const returnSession = params.get("returnSession");
  const graphId = params.get("graphId");
  const destination = returnSession
    ? `${base}/chat/${encodeURIComponent(returnSession)}`
    : `${base}/graphs${graphId ? `/${encodeURIComponent(graphId)}` : ""}${nodeId ? `?node=${encodeURIComponent(nodeId)}` : ""}`;

  if (params.get("mode") === "activity" && (params.has("attempt") || ["lab", "exam"].includes(params.get("tab") ?? ""))) {
    return <Suspense fallback={<p role="status">正在打开学习活动…</p>}><ActivityPage /></Suspense>;
  }
  return <Navigate replace to={destination} />;
}

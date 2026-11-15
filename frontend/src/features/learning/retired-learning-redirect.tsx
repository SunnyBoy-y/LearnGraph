import { Navigate, useParams, useSearchParams } from "react-router-dom";

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

  return <Navigate replace to={destination} />;
}

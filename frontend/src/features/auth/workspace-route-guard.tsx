import { useEffect, useRef, useState, type ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import { Navigate, useLocation, useParams } from "react-router-dom";

import { useAuth } from "./auth-context-value";

export function RouteLoading() {
  return <main aria-live="polite" className="grid min-h-svh place-items-center bg-background"><div className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />正在载入工作区…</div></main>
}

/**
 * Keeps the workspace named in the URL and the signed-in account's active
 * workspace in sync.
 *
 * Denial is recorded as *which* workspace was refused, never as a boolean flag.
 * The fallback is derived from the active workspace, so once the app sits on
 * `/w/<active>/home` a boolean denial would keep rendering a `<Navigate>` aimed
 * at the URL it is already on: React Router still replaces the entry, the guard
 * re-renders, and the page stays empty with no error and nothing left to
 * re-render it (only a refresh resets the guard). A workspace id is
 * self-invalidating instead — a refused workspace is by definition not the
 * active one, so a URL that names the active workspace can never stay blank.
 */
export function WorkspaceRouteGuard({ children }: { children: ReactNode }) {
  const { workspaceId: activeWorkspaceId, setWorkspaceId } = useAuth();
  const { workspaceId = "" } = useParams();
  const location = useLocation();
  const [deniedWorkspaceId, setDeniedWorkspaceId] = useState<string | null>(null);
  const selectionRequestRef = useRef(0);
  const pendingSelectionRef = useRef<string | null>(null);

  useEffect(() => {
    setDeniedWorkspaceId(null);
    if (!workspaceId || workspaceId === activeWorkspaceId) return;
    // The auth value identity churns while the session hydrates (session and
    // workspace-name updates land at different times), which re-runs this
    // effect for the same mismatch. One in-flight selection is enough;
    // duplicates only add late 403s that can outlive the redirect below.
    if (pendingSelectionRef.current === workspaceId) return;
    pendingSelectionRef.current = workspaceId;
    const requestId = selectionRequestRef.current + 1;
    selectionRequestRef.current = requestId;
    void setWorkspaceId(workspaceId)
      .catch(() => {
        // A slower, earlier selection must never deny the workspace that is on
        // screen right now.
        if (requestId === selectionRequestRef.current) setDeniedWorkspaceId(workspaceId);
      })
      .finally(() => {
        if (pendingSelectionRef.current === workspaceId) pendingSelectionRef.current = null;
      });
  }, [activeWorkspaceId, setWorkspaceId, workspaceId]);

  // Only a workspace this account cannot select can stay denied; a refusal for
  // the active workspace is stale by definition and must not block rendering.
  const denied =
    Boolean(workspaceId) &&
    workspaceId !== activeWorkspaceId &&
    deniedWorkspaceId === workspaceId;
  if (!workspaceId || denied) {
    const fallback = `/w/${activeWorkspaceId}/home`;
    // Never redirect to the URL we are already on: that renders nothing, and
    // nothing is left to re-render it.
    return location.pathname === fallback ? <RouteLoading /> : <Navigate replace to={fallback} />;
  }
  if (workspaceId !== activeWorkspaceId) return <RouteLoading />;
  return children;
}

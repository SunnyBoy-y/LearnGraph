import { useRef, type ReactNode } from "react"
import { useLocation } from "react-router-dom"

/**
 * Keeps navigation legible without animating the workspace chrome or blocking
 * the incoming route behind an exit animation. The shell/sidebar stay mounted
 * while the new surface enters immediately with a short CSS keyframe
 * (`backwards` fill-mode leaves resting `transform: none` so fixed/sticky
 * descendants never inherit a containing block).
 */
export function RouteMotion({ children }: { children: ReactNode }) {
  const location = useLocation()
  // Graph query-string changes (for example selecting a node with `?node=`)
  // update the current surface in place. Treating them as a new route would
  // unmount the old outlet before mounting it again, flashing the graph and
  // losing React Flow camera state.
  const isGraphWorkspaceRoute = /^\/w\/[^/]+\/graphs(?:\/[^/]+)?$/.test(
    location.pathname,
  )
  // Chat owns live stream state and resets session-specific UI itself. Keep
  // that canvas mounted across /chat/new -> /chat/:id and session switches;
  // replacing it here strands SSE updates in the outgoing component until
  // the durable history is fetched after generation finishes.
  const chatWorkspacePath = location.pathname.match(/^(\/w\/[^/]+\/chat)\/[^/]+$/)?.[1]
  const routeKey = chatWorkspacePath ?? (isGraphWorkspaceRoute
    ? location.pathname
    : `${location.pathname}${location.search}`)

  const previousKeyRef = useRef(routeKey)
  const hasNavigatedRef = useRef(false)
  if (previousKeyRef.current !== routeKey) {
    previousKeyRef.current = routeKey
    hasNavigatedRef.current = true
  }

  return (
    <div
      className="workspace-route-motion"
      data-route-animate={hasNavigatedRef.current ? "true" : undefined}
      key={routeKey}
    >
      {children}
    </div>
  )
}

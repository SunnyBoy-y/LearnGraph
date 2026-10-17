import type { ReactNode } from "react"
import { AnimatePresence, motion } from "motion/react"
import { useReducedMotionPreference } from "@/lib/motion-preferences"
import { useLocation } from "react-router-dom"

/**
 * Keeps navigation legible without animating the workspace chrome. The small
 * vertical offset gives the next view a direction, while the shell/sidebar
 * stay mounted so a route change never feels like a full reload. Reduced
 * motion keeps the same handoff as a short opacity-only cross-fade.
 */
const routeVariants = {
  initial: {
    opacity: 0,
    y: 10,
  },
  enter: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.2,
      ease: [0.16, 1, 0.3, 1],
    },
  },
  exit: {
    opacity: 0,
    y: -6,
    transition: {
      duration: 0.12,
      ease: [0.7, 0, 0.84, 0],
    },
  },
  reducedInitial: {
    opacity: 0,
    y: 0,
  },
  reduced: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.14, ease: "linear" },
  },
  reducedExit: {
    opacity: 0,
    y: 0,
    transition: { duration: 0.1, ease: "linear" },
  },
} as const

export function RouteMotion({ children }: { children: ReactNode }) {
  const location = useLocation()
  const prefersReducedMotion = useReducedMotionPreference()
  // Graph query-string changes (for example selecting a node with `?node=`)
  // update the current surface in place. Treating them as a new route causes
  // AnimatePresence to unmount the old outlet before mounting it again, which
  // flashes the graph and makes React Flow lose its camera state. Keep the
  // existing query-sensitive transitions on other workspace surfaces.
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

  return (
    <AnimatePresence initial={false} mode="wait">
      <motion.div
        animate={prefersReducedMotion ? "reduced" : "enter"}
        className="workspace-route-motion"
        exit={prefersReducedMotion ? "reducedExit" : "exit"}
        initial={prefersReducedMotion ? "reducedInitial" : "initial"}
        key={routeKey}
        // Keep the resting state at `transform: none`: an identity transform
        // would create a containing block for fixed/sticky descendants in
        // chat and document routes.
        transformTemplate={({ y }, generated) => {
          const numericY =
            typeof y === "number" ? y : Number.parseFloat(String(y ?? "0"));
          return numericY === 0 ? "none" : generated;
        }}
        variants={routeVariants}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  )
}

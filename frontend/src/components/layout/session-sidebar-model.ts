import { selectionExplanationParentMap } from '@/features/chat/selection-explanation'
import { getSessionActivitySnapshot, sortSidebarSessions } from '@/lib/session-activity'

export type SidebarSession = {
  id: string;
  title: string;
  pinned: boolean;
  status: string;
  updated_at?: string | null;
  parent_session_id?: string | null;
  project_id?: string | null;
  session_kind?: string | null;
  has_children?: boolean;
  /** Nested side threads (e.g. 划词解释) shown under this parent as a folder. */
  children?: SidebarSession[];
};
export type SidebarProject = {
  id: string;
  title: string;
  status: string;
  sessions: SidebarSession[];
  /** Old stored projects omit graph bindings and remain valid. */
  graphId?: string;
  graphTitle?: string;
  goalId?: string;
};

export function toSidebarSession(session: {
  id: string;
  title: string;
  pinned: boolean;
  status: string;
  updated_at?: string | null;
  parent_session_id?: string | null;
  project_id?: string | null;
  session_kind?: string | null;
  has_children?: boolean;
}): SidebarSession {
  return {
    id: session.id,
    title: session.title || "新会话",
    pinned: session.pinned,
    status: session.status,
    updated_at: session.updated_at ?? null,
    parent_session_id: session.parent_session_id ?? null,
    project_id: session.project_id ?? null,
    session_kind: session.session_kind ?? null,
    has_children: session.has_children ?? false,
  };
}

/**
 * Nest side threads (划词解释 / branches) under their parent so the sidebar
 * can treat the parent as a collapsible folder. Orphan children whose parent
 * is missing from the current list stay as roots (e.g. parent archived).
 *
 * Also consults local 划词解释 history so sessions created before
 * parent_session_id was written still fold under their source conversation.
 */
export function nestSidebarSessions(
  sessions: SidebarSession[],
  activity: ReturnType<typeof getSessionActivitySnapshot>,
): SidebarSession[] {
  if (!sessions.length) return sessions;
  const localParents = selectionExplanationParentMap();
  const byId = new Map(
    sessions.map((session) => [
      session.id,
      {
        ...session,
        parent_session_id:
          session.parent_session_id || localParents[session.id] || null,
        children: [] as SidebarSession[] | undefined,
      },
    ]),
  );
  const roots: SidebarSession[] = [];
  for (const session of byId.values()) {
    const parentId = session.parent_session_id;
    if (parentId && byId.has(parentId) && parentId !== session.id) {
      byId.get(parentId)!.children!.push(session);
    } else {
      roots.push(session);
    }
  }
  for (const session of byId.values()) {
    if (session.children?.length) {
      session.children = sortSidebarSessions(session.children, activity);
    } else {
      session.children = undefined;
    }
  }
  return sortSidebarSessions(roots, activity);
}

/** Flatten nested sidebar sessions for selection / deletion counts. */
export function flattenSidebarSessions(sessions: SidebarSession[]): SidebarSession[] {
  const out: SidebarSession[] = [];
  for (const session of sessions) {
    out.push(session);
    if (session.children?.length) out.push(...flattenSidebarSessions(session.children));
  }
  return out;
}

/**
 * 1-based position among the row's legacy 「分支.」 siblings, or undefined
 * when nothing in the group carries a legacy prefix.
 */
export function legacyBranchSiblingIndex(
  siblings: SidebarSession[],
  index: number,
): number | undefined {
  let count = 0;
  for (let cursor = 0; cursor <= index && cursor < siblings.length; cursor += 1) {
    if (/^(?:分支\.)+/.test(siblings[cursor].title.trim())) count += 1;
  }
  return count || undefined;
}

/**
 * Branches created before the backend switched to 「原会话名（n）」 titles still
 * carry a 「分支.」 prefix. Sidebar rows are no longer indented, so legacy rows
 * are displayed with the sibling index instead of the old prefix.
 */
export function branchRowDisplayTitle(
  title: string,
  legacyBranchIndex?: number,
): string {
  const match = /^(?:分支\.)+(.*)$/.exec(title.trim());
  if (!match) return title;
  const base = match[1].trim() || "未命名会话";
  return legacyBranchIndex ? `${base}（${legacyBranchIndex}）` : base;
}

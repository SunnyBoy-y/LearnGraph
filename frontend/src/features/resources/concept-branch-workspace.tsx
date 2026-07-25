import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { BookOpenText, ExternalLink, GripHorizontal, Merge, Network, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { listSessionMessages, listSessions, promoteConceptBranch } from "@/api/sessions";
import { createMemory } from "@/api/memory";
import { Button } from "@/components/ui/button";
import type { FileRecord } from "@/types/files";
import type { PendingDocumentSelection } from "./document-chat-panel";
import { DocumentChatPanel } from "./document-chat-panel";

export function ConceptBranchWorkspace({
  branchIds,
  file,
  onCloseBranch,
  selection,
  workspaceId,
}: {
  branchIds: string[];
  file: FileRecord;
  onCloseBranch: (id: string) => void;
  selection: PendingDocumentSelection | null;
  workspaceId: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: listSessions });
  const [activeId, setActiveId] = useState(branchIds.at(-1) ?? "");
  const [position, setPosition] = useState({ x: 72, y: 92 });
  const dragRef = useRef<{ x: number; y: number; startX: number; startY: number } | null>(null);
  const branches = useMemo(
    () => branchIds.map((id) => sessions.data?.find((session) => session.id === id)).filter(Boolean),
    [branchIds, sessions.data],
  );
  const effectiveActiveId = branchIds.includes(activeId) ? activeId : branchIds.at(-1) ?? "";
  const active = sessions.data?.find((session) => session.id === effectiveActiveId);
  const messages = useQuery({
    queryKey: ["messages", effectiveActiveId],
    queryFn: () => listSessionMessages(effectiveActiveId),
    enabled: Boolean(effectiveActiveId),
  });
  const rawAnchor = active?.context_capsule?.anchor;
  const anchor =
    rawAnchor && typeof rawAnchor === "object"
      ? (rawAnchor as Record<string, unknown>)
      : undefined;
  const branchSelection = (
    anchor &&
    typeof anchor.file_id === "string" &&
    typeof anchor.document_revision_id === "string" &&
    typeof anchor.chunk_id === "string" &&
    typeof anchor.selected_text === "string"
      ? {
          file_id: anchor.file_id,
          document_revision_id: anchor.document_revision_id,
          chunk_id: anchor.chunk_id,
          locator: typeof anchor.locator === "object" && anchor.locator
            ? anchor.locator as Record<string, unknown>
            : {},
          locator_label: typeof anchor.source_locator === "string" ? anchor.source_locator : file.original_name,
          selected_text: anchor.selected_text,
        }
      : selection
  );
  const actualSummary = [...(messages.data ?? [])]
    .reverse()
    .find((message) => message.role === "assistant" && message.status === "completed")
    ?.content.trim();

  const promote = useMutation({
    mutationFn: ({ action, summary }: { action: 'merge_summary' | 'standalone'; summary?: string }) =>
      promoteConceptBranch(effectiveActiveId, { action, summary }),
    onSuccess: (session, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (variables.action === "standalone") {
        navigate(`/w/${workspaceId}/chat/${session.id}`);
      } else {
        toast.success("确认摘要已加入主会话");
      }
    },
    onError: (error) => toast.error(error.message),
  });
  const saveNote = useMutation({
    mutationFn: () => createMemory({
      title: `概念解释：${branchSelection?.selected_text ?? active?.title ?? "未命名概念"}`,
      content: actualSummary ?? "",
      namespace: "session",
      session_id: effectiveActiveId,
      zone: "topics",
      record_kind: "concept_branch_note",
      source: "user_confirmed_concept_branch",
      source_ids: [effectiveActiveId, file.id],
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
      toast.success("概念解释已保存为笔记");
    },
    onError: (error) => toast.error(error.message),
  });

  if (!effectiveActiveId || !branchSelection) return null;

  return (
    <motion.section
      animate={{ opacity: 1, scale: 1, x: position.x, y: position.y }}
      aria-label="概念解释分支"
      className="concept-branch-window"
      initial={{ opacity: 0, scale: 0.96, x: position.x, y: position.y + 12 }}
      transition={{ duration: 0.18 }}
    >
      <header
        className="concept-branch-window__drag"
        onPointerDown={(event) => {
          if ((event.target as Element).closest("button")) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { x: position.x, y: position.y, startX: event.clientX, startY: event.clientY };
        }}
        onPointerMove={(event) => {
          if (!dragRef.current) return;
          setPosition({
            x: Math.max(8, dragRef.current.x + event.clientX - dragRef.current.startX),
            y: Math.max(8, dragRef.current.y + event.clientY - dragRef.current.startY),
          });
        }}
        onPointerUp={() => { dragRef.current = null; }}
      >
        <GripHorizontal className="size-4" />
        <div className="min-w-0 flex-1">
          <strong className="block truncate">{active?.title ?? branchSelection.selected_text}</strong>
          <span>关联：{file.original_name} · 独立模式</span>
        </div>
        <Button aria-label="关闭当前概念分支" onClick={() => onCloseBranch(effectiveActiveId)} size="icon-xs" variant="ghost"><X /></Button>
      </header>
      {branchIds.length > 1 ? (
        <nav aria-label="概念分支切换" className="concept-branch-tabs">
          {branchIds.map((id, index) => {
            const branch = branches.find((item) => item?.id === id);
            return (
              <button aria-selected={id === effectiveActiveId} key={id} onClick={() => setActiveId(id)} role="tab" type="button">
                {branch?.title ?? `概念 ${index + 1}`}
              </button>
            );
          })}
        </nav>
      ) : null}
      <div className="concept-branch-stack">
        <AnimatePresence initial={false} mode="popLayout">
          <motion.div
            animate={{ opacity: 1, rotate: 0, scale: 1, x: 0 }}
            className="concept-branch-stack__active"
            exit={{ opacity: 0, rotate: -1.5, scale: 0.97, x: -28 }}
            initial={{ opacity: 0, rotate: 1.2, scale: 0.98, x: 28 }}
            key={effectiveActiveId}
            transition={{ duration: 0.2 }}
          >
            <DocumentChatPanel
              autoSubmitSeed
              file={file}
              onClearSelection={() => undefined}
              onSessionChange={() => undefined}
              questionSeed={{ id: effectiveActiveId, text: `请用百科条目式结构解释“${branchSelection.selected_text}”，先给出定义，再说明它在当前原文中的含义、关键点和一个例子。` }}
              selection={branchSelection}
              sessionId={effectiveActiveId}
              workspaceId={workspaceId}
            />
          </motion.div>
        </AnimatePresence>
      </div>
      <footer className="concept-branch-actions">
        <Button
          disabled={promote.isPending || !actualSummary}
          onClick={() => promote.mutate({ action: "merge_summary", summary: `关于“${branchSelection.selected_text}”的补充结论：\n${actualSummary?.slice(0, 3_800)}` })}
          size="sm"
          variant="outline"
        ><Merge />加入主会话</Button>
        <Button disabled={saveNote.isPending || !actualSummary} onClick={() => saveNote.mutate()} size="sm" variant="ghost"><BookOpenText />保存为笔记</Button>
        <Button onClick={() => navigate(`/w/${workspaceId}/graphs`)} size="sm" variant="ghost"><Network />前往知识图谱</Button>
        <Button disabled={promote.isPending} onClick={() => promote.mutate({ action: "standalone" })} size="sm" variant="ghost"><ExternalLink />转为独立会话</Button>
      </footer>
    </motion.section>
  );
}

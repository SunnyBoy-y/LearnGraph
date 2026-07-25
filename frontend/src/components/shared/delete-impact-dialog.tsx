import { AlertTriangle } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Spinner } from "@/components/ui/spinner";
import type { DeleteImpact } from "@/types/workflow";

const resourceLabels: Record<string, string> = {
  action_item: "行动项",
  answer_record: "作答记录",
  chat_session: "会话",
  evidence: "证据",
  exercise: "练习",
  file: "文件",
  file_batch: "批量文件",
  file_reference: "文件引用",
  file_text_chunk: "文本切片",
  goal: "学习目标",
  graph: "知识图谱",
  graph_edge: "图谱连线",
  graph_node: "知识节点",
  graph_revision: "图谱修订",
  mastery_schedule: "复习计划",
  message: "消息",
  project: "项目",
  research_job: "研究任务",
  roadmap: "学习路线",
  source_link: "资料关联",
  source: "网页来源",
  source_citation: "来源引用",
  session_batch: "会话批次",
  mixed_batch: "批量删除",
  suggested_prompt_batch: "推荐问题",
};

const actionLabels: Record<string, string> = {
  delete: "永久删除",
  detach: "解除关联",
  preserve: "保留",
  preserve_history: "保留历史",
  unlink: "解除关联",
};

function impactLabel(value: string) {
  return resourceLabels[value] ?? value.replaceAll("_", " ");
}

export function DeleteImpactDialog({
  confirmLabel = "确认删除",
  error,
  impact,
  isConfirming = false,
  isLoading = false,
  objectLabel,
  onConfirm,
  onOpenChange,
  open,
  title,
}: {
  confirmLabel?: string;
  error?: string;
  impact?: DeleteImpact;
  isConfirming?: boolean;
  isLoading?: boolean;
  objectLabel: string;
  onConfirm: () => void | Promise<void>;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  title?: string;
}) {
  const visibleImpacts = impact?.impacts.filter((item) => item.count > 0) ?? [];

  return (
    <AlertDialog onOpenChange={onOpenChange} open={open}>
      <AlertDialogContent className="sm:max-w-md">
        <AlertDialogHeader>
          <AlertDialogMedia className="bg-destructive/10 text-destructive">
            <AlertTriangle />
          </AlertDialogMedia>
          <AlertDialogTitle>
            {title ?? `永久删除「${objectLabel}」？`}
          </AlertDialogTitle>
          <AlertDialogDescription>
            删除后无法恢复。请先核对服务端返回的实际影响，再点击确认。
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div
          aria-busy={isLoading}
          className="min-h-16 rounded-lg border border-border/70 bg-muted/35 px-3 py-2.5"
        >
          {isLoading ? (
            <div className="flex min-h-11 items-center gap-2 text-sm text-muted-foreground">
              <Spinner aria-label="正在检查删除影响" />
              正在从服务端检查关联数据…
            </div>
          ) : error ? (
            <p className="text-sm leading-6 text-destructive" role="alert">
              操作失败：{error}
            </p>
          ) : impact ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 truncate font-medium">
                  {impact.title || objectLabel}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {visibleImpacts.length
                    ? `${visibleImpacts.length} 类影响`
                    : "无额外关联"}
                </span>
              </div>
              {visibleImpacts.length ? (
                <ul className="divide-y divide-border/60 text-xs">
                  {visibleImpacts.map((item) => (
                    <li
                      className="flex items-center justify-between gap-3 py-1.5"
                      key={`${item.resource_type}-${item.action}`}
                    >
                      <span className="text-muted-foreground">
                        {impactLabel(item.resource_type)} ·{" "}
                        {actionLabels[item.action] ?? item.action}
                      </span>
                      <strong className="font-mono text-foreground">
                        {item.count}
                      </strong>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs leading-5 text-muted-foreground">
                  未发现额外关联数据；当前对象本身仍会永久删除。
                </p>
              )}
            </div>
          ) : null}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isConfirming}>
            取消，保留
          </AlertDialogCancel>
          <AlertDialogAction
            aria-busy={isConfirming}
            disabled={!impact || isLoading || isConfirming}
            onClick={(event) => {
              event.preventDefault();
              void onConfirm();
            }}
            variant="destructive"
          >
            {isConfirming ? <Spinner aria-label="正在删除" /> : null}
            {isConfirming ? "正在删除…" : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

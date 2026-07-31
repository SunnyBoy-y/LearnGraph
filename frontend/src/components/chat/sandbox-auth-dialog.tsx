import { useMutation } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { createSandboxDestructiveGrant } from "@/api/control";
import { Button } from "@/components/ui/button";

export type SandboxAuthRequest = {
  chatSessionId: string;
  paths: string[];
  action?: string;
  message?: string;
  sandboxSessionId?: string;
};

export function SandboxAuthDialog({
  request,
  onClose,
  onGranted,
}: {
  request: SandboxAuthRequest | null;
  onClose: () => void;
  onGranted?: () => void;
}) {
  const grant = useMutation({
    mutationFn: async () => {
      if (!request) throw new Error("missing request");
      // Grant the common parent prefix for listed paths (MVP: each path).
      const results = [];
      for (const path of request.paths) {
        results.push(
          await createSandboxDestructiveGrant({
            chat_session_id: request.chatSessionId,
            path_prefix: path.startsWith("work/") ? path : `work/${path}`,
            action: "delete_path",
            sandbox_session_id: request.sandboxSessionId,
            ttl_seconds: 300,
            reason: "user_confirmed_single_use_in_chat",
          }),
        );
      }
      return results;
    },
    onSuccess: () => {
      toast.success("已授权下一次匹配的删除命令，使用后立即失效");
      onGranted?.();
      onClose();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  if (!request) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-2xl border bg-background p-5 shadow-xl">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 size-5 text-amber-600" />
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold">需要授权删除会话工作区文件</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              {request.message ||
                "智能体请求删除会话沙箱内的文件。这只影响当前会话工作区，不会删除你电脑上的真实文件。"}
            </p>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                授权仅供下一次匹配这些路径的删除命令使用，使用后立即失效；路径或会话变化时必须重新确认。
              </p>
              <ul className="mt-3 list-disc space-y-1 pl-5 font-mono text-xs">
              {request.paths.map((path) => (
                <li key={path}>{path}</li>
              ))}
            </ul>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onClose} size="sm" variant="outline">
            拒绝
          </Button>
          <Button disabled={grant.isPending} onClick={() => grant.mutate()} size="sm">
            {grant.isPending ? "授权中…" : "允许本次"}
          </Button>
        </div>
      </div>
    </div>
  );
}

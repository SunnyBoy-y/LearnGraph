import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCode2, FolderPlus, RefreshCcw, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  deleteSkillFile,
  listSkillFiles,
  mkdirSkillPath,
  readSkillFile,
  runSkillSandbox,
  validateSkillPackage,
  writeSkillFile,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Skill } from "@/types/extensions";

/** Lightweight MVP editor for agent_skill_package file trees (D-081). */
export function SkillPackageEditor({ skill }: { skill: Skill }) {
  const queryClient = useQueryClient();
  const [selectedPath, setSelectedPath] = useState("SKILL.md");
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [newPath, setNewPath] = useState("");

  const tree = useQuery({
    queryKey: ["skill-files", skill.id],
    queryFn: () => listSkillFiles(skill.id),
  });

  const file = useQuery({
    queryKey: ["skill-file", skill.id, selectedPath],
    queryFn: () => readSkillFile(skill.id, selectedPath),
    enabled: Boolean(selectedPath),
  });

  useEffect(() => {
    if (file.data && !dirty) {
      setDraft(file.data.content);
    }
  }, [file.data, dirty]);

  const files = useMemo(
    () =>
      (tree.data?.files ?? []).filter((item) => !item.is_directory).map(
        (item) => item.relative_path,
      ),
    [tree.data],
  );

  const save = useMutation({
    mutationFn: () =>
      writeSkillFile(
        skill.id,
        selectedPath,
        draft,
        tree.data?.content_hash ?? skill.content_hash ?? null,
      ),
    onSuccess: (result) => {
      setDirty(false);
      toast.success(
        result.reauthorization_required
          ? "已保存；内容变更，需重新授权"
          : "已保存",
      );
      void queryClient.invalidateQueries({ queryKey: ["skill-files", skill.id] });
      void queryClient.invalidateQueries({
        queryKey: ["skill-file", skill.id, selectedPath],
      });
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      void queryClient.invalidateQueries({ queryKey: ["skill", skill.id] });
    },
    onError: (error) => toast.error(error.message),
  });

  const remove = useMutation({
    mutationFn: () => deleteSkillFile(skill.id, selectedPath),
    onSuccess: () => {
      toast.success("文件已删除；如内容变化需重新授权");
      setSelectedPath("SKILL.md");
      setDirty(false);
      void queryClient.invalidateQueries({ queryKey: ["skill-files", skill.id] });
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const mkdir = useMutation({
    mutationFn: (path: string) => mkdirSkillPath(skill.id, path),
    onSuccess: () => {
      toast.success("目录已创建");
      setNewPath("");
      void queryClient.invalidateQueries({ queryKey: ["skill-files", skill.id] });
    },
    onError: (error) => toast.error(error.message),
  });

  const createFile = useMutation({
    mutationFn: (path: string) =>
      writeSkillFile(
        skill.id,
        path,
        path.endsWith(".md")
          ? "# New file\n"
          : path.endsWith(".py")
            ? "# new script\n"
            : "",
        tree.data?.content_hash ?? skill.content_hash ?? null,
      ),
    onSuccess: (_result, path) => {
      toast.success("文件已创建");
      setSelectedPath(path);
      setDirty(false);
      setNewPath("");
      void queryClient.invalidateQueries({ queryKey: ["skill-files", skill.id] });
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const validate = useMutation({
    mutationFn: () => validateSkillPackage(skill.id),
    onSuccess: (result) => {
      if (result.ok) toast.success("校验通过");
      else toast.error(result.issues.join("；") || "校验失败");
    },
    onError: (error) => toast.error(error.message),
  });

  const trialRun = useMutation({
    mutationFn: () =>
      runSkillSandbox(skill.id, {
        script_path: selectedPath.startsWith("scripts/")
          ? selectedPath
          : "scripts/hello.py",
      }),
    onSuccess: (result) => {
      if (!result.available || result.status === "unavailable") {
        toast.error(result.error_message || "沙箱不可用");
        return;
      }
      toast.success(
        result.status === "succeeded"
          ? `沙箱退出码 ${result.exit_code ?? 0}`
          : `试运行结束：${result.status}`,
      );
    },
    onError: (error) => toast.error(error.message),
  });

  if (tree.isPending) return <LoadingState />;
  if (tree.isError)
    return <ErrorState message={tree.error.message} />;

  return (
    <Surface className="overflow-hidden">
      <div className="border-b p-4">
        <SectionHeading
          description={`content_hash ${(tree.data?.content_hash ?? skill.content_hash ?? "—").slice(0, 12)} · 轻量编辑器（非 Monaco）`}
          title={`文件包 · ${skill.name}`}
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {skill.has_scripts || tree.data?.has_scripts ? (
            <Badge variant="secondary">含 scripts/</Badge>
          ) : null}
          <StatePill status={skill.status} />
          <Button
            disabled={validate.isPending}
            onClick={() => validate.mutate()}
            size="xs"
            variant="outline"
          >
            校验 SKILL.md
          </Button>
          {(skill.has_scripts || tree.data?.has_scripts) &&
          selectedPath.startsWith("scripts/") ? (
            <Button
              disabled={trialRun.isPending}
              onClick={() => trialRun.mutate()}
              size="xs"
              variant="outline"
            >
              沙箱试运行
            </Button>
          ) : null}
          <Button
            onClick={() => {
              void tree.refetch();
              void file.refetch();
            }}
            size="xs"
            variant="ghost"
          >
            <RefreshCcw className="size-3" />
            刷新
          </Button>
        </div>
      </div>
      <div className="grid min-h-[320px] lg:grid-cols-[220px_1fr]">
        <div className="space-y-2 border-b p-3 lg:border-b-0 lg:border-r">
          <p className="text-[11px] font-semibold text-muted-foreground">
            文件树
          </p>
          <div className="max-h-72 space-y-1 overflow-auto">
            {files.map((path) => (
              <button
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs ${
                  path === selectedPath
                    ? "bg-primary/10 font-medium text-primary"
                    : "hover:bg-muted"
                }`}
                key={path}
                onClick={() => {
                  if (dirty && !window.confirm("有未保存修改，切换文件？"))
                    return;
                  setSelectedPath(path);
                  setDirty(false);
                }}
                type="button"
              >
                <FileCode2 className="size-3 shrink-0" />
                <span className="truncate font-mono">{path}</span>
              </button>
            ))}
            {!files.length ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                尚无文件
              </p>
            ) : null}
          </div>
          <div className="space-y-2 border-t pt-3">
            <Input
              className="font-mono text-xs"
              onChange={(event) => setNewPath(event.currentTarget.value)}
              placeholder="scripts/new.py 或 notes/"
              value={newPath}
            />
            <div className="flex flex-wrap gap-1">
              <Button
                disabled={!newPath.trim() || createFile.isPending}
                onClick={() => {
                  const path = newPath.trim().replace(/\/$/, "");
                  if (!path) return;
                  createFile.mutate(path);
                }}
                size="xs"
                variant="outline"
              >
                新建文件
              </Button>
              <Button
                disabled={!newPath.trim() || mkdir.isPending}
                onClick={() => {
                  const path = newPath.trim().replace(/\/$/, "");
                  if (!path) return;
                  mkdir.mutate(path);
                }}
                size="xs"
                variant="ghost"
              >
                <FolderPlus className="size-3" />
                目录
              </Button>
            </div>
          </div>
        </div>
        <div className="flex min-h-[280px] flex-col p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <p className="min-w-0 flex-1 truncate font-mono text-xs">
              {selectedPath}
              {dirty ? " · 未保存" : ""}
            </p>
            <Button
              disabled={save.isPending || !dirty}
              onClick={() => save.mutate()}
              size="xs"
            >
              <Save className="size-3" />
              保存
            </Button>
            <Button
              disabled={
                remove.isPending ||
                selectedPath === "SKILL.md" ||
                !selectedPath
              }
              onClick={() => {
                if (window.confirm(`删除 ${selectedPath}？`)) remove.mutate();
              }}
              size="xs"
              variant="ghost"
            >
              <Trash2 className="size-3" />
              删除
            </Button>
          </div>
          {file.isPending ? (
            <LoadingState />
          ) : file.isError ? (
            <ErrorState message={file.error.message} />
          ) : (
            <Textarea
              className="min-h-[240px] flex-1 font-mono text-xs leading-5"
              onChange={(event) => {
                setDraft(event.currentTarget.value);
                setDirty(true);
              }}
              spellCheck={false}
              value={draft}
            />
          )}
        </div>
      </div>
    </Surface>
  );
}

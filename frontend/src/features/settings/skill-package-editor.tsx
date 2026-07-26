import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Eye,
  FileCode2,
  FolderPlus,
  Languages,
  PencilLine,
  RefreshCcw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import {
  deleteSkillFile,
  listSkillFiles,
  mkdirSkillPath,
  readSkillFile,
  reviewSkillSemantics,
  scanSkillSecurity,
  validateSkillPackage,
  writeSkillFile,
} from "@/api";
import { MessageResponse } from "@/components/ai-elements/message";
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
import { SkillTranslateDialog } from "@/features/settings/skills-hub-extras";
import type {
  Skill,
  SkillSecurityScanResult,
  SkillSemanticReviewResult,
} from "@/types/extensions";

/** Split leading YAML frontmatter so preview renders it as code instead of headings. */
function splitFrontmatter(markdown: string): {
  frontmatter: string | null;
  body: string;
} {
  const match = markdown.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!match) return { frontmatter: null, body: markdown };
  return { frontmatter: match[1], body: markdown.slice(match[0].length) };
}

/** Lightweight MVP editor for agent_skill_package file trees (D-081). */
export function SkillPackageEditor({ skill }: { skill: Skill }) {
  const queryClient = useQueryClient();
  const [selectedPath, setSelectedPath] = useState("SKILL.md");
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [translateOpen, setTranslateOpen] = useState(false);

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

  const [securityReport, setSecurityReport] =
    useState<SkillSecurityScanResult | null>(null);
  const [semanticReport, setSemanticReport] =
    useState<SkillSemanticReviewResult | null>(null);

  const securityScan = useMutation({
    mutationFn: () => scanSkillSecurity(skill.id),
    onSuccess: (result) => {
      setSecurityReport(result);
      if (result.risk_level === "low") {
        toast.success(`静态扫描通过（${result.scanned_files} 个文件，无风险发现）`);
      } else {
        toast.warning(
          `静态扫描：风险 ${result.risk_level === "high" ? "高" : "中"} · ${result.finding_count} 处发现`,
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const semanticReview = useMutation({
    mutationFn: () => reviewSkillSemantics(skill.id),
    onSuccess: (result) => {
      setSemanticReport(result);
      const label =
        result.verdict === "pass"
          ? "通过"
          : result.verdict === "warn"
            ? "警告"
            : "不通过";
      (result.verdict === "pass" ? toast.success : toast.warning)(
        `语义审核${result.cached ? "（缓存）" : ""}：${label} · 风险分 ${result.risk_score}`,
      );
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
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
          <Button
            disabled={securityScan.isPending}
            onClick={() => securityScan.mutate()}
            size="xs"
            variant="outline"
          >
            <ShieldAlert className="size-3" />
            {securityScan.isPending ? "扫描中…" : "安全扫描"}
          </Button>
          <Button
            disabled={semanticReview.isPending}
            onClick={() => semanticReview.mutate()}
            size="xs"
            variant="outline"
          >
            <ShieldCheck className="size-3" />
            {semanticReview.isPending ? "审核中…" : "语义审核"}
          </Button>
          <Button
            onClick={() => setTranslateOpen(true)}
            size="xs"
            variant="outline"
          >
            <Languages className="size-3" />
            翻译
          </Button>
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
      {securityReport || semanticReport ? (
        <div className="space-y-3 border-b bg-muted/20 p-4">
          {securityReport ? (
            <div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium">静态扫描（第二层）</span>
                <Badge
                  variant={
                    securityReport.risk_level === "high"
                      ? "destructive"
                      : securityReport.risk_level === "medium"
                        ? "outline"
                        : "secondary"
                  }
                >
                  风险{" "}
                  {securityReport.risk_level === "high"
                    ? "高"
                    : securityReport.risk_level === "medium"
                      ? "中"
                      : "低"}
                </Badge>
                <span className="text-muted-foreground">
                  {securityReport.scanned_files} 文件 ·{" "}
                  {securityReport.finding_count} 处发现
                </span>
              </div>
              {securityReport.findings.length ? (
                <div className="mt-2 max-h-48 space-y-1 overflow-auto">
                  {securityReport.findings.map((finding, index) => (
                    <div
                      className="rounded border bg-background p-2 text-[11px]"
                      key={`${finding.path}-${finding.category}-${index}`}
                    >
                      <p>
                        <span
                          className={
                            finding.severity === "high"
                              ? "font-semibold text-destructive"
                              : "font-semibold"
                          }
                        >
                          [{finding.severity}] {finding.explanation}
                        </span>
                        <span className="ml-2 font-mono text-muted-foreground">
                          {finding.path}
                        </span>
                      </p>
                      {finding.excerpt ? (
                        <p className="mt-1 truncate font-mono text-muted-foreground">
                          {finding.excerpt}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          {semanticReport ? (
            <div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium">语义审核（第三层）</span>
                <Badge
                  variant={
                    semanticReport.verdict === "fail"
                      ? "destructive"
                      : semanticReport.verdict === "warn"
                        ? "outline"
                        : "secondary"
                  }
                >
                  {semanticReport.verdict === "pass"
                    ? "通过"
                    : semanticReport.verdict === "warn"
                      ? "警告"
                      : "不通过"}{" "}
                  · {semanticReport.risk_score}分
                </Badge>
                <span className="text-muted-foreground">
                  {semanticReport.model_id}
                  {semanticReport.cached ? " · 缓存" : ""}
                </span>
              </div>
              {semanticReport.summary ? (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {semanticReport.summary}
                </p>
              ) : null}
              {semanticReport.reasons.length ? (
                <ul className="mt-1 list-inside list-disc text-[11px] text-muted-foreground">
                  {semanticReport.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
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
        <div className="flex min-h-[280px] min-w-0 flex-col p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <p className="min-w-0 flex-1 truncate font-mono text-xs">
              {selectedPath}
              {dirty ? " · 未保存" : ""}
            </p>
            {selectedPath.endsWith(".md") ? (
              <div className="flex overflow-hidden rounded-md border">
                <Button
                  className="rounded-none"
                  onClick={() => setPreviewing(false)}
                  size="xs"
                  variant={previewing ? "ghost" : "secondary"}
                >
                  <PencilLine className="size-3" />
                  编辑
                </Button>
                <Button
                  className="rounded-none border-l"
                  onClick={() => setPreviewing(true)}
                  size="xs"
                  variant={previewing ? "secondary" : "ghost"}
                >
                  <Eye className="size-3" />
                  预览
                </Button>
              </div>
            ) : null}
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
          ) : previewing && selectedPath.endsWith(".md") ? (
            (() => {
              const { frontmatter, body } = splitFrontmatter(draft);
              return (
                <div className="max-h-[520px] min-h-[240px] flex-1 overflow-y-auto rounded-lg border bg-muted/20 p-4">
                  {frontmatter ? (
                    <pre className="mb-4 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs leading-5 text-muted-foreground">
                      {frontmatter}
                    </pre>
                  ) : null}
                  <MessageResponse className="text-sm leading-6">
                    {body}
                  </MessageResponse>
                </div>
              );
            })()
          ) : (
            <Textarea
              className="max-h-[520px] min-h-[240px] flex-1 overflow-y-auto font-mono text-xs leading-5"
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
      <SkillTranslateDialog
        onOpenChange={setTranslateOpen}
        open={translateOpen}
        skill={skill}
      />
    </Surface>
  );
}

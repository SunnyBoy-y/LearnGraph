import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Download,
  File,
  FileImage,
  FileText,
  FileSpreadsheet,
  Presentation,
  FileCode,
  Music,
  Video,
  LayoutGrid,
  List,
  Globe2,
  HardDrive,
  Link2,
  LoaderCircle,
  MoreHorizontal,
  RefreshCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  approveResearch,
  cancelResearch,
  createResearchJob,
  deleteFileConfirmed,
  deleteFilesBatch,
  deleteSourceConfirmed,
  downloadFile,
  fetchSource,
  getFileBatchDeleteImpact,
  getFileDeleteImpact,
  getFileStorageSummary,
  getSourceDeleteImpact,
  listFileParserCapabilities,
  listFiles,
  listResearchEvents,
  listResearchJobs,
  listSourceRecords,
  parseFile,
  pollParseJob,
  planResearch,
  searchWeb,
  uploadFile,
} from "@/api";
import { FileDiagnosticsDialog } from "@/components/resources/file-diagnostics-dialog";
import { SourceAssociationDialog } from "@/components/resources/source-association-dialog";
import { SourceDetailDialog } from "@/components/resources/source-detail-dialog";
import { DeleteImpactDialog } from "@/components/shared/delete-impact-dialog";
import { ResearchDomainAllowlistEditor } from "@/components/shared/domain-allowlist-editor";
import { downloadViaNative, toAbsoluteApiUrl } from "@/lib/native-download";
import { workspaceQueryKey } from "@/lib/query-keys";
import { resolveFilePreviewKind } from "@/lib/file-preview";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
  Timeline,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { listSettings, updateSetting } from "@/api/settings";
import type { WebFetchPolicy } from "@/types/settings";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ResearchPlan, SearchResponse } from "@/types/research";
import type { SearchResult } from "@/types/research";
import type { FileParserCapability, FileRecord } from "@/types/files";
import type { SourceRecord } from "@/types/workflow";

const FILE_PAGE_SIZE = 20;

function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

type FileFilter = "all" | "indexed" | "pending" | "attachment" | "failed";

const fileFilters: Array<{ value: FileFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "indexed", label: "可问答" },
  { value: "pending", label: "未建索引" },
  { value: "attachment", label: "仅附件" },
  { value: "failed", label: "解析失败" },
];

function fileType(file: Pick<FileRecord, "original_name" | "mime_type">) {
  const extension = file.original_name.split(".").pop()?.toLowerCase();
  const extensionLabels: Record<string, string> = {
    csv: "CSV",
    doc: "Word",
    docx: "Word",
    md: "Markdown",
    pdf: "PDF",
    ppt: "PowerPoint",
    pptx: "PowerPoint",
    txt: "文本",
    xls: "Excel",
    xlsx: "Excel",
  };
  if (extension && extensionLabels[extension]) return extensionLabels[extension];
  if (file.mime_type.startsWith("image/")) return "图片";
  if (file.mime_type.startsWith("audio/")) return "音频";
  if (file.mime_type.startsWith("video/")) return "视频";
  return extension ? extension.toUpperCase() : "文件";
}

/**
 * 资料库状态口径（与后端 parse_status / parse_capability 对齐）：
 * - 可问答：已建文本索引；
 * - 未建索引：本可解析、但还没请求过解析（需要时才做）；
 * - 仅附件：按设计只作附件直接使用（图片/音视频/压缩包/SVG，以及智能体产物导出的文件），
 *   不建立文本索引，因此不能叫「待解析」；
 * - 缺少解析器 / 解析失败 / 解析中：解析链路自己的状态。
 */
const ATTACHMENT_ONLY_CATEGORIES: ReadonlySet<FileCategory> = new Set([
  "image",
  "audio",
  "video",
  "archive",
]);

function isAttachmentOnlyFile(file: FileRecord) {
  if (file.parse_capability === "attachment_only") return true;
  return ATTACHMENT_ONLY_CATEGORIES.has(fileCategory(file));
}

function fileStatus(file: FileRecord) {
  if (file.parse_status === "indexed")
    return { label: "可问答", status: "approved" };
  if (file.parse_status === "failed")
    return { label: "解析失败", status: "failed" };
  if (file.parse_status === "processor_required")
    return { label: "缺少解析器", status: "pending" };
  if (["queued", "running", "processing"].includes(file.parse_status))
    return { label: "解析中", status: "pending" };
  if (isAttachmentOnlyFile(file)) return { label: "仅附件", status: "consumed" };
  return { label: "未建索引", status: "pending" };
}

function matchesFileFilter(file: FileRecord, filter: FileFilter) {
  if (filter === "all") return true;
  if (filter === "indexed") return file.parse_status === "indexed";
  if (filter === "failed") return file.parse_status === "failed";
  if (filter === "attachment") return isAttachmentOnlyFile(file);
  return (
    !["indexed", "failed"].includes(file.parse_status) &&
    !isAttachmentOnlyFile(file)
  );
}

function formatUploadTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function parserModeLabel(mode: FileParserCapability["mode"]) {
  if (mode === "built_in") return "内置";
  if (mode === "optional") return "可选";
  return "隔离处理";
}

const fileCategories = [
  ["all", "全部类型"], ["image", "图片"], ["document", "文档"],
  ["spreadsheet", "电子表格"], ["powerpoint", "演示文稿"], ["pdf", "PDF"],
  ["audio", "音频"], ["video", "视频"], ["archive", "压缩包"], ["other", "其他"],
] as const;
type FileCategory = typeof fileCategories[number][0];

function fileCategory(file: FileRecord): FileCategory {
  const ext = file.original_name.split(".").pop()?.toLowerCase();
  if (["zip", "rar", "7z", "tar", "gz"].includes(ext ?? "")) return "archive";
  if (["xls", "csv", "tsv", "ods"].includes(ext ?? "")) return "spreadsheet";
  if (["ppt", "odp"].includes(ext ?? "")) return "powerpoint";
  if (["doc", "odt", "rtf"].includes(ext ?? "")) return "document";
  const kind = resolveFilePreviewKind(file.original_name, file.mime_type);
  if (["word", "text", "html"].includes(kind)) return "document";
  if (["image", "spreadsheet", "powerpoint", "pdf", "audio", "video"].includes(kind)) return kind as FileCategory;
  return "other";
}

function FileIcon({ file, large = false }: { file: FileRecord; large?: boolean }) {
  const category = fileCategory(file);
  const Icon = { image: FileImage, document: FileText, spreadsheet: FileSpreadsheet,
    powerpoint: Presentation, pdf: FileText, audio: Music, video: Video,
    archive: Archive, other: File, all: File }[category];
  const color = { image: "text-purple-500", document: "text-blue-500", spreadsheet: "text-emerald-600",
    powerpoint: "text-orange-500", pdf: "text-red-500", audio: "text-violet-500",
    video: "text-pink-500", archive: "text-amber-600", other: "text-muted-foreground", all: "" }[category];
  const DisplayIcon = /\.(html?|jsx?|tsx?|py|json|css)$/i.test(file.original_name) ? FileCode : Icon;
  return <DisplayIcon aria-hidden="true" className={`${large ? "size-12" : "size-5"} shrink-0 ${color}`} />;
}

function FileThumbnail({ file, large = false }: { file: FileRecord; large?: boolean }) {
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (fileCategory(file) !== "image") return;
    let active = true;
    let objectUrl: string | undefined;
    setFailed(false);
    void downloadFile(file.id).then((blob) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => setFailed(true));
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [file.id, file.mime_type, file.original_name]);
  const isImage = fileCategory(file) === "image";
  return <span className={`flex shrink-0 items-center justify-center overflow-hidden ${large ? `w-full rounded-xl bg-muted/30 ${isImage ? "min-h-40" : "h-40"}` : "size-9 rounded-lg border bg-background"}`}>
    {url && !failed ? <img alt={file.original_name} className={large ? "h-auto max-h-[28rem] w-full object-contain" : "size-full object-cover"} onError={() => setFailed(true)} src={url} /> : <FileIcon file={file} large={large} />}
  </span>;
}

function parserCapabilityForFile(
  file: Pick<FileRecord, "original_name">,
  capabilities: FileParserCapability[] | undefined,
) {
  const extensionIndex = file.original_name.lastIndexOf(".");
  if (extensionIndex < 0) return undefined;
  const extension = file.original_name.slice(extensionIndex).toLowerCase();
  return capabilities?.find((capability) =>
    capability.extensions.some((item) => item.toLowerCase() === extension),
  );
}

export function SourcesPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [webUrl, setWebUrl] = useState("");
  const [webDialogOpen, setWebDialogOpen] = useState(false);
  const [policyOpen, setPolicyOpen] = useState(false);
  const [supportOpen, setSupportOpen] = useState(false);
  const [fileFilter, setFileFilter] = useState<FileFilter>("all");
  const [typeFilter, setTypeFilter] = useState<FileCategory>("all");
  const [viewMode, setViewMode] = useState<"list" | "grid">("grid");
  const [fileSearch, setFileSearch] = useState("");
  const [filePage, setFilePage] = useState(1);
  const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [deleteTarget, setDeleteTarget] = useState<FileRecord | null>(null);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
  const [diagnosticFile, setDiagnosticFile] = useState<FileRecord | null>(null);
  const [sourceDetail, setSourceDetail] = useState<SourceRecord | null>(null);
  const [sourceAssociation, setSourceAssociation] =
    useState<SourceRecord | null>(null);
  const [sourceDeleteTarget, setSourceDeleteTarget] =
    useState<SourceRecord | null>(null);
  // 全局拖入检测：鼠标松开前显示整页“拖入文件”遮罩
  const [dropActive, setDropActive] = useState(false);
  const files = useQuery({ queryKey: ["files"], queryFn: () => listFiles() });
  const storageSummary = useQuery({
    queryKey: ["files-storage-summary"],
    queryFn: getFileStorageSummary,
  });
  const parserCapabilities = useQuery({
    queryKey: ["file-parser-capabilities"],
    queryFn: listFileParserCapabilities,
  });
  const sources = useQuery({
    queryKey: ["source-records"],
    queryFn: listSourceRecords,
  });
  const upload = useMutation({
    mutationFn: (file: File) => uploadFile(file),
    onSuccess: (file) => {
      toast.success(`${file.original_name} 已安全存储`);
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["files"] }),
        queryClient.invalidateQueries({ queryKey: ["files-storage-summary"] }),
      ]);
    },
    onError: (error) => toast.error(error.message),
  });
  const uploadRef = useRef(upload.mutate);
  useEffect(() => {
    uploadRef.current = upload.mutate;
  }, [upload.mutate]);
  // 稳定的上传入口，供整页拖入监听器复用
  const uploadFiles = useCallback((selectedFiles: FileList | File[]) => {
    for (const file of Array.from(selectedFiles)) uploadRef.current(file);
  }, []);
  const download = useMutation({
    mutationFn: async (file: FileRecord) => {
      // 移动端 WebView：真实 URL 交给原生下载器
      if (
        downloadViaNative(
          toAbsoluteApiUrl(`/api/v1/files/${encodeURIComponent(file.id)}/content`),
          file.original_name || "download",
        )
      ) {
        return;
      }
      const blob = await downloadFile(file.id);
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = file.original_name || "download";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
      return file;
    },
    onError: (error) => toast.error(error.message),
  });
  const parse = useMutation({
    // B1-8: parse runs on the durable queue; poll until terminal.
    mutationFn: async (fileId: string) => {
      const job = await parseFile(fileId);
      return pollParseJob(job);
    },
    onSuccess: (job) => {
      toast.success("解析任务已完成");
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["files"] }),
        queryClient.invalidateQueries({ queryKey: ["file-chunks", job.file_id] }),
        queryClient.invalidateQueries({ queryKey: ["file-references", job.file_id] }),
      ]);
    },
    onError: (error) => toast.error(error.message),
  });
  const deleteImpact = useQuery({
    enabled: Boolean(deleteTarget),
    queryKey: ["file-delete-impact", deleteTarget?.id],
    queryFn: () => getFileDeleteImpact(deleteTarget!.id),
  });
  const remove = useMutation({
    mutationFn: ({
      fileId,
      confirmationText,
    }: {
      fileId: string;
      confirmationText: string;
    }) => deleteFileConfirmed(fileId, confirmationText),
    onSuccess: (_result, variables) => {
      toast.success("文件和元数据已删除");
      setDeleteTarget(null);
      setSelectedFileIds((current) => {
        const next = new Set(current);
        next.delete(variables.fileId);
        return next;
      });
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["files"] }),
        queryClient.invalidateQueries({ queryKey: ["files-storage-summary"] }),
      ]);
    },
    onError: (error) => toast.error(error.message),
  });
  const batchFileIds = useMemo(
    () => Array.from(selectedFileIds),
    [selectedFileIds],
  );
  const batchDeleteImpact = useQuery({
    enabled: batchDeleteOpen && batchFileIds.length > 0,
    queryKey: ["file-batch-delete-impact", batchFileIds.join("|")],
    queryFn: () => getFileBatchDeleteImpact(batchFileIds),
  });
  const removeBatch = useMutation({
    mutationFn: ({
      fileIds,
      confirmationText,
    }: {
      fileIds: string[];
      confirmationText: string;
    }) => deleteFilesBatch(fileIds, confirmationText),
    onSuccess: (result) => {
      toast.success(`已删除 ${result.deleted_count} 个文件`);
      setBatchDeleteOpen(false);
      setSelectedFileIds(new Set());
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["files"] }),
        queryClient.invalidateQueries({ queryKey: ["files-storage-summary"] }),
      ]);
    },
    onError: (error) => toast.error(error.message),
  });
  const fetchPage = useMutation({
    mutationFn: () => fetchSource(webUrl.trim()),
    onSuccess: (source) => {
      toast.success(`已保存网页“${source.title}”`);
      setWebUrl("");
      setWebDialogOpen(false);
      setSourceAssociation(source);
      void queryClient.invalidateQueries({ queryKey: ["source-records"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const sourceDeleteImpact = useQuery({
    enabled: Boolean(sourceDeleteTarget),
    queryKey: ["source-delete-impact", sourceDeleteTarget?.id],
    queryFn: () => getSourceDeleteImpact(sourceDeleteTarget!.id),
  });
  const removeSource = useMutation({
    mutationFn: ({
      sourceId,
      confirmationText,
    }: {
      sourceId: string;
      confirmationText: string;
    }) => deleteSourceConfirmed(sourceId, confirmationText),
    onSuccess: async () => {
      toast.success("来源、引用和关联已删除");
      setSourceDeleteTarget(null);
      setSourceDetail(null);
      setSourceAssociation(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["source-records"] }),
        queryClient.invalidateQueries({ queryKey: ["source-record"] }),
        queryClient.invalidateQueries({ queryKey: ["source-links"] }),
      ]);
    },
    onError: (error) => toast.error(error.message),
  });

  useEffect(() => {
    setFilePage(1);
  }, [fileFilter, fileSearch, typeFilter]);

  useEffect(() => {
    // 资料库整页接管拖入：dragover/drop 默认行为必须拦掉，否则浏览器会直接打开文件
    const hasFiles = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types ?? []).includes("Files");
    let depth = 0;
    const onDragEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth += 1;
      setDropActive(true);
    };
    const onDragOver = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      setDropActive(true);
    };
    const onDragLeave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDropActive(false);
    };
    const onDrop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDropActive(false);
      const dropped = event.dataTransfer?.files;
      if (dropped && dropped.length > 0) uploadFiles(dropped);
    };
    const reset = () => {
      depth = 0;
      setDropActive(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      // 拖拽过程中按 Esc 取消：浏览器不一定补发 dragleave，这里兜底
      if (event.key === "Escape") reset();
    };
    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    window.addEventListener("dragend", reset);
    window.addEventListener("blur", reset);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("dragend", reset);
      window.removeEventListener("blur", reset);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [uploadFiles]);

  if (files.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (files.isError)
    return (
      <PageFrame>
        <ErrorState message={files.error.message} />
      </PageFrame>
    );
  const indexed = files.data.filter(
    (file) => file.parse_status === "indexed",
  ).length;
  const failed = files.data.filter(
    (file) => file.parse_status === "failed",
  ).length;
  const pending = files.data.filter(
    (file) =>
      !["indexed", "failed"].includes(file.parse_status) &&
      !isAttachmentOnlyFile(file),
  ).length;
  const attachmentOnly = files.data.filter(isAttachmentOnlyFile).length;
  const normalizedFileSearch = fileSearch.trim().toLocaleLowerCase();
  const filteredFiles = files.data.filter(
    (file) =>
      matchesFileFilter(file, fileFilter) &&
      (typeFilter === "all" || fileCategory(file) === typeFilter) &&
      (!normalizedFileSearch ||
        file.original_name.toLocaleLowerCase().includes(normalizedFileSearch)),
  );
  const totalPages = Math.max(
    1,
    Math.ceil(filteredFiles.length / FILE_PAGE_SIZE),
  );
  const safePage = Math.min(filePage, totalPages);
  const pageStart = (safePage - 1) * FILE_PAGE_SIZE;
  const pagedFiles = filteredFiles.slice(
    pageStart,
    pageStart + FILE_PAGE_SIZE,
  );
  const pageFileIds = pagedFiles.map((file) => file.id);
  const selectedOnPageCount = pageFileIds.filter((id) =>
    selectedFileIds.has(id),
  ).length;
  const allPageSelected =
    pageFileIds.length > 0 && selectedOnPageCount === pageFileIds.length;
  const somePageSelected =
    selectedOnPageCount > 0 && selectedOnPageCount < pageFileIds.length;
  const chooseFiles = () => inputRef.current?.click();
  const toggleFileSelected = (fileId: string, checked: boolean) => {
    setSelectedFileIds((current) => {
      const next = new Set(current);
      if (checked) next.add(fileId);
      else next.delete(fileId);
      return next;
    });
  };
  const togglePageSelection = (checked: boolean) => {
    setSelectedFileIds((current) => {
      const next = new Set(current);
      for (const id of pageFileIds) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  };
  return (
    <PageFrame>
      {dropActive
        ? createPortal(
            <div
              aria-hidden="true"
              className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm"
            >
              <div className="flex w-full max-w-xl flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-primary/50 bg-card/95 px-8 py-14 text-center shadow-lg">
                <span className="grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary ring-1 ring-primary/25">
                  {upload.isPending ? (
                    <LoaderCircle className="size-6 animate-spin" />
                  ) : (
                    <UploadCloud className="size-6" />
                  )}
                </span>
                <strong className="text-lg">
                  {upload.isPending ? "上传中…" : "拖入文件"}
                </strong>
                <span className="text-sm text-muted-foreground">
                  松开鼠标即上传，文件会被安全解析并存储。
                </span>
                <span className="text-xs text-muted-foreground">
                  支持 PDF、Word、Excel、Markdown 等格式；不可解析的文件仍会安全存储。
                </span>
              </div>
            </div>,
            document.body,
          )
        : null}
      <PageIntro
        actions={
          <Button disabled={upload.isPending} onClick={chooseFiles} size="sm">
            {upload.isPending ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <UploadCloud className="size-4" />
            )}
            {upload.isPending ? "上传中…" : "上传资料"}
          </Button>
        }
        description="管理当前工作区中可用于阅读、问答和引用的文件与网页。"
        title="资料库"
      />
      <Input
        className="hidden"
        multiple
        onChange={(event) => {
          uploadFiles(event.target.files ?? []);
          event.currentTarget.value = "";
        }}
        ref={inputRef}
        type="file"
      />

      <div
        aria-label="资料库概况"
        className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground"
      >
        <span>
          <strong className="font-semibold tabular-nums text-foreground">
            {files.data.length}
          </strong>{" "}
          个文件
        </span>
        <span>
          <strong className="font-semibold tabular-nums text-foreground">
            {indexed}
          </strong>{" "}
          个可问答
        </span>
        <span>
          <strong className="font-semibold tabular-nums text-foreground">
            {pending}
          </strong>{" "}
          个未建索引
        </span>
        <span>
          <strong className="font-semibold tabular-nums text-foreground">
            {attachmentOnly}
          </strong>{" "}
          个仅附件（直接使用）
        </span>
        <span>
          <strong className="font-semibold tabular-nums text-foreground">
            {failed}
          </strong>{" "}
          个解析失败
        </span>
        <span className="inline-flex items-center gap-1.5">
          <HardDrive className="size-3.5" aria-hidden="true" />
          总占用{" "}
          <strong className="font-semibold tabular-nums text-foreground">
            {bytes(
              storageSummary.data?.total_bytes ??
                files.data.reduce(
                  (sum, file) => sum + (file.size_bytes || 0),
                  0,
                ),
            )}
          </strong>
        </span>
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <Button
            onClick={() => setWebDialogOpen(true)}
            size="sm"
            variant="outline"
          >
            <Link2 className="size-4" />
            粘贴网页
          </Button>
          <Button onClick={() => setSupportOpen(true)} size="sm" variant="ghost">
            查看支持范围
          </Button>
          <Button onClick={() => setPolicyOpen(true)} size="sm" variant="ghost">
            <ShieldCheck className="size-4" />
            缓存策略
          </Button>
        </span>
      </div>

      <Surface className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" aria-label="按文件类型筛选">
                {fileCategories.find(([value]) => value === typeFilter)?.[1]}
                <ChevronRight className="size-3.5 rotate-90" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              {fileCategories.map(([value, label]) => (
                <DropdownMenuItem key={value} onSelect={() => setTypeFilter(value)}>
                  <span className="flex-1">{label}</span>
                  {typeFilter === value ? <span aria-label="已选">✓</span> : null}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <div role="group" aria-label="资料显示方式" className="flex gap-1 rounded-lg bg-muted/50 p-1">
            <Button aria-label="网格视图" aria-pressed={viewMode === "grid"} size="icon-sm" variant={viewMode === "grid" ? "secondary" : "ghost"} onClick={() => setViewMode("grid")}><LayoutGrid className="size-4" /></Button>
            <Button aria-label="列表视图" aria-pressed={viewMode === "list"} size="icon-sm" variant={viewMode === "list" ? "secondary" : "ghost"} onClick={() => setViewMode("list")}><List className="size-4" /></Button>
          </div>
        </div>
        <div className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-center lg:justify-between">
          <div
            aria-label="资料状态筛选"
            className="flex max-w-full gap-1 overflow-x-auto rounded-lg bg-muted/60 p-1"
            role="group"
          >
            {fileFilters.map((filter) => (
              <Button
                aria-pressed={fileFilter === filter.value}
                className="shrink-0"
                key={filter.value}
                onClick={() => setFileFilter(filter.value)}
                size="xs"
                variant={fileFilter === filter.value ? "secondary" : "ghost"}
              >
                {filter.label}
              </Button>
            ))}
          </div>
          <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center lg:w-auto">
            {selectedFileIds.size > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  已选 {selectedFileIds.size} 项
                </span>
                <Button
                  disabled={removeBatch.isPending}
                  onClick={() => setBatchDeleteOpen(true)}
                  size="xs"
                  variant="destructive"
                >
                  <Trash2 className="size-3.5" />
                  批量删除
                </Button>
                <Button
                  onClick={() => setSelectedFileIds(new Set())}
                  size="xs"
                  variant="ghost"
                >
                  取消选择
                </Button>
              </div>
            ) : null}
            <div className="relative w-full lg:w-64">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label="搜索资料"
                className="pl-9"
                onChange={(event) => setFileSearch(event.target.value)}
                placeholder="搜索文件名"
                value={fileSearch}
              />
            </div>
          </div>
        </div>
        {viewMode === "grid" ? (
          <div className="columns-1 gap-4 p-4 sm:columns-2 xl:columns-3 2xl:columns-4">
            {pagedFiles.map((file) => (
              <article key={file.id} className={`mb-4 min-w-0 break-inside-avoid rounded-2xl border p-4 transition-shadow hover:shadow-sm ${selectedFileIds.has(file.id) ? "bg-muted/40 ring-1 ring-ring" : "bg-card"}`} style={{ contentVisibility: "auto", containIntrinsicSize: "320px" }}>
                <div className="mb-3 flex items-start gap-2">
                  <button type="button" className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:underline" title={file.original_name} onClick={() => setDiagnosticFile(file)}>{file.original_name}</button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button aria-label={`打开 ${file.original_name} 的更多操作`} size="icon-sm" variant="ghost"><MoreHorizontal className="size-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => navigate(`../documents/${file.id}`)}><FileText />打开学习</DropdownMenuItem>
                      <DropdownMenuItem disabled={download.isPending} onSelect={() => download.mutate(file)}><Download />下载文件</DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => setDiagnosticFile(file)}><Search />解析与引用诊断</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem disabled={remove.isPending} onSelect={() => setDeleteTarget(file)} variant="destructive"><Trash2 />删除文件</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                <button type="button" aria-label={`查看 ${file.original_name}`} className="block w-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => navigate(`../documents/${file.id}`)}><FileThumbnail file={file} large /></button>
                <div className="mt-3 flex items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">{fileType(file)} · {bytes(file.size_bytes)}</span>
                  <Checkbox aria-label={`选择 ${file.original_name}`} checked={selectedFileIds.has(file.id)} onCheckedChange={(value) => toggleFileSelected(file.id, value === true)} />
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted-foreground">{formatUploadTime(file.created_at)}</span><StatePill {...fileStatus(file)} /></div>
              </article>
            ))}
          </div>
        ) : <div className="resource-file-table-wrap overflow-x-auto">
          <table className="resource-file-table w-full min-w-[760px] text-left text-sm">
            <thead className="bg-muted/35 text-xs text-muted-foreground">
              <tr>
                <th className="w-12 px-4 py-3">
                  <Checkbox
                    aria-label="全选当前页"
                    checked={
                      allPageSelected
                        ? true
                        : somePageSelected
                          ? "indeterminate"
                          : false
                    }
                    disabled={!pageFileIds.length}
                    onCheckedChange={(value) =>
                      togglePageSelection(value === true)
                    }
                  />
                </th>
                <th className="px-5 py-3">文件</th>
                <th className="px-5 py-3">类型</th>
                <th className="px-5 py-3">大小</th>
                <th className="px-5 py-3">状态</th>
                <th className="px-5 py-3">上传时间</th>
                <th className="sticky right-0 z-10 w-16 border-l bg-muted px-5 py-3 text-right">
                  更多
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {pagedFiles.map((file) => {
                const capability = parserCapabilityForFile(
                  file,
                  parserCapabilities.data,
                );
                const parserUnavailable =
                  capability?.available === false ||
                  file.parse_capability === "attachment_only";
                const status = fileStatus(file);
                const isSelected = selectedFileIds.has(file.id);
                return (
                  <tr
                    className={isSelected ? "bg-muted/25" : undefined}
                    key={file.id}
                  >
                    <td className="px-4 py-4">
                      <Checkbox
                        aria-label={`选择 ${file.original_name}`}
                        checked={isSelected}
                        onCheckedChange={(value) =>
                          toggleFileSelected(file.id, value === true)
                        }
                      />
                    </td>
                    <td className="px-5 py-4">
                      <button
                        className="flex max-w-full items-center gap-2 text-left hover:underline"
                        onClick={() => setDiagnosticFile(file)}
                        type="button"
                      >
                        <FileThumbnail file={file} />
                        <span className="max-w-64 truncate font-medium">
                          {file.original_name}
                        </span>
                      </button>
                    </td>
                    <td className="px-5 py-4 text-xs text-muted-foreground">
                      {fileType(file)}
                    </td>
                    <td className="px-5 py-4 font-mono text-xs">
                      {bytes(file.size_bytes)}
                    </td>
                    <td className="px-5 py-4">
                      <StatePill label={status.label} status={status.status} />
                    </td>
                    <td className="px-5 py-4 text-xs text-muted-foreground">
                      {formatUploadTime(file.created_at)}
                    </td>
                    <td className="sticky right-0 z-10 border-l bg-card px-5 py-4">
                      <div className="flex justify-end">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              aria-label={`打开 ${file.original_name} 的更多操作`}
                              size="icon-sm"
                              variant="ghost"
                            >
                              <MoreHorizontal />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-48">
                            <DropdownMenuItem
                              onSelect={() => navigate(`../documents/${file.id}`)}
                            >
                              <FileText />
                              打开学习
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              disabled={download.isPending}
                              onSelect={() => download.mutate(file)}
                            >
                              <Download />
                              下载文件
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              disabled={
                                parse.isPending ||
                                file.parse_status === "indexed" ||
                                parserUnavailable
                              }
                              onSelect={() => parse.mutate(file.id)}
                            >
                              <RefreshCcw />
                              {file.parse_status === "failed"
                                ? "重新解析"
                                : file.parse_status === "indexed"
                                  ? "已可问答"
                                  : file.parse_capability === "attachment_only"
                                    ? "仅附件，无需解析"
                                    : parserUnavailable
                                      ? "缺少解析器"
                                      : "解析文件"}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onSelect={() => setDiagnosticFile(file)}
                            >
                              <Search />
                              解析与引用诊断
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              disabled={remove.isPending}
                              onSelect={() => setDeleteTarget(file)}
                              variant="destructive"
                            >
                              <Trash2 />
                              删除文件
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>}
        {!files.data.length ? (
          <div className="grid min-h-48 place-items-center p-6 text-center">
            <div>
              <Archive className="mx-auto size-7 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">还没有资料</p>
              <p className="mt-1 text-xs text-muted-foreground">
                上传文件后，存储与解析状态会显示在这里。
              </p>
            </div>
          </div>
        ) : !filteredFiles.length ? (
          <div className="grid min-h-48 place-items-center p-6 text-center">
            <div>
              <Search className="mx-auto size-7 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">没有符合条件的资料</p>
              <p className="mt-1 text-xs text-muted-foreground">
                调整状态筛选或文件名后重试。
              </p>
              <Button
                className="mt-3"
                onClick={() => {
                  setFileFilter("all");
                  setTypeFilter("all");
                  setFileSearch("");
                }}
                size="xs"
                variant="outline"
              >
                清除筛选
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3">
            <p className="text-xs text-muted-foreground">
              第 {safePage} / {totalPages} 页 · 共 {filteredFiles.length} 项 ·
              每页 {FILE_PAGE_SIZE} 项
            </p>
            <div className="flex items-center gap-1">
              <Button
                disabled={safePage <= 1}
                onClick={() => setFilePage((page) => Math.max(1, page - 1))}
                size="sm"
                variant="outline"
              >
                <ChevronLeft className="size-4" />
                上一页
              </Button>
              <Button
                disabled={safePage >= totalPages}
                onClick={() =>
                  setFilePage((page) => Math.min(totalPages, page + 1))
                }
                size="sm"
                variant="outline"
              >
                下一页
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        )}
      </Surface>
      <DeleteImpactDialog
        error={deleteImpact.error?.message ?? remove.error?.message}
        impact={deleteImpact.data}
        isConfirming={remove.isPending}
        isLoading={deleteImpact.isPending && Boolean(deleteTarget)}
        objectLabel={deleteTarget?.original_name ?? "文件"}
        onConfirm={() => {
          if (!deleteTarget || !deleteImpact.data) return;
          remove.mutate({
            fileId: deleteTarget.id,
            confirmationText: deleteImpact.data.confirmation_text,
          });
        }}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleteTarget(null);
        }}
        open={Boolean(deleteTarget)}
      />
      <DeleteImpactDialog
        confirmLabel={`删除 ${batchFileIds.length} 个文件`}
        error={
          batchDeleteImpact.error?.message ?? removeBatch.error?.message
        }
        impact={batchDeleteImpact.data}
        isConfirming={removeBatch.isPending}
        isLoading={batchDeleteImpact.isPending && batchDeleteOpen}
        objectLabel={`${batchFileIds.length} 个文件`}
        onConfirm={() => {
          if (!batchDeleteImpact.data || !batchFileIds.length) return;
          removeBatch.mutate({
            fileIds: batchFileIds,
            confirmationText: batchDeleteImpact.data.confirmation_text,
          });
        }}
        onOpenChange={(open) => {
          if (!open && !removeBatch.isPending) setBatchDeleteOpen(false);
        }}
        open={batchDeleteOpen}
        title={`永久删除选中的 ${batchFileIds.length} 个文件？`}
      />
      {diagnosticFile ? (
        <FileDiagnosticsDialog
          capability={parserCapabilityForFile(
            diagnosticFile,
            parserCapabilities.data,
          )}
          file={diagnosticFile}
          onOpenChange={(open) => {
            if (!open) setDiagnosticFile(null);
          }}
          open={Boolean(diagnosticFile)}
        />
      ) : null}
      {sources.data?.length ? (
        <Surface className="p-5">
          <SectionHeading
            description="正文按规范化 URL 或内容哈希复用，关联关系单独保存。"
            title="网页资料"
          />
          <div className="mt-4 divide-y">
            {sources.data.map((source) => (
              <div className="flex items-center gap-3 py-3" key={source.id}>
                <Globe2 className="size-4 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{source.title}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {source.final_url}
                  </p>
                </div>
                <StatePill
                  label={source.cache_status}
                  status={source.cache_status}
                />
                <Button
                  aria-label={`查看 ${source.title} 的详情与关联`}
                  onClick={() => setSourceDetail(source)}
                  size="xs"
                  variant="outline"
                >
                  详情与关联
                </Button>
              </div>
            ))}
          </div>
        </Surface>
      ) : null}
      {sources.isError ? (
        <Surface className="p-5" role="alert">
          <p className="text-sm text-destructive">无法读取网页资料：{sources.error.message}</p>
        </Surface>
      ) : null}
      {sourceDetail ? (
        <SourceDetailDialog
          onOpenChange={(open) => {
            if (!open) setSourceDetail(null);
          }}
          onRequestDelete={(source) => {
            removeSource.reset();
            setSourceDetail(null);
            setSourceDeleteTarget(source);
          }}
          open={Boolean(sourceDetail)}
          source={sourceDetail}
        />
      ) : null}
      {sourceAssociation ? (
        <SourceAssociationDialog
          onLinked={() => setSourceDetail(sourceAssociation)}
          onOpenChange={(open) => {
            if (!open) setSourceAssociation(null);
          }}
          open
          requireAssociation
          source={sourceAssociation}
        />
      ) : null}
      <DeleteImpactDialog
        error={sourceDeleteImpact.error?.message ?? removeSource.error?.message}
        impact={sourceDeleteImpact.data}
        isConfirming={removeSource.isPending}
        isLoading={
          sourceDeleteImpact.isPending && Boolean(sourceDeleteTarget)
        }
        objectLabel={sourceDeleteTarget?.title ?? "网页来源"}
        onConfirm={() => {
          if (!sourceDeleteTarget || !sourceDeleteImpact.data) return;
          removeSource.mutate({
            sourceId: sourceDeleteTarget.id,
            confirmationText: sourceDeleteImpact.data.confirmation_text,
          });
        }}
        onOpenChange={(open) => {
          if (!open && !removeSource.isPending) {
            removeSource.reset();
            setSourceDeleteTarget(null);
          }
        }}
        open={Boolean(sourceDeleteTarget)}
        title={
          sourceDeleteTarget
            ? `永久删除网页来源「${sourceDeleteTarget.title}」？`
            : undefined
        }
      />
      <Dialog onOpenChange={setSupportOpen} open={supportOpen}>
        <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>支持的文件格式</DialogTitle>
            <DialogDescription>
              以下能力来自当前服务端。文件可以安全上传，不代表其内容一定能够解析或用于问答。
            </DialogDescription>
          </DialogHeader>
          {parserCapabilities.isPending ? (
            <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" />
              正在读取服务端格式能力…
            </p>
          ) : null}
          {parserCapabilities.isError ? (
            <p
              className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
              role="alert"
            >
              无法读取服务端格式能力：{parserCapabilities.error.message}。上传和解析仍会由服务端再次校验。
            </p>
          ) : null}
          {!parserCapabilities.isPending && !parserCapabilities.isError ? (
            <div className="divide-y border-y">
              {(parserCapabilities.data ?? []).map((capability) => (
                <div
                  className="grid gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start"
                  key={capability.capability_id}
                >
                  <div className="min-w-0">
                    <p className="font-mono text-xs font-medium">
                      {capability.extensions.join(" · ")}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      {capability.reason}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 sm:justify-end">
                    <Badge variant="secondary">
                      {parserModeLabel(capability.mode)}
                    </Badge>
                    <StatePill
                      label={capability.available ? "可解析" : "当前不可用"}
                      status={capability.available ? "approved" : "pending"}
                    />
                  </div>
                </div>
              ))}
              {!parserCapabilities.data?.length ? (
                <p className="py-6 text-sm text-muted-foreground">
                  服务端当前未声明可用的解析器。
                </p>
              ) : null}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
      <Dialog onOpenChange={setWebDialogOpen} open={webDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>保存网页到资料库</DialogTitle>
            <DialogDescription>
              系统会校验授权域名与私网地址边界，抓取成功后持久化正文快照。
            </DialogDescription>
          </DialogHeader>
          <Label htmlFor="source-url">网页 URL</Label>
          <Input
            id="source-url"
            onChange={(event) => setWebUrl(event.target.value)}
            placeholder="https://example.com/article"
            value={webUrl}
          />
          <DialogFooter>
            <Button
              disabled={
                fetchPage.isPending || !webUrl.trim().startsWith("http")
              }
              onClick={() => fetchPage.mutate()}
            >
              {fetchPage.isPending ? "抓取中…" : "抓取并保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog onOpenChange={setPolicyOpen} open={policyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>网页缓存策略</DialogTitle>
            <DialogDescription>
              每条网页资料保留原始 URL、最终 URL、抓取时间、内容哈希、授权域名和
              Provider 标识；重复正文会复用
              SourceRecord。删除或重新抓取不会静默改写图谱、路线或掌握度。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            {(sources.data ?? []).slice(0, 5).map((source) => (
              <div className="rounded-lg border p-3" key={source.id}>
                <p className="font-medium">{source.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {source.authorized_domain} ·{" "}
                  {new Date(source.created_at).toLocaleString()} ·{" "}
                  {source.cache_status}
                </p>
              </div>
            ))}
            {!sources.data?.length ? (
              <p className="text-muted-foreground">当前还没有网页缓存记录。</p>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </PageFrame>
  );
}

export function SearchPage() {
  const { workspaceId = "" } = useParams();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [saved, setSaved] = useState<SourceRecord | null>(null);
  const [domainInput, setDomainInput] = useState("");
  const queryClient = useQueryClient();
  const settingsQueryKey = workspaceQueryKey(workspaceId, "settings");
  const settings = useQuery({ queryKey: settingsQueryKey, queryFn: listSettings });
  const fetchPolicy = useMemo<WebFetchPolicy>(() => {
    const raw = settings.data?.find((item) => item.key === "web_fetch.policy")?.value;
    if (!raw || typeof raw !== "object") return { allow_without_confirmation: false, allowed_domains: [] };
    const value = raw as Partial<WebFetchPolicy>;
    return {
      allow_without_confirmation: value.allow_without_confirmation === true,
      allowed_domains: Array.isArray(value.allowed_domains) ? value.allowed_domains.filter((item): item is string => typeof item === "string") : [],
    };
  }, [settings.data]);
  const updateFetchPolicy = useMutation({
    mutationFn: (policy: WebFetchPolicy) => updateSetting("web_fetch.policy", policy),
    onSuccess: (updated) => {
      queryClient.setQueryData(settingsQueryKey, (current: unknown) =>
        Array.isArray(current)
          ? [...current.filter((item) => item?.key !== updated.key), updated]
          : [updated],
      );
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "网页抓取权限更新失败"),
  });
  const search = useMutation({
    mutationFn: () => searchWeb({ query, max_results: 6 }),
    onSuccess: setResult,
  });
  const save = useMutation({
    mutationFn: (item: SearchResult) => fetchSource(item.url),
    onSuccess: (source) => {
      setSaved(source);
      toast.success("已保存到资料库，请选择关联目标");
    },
    onError: (error) => toast.error(error.message),
  });
  const fetchUrl = useMutation({
    mutationFn: () => fetchSource(query.trim()),
    onSuccess: (source) => {
      setSaved(source);
      toast.success("全文已抓取并保存，请选择关联目标");
    },
    onError: (error) => toast.error(error.message),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) search.mutate();
  }
  return (
    <PageFrame>
      <PageIntro
        description="SearchProvider 负责秒级查询，FetchProvider 负责正文获取；Deep Research 是独立的分钟级任务。"
        eyebrow="Search & fetch"
        title="联网搜索与网页获取"
      />
      <Surface className="p-5">
        <form className="flex flex-col gap-3 sm:flex-row" onSubmit={submit}>
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-10"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索关键词，或输入完整 URL 后抓全文"
              value={query}
            />
          </div>
          <Button disabled={search.isPending} type="submit">
            {search.isPending ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Search className="size-4" />
            )}
            搜索
          </Button>
          <Button
            disabled={fetchUrl.isPending || !query.trim().startsWith("http")}
            onClick={() => fetchUrl.mutate()}
            type="button"
            variant="outline"
          >
            <Globe2 className="size-4" />
            {fetchUrl.isPending ? "抓取中…" : "抓全文"}
          </Button>
        </form>
      </Surface>
      <Surface className="space-y-4 p-5">
        <SectionHeading
          description="管理当前工作区的网页抓取确认规则；即使关闭确认，仍会保留公共 URL 与重定向安全校验。"
          title="网页抓取权限"
        />
        <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
          <div className="min-w-0">
            <Label htmlFor="allow-web-fetch-without-confirmation">允许抓取任意网页，不再询问</Label>
            <p className="mt-1 text-xs text-muted-foreground">默认关闭。开启后，智能体抓取公共网页时不显示授权卡片。</p>
          </div>
          <Switch
            checked={fetchPolicy.allow_without_confirmation}
            disabled={updateFetchPolicy.isPending}
            id="allow-web-fetch-without-confirmation"
            onCheckedChange={(checked) => updateFetchPolicy.mutate({ ...fetchPolicy, allow_without_confirmation: checked })}
          />
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            onChange={(event) => setDomainInput(event.target.value)}
            placeholder="添加域名，例如 github.com"
            value={domainInput}
          />
          <Button
            disabled={updateFetchPolicy.isPending || !domainInput.trim()}
            onClick={() => {
              const domain = domainInput.trim();
              if (!domain) return;
              updateFetchPolicy.mutate({ ...fetchPolicy, allowed_domains: [...fetchPolicy.allowed_domains, domain] }, { onSuccess: () => setDomainInput("") });
            }}
            variant="outline"
          >添加</Button>
        </div>
        {fetchPolicy.allowed_domains.length ? (
          <div className="flex flex-wrap gap-2">
            {fetchPolicy.allowed_domains.map((domain) => (
              <Badge className="gap-1.5 py-1" key={domain} variant="secondary">
                {domain}
                <button
                  aria-label={`移除 ${domain}`}
                  className="text-muted-foreground hover:text-destructive"
                  disabled={updateFetchPolicy.isPending}
                  onClick={() => updateFetchPolicy.mutate({ ...fetchPolicy, allowed_domains: fetchPolicy.allowed_domains.filter((item) => item !== domain) })}
                  type="button"
                >×</button>
              </Badge>
            ))}
          </div>
        ) : <p className="text-sm text-muted-foreground">尚未设置永久允许的域名。需要时可在聊天授权卡片中选择“以后都允许”。</p>}
      </Surface>
      <Surface className="space-y-4 p-5">
        <SectionHeading
          description="工作区级来源限制会同时用于普通联网搜索与 Deep Research；请求级域名只能进一步缩小范围。"
          title="搜索与 Deep Research 来源白名单"
        />
        <ResearchDomainAllowlistEditor />
      </Surface>
      {result ? (
        <div className="rounded-xl border border-blue-200 bg-blue-50/55 px-4 py-3 text-sm text-blue-800 dark:border-blue-900 dark:bg-blue-950/25 dark:text-blue-200">
          <strong>
            {result.remote_capability ? "远程搜索结果" : "本地演示索引"}
          </strong>
          ：{result.notice}
        </div>
      ) : null}
      <Surface className="p-5">
        <SectionHeading
          description="加入来源后只形成证据包候选，不直接发布路线"
          title="搜索结果与引用预览"
        />
        <div className="mt-4 divide-y">
          {(result?.results ?? []).map((item, index) => (
            <article
              className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center"
              key={`${item.url}-${index}`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-semibold">{item.title}</p>
                  <StatePill
                    status={
                      item.source_type === "local_notice"
                        ? "pending"
                        : "approved"
                    }
                    label={item.source_type}
                  />
                </div>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                  {item.snippet}
                </p>
                <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                  {item.url}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button asChild size="sm" variant="outline">
                  <a href={item.url} rel="noreferrer" target="_blank">
                    打开
                  </a>
                </Button>
                <Button
                  disabled={save.isPending || !item.url.startsWith("http")}
                  onClick={() => save.mutate(item)}
                  size="sm"
                >
                  {save.isPending ? "保存中…" : "保存到资料库"}
                </Button>
              </div>
            </article>
          ))}
          {!result ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              输入关键词后开始搜索。
            </p>
          ) : null}
          {result && !result.results.length ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              没有找到可保存的结果。
            </p>
          ) : null}
        </div>
      </Surface>
      <Surface className="p-5">
        <SectionHeading title="搜索不是路线负责人" />
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          搜索和抓取只补充资料与证据。需要多来源综合时创建 Deep Research
          任务；其结果仍回到图谱或路线草稿审核。
        </p>
        <Button asChild className="mt-4" size="sm" variant="outline">
          <Link to={`/w/${workspaceId}/research/tasks/new`}>
            <Sparkles className="size-4" />
            创建研究任务
            <ArrowRight className="size-4" />
          </Link>
        </Button>
      </Surface>
      {saved ? (
        <SourceAssociationDialog
          onOpenChange={(open) => {
            if (!open) setSaved(null);
          }}
          open
          requireAssociation
          source={saved}
        />
      ) : null}
    </PageFrame>
  );
}

function packList(pack: Record<string, unknown>, key: string): string[] {
  const value = pack[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

export function ResearchNewTaskPage() {
  const { workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const [budget, setBudget] = useState(0);
  const [plan, setPlan] = useState<ResearchPlan | null>(null);
  const queryClient = useQueryClient();
  const preview = useMutation({
    mutationFn: () =>
      planResearch({ question, budget_cny: budget, source_scope: [] }),
    onSuccess: setPlan,
    onError: (error) => toast.error(error.message),
  });
  const create = useMutation({
    mutationFn: () =>
      createResearchJob({ question, budget_cny: budget, source_scope: [] }),
    onSuccess: (created) => {
      setPlan(null);
      toast.success(
        created.status === "awaiting_approval"
          ? "研究任务等待预算确认"
          : "研究任务已创建",
      );
      void queryClient.invalidateQueries({ queryKey: ["research"] });
      navigate(
        `/w/${encodeURIComponent(workspaceId)}/research/tasks/${encodeURIComponent(created.id)}`,
      );
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <PageFrame>
      <PageIntro
        actions={
          <div className="flex gap-2">
            <Button
              disabled={preview.isPending || !question.trim()}
              onClick={() => preview.mutate()}
              variant="outline"
            >
              预估计划
            </Button>
            <Button
              disabled={create.isPending || !question.trim()}
              onClick={() => create.mutate()}
            >
              <Sparkles className="size-4" />
              {create.isPending ? "创建中…" : "创建研究任务"}
            </Button>
          </div>
        }
        description="研究产物是可审核证据包，不会直接发布图谱、路线或授予成长星级。"
        eyebrow="Deep Research"
        title="新建研究任务"
      />
      <Surface className="p-5">
        <SectionHeading
          description="先预估 Provider、费用和审批要求，再创建可追踪任务。"
          title="研究计划"
        />
        <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_10rem_auto]">
          <div>
            <Label htmlFor="research-question">研究问题</Label>
            <Input
              className="mt-2"
              id="research-question"
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="输入需要多来源综合的问题"
              value={question}
            />
          </div>
          <div>
            <Label htmlFor="research-budget">预算（CNY）</Label>
            <Input
              className="mt-2"
              id="research-budget"
              min="0"
              onChange={(event) => setBudget(Number(event.target.value) || 0)}
              step="0.01"
              type="number"
              value={budget}
            />
          </div>
          <div className="flex items-end">
            <Badge className="h-9 px-3" variant="secondary">
              {plan
                ? `${plan.provider_id} · 预计 ¥${plan.estimated_cost_cny.toFixed(2)}`
                : "尚未预估"}
            </Badge>
          </div>
        </div>
      </Surface>
      <div className="grid gap-5 lg:grid-cols-[.85fr_1.15fr]">
        <Surface className="p-5">
          <SectionHeading title="实时任务流" />
          <p className="py-8 text-center text-sm text-muted-foreground">
            创建任务后，这里会显示服务端事件流。
          </p>
        </Surface>
        <Surface className="p-5">
          <SectionHeading title="证据包摘要" />
          <p className="py-10 text-center text-sm text-muted-foreground">
            尚无研究产物。
          </p>
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/55 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200">
            <strong>审核边界：</strong>证据包
            只进入候选审核，不会直接改写图谱或路线。
          </div>
        </Surface>
      </div>
    </PageFrame>
  );
}

export function ResearchPage() {
  const { taskId, workspaceId = "" } = useParams();
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["research"], queryFn: listResearchJobs });
  const job = taskId
    ? jobs.data?.find((item) => item.id === taskId)
    : undefined;
  const events = useQuery({
    queryKey: ["research-events", job?.id],
    queryFn: () => listResearchEvents(job!.id),
    enabled: Boolean(job),
  });
  const approve = useMutation({
    mutationFn: () => approveResearch(job!.id),
    onSuccess: () => {
      toast.success("预算已确认，研究任务已启动");
      void queryClient.invalidateQueries({ queryKey: ["research"] });
      void queryClient.invalidateQueries({
        queryKey: ["research-events", job?.id],
      });
    },
    onError: (error) => toast.error(error.message),
  });
  const cancel = useMutation({
    mutationFn: () => cancelResearch(job!.id),
    onSuccess: () => {
      toast.success("已提交取消");
      void queryClient.invalidateQueries({ queryKey: ["research"] });
      void queryClient.invalidateQueries({
        queryKey: ["research-events", job?.id],
      });
    },
    onError: (error) => toast.error(error.message),
  });
  if (jobs.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (jobs.isError)
    return (
      <PageFrame>
        <ErrorState message={jobs.error.message} />
      </PageFrame>
    );
  if (!job)
    return (
      <PageFrame>
        <ErrorState message="研究任务不存在或无权访问。" />
      </PageFrame>
    );
  const pack = (job.evidence_pack ?? {}) as Record<string, unknown>;
  const timeline = (events.data ?? []).map((event) => ({
    time: new Date(event.created_at).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
    title: event.event_type,
    detail: JSON.stringify(event.payload),
    status: "done" as const,
  }));
  const canCancel = ![
    "completed",
    "completed_local_demo",
    "completed_source_collection",
    "failed",
    "cancelled",
    "rejected",
  ].includes(job.status);
  return (
    <PageFrame>
      <PageIntro
        actions={
          <Button asChild size="sm" variant="outline">
            <Link to={`/w/${encodeURIComponent(workspaceId)}/research/tasks/new`}>
              <Sparkles className="size-4" />
              新建任务
            </Link>
          </Button>
        }
        description="研究产物是可审核证据包，不会直接发布图谱、路线或授予成长星级。"
        eyebrow="Deep Research"
        title="研究任务详情"
      />
      <div className="grid gap-5 lg:grid-cols-[.85fr_1.15fr]">
        <Surface className="p-5">
          <SectionHeading
            action={
              canCancel ? (
                <Button
                  disabled={cancel.isPending}
                  onClick={() => cancel.mutate()}
                  size="xs"
                  variant="outline"
                >
                  取消任务
                </Button>
              ) : null
            }
            title="实时任务流"
          />
          {job.status === "awaiting_approval" ? (
            <div className="mt-4 rounded-xl border border-amber-200 p-4">
              <p className="text-sm">
                预计费用 ¥{job.estimated_cost_cny.toFixed(2)}
                ，批准后才会调用远程 Provider。
              </p>
              <Button
                className="mt-3"
                disabled={approve.isPending}
                onClick={() => approve.mutate()}
                size="sm"
              >
                确认预算并启动
              </Button>
            </div>
          ) : null}
          <div className="mt-5">
            {timeline.length ? (
              <Timeline items={timeline} />
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">
                创建任务后，这里会显示服务端事件流。
              </p>
            )}
          </div>
        </Surface>
        <Surface className="p-5">
          <SectionHeading action={<StatePill status={job.status} />} title="证据包摘要" />
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {[
              [
                "事实/主张",
                packList(pack, "facts")[0] ??
                  packList(pack, "claims")[0] ??
                  "暂无",
              ],
              ["候选概念", packList(pack, "candidate_concepts")[0] ?? "暂无"],
              ["冲突", packList(pack, "conflicts")[0] ?? "暂无"],
              ["覆盖缺口", packList(pack, "coverage_gaps")[0] ?? "暂无"],
            ].map(([title, text], index) => (
              <div className="rounded-xl border p-4" key={title}>
                <div className="flex items-center gap-2">
                  <span
                    className={
                      index === 3
                        ? "size-2 rounded-full bg-amber-500"
                        : "size-2 rounded-full bg-primary"
                    }
                  />
                  <p className="text-sm font-semibold">{title}</p>
                </div>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  {text}
                </p>
              </div>
            ))}
          </div>
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/55 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/25 dark:text-amber-200">
            <strong>审核边界：</strong>证据包
            只进入候选审核，不会直接改写图谱或路线。
          </div>
        </Surface>
      </div>
    </PageFrame>
  );
}

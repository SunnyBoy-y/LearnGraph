import { useFileStreamUrl } from "@/hooks/use-file-stream-url";
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
  ArrowDownWideNarrow,
  ChevronDown,
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
  LoaderCircle,
  MoreHorizontal,
  RefreshCcw,
  Search,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from '@/lib/notifications';

import {
  deleteFileConfirmed,
  deleteFilesBatch,
  deleteSourceConfirmed,
  fileStreamUrl,
  fetchSource,
  getFileBatchDeleteImpact,
  getFileDeleteImpact,
  getFileStorageSummary,
  getSourceDeleteImpact,
  listFileParserCapabilities,
  listFiles,
  listSourceRecords,
  parseFile,
  pollParseJob,
  searchWeb,
  uploadFile,
} from "@/api";
import { FileDiagnosticsDialog } from "@/components/resources/file-diagnostics-dialog";
import { SourceAssociationDialog } from "@/components/resources/source-association-dialog";
import { SourceDetailDialog } from "@/components/resources/source-detail-dialog";
import { PaperSearchPanel } from "@/features/resources/paper-panels";
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { SearchResponse } from "@/types/search";
import type { SearchResult } from "@/types/search";
import type { FileParserCapability, FileRecord } from "@/types/files";
import type { SourceRecord } from "@/types/workflow";
import "./file-library.css";

const FILE_PAGE_SIZE = 20;
const MAX_BATCH_DELETE_FILES = 100; // FileBatchSelection API limit.

function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

type IndexStatus = "indexed" | "pending" | "attachment" | "processing" | "failed" | "unavailable";
type FileFilter = "all" | IndexStatus;

const indexStatuses: Record<IndexStatus, { label: string; description: string; color: string }> = {
  indexed: { label: "可问答", description: "已建立知识索引，可以被 Agent 检索。", color: "text-emerald-700 dark:text-emerald-400" },
  attachment: { label: "仅附件", description: "文件已保存，但不进入知识检索范围。", color: "text-muted-foreground" },
  pending: { label: "未建索引", description: "尚未建立知识索引，可按需建立。", color: "text-amber-700 dark:text-amber-400" },
  processing: { label: "解析中", description: "已排队或正在提取内容、建立索引。", color: "text-blue-700 dark:text-blue-400" },
  failed: { label: "解析失败", description: "请查看失败原因，或重新解析文件。", color: "text-destructive" },
  unavailable: { label: "缺少解析器", description: "当前环境缺少此格式所需的解析器。", color: "text-amber-700 dark:text-amber-400" },
};

const fileFilters: FileFilter[] = ["all", "indexed", "attachment", "pending", "processing", "failed", "unavailable"];

const fileSorts = [
  ["recent", "最近上传"], ["oldest", "最早上传"],
  ["name-asc", "名称正序"], ["name-desc", "名称倒序"],
  ["size-desc", "大小从大到小"], ["size-asc", "大小从小到大"],
] as const;
type FileSort = typeof fileSorts[number][0];
const fileNameCollator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });

function compareFiles(a: FileRecord, b: FileRecord, sort: FileSort) {
  const recent = () => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0);
  switch (sort) {
    case "oldest": return -recent();
    case "name-asc": return fileNameCollator.compare(a.original_name, b.original_name) || recent();
    case "name-desc": return fileNameCollator.compare(b.original_name, a.original_name) || recent();
    case "size-desc": return b.size_bytes - a.size_bytes || recent();
    case "size-asc": return a.size_bytes - b.size_bytes || recent();
    default: return recent();
  }
}

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

function fileStatus(file: FileRecord, active = false): IndexStatus {
  if (active || ["queued", "running", "processing"].includes(file.parse_status)) return "processing";
  if (file.parse_status === "indexed") return "indexed";
  if (file.parse_status === "failed") return "failed";
  if (file.parse_status === "processor_required") return "unavailable";
  return isAttachmentOnlyFile(file) ? "attachment" : "pending";
}

function IndexStatusButton({ status, onClick }: { status: IndexStatus; onClick: () => void }) {
  const { label, color } = indexStatuses[status];
  return (
    <button
      aria-label={`${label}：查看索引详情`}
      className={`inline-flex min-h-7 items-center gap-1.5 whitespace-nowrap rounded-sm text-xs hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${color}`}
      onClick={onClick}
      type="button"
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {label}
    </button>
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
  if (["word", "rtf", "text", "html"].includes(kind)) return "document";
  if (["image", "spreadsheet", "powerpoint", "pdf", "audio", "video"].includes(kind)) return kind as FileCategory;
  return "other";
}

const fileTones: Record<FileCategory, string> = {
  all: "slate", other: "slate", image: "violet", document: "blue", spreadsheet: "green",
  powerpoint: "amber", pdf: "rose", audio: "violet", video: "violet", archive: "amber",
};

function FileIcon({ file }: { file: FileRecord }) {
  const category = fileCategory(file);
  const Icon = { image: FileImage, document: FileText, spreadsheet: FileSpreadsheet,
    powerpoint: Presentation, pdf: FileText, audio: Music, video: Video,
    archive: Archive, other: File, all: File }[category];
  const DisplayIcon = /\.(html?|jsx?|tsx?|py|json|css)$/i.test(file.original_name) ? FileCode : Icon;
  return <span aria-hidden="true" className={`file-library-tone file-library-tone--${fileTones[category]} grid size-8 shrink-0 place-items-center rounded-md`}><DisplayIcon className="size-4" /></span>;
}

function FileThumbnail({ file }: { file: FileRecord }) {
  const isImage = fileCategory(file) === "image";
  const { source: url, failed: streamFailed } = useFileStreamUrl(isImage ? file.id : undefined);
  const [failedId, setFailedId] = useState("");
  const failed = failedId === file.id || streamFailed;
  const format = uploadExtension(file.original_name).slice(1).toUpperCase();
  const archive = fileCategory(file) === "archive";
  return (
    <span className={`file-library-tone file-library-tone--${fileTones[fileCategory(file)]} grid h-27 w-full place-items-center overflow-hidden rounded-md`}>
      {url && !failed ? (
        <img alt="" className="size-full min-h-0 object-contain" decoding="async" loading="lazy" onError={() => setFailedId(file.id)} src={url} />
      ) : (
        <span aria-hidden="true" className="relative grid h-18 w-14 place-items-center">
          <svg className="size-full" fill="none" viewBox="0 0 56 72">
            <path className="fill-card stroke-current" d="M8 3h27l13 13v49H8z" strokeWidth="1.25" />
            <path d="M35 3v13h13" stroke="currentColor" strokeWidth="1.25" />
            {archive ? <path d="M25 13v4m5 1v4m-5 1v4m5 1v4m-5 1v4m5 1v4" stroke="currentColor" strokeWidth="4" opacity=".55" /> : <path d="M16 25h23m-23 8h23m-23 8h16" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" opacity=".5" />}
          </svg>
          <span className="file-library-format absolute bottom-0 max-w-full truncate rounded px-1.5 text-[10px] leading-4 font-semibold">{format || fileType(file)}</span>
        </span>
      )}
    </span>
  );
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

/**
 * 可执行文件 / 脚本 / 磁盘镜像：与后端 `SPECIAL_BINARY_EXTENSIONS` 对齐。
 * 上传前先给即时提示，服务端仍会二次校验（前端提示不是安全边界）。
 */
const BLOCKED_UPLOAD_EXTENSIONS = new Set([
  ".exe",
  ".dll",
  ".so",
  ".dylib",
  ".msi",
  ".bat",
  ".cmd",
  ".com",
  ".scr",
  ".sys",
  ".apk",
  ".dmg",
  ".iso",
  ".img",
  ".bin",
  ".appimage",
]);

/** 小写扩展名（含点）；无扩展名时返回空串。 */
function uploadExtension(name: string) {
  const index = name.lastIndexOf(".");
  if (index <= 0) return "";
  return name.slice(index).toLowerCase();
}

export function SourcesPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [policyOpen, setPolicyOpen] = useState(false);
  const [supportOpen, setSupportOpen] = useState(false);
  const [fileFilter, setFileFilter] = useState<FileFilter>("all");
  const [typeFilter, setTypeFilter] = useState<FileCategory>("all");
  const [viewMode, setViewMode] = useState<"list" | "grid">("list");
  const [fileSort, setFileSort] = useState<FileSort>("recent");
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
    for (const file of Array.from(selectedFiles)) {
      if (BLOCKED_UPLOAD_EXTENSIONS.has(uploadExtension(file.name))) {
        toast.error(
          `「${file.name}」属于可执行文件/脚本/磁盘镜像，资料库不接受此类文件。`,
        );
        continue;
      }
      uploadRef.current(file);
    }
  }, []);
  const downloadFile = async (file: FileRecord) => {
    if (downloadViaNative(
      toAbsoluteApiUrl(`/api/v1/files/${encodeURIComponent(file.id)}/content`),
      file.original_name || "download",
    )) return;
    const url = await fileStreamUrl(file.id);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = file.original_name || "download";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  };
  const download = useMutation({
    mutationFn: downloadFile,
    onError: (error) => toast.error(error.message),
  });
  const batchDownload = useMutation({
    mutationFn: async (selected: FileRecord[]) => {
      let requested = 0;
      for (const file of selected) {
        try {
          await downloadFile(file);
          requested++;
        } catch {
          // Continue requesting the other selected files if a ticket request fails.
        }
      }
      return { requested, failed: selected.length - requested };
    },
    onSuccess: ({ requested, failed }) => {
      if (failed) toast.warning(`已请求下载 ${requested} 项，${failed} 项无法发起下载`);
      else toast.info(`已请求下载 ${requested} 项。如有提示，请允许此站点下载多个文件。`);
    },
  });
  const invalidateFileIndex = (fileId: string) => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["file-chunks", fileId] }),
    queryClient.invalidateQueries({ queryKey: ["file-references", fileId] }),
    queryClient.invalidateQueries({ queryKey: ["document-revisions", fileId] }),
  ]);
  const parse = useMutation({
    mutationFn: async (fileId: string) => pollParseJob(await parseFile(fileId)),
    onSuccess: () => toast.success("解析任务已完成"),
    onError: (error) => toast.error(error.message),
    onSettled: async (_job, _error, fileId) => {
      await Promise.all([invalidateFileIndex(fileId), queryClient.invalidateQueries({ queryKey: ["files"] })]);
    },
  });
  const batchParse = useMutation({
    mutationFn: async (fileIds: string[]) => {
      const failed: string[] = [];
      // Bound concurrent submissions and polling when selections span multiple pages.
      let cursor = 0;
      await Promise.all(Array.from({ length: Math.min(4, fileIds.length) }, async () => {
        while (cursor < fileIds.length) {
          const fileId = fileIds[cursor++];
          try {
            await pollParseJob(await parseFile(fileId));
          } catch (error) {
            failed.push(error instanceof Error ? error.message : "解析任务失败");
          }
        }
      }));
      return { completed: fileIds.length - failed.length, failed };
    },
    onSuccess: ({ completed, failed }) => {
      if (!failed.length) toast.success(`已完成 ${completed} 个文件索引`);
      else if (completed) toast.warning(`已完成 ${completed} 个文件索引，${failed.length} 个失败`);
      else toast.error(`批量索引失败：${failed[0]}`);
    },
    onSettled: async (_result, _error, fileIds) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["files"] }),
        ...fileIds.map(invalidateFileIndex),
      ]);
    },
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
  }, [fileFilter, fileSearch, fileSort, typeFilter]);

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
  const activeFileIds = new Set(batchParse.isPending ? batchParse.variables : []);
  if (parse.isPending) activeFileIds.add(parse.variables);
  const indexStatus = (file: FileRecord) => fileStatus(file, activeFileIds.has(file.id));
  const counts: Record<FileFilter, number> = { all: files.data.length, indexed: 0, attachment: 0, pending: 0, processing: 0, failed: 0, unavailable: 0 };
  const normalizedFileSearch = fileSearch.trim().toLocaleLowerCase();
  const filteredFiles = files.data.filter((file) => {
    const status = indexStatus(file);
    counts[status]++;
    return (fileFilter === "all" || status === fileFilter) &&
      (typeFilter === "all" || fileCategory(file) === typeFilter) &&
      (!normalizedFileSearch || file.original_name.toLocaleLowerCase().includes(normalizedFileSearch));
  }).sort((a, b) => compareFiles(a, b, fileSort));
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
  const indexBusy = parse.isPending || batchParse.isPending;
  const canIndex = (file: FileRecord, status = indexStatus(file)) => {
    const available = parserCapabilityForFile(file, parserCapabilities.data)?.available;
    return !isAttachmentOnlyFile(file) && available !== false &&
      (status === "pending" || status === "failed" || (status === "unavailable" && available === true));
  };
  const selectedFiles = files.data.filter((file) => selectedFileIds.has(file.id));
  const selectedToIndex = selectedFiles.filter((file) => ["pending", "unavailable"].includes(indexStatus(file)) && canIndex(file));
  const selectedToRetry = selectedFiles.filter((file) => indexStatus(file) === "failed" && canIndex(file));
  const currentDiagnosticFile = files.data.find((file) => file.id === diagnosticFile?.id);
  const storageBytes = storageSummary.data?.total_bytes ?? files.data.reduce((sum, file) => sum + file.size_bytes, 0);
  const fileActions = (file: FileRecord) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button aria-label={`打开 ${file.original_name} 的更多操作`} size="icon-sm" variant="ghost"><MoreHorizontal className="size-4" /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem onSelect={() => navigate(`../documents/${file.id}`)}><FileText />打开学习</DropdownMenuItem>
        <DropdownMenuItem disabled={download.isPending || batchDownload.isPending} onSelect={() => download.mutate(file)}><Download />下载文件</DropdownMenuItem>
        {canIndex(file) ? <DropdownMenuItem disabled={indexBusy} onSelect={() => parse.mutate(file.id)}><RefreshCcw />{indexStatus(file) === "failed" ? "重新解析" : "建立索引"}</DropdownMenuItem> : null}
        <DropdownMenuItem onSelect={() => setDiagnosticFile(file)}><Search />解析与引用诊断</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={remove.isPending || activeFileIds.has(file.id)} onSelect={() => setDeleteTarget(file)} variant="destructive"><Trash2 />删除文件</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
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
    <PageFrame className="file-library-page max-w-[1600px] gap-3 pb-16">
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
      <PageIntro title="资料库" />
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

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b pb-3">
        <div aria-label="资料状态筛选" className="flex min-w-0 flex-wrap gap-1" role="group">
          {fileFilters.filter((filter) => filter !== "unavailable" || counts[filter] > 0 || fileFilter === filter).map((filter) => (
            <Button
              aria-pressed={fileFilter === filter}
              className="h-8 shrink-0 rounded-md px-2.5"
              key={filter}
              onClick={() => setFileFilter(filter)}
              size="sm"
              variant={fileFilter === filter ? "secondary" : "ghost"}
            >
              {filter === "all" ? "全部" : indexStatuses[filter].label}
              <span className="text-xs font-normal tabular-nums text-muted-foreground">{counts[filter]}</span>
            </Button>
          ))}
        </div>
        <span aria-label={`上传资料占用 ${bytes(storageBytes)}`} className="inline-flex shrink-0 items-center gap-1.5 text-xs tabular-nums text-muted-foreground">
          <HardDrive aria-hidden="true" className="size-3.5" /> {bytes(storageBytes)}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[180px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="搜索资料"
              className="h-9 pl-9"
              onChange={(event) => setFileSearch(event.target.value)}
              placeholder="搜索文件名"
              value={fileSearch}
            />
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                aria-label="按文件类型筛选"
                className="h-9 shrink-0"
                variant="outline"
              >
                {fileCategories.find(([value]) => value === typeFilter)?.[1]}
                <ChevronRight className="size-3.5 rotate-90" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {fileCategories.map(([value, label]) => (
                <DropdownMenuItem key={value} onSelect={() => setTypeFilter(value)}>
                  <span className="flex-1">{label}</span>
                  {typeFilter === value ? <span aria-label="已选">✓</span> : null}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button aria-label={`排序：${fileSorts.find(([value]) => value === fileSort)?.[1]}`} className="h-9 shrink-0" variant="outline">
                <ArrowDownWideNarrow className="size-4" />
                {fileSorts.find(([value]) => value === fileSort)?.[1]}
                <ChevronDown className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {fileSorts.map(([value, label]) => (
                <DropdownMenuItem key={value} onSelect={() => setFileSort(value)}>
                  <span className="flex-1">{label}</span>
                  {fileSort === value ? <span aria-label="已选">✓</span> : null}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <div role="group" aria-label="资料显示方式" className="flex shrink-0 gap-0.5 rounded-lg border p-0.5">
            <Button aria-label="列表视图" aria-pressed={viewMode === "list"} size="icon-sm" variant={viewMode === "list" ? "secondary" : "ghost"} onClick={() => setViewMode("list")}><List className="size-4" /></Button>
            <Button aria-label="网格视图" aria-pressed={viewMode === "grid"} size="icon-sm" variant={viewMode === "grid" ? "secondary" : "ghost"} onClick={() => setViewMode("grid")}><LayoutGrid className="size-4" /></Button>
          </div>
          <Button className="ml-auto h-9 shrink-0" disabled={upload.isPending} onClick={chooseFiles} size="sm">
            {upload.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <UploadCloud className="size-4" />}
            {upload.isPending ? "上传中…" : "上传资料"}
          </Button>
      </div>

      {selectedFiles.length > 0 ? (
        <div aria-label="批量操作" className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/35 px-3 py-2">
          <span aria-live="polite" className="mr-1 text-xs font-medium tabular-nums">已选 {selectedFiles.length} 项</span>
          <Button disabled={download.isPending || batchDownload.isPending} onClick={() => batchDownload.mutate(selectedFiles)} size="xs" variant="outline">
            {batchDownload.isPending ? <LoaderCircle className="animate-spin" /> : <Download />} 批量下载
          </Button>
          <Button disabled={indexBusy || !selectedToIndex.length} onClick={() => batchParse.mutate(selectedToIndex.map((file) => file.id))} size="xs" variant="outline">
            <RefreshCcw className={batchParse.isPending ? "animate-spin" : ""} /> 批量建索引{selectedToIndex.length ? ` (${selectedToIndex.length})` : ""}
          </Button>
          <Button disabled={indexBusy || !selectedToRetry.length} onClick={() => batchParse.mutate(selectedToRetry.map((file) => file.id))} size="xs" variant="outline">
            <RefreshCcw className={batchParse.isPending ? "animate-spin" : ""} /> 批量重新解析{selectedToRetry.length ? ` (${selectedToRetry.length})` : ""}
          </Button>
          <Button disabled={removeBatch.isPending || batchFileIds.length > MAX_BATCH_DELETE_FILES || selectedFiles.some((file) => activeFileIds.has(file.id))} onClick={() => setBatchDeleteOpen(true)} size="xs" variant="destructive">
            <Trash2 /> 批量删除
          </Button>
          {batchFileIds.length > MAX_BATCH_DELETE_FILES ? <span className="text-xs text-muted-foreground">每次最多删除 {MAX_BATCH_DELETE_FILES} 个文件</span> : null}
          <Button onClick={() => setSelectedFileIds(new Set())} size="xs" variant="ghost">取消选择</Button>
        </div>
      ) : null}

      <div>
        {pagedFiles.length > 0 && (viewMode === "grid" ? (
          <div className="file-library-grid grid gap-3">
            {pagedFiles.map((file) => {
              const isSelected = selectedFileIds.has(file.id);
              return (
                <article key={file.id} className={`group relative flex min-w-0 flex-col rounded-xl border bg-card p-2 transition-colors ${isSelected ? "border-ring" : "hover:border-foreground/25"}`}>
                  <Checkbox className="file-library-card-checkbox absolute left-4 top-4 z-10 bg-background" aria-label={`选择 ${file.original_name}`} checked={isSelected} onCheckedChange={(value) => toggleFileSelected(file.id, value === true)} />
                  <button type="button" aria-label={`查看 ${file.original_name}`} className="block w-full rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => navigate(`../documents/${file.id}`)}><FileThumbnail file={file} /></button>
                  <div className="flex min-w-0 items-start gap-1 px-1 pt-1.5">
                    <button type="button" className="file-library-name line-clamp-2 min-h-10 min-w-0 flex-1 text-left text-sm leading-5 font-medium hover:underline" title={file.original_name} onClick={() => setDiagnosticFile(file)}>{file.original_name}</button>
                    {fileActions(file)}
                  </div>
                  <p className="truncate px-1 text-xs text-muted-foreground">{uploadExtension(file.original_name).slice(1).toUpperCase() || fileType(file)} · {bytes(file.size_bytes)}</p>
                  <div className="mx-1 mt-2 flex items-center justify-between gap-2 border-t pt-1">
                    <time className="min-w-0 truncate text-xs text-muted-foreground" dateTime={file.created_at} title={file.created_at}>{formatUploadTime(file.created_at)}</time>
                    <IndexStatusButton status={indexStatus(file)} onClick={() => setDiagnosticFile(file)} />
                  </div>
                </article>
              );
            })}
          </div>
        ) : <div className="file-library-table-wrap rounded-xl border bg-card">
          <table className="file-library-table w-full table-fixed text-left text-sm">
            <thead className="bg-muted/35 text-xs text-muted-foreground">
              <tr>
                <th className="w-11 rounded-tl-xl px-3 py-2.5" scope="col">
                  <label className="inline-flex items-center gap-2 font-normal">
                    <Checkbox aria-label="全选当前页" checked={allPageSelected || (somePageSelected ? "indeterminate" : false)} onCheckedChange={(value) => togglePageSelection(value === true)} />
                    <span className="file-library-mobile-select">全选当前页</span>
                  </label>
                </th>
                <th className="px-2 py-2.5 font-medium" scope="col">文件名称</th>
                <th className="w-23 px-2 py-2.5 font-medium" scope="col">类型</th>
                <th className="w-23 px-2 py-2.5 font-medium" scope="col">大小</th>
                <th className="w-25 px-2 py-2.5 font-medium" scope="col">索引状态</th>
                <th className="w-31 px-2 py-2.5 font-medium" scope="col">上传时间</th>
                <th className="w-11 rounded-tr-xl" scope="col"><span className="sr-only">更多操作</span></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {pagedFiles.map((file) => {
                const isSelected = selectedFileIds.has(file.id);
                const openFile = () => navigate(`../documents/${file.id}`);
                return (
                  <tr className={`h-[52px] cursor-pointer transition-colors hover:bg-muted/40 ${isSelected ? "bg-muted/35" : ""}`} key={file.id} onClick={openFile}>
                    <td className="px-3 py-1" onClick={(event) => event.stopPropagation()}>
                      <Checkbox aria-label={`选择 ${file.original_name}`} checked={isSelected} onCheckedChange={(value) => toggleFileSelected(file.id, value === true)} />
                    </td>
                    <td className="px-2 py-1">
                      <button className="flex w-full min-w-0 items-center gap-2 rounded-sm text-left font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={(event) => { event.stopPropagation(); openFile(); }} title={file.original_name} type="button">
                        <FileIcon file={file} />
                        <span className="file-library-name line-clamp-2 min-w-0 leading-5">{file.original_name}</span>
                      </button>
                    </td>
                    <td className="truncate px-2 py-1 text-xs text-muted-foreground" title={fileType(file)}>{fileType(file)}</td>
                    <td className="whitespace-nowrap px-2 py-1 text-xs tabular-nums text-muted-foreground">{bytes(file.size_bytes)}</td>
                    <td className="px-2 py-1" onClick={(event) => event.stopPropagation()}>
                      <IndexStatusButton status={indexStatus(file)} onClick={() => setDiagnosticFile(file)} />
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-xs tabular-nums text-muted-foreground"><time dateTime={file.created_at} title={file.created_at}>{formatUploadTime(file.created_at)}</time></td>
                    <td className="px-1 py-1 text-right" onClick={(event) => event.stopPropagation()}>{fileActions(file)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>)}
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
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <p aria-live="polite" className="text-xs tabular-nums text-muted-foreground">
              第 {pageStart + 1}–{pageStart + pagedFiles.length} 项 · 共 {filteredFiles.length} 项
            </p>
            <div className="flex items-center gap-1">
              <Button
                disabled={safePage <= 1}
                onClick={() => setFilePage(safePage - 1)}
                size="sm"
                variant="outline"
              >
                <ChevronLeft className="size-4" />
                上一页
              </Button>
              <Button
                disabled={safePage >= totalPages}
                onClick={() => setFilePage(safePage + 1)}
                size="sm"
                variant="outline"
              >
                下一页
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        )}
      </div>
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
      {currentDiagnosticFile ? (
        <FileDiagnosticsDialog
          capability={parserCapabilityForFile(currentDiagnosticFile, parserCapabilities.data)}
          file={currentDiagnosticFile}
          indexStatus={indexStatuses[indexStatus(currentDiagnosticFile)]}
          isIndexing={activeFileIds.has(currentDiagnosticFile.id)}
          indexAction={canIndex(currentDiagnosticFile) ? {
            label: indexStatus(currentDiagnosticFile) === "failed" ? "重新解析" : "建立索引",
            disabled: indexBusy,
            run: () => parse.mutate(currentDiagnosticFile.id),
          } : undefined}
          onOpenChange={(open) => {
            if (!open) setDiagnosticFile(null);
          }}
          open
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
        description="SearchProvider 负责秒级查询，FetchProvider 负责正文获取。"
        eyebrow="Search & fetch"
        title="联网搜索与网页获取"
      />
      <PaperSearchPanel key={workspaceId} workspaceId={workspaceId} />
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
          description="工作区级来源限制会同时用于普通联网搜索；请求级域名只能进一步缩小范围。"
          title="搜索来源白名单"
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

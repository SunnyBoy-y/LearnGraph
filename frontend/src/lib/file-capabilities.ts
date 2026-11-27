import {
  requiresLearningIndex,
  resolveFilePreviewKind,
  type FilePreviewKind,
} from "./file-preview";

/**
 * 学习工作台的呈现面向。它只决定「用哪套预览组件」，不参与权限或解析判断。
 */
export type FileSurface = "document" | "image" | "data" | "media" | "other";

/**
 * 单个文件在学习工作台里真正具备的能力。
 *
 * 目录、段落定位、版本历史这些概念只属于文档阅读器；图片、表格、音视频拿到的是
 * 各自适用的能力集。工作台外壳只按这份声明装配组件，不再在组件内部散落
 * `previewKind === "image"` 之类的条件判断。
 */
export interface FileCapabilities {
  surface: FileSurface;
  previewKind: FilePreviewKind;
  /** 能否在线预览原文件。 */
  preview: boolean;
  /** 是否存在章节/段落目录。 */
  outline: boolean;
  /** 能否在预览里选中文本并带到对话。 */
  textSelection: boolean;
  /** 是否支持「定位到原文片段」的证据检索。 */
  citationLocation: boolean;
  /** 是否有文档版本历史。 */
  versionHistory: boolean;
  /** 是否需要以多模态方式把原文件交给模型（图片/音视频）。 */
  multimodalQA: boolean;
  /** 是否可以通过文本索引做问答。 */
  indexedQA: boolean;
  /** 是否提供图片查看工具（缩放/适应窗口/全屏）。 */
  imageTools: boolean;
}

const DOCUMENT_KINDS = new Set<FilePreviewKind>([
  "pdf",
  "word",
  "rtf",
  "epub",
  "powerpoint",
  "text",
  "html",
  "legacy-office",
]);

/** 具备章节/段落目录的格式。表格用工作表标签，媒体与图片没有目录。 */
const OUTLINE_KINDS = new Set<FilePreviewKind>([
  "pdf",
  "word",
  "epub",
  "powerpoint",
  "text",
  "html",
  "legacy-office",
]);

const SELECTION_KINDS = new Set<FilePreviewKind>([
  "pdf",
  "word",
  "epub",
  "text",
  "html",
  "legacy-office",
]);

/** 页码 / 段落定位只在分页或分块的文档上有意义。 */
const CITATION_KINDS = new Set<FilePreviewKind>([
  "pdf",
  "word",
  "epub",
  "legacy-office",
]);

const VERSION_KINDS = new Set<FilePreviewKind>([
  "pdf",
  "word",
  "epub",
  "text",
]);

const MULTIMODAL_KINDS = new Set<FilePreviewKind>(["image", "audio", "video"]);

function surfaceOf(kind: FilePreviewKind): FileSurface {
  if (kind === "image") return "image";
  if (kind === "audio" || kind === "video") return "media";
  if (kind === "spreadsheet") return "data";
  if (DOCUMENT_KINDS.has(kind)) return "document";
  return "other";
}

export function resolveFileCapabilities(
  filename: string,
  mimeType = "",
  parseCapability = "",
): FileCapabilities {
  const previewKind = resolveFilePreviewKind(filename, mimeType);
  const surface = surfaceOf(previewKind);
  const indexedQA = requiresLearningIndex(filename, mimeType, parseCapability);

  return {
    surface,
    previewKind,
    preview: previewKind !== "unsupported",
    outline: OUTLINE_KINDS.has(previewKind),
    textSelection: SELECTION_KINDS.has(previewKind),
    citationLocation: CITATION_KINDS.has(previewKind),
    versionHistory: VERSION_KINDS.has(previewKind) && indexedQA,
    multimodalQA: MULTIMODAL_KINDS.has(previewKind),
    indexedQA,
    imageTools: previewKind === "image",
  };
}

/** 窄屏单面板导航里，「查看区」的标签文案。 */
export function fileViewerLabel(capabilities: FileCapabilities) {
  switch (capabilities.surface) {
    case "image":
      return "图片";
    case "data":
      return "表格";
    case "media":
      return "播放";
    case "other":
      return "文件";
    default:
      return "原文";
  }
}

/**
 * 尚未开始对话时给出的提问建议。只按文件面向给出少量问题，不解释技术细节。
 */
export function fileQuestionSuggestions(
  capabilities: FileCapabilities,
): string[] {
  switch (capabilities.surface) {
    case "image":
      return ["解释图中内容", "分析图中的结构与关系", "提取图中的文字"];
    case "data":
      return ["概括这张表的关键数据", "指出异常的数值或空列", "给出一段数据分析结论"];
    case "media":
      return ["总结这段音视频的要点", "列出其中的关键结论", "整理成可复习的提纲"];
    case "other":
      return ["这个文件可以用来做什么", "如何把它加入学习资料", "帮我规划阅读顺序"];
    default:
      return capabilities.indexedQA
        ? ["概括本文的核心观点", "解释其中的关键概念", "列出可复习的问题"]
        : ["概括这份文件的内容", "解释其中的关键概念", "列出可复习的问题"];
  }
}

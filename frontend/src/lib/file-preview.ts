export type FilePreviewKind =
  | "pdf"
  | "word"
  | "rtf"
  | "epub"
  | "powerpoint"
  | "spreadsheet"
  | "audio"
  | "video"
  | "image"
  | "html"
  | "text"
  | "legacy-office"
  | "unsupported";

const blobIdentities = new WeakMap<object, number>();
let nextBlobIdentity = 0;

/** Stable identity for resetting reader state when its actual bytes change. */
export function filePreviewBlobKey(blob: object): number {
  let identity = blobIdentities.get(blob);
  if (identity === undefined) {
    identity = ++nextBlobIdentity;
    blobIdentities.set(blob, identity);
  }
  return identity;
}

const TEXT_EXTENSIONS = new Set([
  "txt", "md", "markdown", "mdx", "csv", "tsv", "json", "jsonl", "xml",
  "yaml", "yml", "toml", "ini", "cfg", "conf", "log", "rtf", "css", "js",
  "jsx", "ts", "tsx", "py", "sql", "sh",
]);

const CODE_EXTENSIONS = new Set([
  "bash", "bat", "c", "cc", "cjs", "cpp", "cs", "css", "dart", "go", "h",
  "hpp", "java", "js", "jsx", "kt", "kts", "less", "lua", "mjs", "php",
  "pl", "pm", "ps1", "py", "pyi", "r", "rb", "rs", "scala", "scss", "sh",
  "sql", "svelte", "swift", "ts", "tsx", "vue", "zsh",
]);

function extensionOf(filename: string) {
  const leaf = filename.split(/[\\/]/).pop() ?? "";
  const dot = leaf.lastIndexOf(".");
  return dot >= 0 ? leaf.slice(dot + 1).toLowerCase() : "";
}

export function delimitedTextSeparator(filename: string, mimeType = "") {
  const extension = extensionOf(filename);
  const mime = mimeType.toLowerCase().split(";", 1)[0]?.trim();
  if (extension === "tsv" || mime === "text/tab-separated-values") return "\t";
  if (extension === "csv" || ["text/csv", "application/csv"].includes(mime ?? "")) return ",";
  return undefined;
}

/**
 * Resolve binary container formats before generic XML/text checks.
 * OOXML MIME values contain "openxmlformats" and must never be decoded as XML.
 */
export function resolveFilePreviewKind(
  filename: string,
  mimeType = "",
): FilePreviewKind {
  const extension = extensionOf(filename);
  const mime = mimeType.toLowerCase().split(";", 1)[0]?.trim() ?? "";

  if (extension === "pdf" || mime === "application/pdf") return "pdf";
  if (extension === "epub" || mime === "application/epub+zip") return "epub";
  if (
    extension === "docx" ||
    mime.includes("wordprocessingml") ||
    mime === "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  ) return "word";
  if (
    extension === "pptx" ||
    mime.includes("presentationml") ||
    mime === "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  ) return "powerpoint";
  if (
    extension === "xlsx" ||
    mime.includes("spreadsheetml") ||
    mime === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  ) return "spreadsheet";
  if (["doc", "xls", "ppt", "odt", "ods", "odp"].includes(extension) || ["application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"].includes(mime)) return "legacy-office";
  if (extension === "rtf" || ["application/rtf", "text/rtf"].includes(mime)) return "rtf";
  if (delimitedTextSeparator(filename, mime)) return "spreadsheet";
  if (mime.startsWith("audio/") || ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"].includes(extension)) return "audio";
  if (mime.startsWith("video/") || ["mp4", "webm", "mov", "mkv", "m4v"].includes(extension)) return "video";
  if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "avif"].includes(extension)) return "image";
  if (["text/html", "application/xhtml+xml"].includes(mime) || ["html", "htm", "xhtml"].includes(extension)) return "html";
  if (
    mime.startsWith("text/") ||
    ["application/json", "application/ld+json", "application/xml", "application/rtf"].includes(mime) ||
    TEXT_EXTENSIONS.has(extension) || CODE_EXTENSIONS.has(extension)
  ) return "text";
  return "unsupported";
}

/**
 * Whether the learning page should offer document indexing for this file.
 * Media, code, HTML, and attachment-only files remain directly usable without
 * presenting an index as a prerequisite.
 */
export function requiresLearningIndex(
  filename: string,
  mimeType = "",
  parseCapability = "",
) {
  if (parseCapability === "attachment_only") return false;

  const extension = extensionOf(filename);
  const previewKind = resolveFilePreviewKind(filename, mimeType);
  if (
    CODE_EXTENSIONS.has(extension) ||
    ["audio", "video", "image", "html", "unsupported"].includes(previewKind)
  ) {
    return false;
  }

  return true;
}

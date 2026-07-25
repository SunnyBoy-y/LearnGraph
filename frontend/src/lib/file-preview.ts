export type FilePreviewKind =
  | "pdf"
  | "word"
  | "powerpoint"
  | "spreadsheet"
  | "audio"
  | "video"
  | "image"
  | "html"
  | "text"
  | "legacy-office"
  | "unsupported";

const TEXT_EXTENSIONS = new Set([
  "txt", "md", "markdown", "mdx", "csv", "tsv", "json", "jsonl", "xml",
  "yaml", "yml", "toml", "ini", "cfg", "conf", "log", "rtf", "css", "js",
  "jsx", "ts", "tsx", "py", "sql", "sh",
]);

function extensionOf(filename: string) {
  const leaf = filename.split(/[\\/]/).pop() ?? "";
  const dot = leaf.lastIndexOf(".");
  return dot >= 0 ? leaf.slice(dot + 1).toLowerCase() : "";
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
  if (["doc", "xls", "ppt"].includes(extension)) return "legacy-office";
  if (mime.startsWith("audio/") || ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"].includes(extension)) return "audio";
  if (mime.startsWith("video/") || ["mp4", "webm", "mov", "mkv", "m4v"].includes(extension)) return "video";
  if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "avif"].includes(extension)) return "image";
  if (mime === "text/html" || ["html", "htm"].includes(extension)) return "html";
  if (
    mime.startsWith("text/") ||
    ["application/json", "application/ld+json", "application/xml", "application/rtf"].includes(mime) ||
    TEXT_EXTENSIONS.has(extension)
  ) return "text";
  return "unsupported";
}

/**
 * Chat attachment mode policy (D-082 / D-083).
 * Keep extension lists aligned with backend `document_parsers.py`.
 */

export const LOCAL_TEXT_EXTENSIONS = new Set([
  ".txt",
  ".md",
  ".markdown",
  ".html",
  ".htm",
  ".csv",
  ".log",
  ".ini",
  ".cfg",
  ".conf",
  ".toml",
  ".yaml",
  ".yml",
  ".json",
  ".xml",
  ".css",
  ".scss",
  ".less",
  ".py",
  ".pyi",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".java",
  ".go",
  ".rs",
  ".c",
  ".cc",
  ".cpp",
  ".h",
  ".hpp",
  ".cs",
  ".rb",
  ".php",
  ".swift",
  ".kt",
  ".kts",
  ".scala",
  ".sql",
  ".sh",
  ".bash",
  ".zsh",
  ".ps1",
  ".r",
  ".vue",
  ".svelte",
  ".dart",
  ".lua",
  ".pl",
  ".pm",
]);

export const WHITELIST_DOCUMENT_EXTENSIONS = new Set([
  ".pdf",
  ".doc",
  ".docx",
  ".pptx",
  ".xls",
  ".xlsx",
]);

export const IMAGE_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  ".tif",
  ".tiff",
]);
export const AUDIO_EXTENSIONS = new Set([
  ".mp3",
  ".m4a",
  ".wav",
  ".ogg",
  ".flac",
  ".aac",
]);
export const VIDEO_EXTENSIONS = new Set([
  ".mp4",
  ".mov",
  ".avi",
  ".webm",
  ".mkv",
  ".flv",
  ".wmv",
]);
export const SPECIAL_BINARY_EXTENSIONS = new Set([
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

export function fileExtension(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? name;
  const index = base.lastIndexOf(".");
  if (index <= 0) return "";
  return base.slice(index).toLowerCase();
}

export function isImageNameOrMime(name: string, mime?: string | null): boolean {
  const type = (mime ?? "").toLowerCase().split(";", 1)[0].trim();
  return type.startsWith("image/") || IMAGE_EXTENSIONS.has(fileExtension(name));
}

export function isAudioNameOrMime(name: string, mime?: string | null): boolean {
  const type = (mime ?? "").toLowerCase().split(";", 1)[0].trim();
  return type.startsWith("audio/") || AUDIO_EXTENSIONS.has(fileExtension(name));
}

export function isVideoNameOrMime(name: string, mime?: string | null): boolean {
  const type = (mime ?? "").toLowerCase().split(";", 1)[0].trim();
  return type.startsWith("video/") || VIDEO_EXTENSIONS.has(fileExtension(name));
}

export function isSpecialBinaryName(name: string): boolean {
  return SPECIAL_BINARY_EXTENSIONS.has(fileExtension(name));
}

export function isFastThinkingWhitelistDocument(name: string): boolean {
  if (isSpecialBinaryName(name)) return false;
  const extension = fileExtension(name);
  return (
    LOCAL_TEXT_EXTENSIONS.has(extension) ||
    WHITELIST_DOCUMENT_EXTENSIONS.has(extension)
  );
}

export type AttachmentBlockReason =
  | "unsupported"
  | "audio_needs_asr"
  | "document_not_ready"
  | null;

export function classifyNonAgentAttachment(options: {
  name: string;
  mime?: string | null;
  parseStatus?: string | null;
  asrAvailable: boolean;
  requireReady?: boolean;
}): { ok: boolean; reason: AttachmentBlockReason } {
  const {
    name,
    mime,
    parseStatus,
    asrAvailable,
    requireReady = false,
  } = options;
  if (isSpecialBinaryName(name)) return { ok: false, reason: "unsupported" };
  if (isImageNameOrMime(name, mime) || isVideoNameOrMime(name, mime)) {
    return { ok: true, reason: null };
  }
  if (isAudioNameOrMime(name, mime)) {
    return asrAvailable
      ? { ok: true, reason: null }
      : { ok: false, reason: "audio_needs_asr" };
  }
  if (!isFastThinkingWhitelistDocument(name)) {
    return { ok: false, reason: "unsupported" };
  }
  if (requireReady && parseStatus !== "indexed") {
    return { ok: false, reason: "document_not_ready" };
  }
  return { ok: true, reason: null };
}

export function nonAgentAttachmentBlockedMessage(names: string[]): string {
  const uniqueNames = [...new Set(names.filter(Boolean))];
  const preview = uniqueNames.slice(0, 5).map((name) => `「${name}」`).join("、");
  const remainder = uniqueNames.length > 5 ? ` 等 ${uniqueNames.length} 个文件` : "";
  return `极速/思考模式不支持以下附件：${preview}${remainder}。请切换到智能体模式，或者删除不受支持的文件后再发送。`;
}

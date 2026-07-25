/**
 * Chat attachment mode policy (D-082 / D-083).
 * Keep extension lists aligned with backend
 * `app/services/chat_attachment_policy.py`.
 */

export type ResponseModeKind = "fast" | "thinking" | "agentic";

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
  ".docx",
  ".pptx",
  ".xlsx",
  ".ppt",
  ".doc",
  ".xls",
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
  ".webm",
  ".ogg",
  ".flac",
  ".aac",
  ".mp4",
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
  if (type.startsWith("image/")) return true;
  return IMAGE_EXTENSIONS.has(fileExtension(name));
}

export function isAudioNameOrMime(name: string, mime?: string | null): boolean {
  const type = (mime ?? "").toLowerCase().split(";", 1)[0].trim();
  if (type.startsWith("audio/")) return true;
  return AUDIO_EXTENSIONS.has(fileExtension(name));
}

export function isSpecialBinaryName(name: string): boolean {
  return SPECIAL_BINARY_EXTENSIONS.has(fileExtension(name));
}

export function isFastThinkingWhitelistDocument(
  name: string,
  mime?: string | null,
  parseCapability?: string | null,
): boolean {
  if (isImageNameOrMime(name, mime) || isAudioNameOrMime(name, mime)) {
    return false;
  }
  if (isSpecialBinaryName(name)) return false;
  const extension = fileExtension(name);
  if (
    LOCAL_TEXT_EXTENSIONS.has(extension) ||
    WHITELIST_DOCUMENT_EXTENSIONS.has(extension)
  ) {
    return true;
  }
  const capability = (parseCapability ?? "").trim();
  return (
    capability === "local_text" ||
    capability === "built_in_document" ||
    capability === "optional_processor"
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
  parseCapability?: string | null;
  parseStatus?: string | null;
  asrAvailable: boolean;
}): { ok: boolean; reason: AttachmentBlockReason; message: string } {
  const { name, mime, parseCapability, parseStatus, asrAvailable } = options;
  if (isSpecialBinaryName(name)) {
    return {
      ok: false,
      reason: "unsupported",
      message: `「${name}」不能在极速/思考模式使用（可执行或特殊二进制）。请切换到智能体模式，或移除该附件。`,
    };
  }
  if (isImageNameOrMime(name, mime)) {
    return { ok: true, reason: null, message: "" };
  }
  if (isAudioNameOrMime(name, mime)) {
    if (!asrAvailable) {
      return {
        ok: false,
        reason: "audio_needs_asr",
        message: `音频「${name}」在极速/思考模式下需要已启用的 ASR Provider。请在设置中配置转写，切换到智能体模式，或移除该附件。`,
      };
    }
    return { ok: true, reason: null, message: "" };
  }
  if (!isFastThinkingWhitelistDocument(name, mime, parseCapability)) {
    return {
      ok: false,
      reason: "unsupported",
      message: `「${name}」不在极速/思考模式支持的常用文件白名单内。请切换到智能体模式，或移除该附件。`,
    };
  }
  if (parseStatus && parseStatus !== "indexed") {
    return {
      ok: false,
      reason: "document_not_ready",
      message: `「${name}」尚未完成文本解析，极速/思考只能引用解析结果。请等待解析完成、切换智能体，或移除附件。`,
    };
  }
  return { ok: true, reason: null, message: "" };
}

/** HTML accept for fast/thinking file pickers. */
export function fastThinkingAcceptAttribute(asrAvailable: boolean): string {
  const parts = [
    "image/*",
    ...WHITELIST_DOCUMENT_EXTENSIONS,
    ...LOCAL_TEXT_EXTENSIONS,
    ...IMAGE_EXTENSIONS,
  ];
  if (asrAvailable) {
    parts.push("audio/*", ...AUDIO_EXTENSIONS);
  }
  return parts.join(",");
}

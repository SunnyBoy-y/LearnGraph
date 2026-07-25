"""Chat attachment mode policy (D-082 / D-083).

Fast/thinking modes may only use whitelist parseable/common files (plus audio
when ASR is configured). Agent mode may attach any stored workspace file.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.models import FileRecord


# Keep in sync with frontend `chat-attachment-policy.ts` and files.py extensions.
LOCAL_TEXT_EXTENSIONS = {
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
}

BUILT_IN_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}
# PPT may need conversion; still listed for product whitelist messaging.
WHITELIST_DOCUMENT_EXTENSIONS = BUILT_IN_DOCUMENT_EXTENSIONS | {".ppt", ".doc", ".xls"}

OPTIONAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".webm", ".ogg", ".flac", ".aac", ".mp4"}

# Explicitly never allowed in fast/thinking (even if somehow indexed).
SPECIAL_BINARY_EXTENSIONS = {
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
}


def file_extension(name: str) -> str:
    return Path(name or "").suffix.casefold()


def is_image_attachment(file: FileRecord) -> bool:
    mime = (file.mime_type or "").casefold().split(";", 1)[0].strip()
    if mime.startswith("image/"):
        return True
    return file_extension(file.original_name) in OPTIONAL_IMAGE_EXTENSIONS


def is_audio_attachment(file: FileRecord) -> bool:
    mime = (file.mime_type or "").casefold().split(";", 1)[0].strip()
    if mime.startswith("audio/"):
        return True
    return file_extension(file.original_name) in AUDIO_EXTENSIONS


def is_special_binary_attachment(file: FileRecord) -> bool:
    return file_extension(file.original_name) in SPECIAL_BINARY_EXTENSIONS


def is_fast_thinking_whitelist_document(file: FileRecord) -> bool:
    """True when the file is a common parseable document/text (not audio/image)."""

    if is_image_attachment(file) or is_audio_attachment(file):
        return False
    if is_special_binary_attachment(file):
        return False
    extension = file_extension(file.original_name)
    if extension in LOCAL_TEXT_EXTENSIONS or extension in WHITELIST_DOCUMENT_EXTENSIONS:
        return True
    capability = (file.parse_capability or "").strip()
    return capability in {
        "local_text",
        "built_in_document",
        "optional_processor",
    }


def classify_non_agent_attachment(
    file: FileRecord,
    *,
    asr_available: bool,
) -> str:
    """Return a stable readiness class for fast/thinking attachments.

    Classes:
    - image
    - audio_ok / audio_needs_asr
    - document_ready / document_not_ready
    - unsupported
    """

    if is_special_binary_attachment(file):
        return "unsupported"
    if is_image_attachment(file):
        return "image"
    if is_audio_attachment(file):
        return "audio_ok" if asr_available else "audio_needs_asr"
    if not is_fast_thinking_whitelist_document(file):
        return "unsupported"
    if file.parse_status == "indexed":
        return "document_ready"
    # Images OCR optional processor may be unindexed — still whitelist but not ready.
    return "document_not_ready"


def non_agent_attachment_error(
    file: FileRecord,
    classification: str,
) -> tuple[str, str]:
    """Return (error_code, message) for a blocked non-agent attachment."""

    name = file.original_name or file.id
    if classification == "audio_needs_asr":
        return (
            "transcription_provider_unavailable",
            (
                f"音频「{name}」在极速/思考模式下需要已启用的 ASR（转写）Provider。"
                "请在设置中配置 ASR，或切换到智能体模式，或移除该附件。"
            ),
        )
    if classification == "document_not_ready":
        return (
            "attachment_not_indexed",
            (
                f"文件「{name}」尚未完成文本解析/索引，极速/思考模式只能引用解析结果。"
                "请等待解析完成，切换到智能体模式，或移除该附件。"
            ),
        )
    return (
        "attachment_mode_unsupported",
        (
            f"文件「{name}」不能在极速/思考模式中作为解析后附件使用"
            "（例如可执行文件或无法安全解析的格式）。请切换到智能体模式，或移除该附件。"
        ),
    )

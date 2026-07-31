"""Chat attachment mode policy (D-082 / D-083).

Fast/thinking modes may use files that have a safe text extraction path, direct
image/video input, or audio when stored-file ASR is configured. Agent mode may
attach any stored workspace file.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.models import FileRecord
from app.services.document_parsers import (
    BUILT_IN_DOCUMENT_EXTENSIONS,
    ISOLATED_DOCUMENT_EXTENSIONS,
    LOCAL_TEXT_EXTENSIONS,
)


WHITELIST_DOCUMENT_EXTENSIONS = (
    BUILT_IN_DOCUMENT_EXTENSIONS | ISOLATED_DOCUMENT_EXTENSIONS
)
OPTIONAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".flv", ".wmv"}

# Explicitly never allowed in fast/thinking (even if metadata is malformed).
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


def is_video_attachment(file: FileRecord) -> bool:
    mime = (file.mime_type or "").casefold().split(";", 1)[0].strip()
    if mime.startswith("video/"):
        return True
    return file_extension(file.original_name) in VIDEO_EXTENSIONS


def is_special_binary_attachment(file: FileRecord) -> bool:
    return file_extension(file.original_name) in SPECIAL_BINARY_EXTENSIONS


def is_fast_thinking_whitelist_document(file: FileRecord) -> bool:
    """True when the file has a supported safe text extraction path."""

    if is_image_attachment(file) or is_audio_attachment(file) or is_video_attachment(file):
        return False
    if is_special_binary_attachment(file):
        return False
    extension = file_extension(file.original_name)
    return extension in LOCAL_TEXT_EXTENSIONS or extension in WHITELIST_DOCUMENT_EXTENSIONS


def classify_non_agent_attachment(
    file: FileRecord,
    *,
    asr_available: bool,
) -> str:
    """Return a stable readiness class for fast/thinking attachments."""

    if is_special_binary_attachment(file):
        return "unsupported"
    if is_image_attachment(file):
        return "image"
    if is_video_attachment(file):
        return "video"
    if is_audio_attachment(file):
        return "audio_ok" if asr_available else "audio_needs_asr"
    if not is_fast_thinking_whitelist_document(file):
        return "unsupported"
    if file.parse_status == "indexed":
        return "document_ready"
    return "document_not_ready"


def non_agent_attachment_error(
    file: FileRecord,
    classification: str,
) -> tuple[str, str]:
    """Return (error_code, message) for a blocked non-agent attachment."""

    name = file.original_name or file.id
    suffix = "请切换到智能体模式，或删除不受支持的文件后再用极速/思考发送。"
    if classification == "audio_needs_asr":
        return (
            "transcription_provider_unavailable",
            f"音频「{name}」无法使用已配置的文件 ASR 转写。{suffix}",
        )
    if classification == "document_not_ready":
        return (
            "attachment_not_indexed",
            f"文件「{name}」尚未完成文本解析/索引。{suffix}",
        )
    return (
        "attachment_mode_unsupported",
        f"文件「{name}」不是极速/思考模式支持的附件类型。{suffix}",
    )

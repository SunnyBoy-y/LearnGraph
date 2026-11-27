"""Media helpers for IM channels (Feishu & WeChat): file storage, ASR, HTML-to-image, and TTS."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.parser import HTMLParser
import io
import logging
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
import wave

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import FileRecord
from app.domain.schemas.files import AudioTranscriptionCreate
from app.providers.factory import transcription_provider_for_workspace, tts_provider_for_workspace
from app.services.chat_attachment_policy import SPECIAL_BINARY_EXTENSIONS
from app.services.files import FileService

logger = logging.getLogger(__name__)

_TTS_COMMAND_PREFIX_RE = re.compile(r"^\s*/(?:tts|voice)\b[:：]?\s*", re.IGNORECASE)
_TTS_INTENT_RE = re.compile(
    r"(?:用语音(?:回复|回答|发|说|播报|朗读)|发(?:一条|个)?语音(?:回复|给我)?|发回语音|语音回复|"
    r"转成?语音|读给我听|念给我听|tts\s*回复|用\s*tts|voice\s*reply|reply\s*(?:with|in|by)\s*(?:voice|audio))",
    re.IGNORECASE,
)
_HTML_FENCE_RE = re.compile(r"```(?:html|htm)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_FULL_HTML_DOC_RE = re.compile(r"(<!doctype\s+html\b.*?</html>|<html\b.*?</html>)", re.IGNORECASE | re.DOTALL)


def run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def guess_mime_type(filename: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    ext = Path(filename or "").suffix.casefold()
    extra = {
        ".opus": "audio/ogg",
        ".ogg": "audio/ogg",
        ".amr": "audio/amr",
        ".silk": "audio/silk",
        ".webp": "image/webp",
        ".md": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
    }
    return extra.get(ext, fallback)


def store_im_bytes(
    db: Session,
    ctx: WorkspaceContext,
    settings: Settings,
    *,
    filename: str,
    content: bytes,
    mime_type: str | None = None,
    source: str = "im",
) -> FileRecord:
    clean_name = Path(filename or "attachment.bin").name[:255] or "attachment.bin"
    ext = Path(clean_name).suffix.casefold()
    if ext in SPECIAL_BINARY_EXTENSIONS:
        raise AppError(415, "unsupported_file_type", f"不支持的文件类型：{clean_name}")
    resolved_mime = (mime_type or "").strip() or guess_mime_type(clean_name)
    file_service = FileService(db, ctx.workspace_id, ctx.principal.user_id, settings)

    async def _chunks():
        chunk_size = 1024 * 1024
        for idx in range(0, len(content), chunk_size):
            yield content[idx : idx + chunk_size]

    stored = run_async(
        file_service.storage.store(
            ctx.workspace_id,
            clean_name,
            _chunks(),
            settings.max_upload_bytes,
        )
    )
    record = file_service._finalize_stored_upload(
        original_name=clean_name,
        content_type=resolved_mime,
        stored=stored,
        source=source,
    )
    if record.parse_capability in {"built_in_document", "local_text"} and record.parse_status != "parsed":
        try:
            record = file_service.parse(record.id)
        except Exception:
            logger.warning("Non-fatal parse failure for IM file %s (%s)", record.id, record.original_name, exc_info=True)
    return record


def convert_audio_with_ffmpeg(
    content: bytes,
    *,
    src_ext: str,
    dst_ext: str,
    sample_rate: int = 16_000,
    channels: int = 1,
) -> bytes | None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not content:
        return None
    normalized_src = src_ext if src_ext.startswith(".") else f".{src_ext}"
    normalized_dst = dst_ext if dst_ext.startswith(".") else f".{dst_ext}"
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / f"in{normalized_src}"
        dst_path = Path(tmp) / f"out{normalized_dst}"
        src_path.write_bytes(content)
        cmd = [ffmpeg_bin, "-y", "-i", str(src_path), "-ar", str(sample_rate), "-ac", str(channels)]
        if normalized_dst == ".ogg":
            cmd.extend(["-c:a", "libopus"])
        cmd.append(str(dst_path))
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        except Exception:
            return None
        if proc.returncode != 0 or not dst_path.exists():
            return None
        return dst_path.read_bytes()


def transcribe_im_audio(
    db: Session,
    ctx: WorkspaceContext,
    settings: Settings,
    *,
    file_record: FileRecord | None = None,
    raw_bytes: bytes = b"",
    filename: str = "voice.ogg",
    mime_type: str = "audio/ogg",
    idempotency_key: str,
    fallback_text: str = "",
) -> str:
    if file_record is not None:
        try:
            file_service = FileService(db, ctx.workspace_id, ctx.principal.user_id, settings)
            tr = file_service.transcribe(
                file_record.id,
                AudioTranscriptionCreate(),
                idempotency_key=f"im-asr:{idempotency_key}:{file_record.id}",
            )
            if tr.transcript and tr.transcript.strip():
                return tr.transcript.strip()
        except AppError as exc:
            logger.info("FileService.transcribe fallback for %s: %s", file_record.id, exc.code)
        except Exception:
            logger.warning("FileService.transcribe failed for %s", file_record.id, exc_info=True)

    provider = transcription_provider_for_workspace(db, ctx.workspace_id, settings, purpose="stored")
    if provider is not None and raw_bytes:
        ext = Path(filename or "").suffix.casefold() or ".ogg"
        candidates: list[tuple[str, str, bytes]] = [(filename, mime_type, raw_bytes)]
        wav_bytes = convert_audio_with_ffmpeg(raw_bytes, src_ext=ext, dst_ext=".wav")
        if wav_bytes:
            candidates.insert(0, (f"{Path(filename).stem}.wav", "audio/wav", wav_bytes))
        for cand_name, cand_mime, cand_bytes in candidates:
            try:
                result = provider.transcribe(
                    filename=cand_name,
                    mime_type=cand_mime,
                    content=cand_bytes,
                )
                if result and result.text and result.text.strip():
                    return result.text.strip()
            except Exception:
                logger.warning("Direct ASR transcription failed for %s", cand_name, exc_info=True)

    return fallback_text.strip()


def detect_tts_request(text: str) -> tuple[bool, str]:
    stripped = (text or "").strip()
    if not stripped:
        return False, ""
    if _TTS_COMMAND_PREFIX_RE.match(stripped):
        cleaned = _TTS_COMMAND_PREFIX_RE.sub("", stripped, count=1).strip()
        return True, cleaned or "请用语音回复"
    if _TTS_INTENT_RE.search(stripped):
        return True, stripped
    return False, stripped


def strip_markdown_for_tts(text: str) -> str:
    cleaned = text or ""
    cleaned = _HTML_FENCE_RE.sub("HTML 内容已转为图片发送。", cleaned)
    cleaned = re.sub(r"```[\s\S]*?```", " 代码片段 ", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"[*_~]+", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _pcm16_to_wav(pcm_bytes: bytes, *, sample_rate: int = 24_000, channels: int = 1) -> tuple[bytes, int]:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    frame_count = len(pcm_bytes) // (2 * channels)
    duration_ms = int((frame_count / max(1, sample_rate)) * 1000)
    return buf.getvalue(), max(100, duration_ms)


def synthesize_tts_audio(
    db: Session,
    ctx: WorkspaceContext,
    settings: Settings,
    *,
    text: str,
    prefer_opus: bool = False,
) -> tuple[bytes, str, str, int] | None:
    clean_text = strip_markdown_for_tts(text)
    if not clean_text:
        return None
    if len(clean_text) > 3000:
        clean_text = clean_text[:3000] + "。"
    provider = tts_provider_for_workspace(db, ctx.workspace_id, settings)
    if provider is None:
        return None

    async def _collect() -> tuple[bytes, int]:
        pcm_parts: list[bytes] = []
        sample_rate = 24_000
        try:
            async for chunk in provider.stream(clean_text):
                if chunk.audio:
                    pcm_parts.append(chunk.audio)
                    sample_rate = chunk.sample_rate or sample_rate
        finally:
            await provider.close()
        return b"".join(pcm_parts), sample_rate

    try:
        pcm_bytes, sample_rate = run_async(_collect())
    except Exception:
        logger.warning("TTS synthesis failed for workspace %s", ctx.workspace_id, exc_info=True)
        return None

    if not pcm_bytes:
        return None

    wav_bytes, duration_ms = _pcm16_to_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
    if prefer_opus:
        opus_bytes = convert_audio_with_ffmpeg(wav_bytes, src_ext=".wav", dst_ext=".ogg", sample_rate=16_000)
        if opus_bytes:
            return opus_bytes, "reply.ogg", "audio/ogg", duration_ms
    return wav_bytes, "reply.wav", "audio/wav", duration_ms


def extract_html_blocks(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def _replace_fence(match: re.Match[str]) -> str:
        snippet = (match.group(1) or "").strip()
        if snippet:
            blocks.append(snippet)
            return "\n[HTML 内容已转为图片发送]\n"
        return match.group(0)

    cleaned = _HTML_FENCE_RE.sub(_replace_fence, text or "")
    if not blocks:
        def _replace_doc(match: re.Match[str]) -> str:
            snippet = (match.group(1) or "").strip()
            if snippet and len(snippet) >= 40:
                blocks.append(snippet)
                return "\n[HTML 内容已转为图片发送]\n"
            return match.group(0)

        cleaned = _FULL_HTML_DOC_RE.sub(_replace_doc, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, blocks


@dataclass
class _HtmlBlock:
    kind: str  # h1, h2, p, li, code, table_row
    text: str


class _SimpleHtmlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[_HtmlBlock] = []
        self._skip_depth = 0
        self._current_tag: str | None = None
        self._buf: list[str] = []
        self._in_row = False
        self._row_cells: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in {"style", "script", "head", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t in {"h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"}:
            self._flush()
            self._current_tag = t
        elif t == "tr":
            self._flush()
            self._in_row = True
            self._row_cells = []
        elif t in {"td", "th"} and self._in_row:
            self._buf = []
        elif t == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in {"style", "script", "head", "noscript"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if t in {"td", "th"} and self._in_row:
            cell = " ".join("".join(self._buf).split())
            self._row_cells.append(cell)
            self._buf = []
        elif t == "tr" and self._in_row:
            if any(self._row_cells):
                self.blocks.append(_HtmlBlock("table_row", " | ".join(self._row_cells)))
            self._in_row = False
            self._row_cells = []
        elif t in {"h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"}:
            self._flush()
            self._current_tag = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._buf.append(data)

    def _flush(self) -> None:
        raw = "".join(self._buf)
        self._buf = []
        if not raw.strip():
            return
        kind = self._current_tag or "p"
        if kind == "pre":
            self.blocks.append(_HtmlBlock("code", raw.strip("\n")))
        else:
            normalized = " ".join(raw.split())
            if normalized:
                self.blocks.append(_HtmlBlock(kind, normalized))


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            trial = current + ch
            bbox = draw.textbbox((0, 0), trial, font=font)
            if bbox[2] - bbox[0] <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines or [""]


def _render_html_via_browser(html: str, *, width: int = 960) -> bytes | None:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": width, "height": 720}, device_scale_factor=2)
                page.set_content(html, wait_until="load")
                return page.screenshot(full_page=True, type="png")
            finally:
                browser.close()
    except Exception:
        pass

    for binary in ("chromium", "chromium-browser", "google-chrome", "msedge"):
        exe = shutil.which(binary)
        if not exe:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "page.html"
            png_path = Path(tmp) / "page.png"
            html_path.write_text(html, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [
                        exe,
                        "--headless",
                        "--disable-gpu",
                        "--no-sandbox",
                        f"--window-size={width},1080",
                        f"--screenshot={png_path}",
                        html_path.as_uri(),
                    ],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                if proc.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                    return png_path.read_bytes()
            except Exception:
                continue
    return None


def _render_html_via_pillow(html: str, *, width: int = 920) -> bytes:
    parser = _SimpleHtmlExtractor()
    parser.feed(html)
    parser._flush()
    blocks = parser.blocks
    if not blocks:
        fallback_text = re.sub(r"<[^>]+>", " ", html)
        fallback_text = " ".join(fallback_text.split()) or "HTML Preview"
        blocks = [_HtmlBlock("p", fallback_text)]

    pad_x = 40
    pad_y = 36
    max_text_w = width - pad_x * 2
    font_h1 = _load_font(28)
    font_h2 = _load_font(22)
    font_body = _load_font(17)
    font_code = _load_font(15)

    probe = Image.new("RGB", (width, 100), (255, 255, 255))
    probe_draw = ImageDraw.Draw(probe)

    rendered_items: list[tuple[str, list[str], ImageFont.ImageFont, int]] = []
    total_h = pad_y + 12  # top accent bar
    for block in blocks:
        if block.kind == "h1":
            f, line_h, gap = font_h1, 38, 14
            text = block.text
        elif block.kind in {"h2", "h3", "h4"}:
            f, line_h, gap = font_h2, 30, 10
            text = block.text
        elif block.kind == "code":
            f, line_h, gap = font_code, 22, 12
            text = block.text
        elif block.kind == "li":
            f, line_h, gap = font_body, 26, 6
            text = f"• {block.text}"
        elif block.kind == "table_row":
            f, line_h, gap = font_code, 24, 6
            text = block.text
        else:
            f, line_h, gap = font_body, 26, 10
            text = block.text

        wrapped = _wrap_text(probe_draw, text, f, max_text_w - (24 if block.kind == "code" else 0))
        block_h = len(wrapped) * line_h + (16 if block.kind == "code" else 0)
        rendered_items.append((block.kind, wrapped, f, line_h))
        total_h += block_h + gap
        if total_h > 3800:
            break

    total_h = max(200, min(4000, total_h + pad_y))
    img = Image.new("RGB", (width, total_h), (248, 250, 252))
    draw = ImageDraw.Draw(img)
    # Card surface
    draw.rounded_rectangle([(12, 12), (width - 12, total_h - 12)], radius=12, fill=(255, 255, 255), outline=(226, 232, 240), width=1)
    # Accent bar
    draw.rectangle([(12, 12), (width - 12, 18)], fill=(37, 99, 235))

    y = pad_y + 8
    for kind, lines, f, line_h in rendered_items:
        if y >= total_h - pad_y:
            break
        if kind == "code":
            box_h = len(lines) * line_h + 16
            draw.rounded_rectangle(
                [(pad_x - 8, y), (width - pad_x + 8, min(total_h - 18, y + box_h))],
                radius=6,
                fill=(241, 245, 249),
                outline=(226, 232, 240),
            )
            cy = y + 8
            for line in lines:
                if cy >= total_h - pad_y:
                    break
                draw.text((pad_x + 4, cy), line, font=f, fill=(30, 41, 59))
                cy += line_h
            y += box_h + 12
        else:
            color = (15, 23, 42) if kind in {"h1", "h2", "h3", "h4"} else (51, 65, 85)
            for line in lines:
                if y >= total_h - pad_y:
                    break
                draw.text((pad_x, y), line, font=f, fill=color)
                y += line_h
            y += 14 if kind == "h1" else 10 if kind in {"h2", "h3", "h4", "p"} else 6

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def render_html_to_png(html: str, *, width: int = 960) -> bytes:
    browser_png = _render_html_via_browser(html, width=width)
    if browser_png:
        return browser_png
    return _render_html_via_pillow(html, width=min(width, 920))

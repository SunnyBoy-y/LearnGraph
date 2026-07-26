"""Pre-installed sandbox task library (offline; ships with the runner image).

Import from agent workspace scripts::

    from learngraph_tasks import docx_to_pdf
    docx_to_pdf("inputs/report.docx", "outputs/report.pdf")

Everything runs inside the offline sandbox: document rendering uses the
image's Chromium (CJK fonts included), audio work uses the image's ffmpeg.
"""

from __future__ import annotations

import html as _html
import json
import subprocess
import zipfile
from pathlib import Path

__all__ = [
    "audio_transcode",
    "docx_to_html",
    "docx_to_pdf",
    "extract_zip",
    "html_to_pdf",
    "html_to_png",
    "make_zip",
    "media_info",
    "pdf_merge",
]

_RENDER_HELPER = "/opt/learngraph/tasks/render.js"
_CJK_STYLE = (
    "<style>body{font-family:'Noto Sans CJK SC','Noto Sans CJK TC',"
    "'Noto Sans CJK JP',sans-serif;line-height:1.6;margin:2.5em;}"
    "table{border-collapse:collapse;}td,th{border:1px solid #999;padding:4px 8px;}"
    "img{max-width:100%;}</style>"
)


def _run(argv: list[str], *, timeout: int = 150) -> None:
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:2000]
        raise RuntimeError(f"{argv[0]} failed (exit {completed.returncode}): {detail}")


def html_to_pdf(source: str | Path, target: str | Path) -> Path:
    """Render a local HTML file to PDF with the image's headless Chromium."""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["node", _RENDER_HELPER, "pdf", str(source), str(target)])
    return target


def html_to_png(
    source: str | Path,
    target: str | Path,
    *,
    width: int = 1280,
    height: int = 720,
    full_page: bool = True,
) -> Path:
    """Screenshot a local HTML file (e.g. a built vite dist/index.html)."""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "node",
            _RENDER_HELPER,
            "png",
            str(source),
            str(target),
            str(width),
            str(height),
            "true" if full_page else "false",
        ]
    )
    return target


def docx_to_html(source: str | Path) -> str:
    """Convert .docx to an HTML string (mammoth; embedded images inlined)."""

    import mammoth

    with open(source, "rb") as stream:
        result = mammoth.convert_to_html(stream)
    title = _html.escape(Path(source).stem)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>{_CJK_STYLE}</head>"
        f"<body>{result.value}</body></html>"
    )


def docx_to_pdf(source: str | Path, target: str | Path) -> Path:
    """Convert .docx to PDF via mammoth HTML + Chromium print (CJK-safe)."""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    intermediate = target.with_suffix(".render.html")
    intermediate.write_text(docx_to_html(source), encoding="utf-8")
    try:
        html_to_pdf(intermediate, target)
    finally:
        intermediate.unlink(missing_ok=True)
    return target


def pdf_merge(sources: list[str | Path], target: str | Path) -> Path:
    """Merge PDFs in order into a single file."""

    from pypdf import PdfWriter

    writer = PdfWriter()
    for source in sources:
        writer.append(str(source))
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as stream:
        writer.write(stream)
    return target


def audio_transcode(
    source: str | Path,
    target: str | Path,
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
    bitrate: str | None = None,
) -> Path:
    """Transcode audio/video audio tracks with ffmpeg (format from extension).

    Typical use: normalize an exotic recording to 16 kHz mono .wav or .mp3
    before handing it to sandbox_transcribe_audio.
    """

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source), "-vn"]
    if sample_rate:
        argv += ["-ar", str(sample_rate)]
    if channels:
        argv += ["-ac", str(channels)]
    if bitrate:
        argv += ["-b:a", str(bitrate)]
    argv.append(str(target))
    _run(argv)
    return target


def media_info(source: str | Path) -> dict:
    """Return ffprobe stream/format metadata as a dict."""

    completed = subprocess.run(
        [
            "ffprobe",
            "-hide_banner",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(completed.stderr or '').strip()[:2000]}")
    return json.loads(completed.stdout)


def make_zip(target: str | Path, sources: list[str | Path]) -> Path:
    """Zip files and/or directories (stored relative to their parent)."""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in sources:
            item = Path(item)
            if item.is_dir():
                for member in sorted(item.rglob("*")):
                    if member.is_file():
                        bundle.write(member, member.relative_to(item.parent))
            elif item.is_file():
                bundle.write(item, item.name)
            else:
                raise FileNotFoundError(str(item))
    return target


def extract_zip(source: str | Path, target_dir: str | Path) -> list[str]:
    """Extract a zip with zip-slip protection; returns extracted paths."""

    target_dir = Path(target_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with zipfile.ZipFile(source) as bundle:
        for member in bundle.infolist():
            destination = (target_dir / member.filename).resolve()
            if destination != target_dir and target_dir not in destination.parents:
                raise ValueError(f"zip entry escapes the target directory: {member.filename}")
            bundle.extract(member, target_dir)
            extracted.append(str(destination))
    return extracted

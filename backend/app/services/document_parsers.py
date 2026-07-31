from __future__ import annotations

import io
import importlib.util
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


class DocumentParseError(RuntimeError):
    pass


class ProcessorUnavailable(DocumentParseError):
    pass


@dataclass(frozen=True)
class ParsedChunk:
    locator: str
    content: str


@dataclass(frozen=True)
class ParsedDocument:
    parser_name: str
    parser_version: str
    chunks: list[ParsedChunk]


@dataclass(frozen=True)
class ParserCapability:
    capability_id: str
    mode: str
    extensions: list[str]
    available: bool
    parser_name: str | None
    reason: str


IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"]
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
BUILT_IN_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xls", ".xlsx"}
ISOLATED_DOCUMENT_EXTENSIONS = {".doc"}


def parser_capabilities(
    *,
    legacy_doc_available: bool = False,
    legacy_doc_reason: str | None = None,
) -> list[ParserCapability]:
    pdf_available = importlib.util.find_spec("pypdf") is not None
    pillow_available = importlib.util.find_spec("PIL") is not None
    pytesseract_available = importlib.util.find_spec("pytesseract") is not None
    tesseract_executable = shutil.which("tesseract")
    ocr_available = bool(pillow_available and pytesseract_available and tesseract_executable)
    missing_ocr: list[str] = []
    if not pillow_available:
        missing_ocr.append("Pillow")
    if not pytesseract_available:
        missing_ocr.append("pytesseract")
    if not tesseract_executable:
        missing_ocr.append("Tesseract executable")
    return [
        ParserCapability(
            capability_id="plain_text",
            mode="built_in",
            extensions=sorted(LOCAL_TEXT_EXTENSIONS),
            available=True,
            parser_name="local_text",
            reason="UTF-8 text and source code are parsed locally with stable line locators.",
        ),
        ParserCapability(
            capability_id="ooxml",
            mode="built_in",
            extensions=[".docx", ".pptx", ".xlsx"],
            available=True,
            parser_name="ooxml",
            reason="OOXML archives are read as inert XML; macros and embedded objects are not executed.",
        ),
        ParserCapability(
            capability_id="pdf_text",
            mode="built_in",
            extensions=[".pdf"],
            available=pdf_available,
            parser_name="pypdf" if pdf_available else None,
            reason=(
                "pypdf is installed for local page-level text extraction."
                if pdf_available
                else "The required pypdf package is not installed."
            ),
        ),
        ParserCapability(
            capability_id="image_ocr",
            mode="optional",
            extensions=list(IMAGE_EXTENSIONS),
            available=ocr_available,
            parser_name="pytesseract" if ocr_available else None,
            reason=(
                "Local Pillow and Tesseract OCR processors are available."
                if ocr_available
                else "Image OCR is unavailable; missing: " + ", ".join(missing_ocr) + "."
            ),
        ),
        ParserCapability(
            capability_id="image_vision",
            mode="optional",
            extensions=list(IMAGE_EXTENSIONS),
            available=False,
            parser_name=None,
            reason="No image-vision Provider port is configured; OCR availability does not imply visual understanding.",
        ),
        ParserCapability(
            capability_id="legacy_xls",
            mode="built_in",
            extensions=[".xls"],
            available=importlib.util.find_spec("xlrd") is not None,
            parser_name="xlrd" if importlib.util.find_spec("xlrd") is not None else None,
            reason=(
                "xlrd is installed for inert legacy Excel cell extraction."
                if importlib.util.find_spec("xlrd") is not None
                else "The required xlrd package is not installed."
            ),
        ),
        ParserCapability(
            capability_id="legacy_doc",
            mode="isolated",
            extensions=[".doc"],
            available=legacy_doc_available,
            parser_name="antiword" if legacy_doc_available else None,
            reason=(
                "Legacy Word text is extracted by antiword in the isolated, network-disabled sandbox."
                if legacy_doc_available
                else legacy_doc_reason
                or "The isolated antiword sandbox converter is unavailable."
            ),
        ),
        ParserCapability(
            capability_id="legacy_ppt",
            mode="isolated",
            extensions=[".ppt"],
            available=False,
            parser_name=None,
            reason="Legacy PowerPoint requires an isolated converter and is not supported.",
        ),
    ]


def parse_document(original_name: str, payload: bytes) -> ParsedDocument:
    extension = Path(original_name).suffix.casefold()
    if extension in LOCAL_TEXT_EXTENSIONS:
        return _parse_text(payload)
    if extension == ".docx":
        return _parse_docx(payload)
    if extension == ".pptx":
        return _parse_pptx(payload)
    if extension == ".xlsx":
        return _parse_xlsx(payload)
    if extension == ".xls":
        return _parse_xls(payload)
    if extension == ".pdf":
        return _parse_pdf(payload)
    if extension in IMAGE_EXTENSIONS:
        return _parse_image_ocr(payload)
    if extension == ".doc":
        raise ProcessorUnavailable(
            "Legacy Word parsing must run through the isolated antiword sandbox"
        )
    if extension == ".ppt":
        raise ProcessorUnavailable(
            "Legacy PowerPoint requires an isolated converter and is not supported"
        )
    raise ProcessorUnavailable("No parser is available for this file type")


def _parse_text(payload: bytes) -> ParsedDocument:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParseError("Text parser requires UTF-8") from exc
    chunks: list[ParsedChunk] = []
    block: list[str] = []
    block_start = 0
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        if line.strip():
            if not block:
                block_start = line_number
            block.append(line)
        elif block:
            chunks.append(
                ParsedChunk(
                    f"lines:{block_start}-{line_number - 1}",
                    "\n".join(block),
                )
            )
            block = []
    if block:
        chunks.append(
            ParsedChunk(
                f"lines:{block_start}-{len(lines)}",
                "\n".join(block),
            )
        )
    return _document("local_text", chunks)


def _parse_docx(payload: bytes) -> ParsedDocument:
    archive = _open_office_archive(payload)
    try:
        root = _read_xml(archive, "word/document.xml")
        paragraphs: list[ParsedChunk] = []
        paragraph_index = 0
        for paragraph in root.iter():
            if not paragraph.tag.endswith("}p"):
                continue
            paragraph_index += 1
            text = "".join(
                item.text or ""
                for item in paragraph.iter()
                if item.tag.endswith("}t")
            ).strip()
            if text:
                paragraphs.append(ParsedChunk(f"paragraph:{paragraph_index}", text))
        return _document("ooxml_docx", paragraphs)
    finally:
        archive.close()


def _parse_pptx(payload: bytes) -> ParsedDocument:
    archive = _open_office_archive(payload)
    try:
        slide_names = sorted(
            (
                (int(match.group(1)), name)
                for name in archive.namelist()
                if (match := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name))
            ),
            key=lambda item: item[0],
        )
        chunks: list[ParsedChunk] = []
        for slide_number, name in slide_names:
            root = _read_xml(archive, name)
            text = "\n".join(
                item.text.strip()
                for item in root.iter()
                if item.tag.endswith("}t") and item.text and item.text.strip()
            )
            if text:
                chunks.append(ParsedChunk(f"slide:{slide_number}", text))
        return _document("ooxml_pptx", chunks)
    finally:
        archive.close()


def _parse_xlsx(payload: bytes) -> ParsedDocument:
    archive = _open_office_archive(payload)
    try:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = _read_xml(archive, "xl/sharedStrings.xml")
            for item in shared_root.iter():
                if item.tag.endswith("}si"):
                    shared_strings.append(
                        "".join(part.text or "" for part in item.iter() if part.tag.endswith("}t"))
                    )
        sheet_names = sorted(
            (
                (int(match.group(1)), name)
                for name in archive.namelist()
                if (match := re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", name))
            ),
            key=lambda item: item[0],
        )
        chunks: list[ParsedChunk] = []
        for sheet_index, name in sheet_names:
            root = _read_xml(archive, name)
            for row in (element for element in root.iter() if element.tag.endswith("}row")):
                values: list[str] = []
                for cell in (element for element in row if element.tag.endswith("}c")):
                    value = next((item.text for item in cell.iter() if item.tag.endswith("}v")), None)
                    cell_type = cell.attrib.get("t")
                    if cell_type == "s" and value is not None:
                        try:
                            value = shared_strings[int(value)]
                        except (ValueError, IndexError):
                            value = ""
                    elif cell_type == "inlineStr":
                        value = "".join(item.text or "" for item in cell.iter() if item.tag.endswith("}t"))
                    values.append((value or "").strip())
                if any(values):
                    row_number = row.attrib.get("r") or str(len(chunks) + 1)
                    chunks.append(
                        ParsedChunk(
                            f"sheet:{sheet_index}!row:{row_number}",
                            "\t".join(values),
                        )
                    )
        return _document("ooxml_xlsx", chunks)
    finally:
        archive.close()


def _parse_xls(payload: bytes) -> ParsedDocument:
    try:
        import xlrd
    except ImportError as exc:
        raise ProcessorUnavailable("Legacy Excel parsing requires xlrd") from exc
    workbook = None
    try:
        workbook = xlrd.open_workbook(file_contents=payload, on_demand=True)
        chunks: list[ParsedChunk] = []
        for sheet_index, sheet in enumerate(workbook.sheets(), start=1):
            for row_index in range(sheet.nrows):
                values = [str(value).strip() for value in sheet.row_values(row_index)]
                if any(values):
                    chunks.append(
                        ParsedChunk(
                            f"sheet:{sheet_index}!row:{row_index + 1}",
                            "\t".join(values),
                        )
                    )
    except Exception as exc:
        raise DocumentParseError("Legacy Excel parser could not extract cells") from exc
    finally:
        if workbook is not None:
            workbook.release_resources()
    return _document("xlrd", chunks, parser_version=str(xlrd.__version__))


def isolated_text_document(
    text: str,
    *,
    parser_name: str,
    parser_version: str,
) -> ParsedDocument:
    return _document(
        parser_name,
        [ParsedChunk("document", text)],
        parser_version=parser_version,
    )


def _parse_pdf(payload: bytes) -> ParsedDocument:
    try:
        import pypdf
        from pypdf import PdfReader
    except ImportError as exc:
        raise ProcessorUnavailable("PDF parsing requires the optional pypdf processor") from exc
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        chunks = [
            ParsedChunk(f"page:{index}", text)
            for index, page in enumerate(reader.pages, start=1)
            if (text := (page.extract_text() or "").strip())
        ]
    except Exception as exc:
        raise DocumentParseError("PDF parser could not extract text") from exc
    return _document("pypdf", chunks, parser_version=str(pypdf.__version__))


def _parse_image_ocr(payload: bytes) -> ParsedDocument:
    capability = next(
        item for item in parser_capabilities() if item.capability_id == "image_ocr"
    )
    if not capability.available:
        raise ProcessorUnavailable(capability.reason)
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise ProcessorUnavailable("Image OCR requires optional Pillow and Tesseract processors") from exc
    try:
        image = Image.open(io.BytesIO(payload))
        text = pytesseract.image_to_string(image).strip()
    except Exception as exc:
        raise DocumentParseError("Image OCR failed") from exc
    return _document("pytesseract", [ParsedChunk("image:1", text)])


def _open_office_archive(payload: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise DocumentParseError("Office file is not a valid OOXML archive") from exc
    infos = archive.infolist()
    if len(infos) > 2_000 or sum(info.file_size for info in infos) > 100 * 1024 * 1024:
        archive.close()
        raise DocumentParseError("Office archive exceeds parser safety limits")
    return archive


def _read_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(archive.read(name))
    except (KeyError, ElementTree.ParseError) as exc:
        raise DocumentParseError("Office document contains invalid XML") from exc


def _document(
    parser_name: str,
    chunks: list[ParsedChunk],
    *,
    parser_version: str = "1.0.0",
) -> ParsedDocument:
    normalized = [
        ParsedChunk(chunk.locator, chunk.content.strip())
        for chunk in chunks
        if chunk.content and chunk.content.strip()
    ]
    if not normalized:
        raise DocumentParseError("The document contains no extractable text")
    return ParsedDocument(parser_name=parser_name, parser_version=parser_version, chunks=normalized)

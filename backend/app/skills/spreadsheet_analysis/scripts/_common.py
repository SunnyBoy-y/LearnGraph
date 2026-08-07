"""Shared helpers for spreadsheet-analysis scripts (offline sandbox only)."""
from __future__ import annotations

from pathlib import Path


def safe_path(value: str) -> Path:
    """Resolve a workspace-relative path and reject escapes."""
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def suffix(path: Path) -> str:
    return path.suffix.lstrip(".").casefold()


def read_table(path: Path, sheet: str | None, encoding: str, sep: str):
    import pandas as pd

    fmt = suffix(path)
    if fmt in ("csv", "tsv"):
        try:
            return pd.read_csv(path, encoding=encoding or None, sep=sep or None)
        except UnicodeDecodeError:
            if encoding:
                raise
            return pd.read_csv(path, encoding="gbk", sep=sep or None)
    engines = {"xlsx": "openpyxl", "xls": "xlrd", "xlsb": "pyxlsb", "ods": "odf"}
    engine = engines.get(fmt)
    if engine is None:
        raise RuntimeError(f"unsupported format: {path.suffix} (csv/tsv/xlsx/xls/xlsb/ods)")
    return pd.read_excel(path, sheet_name=sheet or 0, engine=engine)


def sheet_names(path: Path) -> list[str]:
    fmt = suffix(path)
    if fmt not in ("xlsx", "xls", "xlsb", "ods"):
        return []
    import pandas as pd

    kwargs = {"engine": "openpyxl"} if fmt == "xlsx" else {}
    return [str(name) for name in pd.ExcelFile(path, **kwargs).sheet_names]

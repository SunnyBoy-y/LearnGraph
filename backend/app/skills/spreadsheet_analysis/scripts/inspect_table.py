#!/usr/bin/env python3
"""Inspect a spreadsheet: columns, dtypes, row count, sheet names, sample rows (offline)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _suffix(path: Path) -> str:
    return path.suffix.lstrip(".").casefold()


def _read_excel(path: Path, sheet: str | None):
    import pandas as pd

    if _suffix(path) == "xlsx":
        return pd.read_excel(path, sheet_name=sheet or 0, engine="openpyxl")
    if _suffix(path) == "xls":
        return pd.read_excel(path, sheet_name=sheet or 0, engine="xlrd")
    if _suffix(path) == "xlsb":
        return pd.read_excel(path, sheet_name=sheet or 0, engine="pyxlsb")
    if _suffix(path) == "ods":
        return pd.read_excel(path, sheet_name=sheet or 0, engine="odf")
    raise RuntimeError(f"unsupported excel format: {path.suffix}")


def _sheet_names(path: Path) -> list[str]:
    if _suffix(path) in ("xlsx", "xls", "xlsb", "ods"):
        import pandas as pd

        kwargs = {"engine": "openpyxl"} if _suffix(path) == "xlsx" else {}
        xls = pd.ExcelFile(path, **kwargs)
        return [str(name) for name in xls.sheet_names]
    return []


def inspect(path: Path, sheet: str | None, encoding: str, sep: str, sample_rows: int) -> dict:
    if _suffix(path) in ("csv", "tsv"):
        import pandas as pd

        try:
            df = pd.read_csv(path, encoding=encoding or None, sep=sep or None)
        except UnicodeDecodeError:
            if encoding:
                raise
            df = pd.read_csv(path, encoding="gbk", sep=sep or None)
        return {
            "format": _suffix(path),
            "rows": int(df.shape[0]),
            "columns": [str(c) for c in df.columns],
            "dtypes": {str(c): str(df[c].dtype) for c in df.columns},
            "sample": df.head(sample_rows).to_dict(orient="records"),
        }
    if _suffix(path) in ("xlsx", "xls", "xlsb", "ods"):
        df = _read_excel(path, sheet)
        return {
            "format": _suffix(path),
            "sheet": sheet or 0,
            "sheets": _sheet_names(path),
            "rows": int(df.shape[0]),
            "columns": [str(c) for c in df.columns],
            "dtypes": {str(c): str(df[c].dtype) for c in df.columns},
            "sample": df.head(sample_rows).to_dict(orient="records"),
        }
    raise RuntimeError(f"unsupported format: {path.suffix} (csv/tsv/xlsx/xls/xlsb/ods)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a spreadsheet (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative table path")
    parser.add_argument("--sheet", default="", help="Excel sheet name/index (default first)")
    parser.add_argument("--encoding", default="", help="csv encoding (default auto: utf-8 then gbk)")
    parser.add_argument("--sep", default="", help="csv separator (default auto)")
    parser.add_argument("--rows", type=int, default=5, help="sample rows to include")
    args = parser.parse_args()
    src = _safe(args.input)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    info = inspect(src, args.sheet or None, args.encoding, args.sep, args.rows)
    info.update({"status": "ok", "input": str(src)})
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc

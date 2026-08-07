#!/usr/bin/env python3
"""Summarize a spreadsheet: describe, nulls, unique counts, groupby (offline)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import read_table, safe_path, sheet_names


def summarize(path: Path, sheet: str | None, encoding: str, sep: str, groupby: str, max_cols: int) -> dict:
    import pandas as pd

    df = read_table(path, sheet, encoding, sep)
    if df.shape[1] == 0:
        raise RuntimeError("table has no columns")
    cols = [str(c) for c in df.columns[:max_cols]]
    summary: dict = {
        "rows": int(df.shape[0]),
        "columns_total": int(df.shape[1]),
        "columns_summarized": len(cols),
        "dtypes": {c: str(df[c].dtype) for c in cols},
        "nulls": {c: int(df[c].isna().sum()) for c in cols},
        "unique": {c: int(df[c].nunique(dropna=False)) for c in cols},
        "numeric": {},
    }
    for c in cols:
        if pd.api.types.is_numeric_dtype(df[c]):
            desc = df[c].describe().to_dict()
            summary["numeric"][c] = {k: round(float(v), 4) for k, v in desc.items()}
    if groupby and groupby in df.columns:
        summary["group_counts"] = {
            str(k): int(v) for k, v in df[groupby].value_counts(dropna=False).head(20).to_dict().items()
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a spreadsheet (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative table path")
    parser.add_argument("--sheet", default="", help="Excel sheet name/index")
    parser.add_argument("--encoding", default="")
    parser.add_argument("--sep", default="")
    parser.add_argument("--groupby", default="", help="column to count groups on")
    parser.add_argument("--max-cols", type=int, default=40, help="cap summarized columns")
    args = parser.parse_args()
    src = safe_path(args.input)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    result = summarize(src, args.sheet or None, args.encoding, args.sep, args.groupby, args.max_cols)
    result.update({"status": "ok", "input": str(src), "sheet": args.sheet or 0, "sheets": sheet_names(src)})
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc

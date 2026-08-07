#!/usr/bin/env python3
"""Profile a CSV: columns, row count, dtypes, nulls, sample rows (offline)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def profile(path: Path, encoding: str, sep: str, max_rows: int, sample: int) -> dict:
    import pandas as pd

    try:
        df = pd.read_csv(path, encoding=encoding or None, sep=sep or None)
    except UnicodeDecodeError:
        if encoding:
            raise
        df = pd.read_csv(path, encoding="gbk", sep=sep or None)
    cols = [str(c) for c in df.columns]
    return {
        "format": path.suffix.lstrip(".").casefold(),
        "rows_total": int(df.shape[0]),
        "rows_profiled": int(min(df.shape[0], max_rows)),
        "columns": cols,
        "dtypes": {c: str(df[c].dtype) for c in cols},
        "nulls": {c: int(df[c].isna().sum()) for c in cols},
        "sample": df.head(sample).to_dict(orient="records"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile a CSV (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative .csv path")
    parser.add_argument("--max-rows", type=int, default=0, help="cap profiled rows (0 = all)")
    parser.add_argument("--sample", type=int, default=5, help="sample rows to include")
    parser.add_argument("--encoding", default="")
    parser.add_argument("--sep", default="")
    args = parser.parse_args()
    src = _safe(args.input)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    report = profile(src, args.encoding, args.sep, args.max_rows or None, args.sample)
    report.update({"status": "ok", "input": str(src)})
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc

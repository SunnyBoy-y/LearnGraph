#!/usr/bin/env python3
"""Clean/transform a spreadsheet: select, dropna, fillna, rename, filter (offline)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from _common import read_table, safe_path, suffix


def _apply(df, columns, dropna, fill, rename, filter_col, filter_value, filter_mode):
    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise RuntimeError(f"unknown columns: {missing}")
        df = df[columns]
    if dropna:
        df = df.dropna()
    if fill:
        for col, value in fill.items():
            if col in df.columns:
                df[col] = df[col].fillna(value)
    if rename:
        df = df.rename(columns=rename)
    if filter_col:
        if filter_col not in df.columns:
            raise RuntimeError(f"unknown filter column: {filter_col}")
        if filter_mode == "eq":
            df = df[df[filter_col].astype(str) == str(filter_value)]
        elif filter_mode == "ne":
            df = df[df[filter_col].astype(str) != str(filter_value)]
        elif filter_mode == "notnull":
            df = df[df[filter_col].notna()]
        else:
            raise RuntimeError(f"unsupported filter mode: {filter_mode}")
    return df


def _parse_kv(raw: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw or []:
        if "=" not in item:
            raise RuntimeError(f"expected key=value, got {item!r}")
        key, _, value = item.partition("=")
        result[key.strip()] = value.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean/transform a spreadsheet (offline).")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True, help=".csv or .xlsx output")
    parser.add_argument("--sheet", default="")
    parser.add_argument("--encoding", default="")
    parser.add_argument("--sep", default="")
    parser.add_argument("--columns", nargs="+", default=[], help="columns to keep")
    parser.add_argument("--dropna", action="store_true", help="drop rows with any null")
    parser.add_argument("--fill", nargs="+", default=[], metavar="COL=VAL", help="fill nulls per column")
    parser.add_argument("--rename", nargs="+", default=[], metavar="OLD=NEW", help="rename columns")
    parser.add_argument("--filter-col", default="")
    parser.add_argument("--filter-value", default="")
    parser.add_argument("--filter-mode", choices=["eq", "ne", "notnull"], default="eq")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = safe_path(args.input)
    dst = safe_path(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    fill = _parse_kv(args.fill)
    rename = _parse_kv(args.rename)
    df = read_table(src, args.sheet or None, args.encoding, args.sep)
    df = _apply(df, args.columns, args.dropna, fill, rename, args.filter_col, args.filter_value, args.filter_mode)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out_fmt = suffix(dst)
    if out_fmt == "xlsx":
        df.to_excel(dst, index=False, engine="openpyxl")
    elif out_fmt == "csv":
        df.to_csv(dst, index=False)
    elif out_fmt == "tsv":
        df.to_csv(dst, index=False, sep="\t")
    else:
        raise RuntimeError(f"unsupported output format: {dst.suffix} (csv/tsv/xlsx)")
    data = dst.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "rows": int(df.shape[0]),
                "columns": [str(c) for c in df.columns],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc

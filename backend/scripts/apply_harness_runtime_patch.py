"""Idempotent, exact-anchor maintenance patch for the large runtime module.

Run with --apply. No Git operations, service restarts, or data migrations.
Refuses ambiguous anchors and preserves existing newline conventions.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path

OLD = '''    @staticmethod
    def _bounded_content(value: dict[str, Any]) -> tuple[str, bool]:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        raw = encoded.encode("utf-8")
        if len(raw) <= AGENT_TOOL_RESULT_MAX_BYTES:
            return encoded, False
        prefix = raw[: AGENT_TOOL_RESULT_MAX_BYTES - 256].decode("utf-8", errors="ignore")
        return (
            json.dumps(
                {
                    "truncated": True,
                    "message": "Tool result exceeded the Agent transcript limit",
                    "preview": prefix,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            True,
        )'''.encode()
NEW = '''    @staticmethod
    def _bounded_content(value: dict[str, Any]) -> tuple[str, bool]:
        from app.services.harness_contracts import bounded_json

        return bounded_json(value, AGENT_TOOL_RESULT_MAX_BYTES)'''.encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "app/services/agent_runtime.py"
    raw = target.read_bytes()
    old, new = OLD, NEW
    if b"\r\n" in raw:
        old, new = old.replace(b"\n", b"\r\n"), new.replace(b"\n", b"\r\n")
    if new in raw and old not in raw:
        print("already applied")
        return
    if raw.count(old) != 1:
        raise SystemExit("Refusing patch: expected exactly one unchanged anchor")
    updated = raw.replace(old, new, 1)
    compile(updated, str(target), "exec")
    if args.apply:
        # Compare again immediately before the short write; do not replace a
        # file changed by another editor since the anchor was examined.
        if target.read_bytes() != raw:
            raise SystemExit("Refusing patch: file changed concurrently")
        target.write_bytes(updated)
    print({"applied": args.apply, "before_sha256": hashlib.sha256(raw).hexdigest(),
           "after_sha256": hashlib.sha256(updated).hexdigest()})


if __name__ == "__main__":
    main()

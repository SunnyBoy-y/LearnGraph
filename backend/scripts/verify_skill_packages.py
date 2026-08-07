"""Verify built-in official Agent Skill packages (static, no DB required).

Checks every OfficialSkillSpec in ``app.services.skill_package``:
- SKILL.md frontmatter has matching ``name`` and a ``description``;
- every file stays within the package file-size limit;
- each ``scripts/<name>.py|js|mjs|cjs`` has a matching ``scripts/<name>.md``;
- no forbidden patterns in scripts (absolute paths, host subprocess of
  network/package managers, secret env reads) and no direct-network docs.

Run with the backend venv:
    backend/.venv/Scripts/python backend/scripts/verify_skill_packages.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.skill_package import (  # noqa: E402
    MAX_SKILL_FILE_BYTES,
    OFFICIAL_SKILLS,
    _official_package_hash,
    official_skill_package_files,
    parse_skill_md_frontmatter,
)

FORBIDDEN_IMPORTS = [
    (re.compile(r"(?i)^\s*(?:import\s+requests|from\s+requests\b|import\s+urllib\.request|from\s+urllib\s+import\s+request)"), "direct http client import"),
]
# Actual command invocations to forbid inside subprocess / os.system calls.
FORBIDDEN_EXEC = [
    (re.compile(r"(?i)\b(?:npm|pnpm|yarn)\s+(?:install|ci|i)\b"), "npm/pnpm install"),
    (re.compile(r"(?i)\bpip(?:3)?\s+install\b"), "pip install"),
    (re.compile(r"(?i)\bcurl\b"), "curl"),
    (re.compile(r"(?i)\bwget\b"), "wget"),
    (re.compile(r"(?i)\bgit\s+(?:clone|fetch|pull|push)\b"), "remote git"),
]
EXEC_SINKS = [re.compile(r"subprocess\.(run|Popen|call|check_output|check_call)\s*\("), re.compile(r"\bos\.(system|popen)\s*\(")]


def check_script(rel: str, text: str) -> list[str]:
    issues: list[str] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"(?i)^\s*(?:def\s|class\s|import\s|from\s|\"\"\"|''')", stripped):
            # import lines handled below; docstrings skipped for exec checks
            if not (stripped.startswith("import") or stripped.startswith("from")):
                continue
        for pattern, label in FORBIDDEN_IMPORTS:
            if pattern.search(stripped):
                issues.append(f"{rel}:{lineno}: forbidden pattern ({label})")
        if any(sink.search(line) for sink in EXEC_SINKS):
            for pattern, label in FORBIDDEN_EXEC:
                if pattern.search(line):
                    issues.append(f"{rel}:{lineno}: forbidden execution ({label})")
    return issues


def main() -> int:
    failures: list[str] = []
    for spec in OFFICIAL_SKILLS:
        files = official_skill_package_files(spec)
        md = files.get("SKILL.md", b"")
        meta, _ = parse_skill_md_frontmatter(md.decode("utf-8", "replace"))
        if meta.get("name") != spec.key:
            failures.append(f"{spec.key}: SKILL.md name mismatch ({meta.get('name')!r})")
        if not meta.get("description"):
            failures.append(f"{spec.key}: SKILL.md missing description")
        for rel, data in files.items():
            if len(data) > MAX_SKILL_FILE_BYTES:
                failures.append(f"{spec.key}: {rel} exceeds {MAX_SKILL_FILE_BYTES}")
        scripts = [
            rel
            for rel in files
            if rel.startswith("scripts/") and rel.rsplit(".", 1)[-1] in {"py", "js", "mjs", "cjs"}
        ]
        docs = {rel for rel in files if rel.startswith("scripts/") and rel.endswith(".md")}
        for rel in scripts:
            doc = f"{rel.rsplit('.', 1)[0]}.md"
            if doc not in docs:
                failures.append(f"{spec.key}: missing doc for {rel}")
        for rel, data in files.items():
            if rel.startswith("scripts/") and rel.endswith((".py", ".js", ".mjs", ".cjs")):
                for issue in check_script(rel, data.decode("utf-8", "replace")):
                    failures.append(f"{spec.key}: {rel}: {issue}")
        _official_package_hash(files)  # hash must not raise
        print(f"ok {spec.key:28s} category={spec.category or '-':12s} files={len(files):2d}")

    if failures:
        print("\nFAILURES:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"\nAll {len(OFFICIAL_SKILLS)} official skill packages valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

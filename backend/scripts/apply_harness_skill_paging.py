"""Apply the reviewed skill.read pagination patch without a whole-file rewrite prompt."""
import ast
import hashlib
import os
from pathlib import Path
import tempfile

p = Path(__file__).resolve().parents[1] / "app/services/mcp_skills.py"
original = p.read_bytes()
text = original.decode("utf-8")
patches = [
    (
        '                                    "description": "Package-relative file path; defaults to SKILL.md.",\n                                },\n                            },\n                            "required": ["skill_key"],',
        '                                    "description": "Package-relative file path; defaults to SKILL.md.",\n                                },\n                                "cursor": {\n                                    "type": "string",\n                                    "maxLength": 2048,\n                                    "description": "Use next_cursor from the previous page; bound to the file version.",\n                                },\n                                "max_chars": {\n                                    "type": "integer",\n                                    "minimum": 1,\n                                    "maximum": SKILL_READ_MAX_CONTENT_CHARS,\n                                    "description": "Maximum text characters per page. Follow next_cursor until has_more is false.",\n                                },\n                            },\n                            "required": ["skill_key"],',
    ),
    (
        '        input_json = {"skill_key": skill_key, "path": raw_path}',
        '        input_json = {"skill_key": skill_key, "path": raw_path,\n                      "cursor": arguments.get("cursor"),\n                      "max_chars": arguments.get("max_chars", SKILL_READ_MAX_CONTENT_CHARS)}',
    ),
    (
        '        truncated = len(content) > SKILL_READ_MAX_CONTENT_CHARS\n        if truncated:\n            content = content[:SKILL_READ_MAX_CONTENT_CHARS]',
        '''        from app.services.harness_contracts import text_page

        try:
            max_chars = arguments.get("max_chars", SKILL_READ_MAX_CONTENT_CHARS)
            cursor = arguments.get("cursor")
            if type(max_chars) is not int or not 1 <= max_chars <= SKILL_READ_MAX_CONTENT_CHARS:
                raise AppError(422, "invalid_page_size", "max_chars is outside the skill read budget")
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048):
                raise AppError(422, "invalid_read_cursor", "Use next_cursor from the previous read")
            page = text_page(
                content, key=f"skill:{self.workspace_id}:{skill.id}:{path}",
                max_chars=max_chars, cursor=cursor,
            )
        except AppError as exc:
            self._fail_invocation(
                invocation, exc.code, exc.message, status="failed", http_status=exc.status_code,
            )''',
    ),
    (
        '            "content": content,\n            "truncated": truncated,\n            "package_files": files[:64],',
        '            **page,\n            "package_files": files[:64],',
    ),
    (
        '                            "or to load its references/ files on demand."',
        '                            "or to load its references/ files on demand. Follow next_cursor while has_more is true."',
    ),
    (
        '                "truncated": truncated,\n                "content_chars": len(content),',
        '                "truncated": page["truncated"],\n                "content_chars": len(page["content"]),',
    ),
]
for old, new in patches:
    old, new = old.replace("\\n", "\n"), new.replace("\\n", "\n")
    count = text.count(old)
    if count == 0 and text.count(new) == 1:
        continue
    if count != 1:
        raise SystemExit(f"Expected exactly one anchor, found {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)
ast.parse(text, filename=str(p))
if p.read_bytes() != original:
    raise SystemExit("Source changed during patch preparation; no write performed")
with tempfile.NamedTemporaryFile(dir=p.parent, prefix=p.name + ".", suffix=".tmp", delete=False) as f:
    f.write(text.encode("utf-8"))
    tmp = f.name
os.replace(tmp, p)
print({"path": str(p), "applied": True, "replacements": len(patches),
       "before_sha256": hashlib.sha256(original).hexdigest(),
       "after_sha256": hashlib.sha256(p.read_bytes()).hexdigest()})

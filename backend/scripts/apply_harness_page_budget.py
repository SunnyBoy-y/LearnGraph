"""Patch only the inspected skill paging and runtime budget anchors."""
import ast
import hashlib
import os
from pathlib import Path
import tempfile

root = Path(__file__).resolve().parents[1] / "app/services"
patches = {
    "agent_runtime.py": [(
        'AGENT_TOOL_RESULT_MAX_BYTES = 128 * 1024',
        'from app.services.harness_contracts import AGENT_TOOL_RESULT_MAX_BYTES',
    )],
    "mcp_skills.py": [(
        '        from app.services.harness_contracts import text_page',
        '        from app.services.harness_contracts import AGENT_TOOL_RESULT_MAX_BYTES, canonical_json, text_page',
    ), (
        '''        result = {
            "skill_key": skill.skill_key,
            "name": skill.name,
            "path": path,
            **page,
            "package_files": files[:64],
        }
        invocation.status = "succeeded"''',
        '''        result = {
            "skill_key": skill.skill_key,
            "name": skill.name,
            "path": path,
            **page,
            "package_files": files[:64],
            "package_files_total": len(files),
            "package_files_truncated": len(files) > 64,
        }

        def model_payload() -> dict[str, Any]:
            # Match invoke_agent_function -> _invocation_data -> runtime._success.
            return {
                "invocation_id": invocation.id,
                "target_type": invocation.target_type,
                "tool_name": invocation.tool_name,
                "status": "succeeded",
                "result": result,
                "skill_trigger": {
                    "skill_key": skill.skill_key,
                    "skill_name": skill.skill_key,
                    "skill_id": None,
                    "origin": "catalog_read",
                },
            }

        page_chars = max_chars
        while len(canonical_json(model_payload()).encode("utf-8")) > AGENT_TOOL_RESULT_MAX_BYTES:
            visible_files = result["package_files"]
            directory_bytes = len(canonical_json(visible_files).encode("utf-8"))
            if visible_files and (directory_bytes > AGENT_TOOL_RESULT_MAX_BYTES // 3 or page_chars == 1):
                result["package_files"] = visible_files[:len(visible_files) // 2]
                result["package_files_truncated"] = True
            elif page_chars > 1:
                page_chars = max(1, page_chars // 2)
                page = text_page(
                    content, key=f"skill:{self.workspace_id}:{skill.id}:{path}",
                    max_chars=page_chars, cursor=cursor,
                )
                result.update(page)
            else:
                self._fail_invocation(
                    invocation, "skill_page_metadata_too_large",
                    "Skill metadata exceeds the tool result byte budget",
                    status="failed", http_status=422,
                )
        invocation.status = "succeeded"''',
    )],
}
prepared = []
for name, replacements in patches.items():
    path = root / name
    original = path.read_bytes()
    text = original.decode("utf-8")
    applied = 0
    for old, new in replacements:
        if text.count(old) == 0 and text.count(new) == 1:
            continue
        if text.count(old) != 1:
            raise SystemExit(f"Expected one anchor in {name}: {old[:100]!r}")
        text = text.replace(old, new, 1)
        applied += 1
    ast.parse(text, filename=str(path))
    prepared.append((path, original, text.encode("utf-8"), applied))
for path, original, updated, applied in prepared:
    if path.read_bytes() != original:
        raise SystemExit(f"Source changed: {path}; no overwrite")
    if applied:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            handle.write(updated)
            temporary = handle.name
        os.replace(temporary, path)
    print({"path": str(path), "applied_replacements": applied,
           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

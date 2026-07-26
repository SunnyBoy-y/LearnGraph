"""Static security scan for Agent Skill packages (audit layer 2 of 4).

Layer 1 is format validation (``SkillPackageService.validate``); this module
adds offline pattern scanning over package text: dangerous shell/script
constructs, prompt-injection and retrieval-bait phrasing in SKILL.md (the
semantic supply-chain surface described in arXiv:2605.11418), and obfuscation
markers (zero-width unicode, large base64 blobs).  Layer 3 (model-based
semantic review) lives in ``skill_semantic_review.py``; layer 4 is the Docker
sandbox trial run.

The scanner is intentionally conservative: findings are advisories attached to
``validation_report["security_scan"]`` — they never block installation on
their own, but the UI surfaces medium/high risk before authorization.
"""

from __future__ import annotations

import re
from typing import Any

# (severity, category, compiled pattern, human explanation, scope)
# scope: "scripts" = code files only, "docs" = markdown/text docs, "all" = both.
_RULES: list[tuple[str, str, re.Pattern[str], str, str]] = []


def _rule(severity: str, category: str, pattern: str, explanation: str, scope: str) -> None:
    _RULES.append(
        (severity, category, re.compile(pattern, re.IGNORECASE), explanation, scope)
    )


# --- dangerous commands / code (scripts + fenced examples in docs) ----------
_rule("high", "destructive-command", r"\brm\s+-[a-z]*[rf][a-z]*[rf]\b", "递归强制删除命令", "all")
_rule("high", "remote-exec", r"\b(?:curl|wget)\b[^\n]{0,200}\|\s*(?:ba|z|da)?sh\b", "下载并直接执行远程脚本", "all")
_rule("high", "remote-exec", r"\bbase64\s+(?:-d|--decode)\b[^\n]{0,120}\|\s*(?:sh|bash|python)", "base64 解码后执行", "all")
_rule("high", "reverse-shell", r"/dev/tcp/|\bnc\s+-e\b|\bmkfifo\b[^\n]{0,80}\|\s*(?:sh|bash)", "反向 shell 特征", "all")
_rule("high", "credential-access", r"~/\.ssh\b|\bid_rsa\b|\bauthorized_keys\b", "SSH 私钥/密钥文件访问", "all")
_rule("high", "credential-access", r"\bkeyring\.get_password\b|\bsecurity\s+find-generic-password\b|\bcmdkey\b", "系统凭据库读取", "all")
_rule("high", "credential-access", r"(?:Chrome|Chromium|Edge|Firefox|Brave)[^\n]{0,80}(?:Cookies|Login Data|Local State)", "浏览器 Cookie/凭据文件访问", "all")
_rule("medium", "credential-access", r"(?:cat|type|open|read_text)\s*\(?\s*['\"]?\.env\b", ".env 环境密钥文件读取", "all")
_rule("medium", "dynamic-exec", r"\beval\s*\(|\bexec\s*\(|new\s+Function\s*\(", "动态代码执行", "scripts")
_rule("medium", "shell-exec", r"\bos\.system\s*\(|\bsubprocess\.[a-z_]+\([^)]{0,200}shell\s*=\s*True|\bchild_process\b", "shell 命令执行", "scripts")
_rule("medium", "supply-chain", r"\b(?:pip|pip3)\s+install\b|\bnpm\s+install\s+-g\b|\buv\s+pip\s+install\b", "运行时安装第三方包", "scripts")
_rule("medium", "git-config", r"\bgit\s+config\s+--global\b", "修改全局 Git 配置", "all")
_rule("medium", "network", r"\brequests\.(?:get|post|put|delete)\s*\(|\burllib\.request\b|\bfetch\s*\(|\baxios\b|\bhttp\.client\b", "网络请求代码", "scripts")

# --- prompt-injection / retrieval bait (natural-language surface) -----------
_rule("high", "prompt-injection", r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions", "诱导忽略系统/先前指令", "docs")
_rule("high", "prompt-injection", r"disregard\s+[^\n]{0,40}(?:safety|system\s+prompt|guardrail|polic)", "诱导绕过安全策略", "docs")
_rule("high", "deception", r"do\s+not\s+(?:tell|inform|mention|reveal)[^\n]{0,40}(?:user|human)", "要求向用户隐瞒行为", "docs")
_rule("high", "deception", r"without\s+(?:asking|telling|informing|notifying)\s+the\s+user", "绕过用户确认", "docs")
_rule("high", "exfiltration", r"(?:send|upload|post|transmit|forward)[^\n]{0,60}(?:api[-_ ]?key|token|password|credential|secret)", "凭据外传指令", "docs")
_rule("medium", "retrieval-bait", r"(?:always|must)\s+(?:use|select|prefer|choose|invoke)\s+this\s+skill|(?:highest|top)\s+priority\s+skill|use\s+this\s+skill\s+for\s+(?:all|every|any)\b", "夸大触发概率的检索诱饵", "docs")
_rule("high", "prompt-injection", r"忽略[^\n]{0,10}(?:指令|提示|规则)|无视[^\n]{0,10}(?:系统|安全)", "诱导忽略指令（中文）", "docs")
_rule("high", "deception", r"不要(?:告诉|告知|提醒)用户|瞒着用户|不经用户(?:确认|同意)", "要求向用户隐瞒（中文）", "docs")
_rule("high", "exfiltration", r"(?:发送|上传|回传)[^\n]{0,30}(?:密钥|凭证|密码|令牌)", "凭据外传指令（中文）", "docs")
_rule("medium", "retrieval-bait", r"(?:总是|必须|优先)(?:使用|选择|调用)(?:本|此|这个)(?:技能|skill)", "检索诱饵（中文）", "docs")

# --- obfuscation ------------------------------------------------------------
_rule("high", "obfuscation", r"[​-‏‪-‮⁠-⁤﻿]", "零宽/双向控制等不可见字符（可隐藏指令）", "all")
_rule("medium", "obfuscation", r"[A-Za-z0-9+/=]{240,}", "超长 base64 状字符串", "all")
_rule("medium", "obfuscation", r"data:text/(?:html|javascript)", "内联 data: 可执行内容", "all")

_SCRIPT_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".sh")
MAX_FINDINGS = 50
EXCERPT_CHARS = 120


def _is_script(path: str) -> bool:
    return path.startswith("scripts/") or path.lower().endswith(_SCRIPT_SUFFIXES)


def _excerpt(text: str, start: int, end: int) -> str:
    lo = max(0, start - 40)
    hi = min(len(text), end + 40)
    snippet = text[lo:hi].replace("\n", " ")
    # Make invisible characters visible in the report.
    snippet = re.sub(
        r"[​-‏‪-‮⁠-⁤﻿]",
        "�",
        snippet,
    )
    return snippet[:EXCERPT_CHARS]


def scan_skill_files(files: list[tuple[str, str]]) -> dict[str, Any]:
    """Scan (path, text) pairs; returns a JSON-safe report for validation_report."""

    findings: list[dict[str, str]] = []
    counts = {"high": 0, "medium": 0, "low": 0}
    for path, text in files:
        if not text:
            continue
        scope_kind = "scripts" if _is_script(path) else "docs"
        for severity, category, pattern, explanation, scope in _RULES:
            if scope != "all" and scope != scope_kind:
                continue
            match = pattern.search(text)
            if match is None:
                continue
            counts[severity] = counts.get(severity, 0) + 1
            if len(findings) < MAX_FINDINGS:
                findings.append(
                    {
                        "severity": severity,
                        "category": category,
                        "path": path,
                        "pattern": pattern.pattern[:120],
                        "explanation": explanation,
                        "excerpt": _excerpt(text, match.start(), match.end()),
                    }
                )
    if counts.get("high"):
        risk = "high"
    elif counts.get("medium"):
        risk = "medium"
    else:
        risk = "low"
    return {
        "schema_version": "1.0",
        "risk_level": risk,
        "finding_count": sum(counts.values()),
        "counts": counts,
        "findings": findings,
        "scanned_files": len(files),
    }


def attach_scan_report(skill: Any, files: list[tuple[str, str]]) -> dict[str, Any]:
    """Run the scan and merge it into ``skill.validation_report``."""

    report = scan_skill_files(files)
    report["content_hash"] = skill.content_hash or ""
    merged = dict(skill.validation_report or {})
    merged["security_scan"] = report
    skill.validation_report = merged
    return report

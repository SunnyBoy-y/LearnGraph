from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import tomllib
from urllib.parse import urlsplit, urlunsplit

from app.core.errors import AppError

MAX_JSON_BYTES = 4_000_000
MAX_DB_BYTES = 64_000_000
MAX_CANDIDATES = 200


def canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        port = parsed.port
        host = parsed.hostname.lower()
        if ':' in host:
            host = f'[{host}]'
        if port and (parsed.scheme, port) not in {('https', 443), ('http', 80)}:
            host += f':{port}'
        result = urlunsplit((parsed.scheme, host, parsed.path.rstrip('/'), '', ''))
        if len(result) > 500:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError('配置中的API地址不是可安全导入的HTTP(S)地址') from None


def configured_sources(workspace_id: str) -> list[dict]:
    try:
        raw = json.loads(os.environ.get('LEARNGRAPH_PROVIDER_IMPORT_SOURCES', '[]'))
    except (ValueError, TypeError):
        raise AppError(503, 'import_sources_invalid', '部署管理员配置的导入来源格式无效') from None
    if not isinstance(raw, list) or len(raw) > 16:
        raise AppError(503, 'import_sources_invalid', '导入来源必须为至多16项的白名单')
    result, seen = [], set()
    for source in raw:
        if not isinstance(source, dict):
            raise AppError(503, 'import_sources_invalid', '导入来源配置无效')
        # Explicit workspace authorization is mandatory, never a wildcard.
        allowed = source.get('workspace_ids')
        if not isinstance(allowed, list) or workspace_id not in allowed:
            continue
        sid = source.get('id')
        if not isinstance(sid, str) or not sid or len(sid) > 80 or sid in seen:
            raise AppError(503, 'import_sources_invalid', '导入来源ID缺失或重复')
        if source.get('kind') not in {'ccswitch', 'codexplusplus', 'codex'}:
            raise AppError(503, 'import_sources_invalid', '不支持的配置来源类型')
        seen.add(sid)
        result.append(dict(source))
    return result


def source_path(source: dict) -> Path:
    root = Path(os.environ.get('LEARNGRAPH_PROVIDER_IMPORT_ROOT', '/run/learngraph/provider-import')).resolve()
    raw = source.get('path')
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
        raise ValueError('来源路径必须为授权根目录内的相对路径')
    candidate = root / raw
    if '..' in Path(raw).parts:
        raise ValueError('来源路径越界')
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError('来源不在授权目录或不是文件')
    current = candidate
    while current != root:
        if current.is_symlink():
            raise ValueError('不允许通过符号链接读取来源配置')
        current = current.parent
    return resolved


def _object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _candidate(item: dict, app: str, index: int) -> dict:
    settings = _object(item.get('settingsConfig') or item.get('settings_config'))
    if not settings:
        settings = item
    env = _object(settings.get('env'))
    auth = _object(settings.get('auth') or settings.get('authContents'))
    config = settings.get('config') or settings.get('configContents') or {}
    if isinstance(config, str):
        try:
            config = tomllib.loads(config)
        except tomllib.TOMLDecodeError:
            raise ValueError('Codex TOML配置无法解析') from None
    if not isinstance(config, dict):
        raise ValueError('配置结构无法识别')
    providers = _object(config.get('model_providers'))
    selected = config.get('model_provider')
    provider = _object(providers.get(selected)) if isinstance(selected, str) else {}
    # A single configured provider is unambiguous, unlike choosing an arbitrary first profile.
    if not provider and len(providers) == 1:
        provider = _object(next(iter(providers.values())))
    anthropic = app in {'claude', 'anthropic'} or bool(env.get('ANTHROPIC_BASE_URL') or env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY'))
    key = (env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')) if anthropic else (
        provider.get('experimental_bearer_token') or auth.get('OPENAI_API_KEY') or
        env.get('OPENAI_API_KEY') or settings.get('apiKey')
    )
    if not isinstance(key, str) or not key.strip() or key.strip().upper() in {'PROXY_MANAGED', 'PLACEHOLDER', 'YOUR_API_KEY', 'DUMMY'}:
        raise ValueError('未找到可导入的API Key（登录态或代理占位凭据不会作为Key导入）')
    key = key.strip()
    if len(key) > 32_768:
        raise ValueError('凭据大小超过限制')
    url = (env.get('ANTHROPIC_BASE_URL') or 'https://api.anthropic.com') if anthropic else (
        provider.get('base_url') or env.get('OPENAI_BASE_URL') or settings.get('baseUrl')
    )
    if not isinstance(url, str) or not url.strip():
        raise ValueError('配置缺少明确的API地址')
    wire = provider.get('wire_api')
    if wire is not None and (not isinstance(wire, str) or wire not in {'responses', 'chat'}):
        raise ValueError('暂不支持该API协议')
    provider_type = 'anthropic_messages' if anthropic else ('openai_responses' if wire == 'responses' or (app == 'codex' and wire is None) else 'openai_compatible_chat')
    model_values = [config.get('model'), settings.get('model'), env.get('ANTHROPIC_MODEL'),
                    env.get('ANTHROPIC_DEFAULT_HAIKU_MODEL'), env.get('ANTHROPIC_DEFAULT_SONNET_MODEL'),
                    env.get('ANTHROPIC_DEFAULT_OPUS_MODEL')]
    models = list(dict.fromkeys(v.strip() for v in model_values if isinstance(v, str) and 0 < len(v.strip()) <= 160))
    return dict(native_id=f"{index}:{item.get('id') or ''}", name=str(item.get('name') or item.get('label') or f'导入连接 {index + 1}')[:140],
                provider_type=provider_type, base_url=canonical_url(url), api_key=key, model_ids=models)


def parse_document(data: object, kind: str) -> tuple[list[dict], list[str]]:
    if not isinstance(data, dict):
        raise ValueError('配置必须是JSON对象')
    raw = data.get('relayProfiles') if kind == 'codexplusplus' else data.get('providers')
    if raw is None and kind == 'codex':
        raw = [data]
    if isinstance(raw, dict):
        entries = [dict(value, id=key) for key, value in raw.items() if isinstance(value, dict)]
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError('不支持此配置格式；需providers或relayProfiles结构')
    if len(entries) > MAX_CANDIDATES:
        raise ValueError('候选数量超过200项限制')
    candidates, warnings = [], []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            warnings.append(f'第{index + 1}项不是可识别的供应商配置')
            continue
        try:
            candidates.append(_candidate(item, str(item.get('app_type') or ('codex' if kind in {'codex', 'codexplusplus'} else '')), index))
        except ValueError as exc:
            warnings.append(f'第{index + 1}项：{exc}')
    return candidates, warnings


def read_source(source: dict) -> tuple[list[dict], list[str], str]:
    path = source_path(source)
    size = path.stat().st_size
    warnings = []
    if source['kind'] == 'codex' and path.suffix.lower() == '.toml':
        auth_path = source_path({**source, 'path': source['auth_path']}) if source.get('auth_path') else None
        paths = [path] + ([auth_path] if auth_path else [])
        before = [(p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
        parts = []
        for current in paths:
            with current.open('rb') as handle:
                content = handle.read(MAX_JSON_BYTES + 1)
            if len(content) > MAX_JSON_BYTES:
                raise ValueError('Codex配置文件超过4MB限制')
            parts.append(content)
        after = [(p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
        if before != after:
            raise ValueError('Codex配置在读取期间已变化，请重新扫描')
        try:
            document = {'config': tomllib.loads(parts[0].decode('utf-8-sig')),
                        'auth': json.loads(parts[1].decode('utf-8-sig')) if auth_path else {}}
        except (ValueError, UnicodeError):
            raise ValueError('Codex TOML或认证JSON格式无效') from None
        candidates, warnings = parse_document(document, 'codex')
    elif source['kind'] == 'ccswitch' and path.suffix.lower() in {'.db', '.sqlite', '.sqlite3'}:
        if size > MAX_DB_BYTES:
            raise ValueError('配置数据库超过64MB限制')
        for suffix in ('-wal', '-shm'):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() and (sidecar.is_symlink() or sidecar.stat().st_size > MAX_DB_BYTES):
                raise ValueError('数据库附属文件不安全或过大')
        # mode=ro honors WAL. Never use immutable=1 against a live application DB.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1.0)) as db:
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            rows = db.execute('SELECT id, app_type, name, settings_config FROM providers LIMIT 201').fetchall()
        if len(rows) > MAX_CANDIDATES:
            raise ValueError('配置数据库供应商超过200项限制')
        candidates = []
        for index, (sid, app, name, config) in enumerate(rows):
            if app not in {'claude', 'codex'}:
                warnings.append(f'第{index + 1}项应用类型暂不支持')
                continue
            try:
                candidates.append(_candidate(dict(id=f'{app}:{sid}', name=name, settings_config=config), app, index))
            except ValueError as exc:
                warnings.append(f'第{index + 1}项：{exc}')
    else:
        if size > MAX_JSON_BYTES:
            raise ValueError('JSON配置超过4MB限制')
        with path.open('rb') as handle:
            content = handle.read(MAX_JSON_BYTES + 1)
        if len(content) > MAX_JSON_BYTES:
            raise ValueError('JSON配置超过4MB限制')
        try:
            document = json.loads(content.decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            raise ValueError('配置不是有效UTF-8 JSON') from None
        candidates, warnings = parse_document(document, source['kind'])
    # Only a one-way revision goes into the preview table, never the plaintext.
    revision = hashlib.sha256(json.dumps(candidates, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return candidates, warnings, revision

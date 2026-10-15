from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.core.security import mask_secret
from app.domain.models import ProviderConfig
from app.domain.provider_import_models import ProviderImportIdentity, ProviderImportPreview
from app.domain.schemas.management import ProviderCreateRequest
from app.services.provider_config_sources import canonical_url, configured_sources, read_source, source_path


def _identity(item: dict) -> str:
    return hashlib.sha256(json.dumps([item['provider_type'], canonical_url(item['base_url']),
        mask_secret(item['api_key'])[1]], ensure_ascii=False).encode()).hexdigest()


def _existing(service, item: dict):
    fingerprint = mask_secret(item['api_key'])[1]
    for provider in service.db.scalars(select(ProviderConfig).where(
        ProviderConfig.workspace_id == service.workspace_id,
        ProviderConfig.provider_type == item['provider_type'],
        ProviderConfig.secret_fingerprint == fingerprint,
    )):
        try:
            if canonical_url(provider.base_url or '') == canonical_url(item['base_url']):
                return provider
        except ValueError:
            continue
    return None


def list_sources(service) -> dict:
    sources = []
    for source in configured_sources(service.workspace_id):
        status, message = 'available', '只读配置来源已授权；点击扫描预览，不会请求供应商'
        try:
            source_path(source)
        except (OSError, ValueError):
            status, message = 'unavailable', '来源文件不可读或未正确挂载，请联系部署管理员'
        sources.append(dict(id=source['id'], label=str(source.get('label') or source['id'])[:100],
                            kind=source['kind'], status=status, message=message))
    return {'sources': sources}


def preview_sources(service, source_ids: list[str] | None = None) -> dict:
    sources = {source['id']: source for source in configured_sources(service.workspace_id)}
    selected = list(dict.fromkeys(source_ids)) if source_ids is not None else list(sources)
    if not selected:
        raise AppError(409, 'import_sources_not_configured', '尚未接入本工作区可读取的部署机器配置，请先配置授权来源和只读挂载')
    if any(sid not in sources for sid in selected):
        raise AppError(403, 'import_source_forbidden', '来源未授权给当前工作区')
    candidates, warnings, snapshots = [], [], {}
    for sid in selected:
        source = sources[sid]
        try:
            items, notes, revision = read_source(source)
        except (OSError, ValueError, sqlite3.Error):
            warnings.append(f'{sid}：来源不可读、格式不支持或超过限制；未读取其他目录')
            continue
        snapshots[sid] = revision
        warnings.extend(f'{sid}：{note}' for note in notes)
        for item in items:
            existing = _existing(service, item)
            candidates.append(dict(
                id=str(uuid4()), native_id=item['native_id'], source_id=sid,
                source_label=str(source.get('label') or sid)[:100], name=item['name'],
                provider_type=item['provider_type'], base_url=item['base_url'],
                has_key=True, model_ids=item['model_ids'], duplicate=existing is not None,
                duplicate_provider_id=existing.id if existing else None,
            ))
    preview_id = str(uuid4())
    service.db.add(ProviderImportPreview(
        id=preview_id, workspace_id=service.workspace_id, actor_id=service.actor_id,
        expires_at=time.time() + 600, snapshots=snapshots, candidates=candidates, state='ready',
    ))
    service.db.commit()
    return dict(preview_id=preview_id, candidates=[{k: v for k, v in item.items() if k != 'native_id'} for item in candidates], warnings=warnings)


def commit_preview(service, preview_id: str, candidate_ids: list[str]) -> dict:
    selected = sorted(set(candidate_ids))
    row = service.db.get(ProviderImportPreview, preview_id)
    if row is None or row.workspace_id != service.workspace_id or row.actor_id != service.actor_id:
        raise AppError(404, 'import_preview_not_found', '导入预览不存在')
    if row.state == 'completed':
        if selected != sorted(row.selected_ids):
            raise AppError(409, 'import_selection_changed', '该预览已提交；更改选择请重新扫描')
        return row.result or {'created': [], 'skipped': [], 'failed': []}
    if row.expires_at < time.time():
        raise AppError(409, 'import_preview_expired', '预览已过期，请重新扫描')
    by_id = {item['id']: item for item in row.candidates}
    if not selected or len(selected) > 200 or any(cid not in by_id for cid in selected):
        raise AppError(422, 'import_selection_invalid', '请选择有效的导入项')
    sources = {source['id']: source for source in configured_sources(service.workspace_id)}
    fresh = {}
    for sid in {by_id[cid]['source_id'] for cid in selected}:
        if sid not in sources:
            raise AppError(403, 'import_source_forbidden', '来源授权已撤销')
        try:
            items, _, revision = read_source(sources[sid])
        except (OSError, ValueError, sqlite3.Error):
            raise AppError(409, 'import_source_changed', '来源文件已变化或不可读，请重新扫描') from None
        if revision != row.snapshots.get(sid):
            raise AppError(409, 'import_source_changed', '来源配置已变化，请重新扫描确认')
        fresh[sid] = {item['native_id']: item for item in items}
    # The claim and all provider writes share one transaction. A crash rolls back
    # both, so retrying a confirmed preview never creates a partial second batch.
    claimed = service.db.execute(update(ProviderImportPreview).where(
        ProviderImportPreview.id == row.id, ProviderImportPreview.state == 'ready',
    ).values(state='committing', selected_ids=selected))
    if not claimed.rowcount:
        service.db.rollback()
        raise AppError(409, 'import_busy', '该预览正在导入，请稍后查看结果')
    result = {'created': [], 'skipped': [], 'failed': []}
    try:
        for cid in selected:
            candidate = by_id[cid]
            item = fresh[candidate['source_id']].get(candidate['native_id'])
            if item is None:
                raise AppError(409, 'import_source_changed', '来源配置已变化，请重新扫描')
            identity = _identity(item)
            existing = _existing(service, item)
            if existing:
                result['skipped'].append(dict(name=item['name'], reason='相同地址、协议和凭据已存在', provider_id=existing.id))
                continue
            try:
                with service.db.begin_nested():
                    known = service.db.get(ProviderImportIdentity, (service.workspace_id, identity))
                    # _existing() checked the live connection. A historical mapping
                    # alone is not a duplicate after its provider URL/key changes.
                    capabilities = {'discovered_model_ids': item['model_ids'], 'import_source': candidate['source_id']}
                    if item['model_ids']:
                        capabilities['default_model'] = item['model_ids'][0]
                    provider = service.create(ProviderCreateRequest(
                        display_name=service._unique_display_name(item['name']),
                        provider_type=item['provider_type'], base_url=item['base_url'],
                        api_key=SecretStr(item['api_key']), capabilities=capabilities,
                    ), auto_probe=False, commit=False)
                    if known:
                        known.provider_id = provider.id
                    else:
                        service.db.add(ProviderImportIdentity(workspace_id=service.workspace_id, fingerprint=identity, provider_id=provider.id))
                    service.db.flush()
                result['created'].append(dict(name=provider.display_name, provider_id=provider.id, enabled=provider.enabled, models=item['model_ids']))
            except IntegrityError:
                # A concurrent preview may have imported the same identity.
                existing = _existing(service, item)
                if existing:
                    result['skipped'].append(dict(name=item['name'], reason='已由另一导入任务创建', provider_id=existing.id))
                else:
                    result['failed'].append(dict(name=item['name'], reason='连接名称或身份冲突，请重新预览'))
            except AppError as exc:
                result['failed'].append(dict(name=item['name'], reason=exc.code))
        service.db.execute(update(ProviderImportPreview).where(ProviderImportPreview.id == row.id).values(
            state='completed', result=result, selected_ids=selected,
        ))
        service.db.commit()
    except Exception:
        service.db.rollback()
        raise
    return result

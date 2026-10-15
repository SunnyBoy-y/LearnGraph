from __future__ import annotations

import os
from pydantic import SecretStr

from app.core.errors import AppError
from app.domain.schemas.management import ProviderCreateRequest
from app.services.provider_config_sources import canonical_url

# The source, endpoint and permitted capability types form one server-owned tuple.
SOURCES = {
    'env-DASHSCOPE_API_KEY': ('DASHSCOPE_API_KEY', 'https://dashscope.aliyuncs.com/compatible-mode/v1', {'qwen', 'qwen_image_search', 'openai_compatible_transcription', 'openai_compatible_embedding'}),
    'env-DEEPSEEK_API_KEY': ('DEEPSEEK_API_KEY', 'https://api.deepseek.com', {'openai_compatible_chat'}),
    'env-OPENAI_API_KEY': ('OPENAI_API_KEY', 'https://api.openai.com/v1', {'openai_responses', 'openai_images', 'openai_compatible_embedding'}),
    'discovered-ollama': (None, 'http://127.0.0.1:11434/v1', {'ollama', 'ollama_embedding'}),
    'discovered-lmstudio': (None, 'http://127.0.0.1:1234/v1', {'openai_compatible_chat', 'openai_compatible_vision'}),
}


def import_discovered(service, payload):
    source = SOURCES.get(payload.source_id)
    if source is None:
        raise AppError(422, 'invalid_discovered_source', '不是受支持的服务端发现来源')
    variable, endpoint, allowed_types = source
    if payload.target_provider_type not in allowed_types:
        raise AppError(422, 'invalid_discovered_target', '该来源不支持所选协议或能力')
    if payload.base_url:
        try:
            valid = canonical_url(payload.base_url) == canonical_url(endpoint)
        except ValueError:
            valid = False
        if not valid:
            raise AppError(422, 'discovered_endpoint_mismatch', '来源地址不能被客户端替换；如需其他地址请手动配置')
    secret = os.environ.get(variable, '').strip() if variable else None
    if variable and not secret:
        raise AppError(409, 'env_secret_unavailable', '来源凭据已不可用，请重新扫描')
    from app.providers.catalog import provider_type_spec
    spec = provider_type_spec(payload.target_provider_type)
    if spec is None or not spec.create_allowed:
        raise AppError(422, 'unsupported_provider_type', '不支持该供应商类型')
    provider = service.create(ProviderCreateRequest(
        display_name=service._unique_display_name((payload.display_name or spec.label).strip()),
        provider_type=payload.target_provider_type, base_url=endpoint,
        api_key=SecretStr(secret) if secret else None,
    ), auto_probe=False)
    # One explicit discovery, not create-probe followed by the same discovery again.
    if spec.supports_model_discovery:
        provider = service._import_discover(provider)
    return provider

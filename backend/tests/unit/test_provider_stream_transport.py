import httpx
import pytest

from app.providers.error_policy import provider_stream_retry_category
from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.deepseek import DeepSeekChatProvider
from app.providers.remote.openai import (
    ProviderHTTPError, ProviderInvalidUrlError, ProviderResponseError,
    ProviderTimeoutError, ProviderTransportError,
)


@pytest.mark.parametrize('error,category', [
    (ProviderTransportError('connection closed'), 'upstream_transport'),
    (ProviderTimeoutError('timeout'), 'timeout'),
    (ProviderHTTPError('overloaded', status_code=503), 'upstream_http'),
    (ProviderHTTPError('invalid model', status_code=400), None),
    (ProviderHTTPError('invalid key', status_code=401), None),
    (ProviderHTTPError('vendor error with no status'), None),
    (ProviderResponseError('bad SSE JSON'), None),
    (ProviderInvalidUrlError('bad URL'), None),
])
def test_stream_retry_classification(error, category):
    assert provider_stream_retry_category(error) == category


def test_legacy_adapter_transport_cause_remains_retryable():
    error = ProviderHTTPError('connection failed')
    error.__cause__ = httpx.RemoteProtocolError('Server disconnected without sending a response.')
    assert provider_stream_retry_category(error) == 'upstream_transport'


def test_deepseek_discards_failed_pool_and_accepts_next_request():
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError('Server disconnected without sending a response.', request=request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    provider = DeepSeekChatProvider(provider_id='p', model_id='deepseek-flash',
        base_url='https://api.deepseek.com', api_key='test', transport=httpx.MockTransport(respond))
    messages = [ProviderChatMessage(role='user', content='hello')]
    old_client = provider._client()
    with pytest.raises(ProviderTransportError):
        list(provider.stream_chat(messages))
    assert old_client.is_closed
    assert provider._http_client is None
    events = list(provider.stream_chat(messages))
    assert ''.join(e.content or '' for e in events if e.type == 'text_delta') == 'OK'
    provider.reset_transport()


def test_truncated_stream_does_not_execute_partial_tool_calls():
    provider = DeepSeekChatProvider(provider_id='p', model_id='deepseek-flash',
        base_url='https://api.deepseek.com', api_key='test',
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lg_goal_create","arguments":"{}"}}]}}]}\n\n')))
    events = []
    with pytest.raises(ProviderTransportError):
        for event in provider.stream_chat([ProviderChatMessage(role='user', content='hello')]):
            events.append(event)
    assert not any(event.type == 'tool_calls' for event in events)
    assert provider._http_client is None

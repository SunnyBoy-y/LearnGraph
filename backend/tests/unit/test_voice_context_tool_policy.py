from types import SimpleNamespace

from app.services.chat import ChatService
from app.services.voice_context import VoiceContextService


def test_voice_snapshot_does_not_disable_delegation_tools_with_text_fast_mode():
    class Chat:
        model_provider = SimpleNamespace(provider_id="p", model_id="m")
        def _session_timeline(self, _session): return []
        def _input_token_budget(self): return 1000
        def _build_model_prompt(self, _session, _query, **options):
            assert options["agent_mode"] is True
            return ChatService._mode_tool_policy(agent_mode_enabled=options["agent_mode"]), None
    snapshot = VoiceContextService(Chat()).load_snapshot("chat")
    assert "本轮不提供可调用的工具接口" not in snapshot.prompt_block
    assert "已授权的工具" in snapshot.prompt_block

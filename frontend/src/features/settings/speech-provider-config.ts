import type { ProviderCreateRequest, ProviderTypeCatalogItem, SpeechModelPreset } from "@/types/providers";

export interface SpeechProviderDraft {
  modelId: string;
  name: string;
  apiKey: string;
  baseUrl: string;
  websocketUrl: string;
  voice: string;
  resourceId: string;
  sampleRate: string;
  silenceMs: string;
  language: string;
  emotion: string;
  speechRate: string;
}

export function speechModelsForRole(catalog: ProviderTypeCatalogItem[], role: string): SpeechModelPreset[] {
  const models = catalog.filter((item) => item.role === role && item.create_allowed)
    .flatMap((item) => item.speech_models ?? []);
  return [...new Map(models.map((model) => [model.id, model])).values()];
}

export function speechDraftForModel(model?: SpeechModelPreset): SpeechProviderDraft {
  const defaults = model?.default_capabilities ?? {};
  return {
    modelId: model?.id ?? "", name: model?.label ?? "", apiKey: "",
    baseUrl: model?.default_base_url ?? "",
    websocketUrl: String(defaults.realtime_ws_url ?? ""),
    voice: String(defaults.voice_type ?? ""), resourceId: String(defaults.resource_id ?? ""),
    sampleRate: String(defaults.sample_rate ?? defaults.realtime_sample_rate ?? 24000),
    silenceMs: String(defaults.realtime_silence_ms ?? 400), language: String(defaults.realtime_language ?? "zh"),
    emotion: String(defaults.emotion ?? ""), speechRate: String(defaults.speech_rate ?? 0),
  };
}

function assertEndpoint(value: string, protocols: string[], label: string) {
  try {
    const url = new URL(value);
    if (!protocols.includes(url.protocol) || !url.hostname || url.username || url.password) throw new Error();
  } catch { throw new Error(`${label}格式不正确，请填写完整的 ${protocols.join(" / ")} 地址。`); }
}

function numberInRange(value: string, min: number, max: number, label: string): number {
  const parsed = Number(value);
  if (!value.trim() || !Number.isInteger(parsed) || parsed < min || parsed > max) {
    throw new Error(`${label}应为 ${min}–${max} 之间的整数。`);
  }
  return parsed;
}

export function buildSpeechProviderPayload(draft: SpeechProviderDraft, model?: SpeechModelPreset): ProviderCreateRequest {
  if (!model || draft.modelId !== model.id) throw new Error("请先选择已适配模型。");
  const baseUrl = draft.baseUrl.trim();
  assertEndpoint(baseUrl, model.purpose === "tts" ? ["wss:", "ws:"] : ["https:", "http:"], "服务地址");
  const capabilities: Record<string, unknown> = {
    ...model.default_capabilities, speech_model_id: model.id, default_model: model.id,
  };
  if (model.purpose === "tts") {
    if (!draft.voice.trim()) throw new Error("请填写音色 ID。");
    if (!draft.resourceId.trim()) throw new Error("请填写资源 ID。");
    Object.assign(capabilities, {
      default_tts_model_id: model.id, voice_type: draft.voice.trim(), resource_id: draft.resourceId.trim(),
      sample_rate: numberInRange(draft.sampleRate, 8000, 48000, "采样率"),
      emotion: draft.emotion.trim(), speech_rate: numberInRange(draft.speechRate, -50, 100, "语速"),
    });
  } else if (model.purpose === "realtime") {
    assertEndpoint(draft.websocketUrl.trim(), ["wss:", "ws:"], "实时连接地址");
    Object.assign(capabilities, {
      default_realtime_transcription_model_id: model.id, realtime_ws_url: draft.websocketUrl.trim(),
      realtime_sample_rate: numberInRange(draft.sampleRate, 8000, 48000, "采样率"),
      realtime_silence_ms: numberInRange(draft.silenceMs, 100, 3000, "断句等待"),
      realtime_language: draft.language.trim(),
    });
  } else if (model.purpose === "stored_async") {
    capabilities.default_async_transcription_model_id = model.id;
  } else {
    capabilities.default_transcription_model_id = model.id;
  }
  return {
    display_name: draft.name.trim() || model.label, provider_type: model.provider_type,
    base_url: baseUrl, api_key: draft.apiKey.trim() || undefined, capabilities,
  };
}

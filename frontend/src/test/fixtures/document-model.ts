import type { Provider, ProviderModel } from "@/types/providers";
import type { FileRecord } from "@/types/files";

// Synthetic test data; these names do not represent configured providers.
export const documentPreviewProviders: Provider[] = ["A", "B"].map((name) => ({
  id: `preview-provider-${name}`,
  workspace_id: "document-model-preview",
  display_name: `演示服务商 ${name}`,
  provider_type: "openai_compatible_chat",
  base_url: null,
  api_key_masked: "",
  enabled: true,
  remote_capability: true,
  capabilities: {
    default_model: `demo-model-${name.toLowerCase()}`,
    reasoning_efforts: ["low", "medium", "high", "xhigh"],
  },
  status: "configured_enabled",
  secret_status: "active",
  secret_version: 1,
  secret_key_provider: "environment",
  secret_key_version: 1,
  created_at: "2026-10-15T00:00:00Z",
}));

export const documentPreviewModels = (provider: Provider): ProviderModel[] => [{
  id: String(provider.capabilities.default_model),
  roles: ["llm"],
  streaming: true,
  remote: true,
  enabled: true,
}];

export const documentPreviewFile: FileRecord = {
  id: "preview-map", workspace_id: "document-model-preview",
  original_name: "preview-map.png", mime_type: "image/png", size_bytes: 1024,
  sha256: "preview", storage_status: "available", parse_capability: "image",
  parse_status: "not_required", parser_name: null, parser_version: null,
  error_message: null, created_at: "2026-10-15T00:00:00Z",
};

import { describe, expect, it } from "vitest";
import { modelSupportsAgent, providerModelOptions } from "./model-choices";
import { isModelProvider, type Provider, type ProviderModel, type ProviderModelCapabilities } from "@/types/providers";

const caps = (values: Partial<ProviderModelCapabilities>) => values as ProviderModelCapabilities;
const provider = (values: Partial<Provider> = {}) => ({
  id: "p", provider_type: "openai_compatible_chat", enabled: true, remote_capability: true,
  capabilities: {}, ...values,
}) as Provider;
const model = (id: string, values: Partial<ProviderModel> = {}): ProviderModel => ({
  id, roles: ["llm"], streaming: true, remote: true, enabled: true, ...values,
});

describe("server role is authoritative and old servers remain compatible", () => {
  it("accepts a new model provider without updating a frontend whitelist", () => {
    expect(isModelProvider(provider({ provider_type: "new-server-model", role: "model" }))).toBe(true);
  });
  it("does not turn vision/search providers into chat or Agent providers", () => {
    expect(isModelProvider(provider({ role: "vision" }))).toBe(false);
    expect(modelSupportsAgent(provider({ role: "search" }), model("a"))).toBe(false);
  });
  it("includes Ollama Cloud when an old server omits role", () => {
    expect(isModelProvider(provider({ provider_type: "ollama_cloud" }))).toBe(true);
  });
});

describe("model selection and effective saved capabilities", () => {
  it("orders an available explicit default first without losing valid history choices", () => {
    const choices = providerModelOptions(provider({ capabilities: { default_model: "b" } }), [model("a"), model("b")]);
    expect(choices.map(m => m.id)).toEqual(["b", "a"]);
    expect(choices.find(m => m.id === "a")?.id).toBe("a");
  });
  it("does not reinsert a configured model absent from a successful discovery", () => {
    expect(providerModelOptions(provider({ capabilities: { default_model: "gone" } }), [model("a")]).map(m => m.id)).toEqual(["a"]);
  });
  it("retains a manually configured fallback when discovery is unavailable", () => {
    expect(providerModelOptions(provider({ capabilities: { default_model: "manual" } }), undefined)[0]?.id).toBe("manual");
  });
  it("never chooses disabled or hidden defaults", () => {
    const p = provider({ capabilities: { default_model: "b", model_states: { b: false }, hidden_model_ids: ["c"] } });
    expect(providerModelOptions(p, [model("a"), model("b"), model("c")]).map(m => m.id)).toEqual(["a"]);
  });
  it("keeps manually pinned private models even if the vendor did not list them", () => {
    const p = provider({ capabilities: { default_model: "private", discovered_model_ids: ["a"], models: {
      private: caps({ capability_source: "user_declared", supports_agent_tools: false }),
    } } });
    expect(providerModelOptions(p, [model("a")])[0]?.id).toBe("private");
  });
  it("preserves an explicit per-model tool restriction over group and discovery", () => {
    const p = provider({ capabilities: { model_defaults_enabled: true,
      model_defaults: caps({ supports_agent_tools: true }),
      models: { a: caps({ capability_source: "user_declared", supports_agent_tools: false, reasoning_efforts: ["high"] }) },
    } });
    const selected = providerModelOptions(p, [model("a", { capabilities: caps({ supports_agent_tools: true }) })])[0]!;
    expect(modelSupportsAgent(p, selected)).toBe(false);
    expect(selected.capabilities?.reasoning_efforts).toEqual(["high"]);
  });
  it("uses group defaults over automatic catalogue snapshots, but only when enabled", () => {
    const p = provider({ capabilities: { model_defaults_enabled: true,
      model_defaults: caps({ supports_agent_tools: false }),
      models: { a: caps({ capability_source: "official_catalog", supports_agent_tools: true }) },
    } });
    expect(providerModelOptions(p, [model("a")])[0]?.capabilities?.supports_agent_tools).toBe(false);
    p.capabilities.model_defaults_enabled = false;
    expect(providerModelOptions(p, [model("a")])[0]?.capabilities?.supports_agent_tools).toBe(true);
  });
  it("excludes image-only outputs from all default text-chat pickers", () => {
    const choices = [model("image", { capabilities: caps({ supports_text_output: false }) }), model("text")];
    expect(providerModelOptions(provider(), choices).map(m => m.id)).toEqual(["text"]);
    expect(providerModelOptions(provider(), choices, "default_image_generation_model_id").map(m => m.id)).toEqual(["image", "text"]);
  });
});

describe("the same Agent gate for first send, retry, rail and document", () => {
  it.each([
    [provider(), model("a"), true],
    [provider({ enabled: false }), model("a"), false],
    [provider({ capabilities: { supports_agent_tools: false } }), model("a"), false],
    [provider(), model("a", { capabilities: caps({ supports_agent_tools: false }) }), false],
    [provider(), model("a", { remote: false }), false],
    [provider(), undefined, false],
  ])("respects provider and selected-model capabilities", (p, m, expected) => {
    expect(modelSupportsAgent(p as Provider, m as ProviderModel | undefined)).toBe(expected);
  });
});

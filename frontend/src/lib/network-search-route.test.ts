import { describe, expect, it } from "vitest";
import { resolveNetworkSearchRoute } from "./network-search-route";

describe("network search route", () => {
  it("prefers hosted search even when AnySearch is configured", () => {
    expect(resolveNetworkSearchRoute("auto", true, true)).toBe("model_native");
  });
  it("supports native search without an external provider", () => {
    expect(resolveNetworkSearchRoute("auto", true, false)).toBe("model_native");
    expect(resolveNetworkSearchRoute("model_native", true, false)).toBe("model_native");
  });
  it("retains explicit external/local choices", () => {
    expect(resolveNetworkSearchRoute("external", true, true)).toBe("external");
    expect(resolveNetworkSearchRoute("local", true, true)).toBe("local");
  });
  it("never enables a disabled route or silently falls back from native", () => {
    expect(resolveNetworkSearchRoute("disabled", true, true)).toBe("disabled");
    expect(resolveNetworkSearchRoute("model_native", false, true)).toBe("disabled");
  });
  it("preserves external search for models without native search", () => {
    expect(resolveNetworkSearchRoute("auto", false, true)).toBe("auto");
    expect(resolveNetworkSearchRoute("auto", false, false)).toBe("disabled");
    expect(resolveNetworkSearchRoute("external", true, false)).toBe("disabled");
  });
});

import type { SearchRoute } from "@/lib/session-composer-prefs";

/** Resolve the actual search lane without silently falling back from native. */
export function resolveNetworkSearchRoute(
  route: SearchRoute,
  nativeSearch: boolean,
  externalSearch: boolean,
): SearchRoute {
  if (route === "disabled") return "disabled";
  if (route === "model_native") return nativeSearch ? route : "disabled";
  // Keep a user's explicit external/local choice; only auto prefers native.
  if (route === "auto" && nativeSearch) return "model_native";
  return externalSearch ? route : "disabled";
}

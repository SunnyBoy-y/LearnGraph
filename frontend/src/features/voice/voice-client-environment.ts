/** Browser egress gives a city-level hint; server egress is never user location.
 * Raw IP is never persisted or forwarded to the backend. Missing hints require an explicit city.
 */
export type VoiceClientEnvironment = {
  timezone: string;
  location_status: "pending" | "available" | "unavailable";
  location?: { city: string; region: string; country_code: string; latitude: number; longitude: number; timezone: string; source: "client_ip" };
};

export function initialVoiceEnvironment(): VoiceClientEnvironment {
  return { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", location_status: "pending" };
}

export async function resolveVoiceEnvironment(signal?: AbortSignal): Promise<VoiceClientEnvironment> {
  const initial = initialVoiceEnvironment();
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, 8000);
  try {
    const response = await fetch("https://get.geojs.io/v1/ip/geo.json", { signal: controller.signal, credentials: "omit", referrerPolicy: "no-referrer" });
    if (!response.ok) throw new Error("location unavailable");
    const data = await response.json();
    const latitude = typeof data.latitude === "string" && data.latitude.trim() ? Number(data.latitude) : data.latitude;
    const longitude = typeof data.longitude === "string" && data.longitude.trim() ? Number(data.longitude) : data.longitude;
    if ( typeof data.city !== "string" || !data.city.trim() || data.city.length > 120 || typeof latitude !== "number" || !Number.isFinite(latitude) || Math.abs(latitude) > 90 || typeof longitude !== "number" || !Number.isFinite(longitude) || Math.abs(longitude) > 180 || typeof data.timezone !== "string") throw new Error("invalid location");
    new Intl.DateTimeFormat("en", { timeZone: data.timezone });
    return { ...initial, location_status: "available", location: { city: data.city, region: String(data.region || "").slice(0, 120), country_code: String(data.country_code || "").slice(0, 2), latitude: Math.round(latitude * 100) / 100, longitude: Math.round(longitude * 100) / 100, timezone: data.timezone, source: "client_ip" } };
  } catch {
    return { ...initial, location_status: "unavailable" };
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

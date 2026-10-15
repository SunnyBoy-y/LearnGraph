import { getVoiceIceServers } from "@/api/voice";

/**
 * Fetch once for each new RTCPeerConnection. The backend already caches minted
 * credentials and invalidates them on configuration writes. A browser-level
 * five-minute cache would hide saves/disable actions (including HTTP 200 + []).
 * Do not invent a public fallback here: the deployment administrator controls
 * whether STUN is allowed, and an empty response can be an explicit disable.
 */
export async function loadIceServers(): Promise<RTCIceServer[]> {
  try {
    const config = await getVoiceIceServers();
    return (config.iceServers ?? [])
      .filter((item) => Array.isArray(item.urls) ? item.urls.length > 0 : Boolean(item.urls))
      .map((item) => ({
        urls: item.urls,
        username: item.username ?? undefined,
        credential: item.credential ?? undefined,
      }));
  } catch (error) {
    console.warn("[voice] ICE configuration unavailable; attempting host-only connectivity", error);
    return [];
  }
}

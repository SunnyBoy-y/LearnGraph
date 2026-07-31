const MAX_BROWSER_HASH_BYTES = 16 * 1024 * 1024;

/**
 * Hash small files for fast client-side reuse. Large files intentionally skip
 * browser hashing so upload streaming and server-side SHA-256 remain bounded.
 */
export async function hashFileSha256(file: Blob): Promise<string | null> {
  if (file.size > MAX_BROWSER_HASH_BYTES) return null;
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export function readDocumentBytes(blob: Blob): Promise<ArrayBuffer> {
  return typeof blob.arrayBuffer === "function" ? blob.arrayBuffer() : new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("无法读取文件"));
    reader.readAsArrayBuffer(blob);
  });
}

/** Decode common document encodings without silently replacing Chinese text. */
export async function readDocumentText(blob: Blob): Promise<string> {
  const buffer = await readDocumentBytes(blob);
  const bytes = new Uint8Array(buffer);
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes);
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes);
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return new TextDecoder().decode(bytes);
  const declared = blob.type.match(/charset\s*=\s*["']?([^;\s"']+)/i)?.[1]
    ?? new TextDecoder().decode(bytes.subarray(0, 1024)).match(/<meta\b[^>]*charset\s*=\s*["']?([^\s"'/>;]+)/i)?.[1];
  if (declared) {
    try { return new TextDecoder(declared).decode(bytes); } catch { /* Fall back if the label is unknown. */ }
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    try { return new TextDecoder("gb18030", { fatal: true }).decode(bytes); }
    catch { return new TextDecoder().decode(bytes); }
  }
}

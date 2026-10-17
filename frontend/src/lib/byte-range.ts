/** Reject ignored or malformed ranges before buffering response content. */
export async function readByteRange(response: Response, start: number, end: number) {
  const match = response.headers.get("Content-Range")?.match(/^bytes (\d+)-(\d+)\/(\d+)$/);
  const total = Number(match?.[3]);
  const expected = Number(match?.[2]) - start + 1;
  if (response.status !== 206 || !match || Number(match[1]) !== start ||
      !Number.isSafeInteger(total) || total <= 0 || expected <= 0 || expected > 1024 * 1024 ||
      Number(match[2]) !== Math.min(end, total) - 1) {
    await response.body?.cancel();
    throw new Error("文件服务没有返回有效的分段数据，请更新服务或下载后查看。");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("浏览器不支持文件分段读取");
  const data = new Uint8Array(expected);
  let received = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (received + value.length > expected) throw new Error("文件服务返回了过大的分段");
      data.set(value, received);
      received += value.length;
    }
    if (received !== expected) throw new Error("文件分段未完整传输");
    return { data, size: total };
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}

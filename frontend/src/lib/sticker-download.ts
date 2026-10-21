import { downloadStickerPack } from '@/api/stickers';
import { saveBlobViaNative, nativeDownloadSupported } from './native-download';

export async function saveStickerArchive(id: string, name: string, market = false) {
  const blob = await downloadStickerPack(id, market);
  const safeName = Array.from(name).filter(character => character.charCodeAt(0) >= 32).join('').replace(/[<>:"/\\|?*]/g, '-').slice(0, 80) || 'learngraph-stickers';
  const filename = `${safeName}.zip`;
  if (await saveBlobViaNative(blob, filename)) return;
  if (nativeDownloadSupported()) throw new Error('移动端保存失败，请在桌面浏览器下载');
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

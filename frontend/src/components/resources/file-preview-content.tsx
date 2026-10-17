import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { downloadFile, fileStreamUrl, getFile, uploadFile } from "@/api/files";
import { delimitedTextSeparator, filePreviewBlobKey, resolveFilePreviewKind } from "@/lib/file-preview";
import { blobFileSource, PREVIEW_BUFFER_LIMIT, PREVIEW_RANGE_BYTES, remoteFileSource, urlFileSource, type SeekableFile } from "@/lib/seekable-file";
import { Button } from "@/components/ui/button";
import { FilePreviewCanvas, type FilePreviewCanvasProps } from "./file-preview";

export interface FilePreviewSource {
  fileId?: string;
  blob?: Blob;
  url?: string;
  fileSize?: number;
}

type PreviewData = {
  blob?: Blob;
  source?: SeekableFile;
  mediaUrl?: string;
  filename: string;
  mimeType: string;
  needsUpload?: boolean;
};

type FilePreviewContentProps = FilePreviewSource & Omit<FilePreviewCanvasProps, "blob" | "source" | "mediaUrl"> & {
  enabled?: boolean;
  onPreviewError?: (error: Error) => void;
  hideError?: boolean;
};

const OFFICE_KINDS = new Set(["word", "powerpoint", "spreadsheet", "rtf"]);
const MEDIA_KINDS = new Set(["audio", "video", "image"]);

/** The loader chooses a seekable source before any potentially large Blob download. */
function useFilePreview({ fileId, blob, url, fileSize, filename, mimeType = "", enabled = true }: FilePreviewContentProps) {
  const owner = fileId || blob || url || "";
  const [result, setResult] = useState<{ owner: typeof owner; data?: PreviewData; error?: Error }>();
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setResult(undefined);
    const load = async (): Promise<PreviewData> => {
      const kind = resolveFilePreviewKind(filename, mimeType || blob?.type);
      const base = { filename, mimeType: mimeType || blob?.type || "" };
      const delimited = !!delimitedTextSeparator(filename, base.mimeType);
      const office = OFFICE_KINDS.has(kind) && !delimited;
      const plain = kind === "text" || kind === "html" || delimited;
      const textName = kind === "text" ? filename : `${filename}.txt`;
      if (blob) {
        if (kind === "pdf" || kind === "epub") return { ...base, source: blobFileSource(blob) };
        if (MEDIA_KINDS.has(kind)) {
          objectUrl = URL.createObjectURL(blob);
          return { ...base, mediaUrl: objectUrl, blob: kind === "audio" && blob.size <= 4 * 1024 * 1024 ? blob : undefined };
        }
        if (kind === "legacy-office" || (blob.size > PREVIEW_BUFFER_LIMIT && office)) return { ...base, needsUpload: true };
        if (blob.size > PREVIEW_BUFFER_LIMIT && plain) return { filename: textName, mimeType: "text/plain", source: blobFileSource(blob) };
        if (blob.size > PREVIEW_BUFFER_LIMIT) throw new Error("此格式暂不支持大文件分段预览，请下载后查看。");
        return { ...base, blob };
      }
      if (!fileId) {
        if (!url) throw new Error("没有可读取的文件内容。");
        if (MEDIA_KINDS.has(kind)) return { ...base, mediaUrl: url };
        const source = await urlFileSource(url, controller.signal);
        if (kind === "pdf" || kind === "epub") return { ...base, source };
        if (source.size > PREVIEW_BUFFER_LIMIT) {
          if (plain) return { filename: textName, mimeType: "text/plain", source };
          throw new Error("此附件超过直接预览限制，请重新选择原文件后上传阅读。");
        }
        const parts: Uint8Array<ArrayBuffer>[] = [];
        for (let begin = 0; begin < source.size; begin += PREVIEW_RANGE_BYTES) {
          parts.push(await source.read(begin, Math.min(source.size, begin + PREVIEW_RANGE_BYTES), controller.signal) as Uint8Array<ArrayBuffer>);
        }
        return { ...base, blob: new Blob(parts, { type: base.mimeType }), needsUpload: kind === "legacy-office" };
      }
      if (kind === "pdf" || kind === "epub" || kind === "legacy-office") {
        return { filename: kind === "legacy-office" ? `${filename}.pdf` : filename, mimeType: kind === "legacy-office" ? "application/pdf" : base.mimeType, source: await remoteFileSource(fileId, controller.signal, kind === "legacy-office") };
      }
      if (MEDIA_KINDS.has(kind)) return { ...base, mediaUrl: await fileStreamUrl(fileId, { signal: controller.signal }) };
      if (kind === "unsupported") return base;
      const size = fileSize ?? (await getFile(fileId, { signal: controller.signal })).size_bytes;
      if (size > PREVIEW_BUFFER_LIMIT) {
        if (office) return { filename: `${filename}.pdf`, mimeType: "application/pdf", source: await remoteFileSource(fileId, controller.signal, true) };
        if (plain) return { filename: textName, mimeType: "text/plain", source: await remoteFileSource(fileId, controller.signal) };
        throw new Error("此格式暂不支持大文件分段预览，请下载后查看。");
      }
      return { ...base, blob: await downloadFile(fileId, { signal: controller.signal }, PREVIEW_BUFFER_LIMIT) };
    };
    void load().then(
      (data) => { if (!controller.signal.aborted) setResult({ owner, data }); },
      (reason: unknown) => { if (!controller.signal.aborted) setResult({ owner, error: reason instanceof Error ? reason : new Error("无法读取文件") }); },
    );
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [blob, enabled, fileId, fileSize, filename, mimeType, owner, url]);
  const current = result?.owner === owner ? result : undefined;
  return { ...current, loading: enabled && !current };
}

export function FilePreviewContent(props: FilePreviewContentProps) {
  const key = props.fileId ? `file:${props.fileId}` : props.blob ? `local:${filePreviewBlobKey(props.blob)}` : props.url || "empty";
  return <SourcePreview key={key} {...props} />;
}

function SourcePreview({ onPreviewError, hideError, enabled = true, ...props }: FilePreviewContentProps) {
  const [uploaded, setUploaded] = useState<string>();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const uploadController = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => uploadController.current?.abort(), []);
  const preview = useFilePreview({ ...props, fileId: uploaded ?? props.fileId, blob: uploaded ? undefined : props.blob, enabled });
  useEffect(() => { if (preview.error) onPreviewError?.(preview.error); }, [onPreviewError, preview.error]);
  if (!enabled) return null;
  if (preview.loading) return <div className="flex min-h-[24rem] items-center justify-center gap-2 text-sm text-muted-foreground" role="status"><LoaderCircle className="size-4 animate-spin" />正在加载预览…</div>;
  if (preview.error) return hideError ? null : <div className="grid min-h-[24rem] place-items-center p-8 text-center text-sm text-destructive" role="alert">预览加载失败：{preview.error.message}</div>;
  if (preview.data?.needsUpload) return (
    <div className="grid min-h-[24rem] place-items-center p-8 text-center text-sm">
      <div className="max-w-md space-y-4">
        <p>此文件需要服务端转换阅读。将文件上传到当前工作区后，按页加载预览。</p>
        <Button disabled={uploading} onClick={() => {
          setUploading(true); setUploadError("");
          const controller = new AbortController();
          uploadController.current = controller;
          void uploadFile(new File([props.blob ?? preview.data!.blob!], props.filename, { type: props.mimeType || props.blob?.type }), { signal: controller.signal })
            .then((file) => { if (!controller.signal.aborted) setUploaded(file.id); })
            .catch((reason: unknown) => { if (!controller.signal.aborted) setUploadError(reason instanceof Error ? reason.message : "上传失败"); })
            .finally(() => { if (!controller.signal.aborted) setUploading(false); });
        }}>{uploading ? "正在上传…" : "上传并阅读"}</Button>
        {uploadError ? <p className="text-destructive" role="alert">{uploadError}</p> : null}
      </div>
    </div>
  );
  return preview.data ? <FilePreviewCanvas {...props} {...preview.data} /> : null;
}

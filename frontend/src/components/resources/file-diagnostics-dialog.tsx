import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileSearch, Link2, TextSearch } from "lucide-react";

import { listFileChunks, listFileReferences } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type {
  FileParserCapability,
  FileRecord,
  FileReference,
} from "@/types/files";

type FileDiagnosticsDialogProps = {
  capability?: FileParserCapability;
  file: FileRecord;
  onOpenChange: (open: boolean) => void;
  open: boolean;
};

const CHUNK_PAGE_SIZE = 20;

function formattedDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function ReferenceMetadata({ reference }: { reference: FileReference }) {
  if (!Object.keys(reference.metadata_json).length) return null;
  return (
    <details className="mt-2 text-xs text-muted-foreground">
      <summary className="cursor-pointer">查看引用元数据</summary>
      <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-2 font-mono text-[11px] leading-5 text-foreground">
        {JSON.stringify(reference.metadata_json, null, 2)}
      </pre>
    </details>
  );
}

export function FileDiagnosticsDialog({
  capability,
  file,
  onOpenChange,
  open,
}: FileDiagnosticsDialogProps) {
  const [visibleChunkCount, setVisibleChunkCount] = useState(CHUNK_PAGE_SIZE);
  const chunks = useQuery({
    queryKey: ["file-chunks", file.id],
    queryFn: () => listFileChunks(file.id),
    enabled: open,
  });

  useEffect(() => {
    setVisibleChunkCount(CHUNK_PAGE_SIZE);
  }, [file.id, open]);

  const allChunks = chunks.data ?? [];
  const visibleChunks = allChunks.slice(0, visibleChunkCount);
  const references = useQuery({
    queryKey: ["file-references", file.id],
    queryFn: () => listFileReferences(file.id),
    enabled: open,
  });

  return (
    <Sheet onOpenChange={onOpenChange} open={open}>
      <SheetContent className="overflow-y-auto sm:max-w-xl">
        <div className="space-y-4 p-5 pt-12">
        <SheetHeader className="p-0">
          <SheetTitle>文件解析与引用诊断</SheetTitle>
          <SheetDescription>
            查看服务端已保存的解析状态、文本切片和领域引用；这些信息用于定位资料如何进入后续学习流程。
          </SheetDescription>
        </SheetHeader>

        <section className="rounded-xl border p-4" aria-label="文件解析状态">
          <div className="flex items-start gap-3">
            <FileSearch className="mt-0.5 size-4 text-primary" />
            <div className="min-w-0 flex-1">
              <p className="font-medium">{file.original_name}</p>
              <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                {file.mime_type} · SHA-256 {file.sha256}
              </p>
            </div>
          </div>
          <dl className="mt-4 grid gap-x-5 gap-y-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">解析状态</dt>
              <dd className="mt-1 font-medium">{file.parse_status}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">解析器</dt>
              <dd className="mt-1 font-medium">{file.parser_name ?? "尚未运行"}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">解析器版本</dt>
              <dd className="mt-1 font-medium">{file.parser_version ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">格式能力</dt>
              <dd className="mt-1 font-medium">
                {capability
                  ? `${capability.available ? "可用" : "当前不可用"} · ${capability.mode}`
                  : "未匹配到格式声明"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">服务端解析边界</dt>
              <dd className="mt-1 font-medium">{file.parse_capability}</dd>
            </div>
          </dl>
          {capability ? (
            <p className="mt-3 rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">
              {capability.reason}
            </p>
          ) : null}
          {file.error_message ? (
            <p className="mt-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
              服务端解析错误：{file.error_message}
            </p>
          ) : null}
        </section>

        <section className="space-y-3 rounded-xl border p-4" aria-label="文件文本切片">
          <div className="flex items-center gap-2">
            <TextSearch className="size-4 text-primary" />
            <h3 className="font-medium">文本切片</h3>
            {allChunks.length ? (
              <span className="ml-auto text-xs text-muted-foreground">
                {allChunks.length} 段
              </span>
            ) : null}
          </div>
          {chunks.isPending ? <p className="text-sm text-muted-foreground">正在读取服务端切片…</p> : null}
          {chunks.isError ? (
            <p className="text-sm text-destructive" role="alert">无法读取文本切片：{chunks.error.message}</p>
          ) : null}
          {!chunks.isPending && !chunks.isError && !chunks.data?.length ? (
            <p className="text-sm leading-6 text-muted-foreground">
              服务端尚未为此文件保存文本切片。文件未索引、格式不可用或解析失败时都可能出现这一状态。
            </p>
          ) : null}
          <ol className="space-y-3">
            {visibleChunks.map((chunk) => (
              <li className="rounded-lg border p-3" key={chunk.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">第 {chunk.ordinal} 段</Badge>
                  <span className="font-mono text-[11px] text-muted-foreground">{chunk.locator}</span>
                  <span className="ml-auto text-[11px] text-muted-foreground">{formattedDate(chunk.created_at)}</span>
                </div>
                <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
                  {chunk.content}
                </p>
                <details className="mt-2 text-xs text-muted-foreground">
                  <summary className="cursor-pointer">查看完整切片与哈希</summary>
                  <p className="mt-2 whitespace-pre-wrap rounded-md bg-muted p-3 text-sm leading-6 text-foreground">
                    {chunk.content}
                  </p>
                  <p className="mt-2 break-all font-mono text-[11px]">SHA-256 {chunk.content_hash}</p>
                </details>
              </li>
            ))}
          </ol>
          {visibleChunks.length < allChunks.length ? (
            <div className="flex items-center justify-between gap-3 border-t pt-3">
              <span className="text-xs text-muted-foreground">
                已显示 {visibleChunks.length} / {allChunks.length}
              </span>
              <Button
                onClick={() =>
                  setVisibleChunkCount((current) =>
                    Math.min(current + CHUNK_PAGE_SIZE, allChunks.length),
                  )
                }
                size="sm"
                variant="outline"
              >
                显示更多
              </Button>
            </div>
          ) : null}
        </section>

        <section className="space-y-3 rounded-xl border p-4" aria-label="文件引用关系">
          <div className="flex items-center gap-2">
            <Link2 className="size-4 text-primary" />
            <h3 className="font-medium">已保存的引用关系</h3>
          </div>
          {references.isPending ? <p className="text-sm text-muted-foreground">正在读取服务端引用关系…</p> : null}
          {references.isError ? (
            <p className="text-sm text-destructive" role="alert">无法读取引用关系：{references.error.message}</p>
          ) : null}
          {!references.isPending && !references.isError && !references.data?.length ? (
            <p className="text-sm leading-6 text-muted-foreground">
              服务端尚未保存此文件的显式领域引用。删除预检仍会以服务端当前事实为准。
            </p>
          ) : null}
          <ol className="space-y-3">
            {(references.data ?? []).map((reference) => (
              <li className="rounded-lg border p-3" key={reference.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{reference.target_type}</Badge>
                  <Badge variant="outline">{reference.relation}</Badge>
                  <span className="ml-auto text-[11px] text-muted-foreground">{formattedDate(reference.created_at)}</span>
                </div>
                <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
                  目标 ID：{reference.target_id}
                </p>
                {reference.locator ? (
                  <p className="mt-1 text-xs text-muted-foreground">定位：{reference.locator}</p>
                ) : null}
                <ReferenceMetadata reference={reference} />
              </li>
            ))}
          </ol>
        </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}

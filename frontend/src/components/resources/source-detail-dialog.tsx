import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Globe2, Link2, Trash2 } from "lucide-react";

import { getSourceRecord, listSourceLinks } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { StatePill } from "@/components/shared/page-elements";
import { SourceAssociationDialog } from "@/components/resources/source-association-dialog";
import type { SourceRecord } from "@/types/workflow";

type SourceDetailDialogProps = {
  onOpenChange: (open: boolean) => void;
  onRequestDelete: (source: SourceRecord) => void;
  open: boolean;
  source: SourceRecord;
};

function formattedDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function externalUrl(value: string) {
  return value.startsWith("https://") || value.startsWith("http://") ? value : undefined;
}

export function SourceDetailDialog({
  onOpenChange,
  onRequestDelete,
  open,
  source,
}: SourceDetailDialogProps) {
  const [associationOpen, setAssociationOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["source-record", source.id],
    queryFn: () => getSourceRecord(source.id),
    enabled: open,
  });
  const links = useQuery({
    queryKey: ["source-links", source.id],
    queryFn: () => listSourceLinks(source.id),
    enabled: open,
  });
  const record = detail.data ?? source;
  const destination = externalUrl(record.final_url);

  return (
    <>
      <Dialog
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setAssociationOpen(false);
          onOpenChange(nextOpen);
        }}
        open={open}
      >
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>网页来源详情与关联</DialogTitle>
          <DialogDescription>
            来源正文、抓取元数据和关联关系均来自当前工作区服务端记录；查看不会改写图谱或掌握度。
          </DialogDescription>
        </DialogHeader>
        {detail.isError ? (
          <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
            无法刷新来源详情：{detail.error.message}
          </p>
        ) : null}

        <section className="rounded-xl border p-4" aria-label="网页来源详情">
          <div className="flex items-start gap-3">
            <Globe2 className="mt-0.5 size-4 text-primary" />
            <div className="min-w-0 flex-1">
              <p className="font-medium">{record.title}</p>
              {destination ? (
                <a className="mt-1 block break-all text-xs text-primary underline underline-offset-3" href={destination} rel="noreferrer" target="_blank">
                  {record.final_url}
                </a>
              ) : (
                <p className="mt-1 break-all text-xs text-muted-foreground">{record.final_url}</p>
              )}
            </div>
            <StatePill label={record.cache_status} status={record.cache_status} />
          </div>
          <dl className="mt-4 grid gap-x-5 gap-y-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">授权域名</dt>
              <dd className="mt-1 break-all font-medium">{record.authorized_domain}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Provider</dt>
              <dd className="mt-1 break-all font-medium">{record.provider_id}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">内容类型</dt>
              <dd className="mt-1 font-medium">{record.content_type}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">抓取时间</dt>
              <dd className="mt-1 font-medium">{formattedDate(record.created_at)}</dd>
            </div>
          </dl>
          <p className="mt-3 break-all font-mono text-[11px] text-muted-foreground">内容哈希：{record.content_hash}</p>
          <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">原始 URL：{record.source_url}</p>
          {record.research_job_id ? <p className="mt-1 font-mono text-[11px] text-muted-foreground">研究任务：{record.research_job_id}</p> : null}
          <details className="mt-4 rounded-lg border bg-muted/20 p-3">
            <summary className="cursor-pointer text-sm font-medium">查看已持久化的网页正文（{record.content.length} 个字符）</summary>
            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{record.content}</p>
          </details>
          {Object.keys(record.metadata_json).length ? (
            <details className="mt-3 text-xs text-muted-foreground">
              <summary className="cursor-pointer">查看抓取元数据</summary>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-2 font-mono text-[11px] leading-5 text-foreground">
                {JSON.stringify(record.metadata_json, null, 2)}
              </pre>
            </details>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              onClick={() => setAssociationOpen(true)}
              size="sm"
              type="button"
              variant="outline"
            >
              <Link2 className="size-4" />
              关联到…
            </Button>
            <Button
              onClick={() => onRequestDelete(record)}
              size="sm"
              type="button"
              variant="outline"
            >
              <Trash2 className="size-4 text-destructive" />
              删除此来源
            </Button>
          </div>
        </section>

        <section className="space-y-3 rounded-xl border p-4" aria-label="网页来源已有关联">
          <div className="flex items-center gap-2">
            <Link2 className="size-4 text-primary" />
            <h3 className="font-medium">已有 SourceLink</h3>
          </div>
          {links.isPending ? <p className="text-sm text-muted-foreground">正在读取服务端关联…</p> : null}
          {links.isError ? (
            <p className="text-sm text-destructive" role="alert">无法读取已有关联：{links.error.message}</p>
          ) : null}
          {!links.isPending && !links.isError && !links.data?.length ? (
            <p className="text-sm leading-6 text-muted-foreground">
              此来源尚未关联到 Project、Goal、Graph 或 Node。可使用上方“关联到…”重试或改选目标。
            </p>
          ) : null}
          <ol className="space-y-3">
            {(links.data ?? []).map((link) => (
              <li className="rounded-lg border p-3" key={link.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{link.target_type}</Badge>
                  <Badge variant="outline">{link.relation}</Badge>
                  <span className="ml-auto text-[11px] text-muted-foreground">{formattedDate(link.created_at)}</span>
                </div>
                <p className="mt-2 break-all font-mono text-xs text-muted-foreground">目标 ID：{link.target_id}</p>
              </li>
            ))}
          </ol>
        </section>
        </DialogContent>
      </Dialog>
      {associationOpen ? (
        <SourceAssociationDialog
          onOpenChange={setAssociationOpen}
          open
          source={record}
        />
      ) : null}
    </>
  );
}

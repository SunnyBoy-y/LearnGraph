import { useQuery } from "@tanstack/react-query";
import { Download, ExternalLink, FileText } from "lucide-react";
import { getSkillMarketDetail, translateSkillMarket } from "@/api/extensions";
import { LazyStreamdown } from "@/components/ai-elements/lazy-streamdown";
import { ErrorState, LoadingState } from "@/components/shared/page-elements";
import { MarketTranslationControls, useMarketTranslation } from "./market-translation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { SkillMarketCard, SkillMarketDetail } from "@/types/extensions";
function fileSize(bytes: number | null) {
  if (bytes === null) return "大小未提供";
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function SkillMarketDetailDialog({ card, onClose, onInstall, installing }: {
  card: SkillMarketCard | null;
  onClose: () => void;
  onInstall: (detail: SkillMarketDetail) => void;
  installing: boolean;
}) {
  const detail = useQuery({
    queryKey: ["skill-market-detail", card?.market_id, card?.version],
    queryFn: () => getSkillMarketDetail(card!.market_id, card!.version || undefined),
    enabled: Boolean(card), staleTime: 60_000, retry: false,
  });
  const data = detail.data;
  const translation = useMarketTranslation({
    queryKey: ["skill-market-translation", data?.market_id, data?.version],
    request: async (selection) => {
      const result = await translateSkillMarket({ market_id: data!.market_id, version: data!.version, ...selection });
      if (result.market_id !== data!.market_id || result.version !== data!.version) throw new Error("翻译版本与当前说明不一致，请重试。");
      return result;
    },
  });
  const translated = translation.translated;
  return <Dialog open={Boolean(card)} onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent className="max-h-[88dvh] overflow-y-auto sm:max-w-4xl">
      <DialogHeader>
        <DialogTitle>{data?.name || card?.name || "Skill 详情"}</DialogTitle>
        <DialogDescription className="break-all">{card?.market_id} · ClawHub</DialogDescription>
      </DialogHeader>
      {detail.isPending ? <LoadingState /> : detail.isError ? <div className="space-y-3"><ErrorState message={detail.error.message} /><Button variant="outline" size="sm" onClick={() => void detail.refetch()}>重试详情</Button></div> : data && <div className="min-w-0 space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          {data.version && <Badge variant="outline">v{data.version}</Badge>}
          {data.owner && <Badge variant="secondary">{data.owner}</Badge>}
          <a className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" href={data.homepage_url} target="_blank" rel="noreferrer">查看 ClawHub 来源<ExternalLink className="size-3" /></a>
          <Button className="ml-auto" size="sm" disabled={!data.installable || installing} onClick={() => onInstall(data)}><Download className="size-3" />{installing ? "安装中…" : "安装此版本"}</Button>
        </div>
        <div data-skill-market-translation><MarketTranslationControls controller={translation} disabled={Boolean(data.instructions_error) || !data.version} description="使用工作区当前文本模型翻译摘要、完整说明与更新说明；点击后调用模型。" /></div>
        {data.description && <p className="whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">{translated?.description ?? data.description}</p>}
        <Tabs defaultValue="instructions" key={`${data.market_id}:${data.version}`}>
          <TabsList className="h-auto flex-wrap justify-start">
            <TabsTrigger value="instructions">完整说明</TabsTrigger>
            <TabsTrigger value="files">文件列表{data.files_error ? "" : ` (${data.files.length})`}</TabsTrigger>
            {data.changelog && <TabsTrigger value="changelog">更新说明</TabsTrigger>}
          </TabsList>
          <TabsContent value="instructions" className="min-w-0 space-y-4 pt-3">
            {data.instructions_error ? <div className="space-y-3"><ErrorState message={data.instructions_error} /><Button size="sm" variant="outline" onClick={() => void detail.refetch()}>重试说明</Button></div> : <>
              <div className="min-w-0 break-words text-sm leading-7 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
                <LazyStreamdown mode="static" codeHighlight="plain">{(translated?.instructions_markdown ?? data.instructions_markdown) || "此版本的 SKILL.md 内容为空。"}</LazyStreamdown>
              </div>
              <details className="rounded-lg border p-3"><summary className="cursor-pointer text-xs text-muted-foreground">查看 SKILL.md 原文</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all font-mono text-xs leading-5">{data.skill_md}</pre></details>
            </>}
          </TabsContent>
          <TabsContent value="files" className="space-y-3 pt-3">
            {data.files_error ? <div className="space-y-3"><ErrorState message={data.files_error} /><Button size="sm" variant="outline" onClick={() => void detail.refetch()}>重试文件列表</Button></div> : <>
              <p className="text-xs text-muted-foreground">{data.version ? `v${data.version} · ` : ""}共 {data.files.length} 个文件 · 来源版本清单</p>
              {data.files.length ? <ul className="divide-y rounded-xl border">{data.files.map((file) => <li className="min-w-0 p-3" key={file.path}>
                <div className="flex items-start gap-2"><FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><code className="min-w-0 flex-1 break-all text-xs leading-5">{file.path}</code><span className="shrink-0 text-xs text-muted-foreground">{fileSize(file.size_bytes)}</span></div>
                {file.content_type && <p className="mt-1 pl-6 text-[11px] text-muted-foreground">{file.content_type}</p>}
                {file.sha256 && <details className="mt-2 pl-6 text-[11px] text-muted-foreground"><summary className="cursor-pointer">SHA-256 校验值</summary><code className="mt-1 block break-all">{file.sha256}</code></details>}
              </li>)}</ul> : <p className="py-8 text-center text-sm text-muted-foreground">此版本的文件清单为空。</p>}
            </>}
          </TabsContent>
          {data.changelog && <TabsContent value="changelog" className="min-w-0 break-words pt-3 text-sm leading-7"><LazyStreamdown mode="static" codeHighlight="plain">{translated?.changelog ?? data.changelog}</LazyStreamdown></TabsContent>}
        </Tabs>
        <p className="text-xs text-muted-foreground">说明与文件清单来自 ClawHub 的所选版本。安装后仍需授权。</p>
      </div>}
    </DialogContent>
  </Dialog>;
}

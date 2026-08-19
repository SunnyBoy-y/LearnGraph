import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { useNavigate } from "react-router-dom";
import {
  Check,
  Download,
  ExternalLink,
  Eye,
  FileText,
  ImageIcon,
  LoaderCircle,
  Maximize2,
  Network,
  Quote,
  ShieldAlert,
  Sparkles,
  X,
} from "lucide-react";

import { MessageResponse } from "@/components/ai-elements/message";
import type { CodeHighlightMode } from "@/components/ai-elements/lazy-streamdown";
import { IncrementalMarkdown } from "@/components/ai-elements/incremental-markdown";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import {
  Source,
  Sources,
  SourcesContent,
  SourcesTrigger,
} from "@/components/ai-elements/sources";
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { MagicCardHost } from "@/components/chat/magic-card-host";
import {
  isSandboxImageArtifactPart,
  SandboxImageArtifact,
} from "@/components/chat/sandbox-image-artifact";
import { SandboxArtifact } from "@/components/chat/sandbox-artifact";
import { SandboxFileArtifact } from "@/components/chat/sandbox-file-artifact";
import { FilePreviewCanvas } from "@/components/resources/file-preview";
import { downloadFile } from "@/api/files";
import { apiClient } from "@/api/client";
import { confirmSkillDeletion } from "@/api/extensions";
import { approveResearch } from "@/api/research";
import { decideFetchAuthorization, resumeFetchAuthorization } from "@/api/fetch-authorizations";
import { decideEgressApproval, resumeEgressApproval } from "@/api/egress";
import {
  TrustedComponentRenderer,
  type TrustedComponentAction,
} from "@/components/chat/trusted-component-renderer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAuth } from "@/features/auth/auth-context-value";
import { toast } from "sonner";
import {
  documentHref,
  isDocumentCitationHref,
  isWebCitationHref,
  parseDocumentCitationHref,
  parseWebCitationHref,
  rewriteAllCitations,
} from "@/lib/document-citations";
import { decodeUrlForDisplay } from "@/lib/url-display";
import { resolveFilePreviewKind } from "@/lib/file-preview";
import { cn } from "@/lib/utils";
import type { MessagePart } from "@/types/sessions";
import type { FetchAuthorizationData, FetchAuthorizationDecision } from "@/types/fetch-authorization";
import type { EgressAuthorizationCardData, EgressAuthorizationDecision } from "@/types/egress";

type PartData = Record<string, unknown> | undefined;

// recharts 的循环 ESM 导入会被 rolldown 静态合并时求值顺序破坏（module-eval
// TDZ crash，同 streamdown 问题）；ChartPart 独立成按需加载 chunk。
const ChartPart = lazy(() =>
  import("@/components/chat/chart-part").then((module) => ({
    default: module.ChartPart,
  })),
);

type SourceItem = {
  title: string;
  href: string;
  fileId: string;
  filename: string;
  locator: string;
  chunkId: string;
  quote: string;
  isDocument: boolean;
  index?: number;
  /** Optional provider thumbnail (http(s) only). Display reference, not proxied. */
  imageUrl?: string;
};

function graphLabels(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string") return [item];
    if (item && typeof item === "object" && "label" in item) {
      const label = (item as { label?: unknown }).label;
      return typeof label === "string" ? [label] : [];
    }
    return [];
  });
}

function EmptyPart({ children }: { children: string }) {
  return (
    <div className="message-part-empty" role="status">
      {children}
    </div>
  );
}

function SandboxStatusPart({
  data,
  status,
}: {
  data: Record<string, unknown> | undefined;
  status?: string;
}) {
  const authRequired = data?.auth_required === true;
  const paths = Array.isArray(data?.paths)
    ? data.paths.filter((item): item is string => typeof item === "string")
    : [];
  const jobId =
    typeof data?.job_id === "string" && data.job_id ? data.job_id : undefined;
  const [live, setLive] = useState<{ status?: string; reason?: string } | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let disposed = false;
    let timer: number | undefined;
    const terminal = new Set(["succeeded", "failed", "cancelled", "expired"]);
    const tick = async () => {
      try {
        const job = await apiClient.get<{
          status: string;
          reason?: string | null;
        }>(`/sandbox/jobs/${jobId}`);
        if (disposed) return;
        const normalized = job.status?.toLowerCase() ?? "";
        setLive({ status: normalized, reason: job.reason ?? undefined });
        if (!terminal.has(normalized)) {
          timer = window.setTimeout(tick, 3000);
        }
      } catch {
        // Transient network/auth failure: keep polling at a slower interval.
        if (!disposed) timer = window.setTimeout(tick, 5000);
      }
    };
    timer = window.setTimeout(tick, 1200);
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId]);

  const phase: string =
    live?.status ?? (typeof data?.phase === "string" ? data.phase : status ?? "");
  const reason: string =
    live?.reason ?? (typeof data?.reason === "string" ? data.reason : "");
  const queued = phase === "queued";
  const cancelled = phase === "cancelled";
  return (
    <div
      className={
        authRequired
          ? "rounded-xl border border-amber-300 bg-amber-50/70 px-3 py-2 text-xs text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
          : queued
            ? "rounded-xl border border-sky-300 bg-sky-50/70 px-3 py-2 text-xs text-sky-950 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-100"
            : cancelled
              ? "rounded-xl border border-slate-300 bg-slate-50/70 px-3 py-2 text-xs text-slate-700 dark:border-slate-800 dark:bg-slate-950/30 dark:text-slate-300"
              : "rounded-xl border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
      }
    >
      <strong className="text-foreground">
        {authRequired
          ? "需要授权沙箱操作"
          : queued
            ? "沙箱任务排队中"
            : cancelled
              ? "沙箱任务已取消"
              : "沙箱执行"}
      </strong>
      <span className="ml-2">
        {phase}
        {typeof data?.latency_ms === "number" ? ` · ${data.latency_ms} ms` : ""}
        {typeof data?.exit_code === "number" ? ` · exit ${data.exit_code}` : ""}
      </span>
      {queued ? (
        <p className="mt-1 leading-5">
          服务器执行资源繁忙，任务已进入队列，资源可用后将自动开始，无需重新提交。
          {reason ? `（${reason}）` : ""}
        </p>
      ) : null}
      {typeof data?.message_zh === "string" && data.message_zh ? (
        <p className="mt-1 leading-5">{data.message_zh}</p>
      ) : null}
      {authRequired && paths.length ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 font-mono text-[10px]">
          {paths.map((path) => (
            <li key={path}>{path}</li>
          ))}
        </ul>
      ) : null}
      {typeof data?.stdout_summary === "string" && data.stdout_summary ? (
        <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[10px]">
          {data.stdout_summary}
        </pre>
      ) : null}
      {typeof data?.stderr_summary === "string" && data.stderr_summary ? (
        <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-amber-800 dark:text-amber-200">
          {data.stderr_summary}
        </pre>
      ) : null}
    </div>
  );
}

function GraphContextPart({ data }: { data: PartData }) {
  const nodes = graphLabels(data?.nodes);
  return (
    <section className="message-graph-context" aria-label="图谱上下文">
      <div>
        <Network className="size-4" />
        <strong>图谱上下文</strong>
        <Badge className="ml-auto font-normal" variant="secondary">
          {nodes.length} 个节点
        </Badge>
      </div>
      {nodes.length ? (
        <div className="message-graph-context__nodes">
          {nodes.map((node) => (
            <Badge className="font-normal" key={node} variant="outline">
              {node}
            </Badge>
          ))}
        </div>
      ) : (
        <p>本轮没有绑定图谱节点。</p>
      )}
    </section>
  );
}

type QuizOption = { id: string; label: string };

function quizOptions(value: unknown): QuizOption[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item, index) => {
    if (typeof item === "string") return [{ id: String(index), label: item }];
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    if (typeof record.label !== "string") return [];
    return [
      {
        id: typeof record.id === "string" ? record.id : String(index),
        label: record.label,
      },
    ];
  });
}

function QuizPart({
  data,
  onAction,
  partId,
}: {
  data: PartData;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  partId: string;
}) {
  const options = useMemo(() => quizOptions(data?.options), [data?.options]);
  const [answer, setAnswer] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const prompt = typeof data?.prompt === "string" ? data.prompt.trim() : "";

  if (!prompt || !options.length) {
    return <EmptyPart>习题数据不完整，已停止渲染交互项。</EmptyPart>;
  }

  return (
    <section className="message-quiz" aria-label="即时验收题">
      <div className="message-quiz__heading">
        <Sparkles className="size-4" />
        <strong>即时验收题</strong>
      </div>
      <p>{prompt}</p>
      <RadioGroup
        className="message-quiz__options"
        disabled={submitted}
        onValueChange={setAnswer}
        value={answer}
      >
        {options.map((option, index) => (
          <Label
            className={cn("message-quiz__option", answer === option.id && "is-selected")}
            htmlFor={`quiz-${partId}-${option.id}`}
            key={option.id}
          >
            <RadioGroupItem
              id={`quiz-${partId}-${option.id}`}
              value={option.id}
            />
            <span>{String.fromCharCode(65 + index)}. {option.label}</span>
            {submitted && answer === option.id ? <Check className="size-3.5" /> : null}
          </Label>
        ))}
      </RadioGroup>
      <div className="message-quiz__actions">
        <Button
          disabled={!answer || submitted || !onAction}
          onClick={() => {
            const selected = options.find((option) => option.id === answer);
            if (!selected) return;
            setSubmitted(true);
            void onAction?.({
              componentId: partId,
              componentType: "quiz",
              event: "submit",
              payload: { answer_id: answer, answer: selected.label },
            });
          }}
          size="sm"
        >
          提交答案
        </Button>
        {submitted ? <span>答案已提交到当前会话。</span> : null}
      </div>
    </section>
  );
}

function safeHref(value: unknown) {
  if (typeof value !== "string") return "";
  if (value.startsWith("data:image/")) return value;
  if (value.startsWith("/")) return value;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.toString() : "";
  } catch {
    return "";
  }
}

function collectSourceItems(data: PartData, workspaceId: string): SourceItem[] {
  const rawItems = Array.isArray(data?.results)
    ? data.results
    : Array.isArray(data?.sources)
      ? data.sources
      : [];
  return rawItems.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    const fileId = typeof record.file_id === "string" ? record.file_id : "";
    const filename =
      typeof record.filename === "string"
        ? record.filename
        : typeof record.title === "string"
          ? record.title.split(" · ")[0] || record.title
          : "";
    const locator = typeof record.locator === "string" ? record.locator : "";
    const chunkId = typeof record.chunk_id === "string" ? record.chunk_id : "";
    const quote = typeof record.quote === "string" ? record.quote : "";
    const title =
      typeof record.title === "string" && record.title.trim()
        ? record.title
        : filename
          ? `${filename}${locator ? ` · ${locator}` : ""}`
          : fileId
            ? `文档 ${fileId.slice(0, 8)}`
            : "";
    const documentPath = fileId
      ? documentHref(workspaceId, fileId, {
          chunkId: chunkId || undefined,
          locator: locator || undefined,
        })
      : "";
    const href = documentPath || safeHref(record.url ?? record.href);
    if (!title || !href) return [];
    const index =
      typeof record.index === "number" && Number.isFinite(record.index)
        ? record.index
        : undefined;
    const imageUrl =
      typeof record.image_url === "string" && record.image_url.trim()
        ? safeHref(record.image_url)
        : "";
    return [
      {
        title,
        href,
        fileId,
        filename: filename || title,
        locator,
        chunkId,
        quote,
        isDocument: Boolean(fileId),
        index,
        ...(imageUrl ? { imageUrl } : {}),
      },
    ];
  });
}

/** Display-only deduplication for the source list.

Keeps the first occurrence of each (title, href) pair so the rendered list
cannot carry duplicate children with identical React keys. This is display
scoped on purpose: the citation lookup (``buildCitationLookup``) must keep the
full original list so in-text citation numbers keep pointing at the right
sources even when the backend returned the same page twice.
 */
function dedupeSourceItemsForDisplay(items: SourceItem[]): SourceItem[] {
  const seen = new Set<string>();
  const deduped: SourceItem[] = [];
  for (const item of items) {
    const key = `${item.title}\u0000${item.href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
  }
  return deduped;
}

function SourceListPart({ data }: { data: PartData }) {
  const { workspaceId } = useAuth();
  const navigate = useNavigate();
  const items = useMemo(
    () => dedupeSourceItemsForDisplay(collectSourceItems(data, workspaceId)),
    [data, workspaceId],
  );

  if (!items.length) return <EmptyPart>服务端没有返回可访问的来源。</EmptyPart>;
  return (
    <Sources className="message-sources" defaultOpen={false}>
      <SourcesTrigger count={items.length}>
        <>
          <p className="font-medium">引用了 {items.length} 个来源</p>
        </>
      </SourcesTrigger>
      <SourcesContent>
        {items.map((item) =>
          item.isDocument ? (
            <button
              className="message-source-item"
              key={`${item.href}-${item.locator}`}
              onClick={() => navigate(item.href)}
              type="button"
            >
              <FileText className="size-3.5 flex-none" />
              <span className="min-w-0">
                <strong className="block truncate font-medium">{item.filename}</strong>
                {item.locator ? (
                  <small className="block truncate text-[10px] text-muted-foreground">
                    {item.locator}
                  </small>
                ) : null}
                {item.quote ? (
                  <small className="mt-0.5 line-clamp-2 block text-[11px] leading-4 text-muted-foreground">
                    {item.quote}
                  </small>
                ) : null}
              </span>
              <ExternalLink className="ml-auto size-3.5 flex-none opacity-60" />
            </button>
          ) : item.imageUrl ? (
            <a
              className="message-source-item"
              href={item.href}
              key={`${item.title}-${item.href}`}
              rel="noreferrer"
              target="_blank"
            >
              <img
                alt=""
                className="size-8 flex-none rounded object-cover"
                loading="lazy"
                referrerPolicy="no-referrer"
                src={item.imageUrl}
              />
              <span className="min-w-0">
                <strong className="block truncate font-medium">{item.title}</strong>
                <small className="block truncate font-mono text-[10px] text-muted-foreground">
                  {decodeUrlForDisplay(item.href)}
                </small>
              </span>
              <ExternalLink className="ml-auto size-3.5 flex-none opacity-60" />
            </a>
          ) : (
            <Source
              href={item.href}
              key={`${item.title}-${item.href}`}
              title={item.title}
            />
          ),
        )}
      </SourcesContent>
    </Sources>
  );
}

type CitationLookup = {
  byFileId: Map<string, SourceItem[]>;
  byWebIndex: Map<number, SourceItem>;
  webIndexes: Set<number>;
};

function buildCitationLookup(
  parts: MessagePart[] | undefined,
  workspaceId: string,
): CitationLookup {
  const byFileId = new Map<string, SourceItem[]>();
  const byWebIndex = new Map<number, SourceItem>();
  const webIndexes = new Set<number>();
  if (!parts) return { byFileId, byWebIndex, webIndexes };
  let webOrdinal = 0;
  for (const part of parts) {
    if (part.type !== "source_list") continue;
    const items = collectSourceItems(part.data, workspaceId);
    for (const item of items) {
      if (item.fileId) {
        const list = byFileId.get(item.fileId) ?? [];
        list.push(item);
        byFileId.set(item.fileId, list);
        continue;
      }
      if (!item.href) continue;
      webOrdinal += 1;
      const index =
        typeof item.index === "number" && item.index >= 1 ? item.index : webOrdinal;
      if (!byWebIndex.has(index)) {
        byWebIndex.set(index, { ...item, index });
        webIndexes.add(index);
      }
    }
  }
  return { byFileId, byWebIndex, webIndexes };
}

function CitationBadge({
  fileId,
  locators,
  index,
  lookup,
}: {
  fileId: string;
  locators: string;
  index: number;
  lookup: CitationLookup;
}) {
  const { workspaceId } = useAuth();
  const navigate = useNavigate();
  const sources = lookup.byFileId.get(fileId) ?? [];
  const preferred =
    sources.find(
      (item) =>
        item.locator &&
        locators &&
        (locators.includes(item.locator) || item.locator.includes(locators.split(/[、,，]/)[0] ?? "")),
    ) ?? sources[0];
  const filename = preferred?.filename || preferred?.title || "引用文档";
  const quote = preferred?.quote || "";
  const href =
    preferred?.fileId && workspaceId
      ? documentHref(workspaceId, preferred.fileId, {
          chunkId: preferred.chunkId || undefined,
          locator: preferred.locator || locators || undefined,
        })
      : workspaceId
        ? documentHref(workspaceId, fileId, { locator: locators || undefined })
        : "";

  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            aria-label={`打开引用文件 ${filename}`}
            className="message-citation-badge"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (href) navigate(href);
            }}
            type="button"
          >
            {index}
          </button>
        </TooltipTrigger>
        <TooltipContent
          className="message-citation-tooltip max-w-72"
          side="top"
          sideOffset={6}
        >
          <div className="grid gap-1.5">
            <div className="flex items-start gap-2">
              <FileText className="mt-0.5 size-3.5 flex-none opacity-80" />
              <div className="min-w-0">
                <strong className="block truncate text-[12px] font-semibold">
                  {filename}
                </strong>
                {locators ? (
                  <span className="block font-mono text-[10px] opacity-80">
                    {locators}
                  </span>
                ) : null}
              </div>
            </div>
            {quote ? (
              <p className="line-clamp-4 text-[11px] leading-4 opacity-90">
                {quote}
              </p>
            ) : (
              <p className="text-[11px] opacity-80">点击打开该引用文件</p>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function WebCitationBadge({
  index,
  lookup,
  missing = false,
}: {
  index: number;
  lookup: CitationLookup;
  missing?: boolean;
}) {
  const source = missing ? undefined : lookup.byWebIndex.get(index);
  const title = source?.title || source?.filename || `来源 ${index}`;
  const href = source?.href ? safeHref(source.href) : "";
  const quote = source?.quote || "";

  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          {href ? (
            <a
              aria-label={`打开网页来源 ${index}: ${title}`}
              className="message-citation-badge"
              href={href}
              onClick={(event) => event.stopPropagation()}
              rel="noreferrer"
              target="_blank"
            >
              {index}
            </a>
          ) : (
            <button
              aria-label={missing ? `网页来源 ${index} 不可用` : `网页来源 ${index}`}
              className={cn(
                "message-citation-badge",
                missing && "message-citation-badge--missing",
              )}
              type="button"
            >
              {index}
            </button>
          )}
        </TooltipTrigger>
        <TooltipContent
          className="message-citation-tooltip max-w-72"
          side="top"
          sideOffset={6}
        >
          <div className="grid gap-1.5">
            <div className="flex items-start gap-2">
              <ExternalLink className="mt-0.5 size-3.5 flex-none opacity-80" />
              <div className="min-w-0">
                <strong className="block truncate text-[12px] font-semibold">
                  {missing ? `来源 ${index} 不可用` : title}
                </strong>
                {href ? (
                  <span className="block truncate font-mono text-[10px] opacity-80">
                    {decodeUrlForDisplay(href)}
                  </span>
                ) : null}
              </div>
            </div>
            {missing ? (
              <p className="text-[11px] opacity-80">该引用未对应到本次提供的来源列表</p>
            ) : quote ? (
              <p className="line-clamp-4 text-[11px] leading-4 opacity-90">
                {quote}
              </p>
            ) : href ? (
              <p className="text-[11px] opacity-80">点击跳转到该网页</p>
            ) : (
              <p className="text-[11px] opacity-80">来源详情不可用</p>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * Streaming texts beyond this size switch from a full-document re-parse on
 * every chunk to the incremental frozen-prefix renderer, which only re-parses
 * the trailing blocks per frame.
 */
const INCREMENTAL_RENDER_MIN_CHARS = 8_192;

function TextWithCitations({
  content,
  lookup,
  className,
  codeHighlight = "shiki",
  streaming = false,
}: {
  content: string;
  lookup: CitationLookup;
  className?: string;
  codeHighlight?: CodeHighlightMode;
  streaming?: boolean;
}) {
  const { markdown } = useMemo(
    () => rewriteAllCitations(content, lookup.webIndexes),
    [content, lookup.webIndexes],
  );

  const components = useMemo<ComponentProps<typeof MessageResponse>["components"]>(
    () => ({
      a: ({ href, children, ...props }) => {
        if (isDocumentCitationHref(href)) {
          const parsed = parseDocumentCitationHref(href ?? "");
          if (parsed) {
            return (
              <CitationBadge
                fileId={parsed.fileId}
                index={parsed.index}
                locators={parsed.locators}
                lookup={lookup}
              />
            );
          }
        }
        if (isWebCitationHref(href)) {
          const parsed = parseWebCitationHref(href ?? "");
          if (parsed) {
            return (
              <WebCitationBadge
                index={parsed.index}
                lookup={lookup}
                missing={parsed.missing}
              />
            );
          }
        }
        const safe =
          typeof href === "string" &&
          (href.startsWith("http://") ||
            href.startsWith("https://") ||
            href.startsWith("/"))
            ? href
            : undefined;
        // Decode percent-encoded link text (e.g. %E7%9F%A5… → 知识…) when the
        // visible children are just the raw URL or still encoded.
        const childText =
          typeof children === "string"
            ? children
            : Array.isArray(children)
              ? children.map((child) => (typeof child === "string" ? child : "")).join("")
              : "";
        const looksEncoded =
          /%[0-9A-Fa-f]{2}/.test(childText) ||
          (safe && childText === href) ||
          (safe && childText === safe);
        const label =
          looksEncoded && childText
            ? decodeUrlForDisplay(childText)
            : children;
        return (
          <a
            href={safe}
            rel="noreferrer"
            target={safe?.startsWith("http") ? "_blank" : undefined}
            title={safe ? decodeUrlForDisplay(safe) : undefined}
            {...props}
          >
            {label}
          </a>
        );
      },
      img: ({ src, alt, ...props }) => {
        if (
          typeof src === "string" &&
          src.toLowerCase().startsWith("sandbox:")
        ) {
          // Inline-downloaded image markers are replaced by real image parts
          // once the turn finalizes; render a subtle placeholder meanwhile so
          // the stream never shows a broken-image icon.
          return (
            <span
              aria-label={alt ?? "图片加载中"}
              className="inline-flex h-24 w-44 items-center justify-center rounded-lg border border-dashed border-muted bg-muted/30 px-3 text-xs text-muted-foreground"
            >
              正在嵌入图片…
            </span>
          );
        }
        return <img alt={alt} src={src} {...props} />;
      },
    }),
    [lookup],
  );

  // Large streaming texts render through the incremental frozen-prefix
  // renderer: only the trailing blocks re-parse per chunk, the frozen prefix
  // keeps cached element identity (dsh IncrementalMarkdownParser port). Small
  // or settled texts keep the single full-document render for exactness.
  const useIncremental = streaming && markdown.length > INCREMENTAL_RENDER_MIN_CHARS;

  return (
    <div data-message-selectable-text>
      {useIncremental ? (
        <IncrementalMarkdown codeHighlight={codeHighlight} text={markdown} />
      ) : (
        <MessageResponse
          className={cn("min-w-0", className)}
          codeHighlight={codeHighlight}
          components={components}
        >
          {markdown}
        </MessageResponse>
      )}
    </div>
  );
}

function UserConfirmationPart({ data }: { data: PartData }) {
  const confirmationId =
    typeof data?.confirmation_id === "string" ? data.confirmation_id : "";
  const skillName =
    typeof data?.skill_name === "string" ? data.skill_name : "";
  const [confirmationText, setConfirmationText] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<"pending" | "submitting" | "confirmed">(
    "pending",
  );
  const [error, setError] = useState("");

  if (!confirmationId || !skillName) {
    return <EmptyPart>删除确认请求无效或已过期。</EmptyPart>;
  }
  if (status === "confirmed") {
    return (
      <section className="rounded-xl border border-destructive/30 bg-destructive/5 p-4">
        <strong>Skill 已由用户确认删除</strong>
      </section>
    );
  }
  return (
    <section
      aria-label={`永久删除 Skill ${skillName}`}
      className="space-y-4 rounded-xl border border-destructive/40 bg-destructive/5 p-4"
    >
      <div>
        <strong className="text-destructive">需要用户本人二次确认</strong>
        <p className="mt-1 text-sm text-muted-foreground">
          永久删除 Skill“{skillName}”不可恢复。智能体不能代替你完成此操作。
        </p>
      </div>
      <Label>
        输入 Skill 名称
        <Input
          autoComplete="off"
          onChange={(event) => setConfirmationText(event.currentTarget.value)}
          value={confirmationText}
        />
      </Label>
      <Label>
        输入当前账户密码
        <Input
          autoComplete="current-password"
          onChange={(event) => setPassword(event.currentTarget.value)}
          type="password"
          value={password}
        />
      </Label>
      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <Button
        disabled={
          status === "submitting" ||
          confirmationText !== skillName ||
          password.length === 0
        }
        onClick={(event) => {
          if (!event.isTrusted) return;
          setStatus("submitting");
          setError("");
          void confirmSkillDeletion(
            confirmationId,
            confirmationText,
            password,
          )
            .then(() => {
              setPassword("");
              setStatus("confirmed");
            })
            .catch((reason: unknown) => {
              setStatus("pending");
              setError(
                reason instanceof Error ? reason.message : "删除确认失败",
              );
            });
        }}
        variant="destructive"
      >
        {status === "submitting" ? "正在确认…" : "由我本人确认永久删除"}
      </Button>
    </section>
  );
}

/**
 * 二级弹窗图片预览（复用 chat-image-lightbox 样式，与流式图片灯箱一致）。
 */
function ImageLightbox({
  alt,
  filename,
  onOpenChange,
  open,
  src,
}: {
  alt: string;
  filename: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  src: string;
}) {
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        aria-describedby={undefined}
        className="chat-image-lightbox"
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">预览图片 {filename}</DialogTitle>
        <img alt={alt} className="chat-image-lightbox__image" src={src} />
        <div className="chat-image-lightbox__toolbar">
          <DialogClose asChild>
            <button type="button">
              <X className="size-4" />
              关闭
            </button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ImagePart({ data, status }: { data: PartData; status: string }) {
  const directSrc = safeHref(data?.src ?? data?.url);
  const fileId = typeof data?.file_id === "string" ? data.file_id : "";
  const [fileSrc, setFileSrc] = useState("");
  const [lightboxOpen, setLightboxOpen] = useState(false);
  useEffect(() => {
    if (!fileId || directSrc) return;
    let objectUrl = "";
    let cancelled = false;
    void downloadFile(fileId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setFileSrc(objectUrl);
      })
      .catch(() => setFileSrc(""));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [directSrc, fileId]);
  const title = typeof data?.title === "string" ? data.title : "图片任务";
  const alt = typeof data?.alt === "string" ? data.alt : title;
  const src = directSrc || fileSrc;
  return (
    <figure className="message-image-thumb">
      {src && status === "completed" ? (
        <>
          <button
            aria-label={`放大预览 ${title}`}
            className="message-image-thumb__frame"
            onClick={() => setLightboxOpen(true)}
            type="button"
          >
            <img alt={alt} src={src} />
            <span className="message-image-thumb__zoom">
              <Maximize2 className="size-3.5" />
            </span>
          </button>
          <figcaption className="message-image-thumb__meta">
            <strong title={title}>{title}</strong>
          </figcaption>
        </>
      ) : (
        <div className="message-image-thumb__state">
          <ImageIcon className="size-6" />
          <strong>{title}</strong>
          <span>{status}</span>
        </div>
      )}
      {src ? (
        <ImageLightbox
          alt={alt}
          filename={title}
          onOpenChange={setLightboxOpen}
          open={lightboxOpen}
          src={src}
        />
      ) : null}
    </figure>
  );
}

/**
 * 文档类附件的内置预览弹窗：按需下载内容并复用 FilePreviewCanvas
 * （pdf/word/ppt/xlsx/音频/视频/html/文本 均有浏览器内查看器）。
 */
function AttachmentPreviewDialog({
  fileId,
  filename,
  kindLabel,
  mimeType,
  onOpenChange,
  open,
}: {
  fileId: string;
  filename: string;
  kindLabel: string;
  mimeType: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  const [blob, setBlob] = useState<Blob | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!open || !fileId || blob) return;
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    void downloadFile(fileId)
      .then((next) => {
        if (!cancelled) setBlob(next);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [blob, fileId, open]);

  return (
    <Dialog
      onOpenChange={(next) => {
        if (!next) setBlob(null);
        onOpenChange(next);
      }}
      open={open}
    >
      <DialogContent
        className="flex max-h-[min(92svh,58rem)] w-full max-w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl"
        showCloseButton
      >
        <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12">
          <DialogTitle className="truncate">{filename}</DialogTitle>
          <DialogDescription className="truncate">
            {kindLabel} · 内置预览
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-auto bg-muted/15">
          {loading ? (
            <div
              className="grid min-h-[24rem] place-items-center gap-2 text-sm text-muted-foreground"
              role="status"
            >
              <LoaderCircle className="size-4 animate-spin" />
              正在加载预览…
            </div>
          ) : failed ? (
            <div
              className="grid min-h-[24rem] place-items-center p-8 text-sm text-destructive"
              role="alert"
            >
              预览加载失败，请下载后使用本地应用查看。
            </div>
          ) : blob ? (
            <FilePreviewCanvas
              blob={blob}
              className="min-h-[32rem]"
              filename={filename}
              mimeType={mimeType}
            />
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function AttachmentPart({ data, status }: { data: PartData; status: string }) {
  const [downloading, setDownloading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const fileId = typeof data?.file_id === "string" ? data.file_id : "";
  const href = safeHref(data?.url ?? data?.src);
  const filename =
    typeof data?.filename === "string"
      ? data.filename
      : typeof data?.original_name === "string"
        ? data.original_name
        : "学习资料";
  const mediaType =
    typeof data?.mime_type === "string"
      ? data.mime_type
      : typeof data?.media_type === "string"
        ? data.media_type
        : "";
  const previewKind = resolveFilePreviewKind(filename, mediaType);
  const isImage = previewKind === "image";
  const kind = data?.relation === "context_reference"
    ? "回答引用的上下文"
    : isImage
      ? "图片"
      : mediaType.includes("presentation") || /\.pptx?$/i.test(filename)
        ? "演示文稿"
        : mediaType.includes("word") || /\.docx?$/i.test(filename)
          ? "文档"
          : mediaType || "文件";
  // 图片走缩略图 + 灯箱；其余类型支持浏览器内预览，unsupported 只能下载。
  const canPreview =
    !isImage && previewKind !== "unsupported" && Boolean(fileId);

  // 图片附件缩略图：优先直接地址，否则按 file_id 拉取内容。
  const [imageSrc, setImageSrc] = useState("");
  useEffect(() => {
    if (!isImage || !fileId || href) return;
    let objectUrl = "";
    let cancelled = false;
    void downloadFile(fileId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImageSrc(objectUrl);
      })
      .catch(() => setImageSrc(""));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [href, fileId, isImage]);
  const imageHref = href || imageSrc;

  async function download() {
    if (downloading) return;
    setDownloading(true);
    try {
      const blob = fileId ? await downloadFile(fileId) : undefined;
      const url = blob ? URL.createObjectURL(blob) : href;
      if (!url) return;
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      if (blob) URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  if (isImage) {
    return (
      <div className="message-attachment message-attachment--image">
        <button
          aria-label={`预览图片 ${filename}`}
          className="message-attachment__thumb"
          disabled={!imageHref}
          onClick={() => setLightboxOpen(true)}
          type="button"
        >
          {imageHref ? (
            <img alt={filename} src={imageHref} />
          ) : (
            <span className="message-attachment__thumb-placeholder">
              <ImageIcon className="size-5" />
            </span>
          )}
          <span className="message-attachment__thumb-zoom">
            <Maximize2 className="size-3.5" />
          </span>
        </button>
        <div className="message-attachment__footer">
          <span className="message-attachment__meta">
            <strong title={filename}>{filename}</strong>
            <small>{kind}{status !== "completed" ? ` · ${status}` : ""}</small>
          </span>
          <Button
            aria-label={`下载 ${filename}`}
            disabled={downloading || (!fileId && !href)}
            onClick={() => void download()}
            size="icon-sm"
            variant="ghost"
          >
            {downloading ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
          </Button>
        </div>
        {imageHref ? (
          <ImageLightbox
            alt={filename}
            filename={filename}
            onOpenChange={setLightboxOpen}
            open={lightboxOpen}
            src={imageHref}
          />
        ) : null}
      </div>
    );
  }

  return (
    <>
      <div className="message-attachment">
        <span className="message-attachment__icon"><FileText className="size-4" /></span>
        <span className="message-attachment__meta">
          <strong title={filename}>{filename}</strong>
          <small>{kind}{status !== "completed" ? ` · ${status}` : ""}</small>
        </span>
        {canPreview ? (
          <Button
            aria-label={`预览 ${filename}`}
            onClick={() => setPreviewOpen(true)}
            size="icon-sm"
            title="预览"
            variant="ghost"
          >
            <Eye className="size-4" />
          </Button>
        ) : null}
        <Button
          aria-label={`下载 ${filename}`}
          disabled={downloading || (!fileId && !href)}
          onClick={() => void download()}
          size="icon-sm"
          variant="ghost"
        >
          {downloading ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
        </Button>
      </div>
      <AttachmentPreviewDialog
        fileId={fileId}
        filename={filename}
        kindLabel={kind}
        mimeType={mediaType}
        onOpenChange={setPreviewOpen}
        open={previewOpen}
      />
    </>
  );
}


function DocumentSelectionPart({ content, data }: { content: string; data: PartData }) {
  const filename = stringField(data, "filename") || "文档选区";
  const locator =
    stringField(data, "locator_label") ||
    stringField(data, "locator") ||
    "可验证原文定位";
  const quote = stringField(data, "selected_text") || content;
  return (
    <section className="border-l-2 border-primary bg-muted/35 px-3 py-2 text-xs" aria-label="文档选区">
      <div className="flex min-w-0 items-center gap-2">
        <Quote className="size-3.5 flex-none" />
        <strong className="truncate">{filename}</strong>
        <span className="ml-auto truncate font-mono text-[10px] text-muted-foreground">{locator}</span>
      </div>
      <p className="mt-1 line-clamp-4 whitespace-pre-wrap leading-5 text-muted-foreground">{quote}</p>
    </section>
  );
}

function SelectionQuotePart({ content, data }: { content: string; data: PartData }) {
  const sourceRole = stringField(data, "source_role");
  const sourceLabel = sourceRole === "assistant" ? "引用模型回答" : "引用会话内容";
  return (
    <blockquote
      aria-label={sourceLabel}
      className="border-l-2 border-primary bg-muted/35 px-3 py-2 text-xs"
    >
      <div className="flex items-center gap-2 font-medium">
        <Quote className="size-3.5 flex-none" />
        <span>{sourceLabel}</span>
      </div>
      <p className="mt-1 line-clamp-4 whitespace-pre-wrap leading-5 text-muted-foreground">
        {content}
      </p>
    </blockquote>
  );
}

const TOOL_AUTO_CLOSE_DELAY = 1000;

function stringField(data: PartData, field: string) {
  const value = data?.[field];
  return typeof value === "string" ? value.trim() : "";
}

function AgentStepPart({
  content,
  data,
  status,
  streaming,
}: {
  content: string;
  data: PartData;
  status: string;
  streaming: boolean;
}) {
  const title = stringField(data, "title") || stringField(data, "label");
  const summary = stringField(data, "summary");
  // Prefer real model/tool narration; never invent fixed host status copy.
  const detail = content.trim() || summary || title;
  if (!detail) {
    // Empty agent_step is a host bookkeeping shell — hide fixed boilerplate.
    return null;
  }
  const completedLabel = title || detail;
  const label =
    status === "failed"
      ? title || "智能体步骤失败"
      : streaming
        ? title || "正在执行智能体步骤"
        : completedLabel;

  return (
    <Reasoning
      className="chat-reasoning chat-agent-step"
      defaultOpen={false}
      isStreaming={streaming}
    >
      <ReasoningTrigger
        aria-label="展开或收起智能体步骤"
        getThinkingMessage={() => label}
      />
      <ReasoningContent>{detail}</ReasoningContent>
    </Reasoning>
  );
}

function toolDisplayTitle(
  toolName: string | undefined,
  data: PartData,
  selectedNodeLabels: string[],
) {
  const explicit = stringField(data, "title");
  if (explicit) return explicit;
  if (toolName === "resolve_learning_context" && selectedNodeLabels.length) {
    return `已读取学习节点 · ${selectedNodeLabels.join("、")}`;
  }
  if (toolName === "search_web" || (toolName && /search|检索|搜索/i.test(toolName))) {
    const input =
      data?.input && typeof data.input === "object" && !Array.isArray(data.input)
        ? (data.input as Record<string, unknown>)
        : null;
    const query = typeof input?.query === "string" ? input.query.trim() : "";
    return query ? `搜索 ${query}` : "联网搜索";
  }
  if (toolName === "sandbox_exec" || toolName === "sandbox_run") {
    return "沙箱执行";
  }
  if (toolName === "start_deep_research") {
    return "启动深度研究";
  }
  if (toolName === "get_deep_research") {
    return "查询深度研究";
  }
  return toolName ?? "工具调用";
}

function isSearchLikeTool(toolName: string | undefined) {
  if (!toolName) return false;
  return (
    toolName === "search_web" ||
    /search|检索|搜索|web_search/i.test(toolName)
  );
}

function ToolCallPart({
  content,
  part,
  streaming,
}: {
  content: string;
  part: MessagePart;
  streaming: boolean;
}) {
  const isPending = part.status === "pending";
  const isRunning = part.status === "streaming" || (isPending && streaming);
  const isAwaitingResult = isRunning || isPending;
  // ChatGPT-style: keep tool rows collapsed by default; user expands for I/O.
  const [open, setOpen] = useState(false);
  const hasStreamedRef = useRef(isRunning);

  useEffect(() => {
    if (isRunning) {
      hasStreamedRef.current = true;
      // Stay collapsed while running — the activity line already surfaces status.
      return;
    }
    if (!hasStreamedRef.current) return;
    const timer = window.setTimeout(() => setOpen(false), TOOL_AUTO_CLOSE_DELAY);
    return () => window.clearTimeout(timer);
  }, [isRunning]);

  const toolName =
    typeof part.data?.tool_name === "string" ? part.data.tool_name : undefined;
  const selectedNodes = Array.isArray(part.data?.selected_nodes)
    ? part.data.selected_nodes.filter(
        (node): node is Record<string, unknown> =>
          Boolean(node) && typeof node === "object",
      )
    : [];
  const selectedNodeLabels = selectedNodes
    .map((node) =>
      typeof node.label === "string" && node.label.trim()
        ? node.label.trim()
        : typeof node.id === "string"
          ? node.id
          : "",
    )
    .filter(Boolean);
  const title = toolDisplayTitle(toolName, part.data, selectedNodeLabels);
  const searchLike = isSearchLikeTool(toolName);
  const toolInput =
    part.data?.input &&
    typeof part.data.input === "object" &&
    !Array.isArray(part.data.input)
      ? part.data.input
      : toolName === "resolve_learning_context"
        ? {
            node_ids: part.data?.node_ids ?? [],
            selected_nodes: selectedNodes,
            file_ids: part.data?.file_ids ?? [],
            document_selection: part.data?.document_selection ?? null,
            message_selection: part.data?.message_selection ?? null,
          }
        : (part.data ?? {});
  const toolState =
    part.status === "failed"
      ? "output-error"
      : isPending
        ? "input-streaming"
        : isRunning
          ? "input-available"
          : "output-available";

  return (
    <Tool
      className={cn(
        "chat-tool-call",
        searchLike && "chat-tool-call--search",
        isAwaitingResult && "is-running",
        part.status === "failed" && "is-failed",
      )}
      onOpenChange={setOpen}
      open={open}
    >
      <ToolHeader
        className="chat-tool-call__header"
        state={toolState}
        title={title}
        toolName={title}
        type="dynamic-tool"
      />
      <ToolContent>
        {/*
          Only mount I/O bodies while expanded. Collapsed tools used to keep
          full JSON + Shiki trees for every agent step in long sessions.
        */}
        {open ? (
          <>
            <ToolInput input={toolInput} />
            {isAwaitingResult ? (
              <p
                aria-live="polite"
                className="text-xs text-muted-foreground"
                role="status"
              >
                正在等待工具结果…
              </p>
            ) : (
              <ToolOutput
                errorText={
                  part.status === "failed"
                    ? content || "工具调用失败"
                    : undefined
                }
                output={
                  part.status === "failed"
                    ? part.data?.output
                    : (part.data?.output ??
                      (content || "未返回可展示的工具输出"))
                }
              />
            )}
          </>
        ) : null}
      </ToolContent>
    </Tool>
  );
}

type DeepResearchApprovalData = {
  user_approval_required?: boolean;
  research_job_id?: string;
  estimated_cost_cny?: number | string;
  budget_cny?: number | string;
  question?: string;
};

/**
 * Budget-approval card for an agent-initiated Deep Research job.
 * Rendered outside the thinking chain so the user can approve without expanding
 * the fold; approval auto-re-drives the agent.
 */
export function DeepResearchApprovalFromPart({ part }: { part: MessagePart }) {
  const output = part.data?.output as DeepResearchApprovalData | undefined;
  if (
    part.data?.tool_name !== "start_deep_research" ||
    output?.user_approval_required !== true
  ) {
    return null;
  }
  return <DeepResearchApprovalCard data={output} />;
}

function DeepResearchApprovalCard({ data }: { data: DeepResearchApprovalData }) {
  const jobId = typeof data.research_job_id === "string" ? data.research_job_id : "";
  const estimated =
    typeof data.estimated_cost_cny === "number"
      ? data.estimated_cost_cny
      : Number(data.estimated_cost_cny) || 0;
  const budget =
    typeof data.budget_cny === "number"
      ? data.budget_cny
      : Number(data.budget_cny) || 0;
  const displayCost = estimated > 0 ? estimated : budget;
  const [status, setStatus] = useState<"pending" | "submitting" | "approved">(
    "pending",
  );
  const [error, setError] = useState("");

  if (!jobId) {
    return (
      <section className="mt-3 rounded-xl border border-amber-300 p-4">
        <p className="text-sm text-muted-foreground">
          研究任务等待预算确认，但缺少任务标识。
        </p>
      </section>
    );
  }
  if (status === "approved") {
    return (
      <section className="mt-3 rounded-xl border border-emerald-300 bg-emerald-50 p-4 dark:bg-emerald-950/30">
        <strong className="text-emerald-700 dark:text-emerald-400">
          预算已确认并启动，等待智能体续跑取回研究结果。
        </strong>
      </section>
    );
  }
  return (
    <section
      aria-label="研究任务预算确认"
      className="mt-3 space-y-3 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:bg-amber-950/20"
    >
      <div>
        <strong className="text-amber-700 dark:text-amber-400">
          需要确认研究预算
        </strong>
        <p className="mt-1 text-sm text-muted-foreground">
          {displayCost > 0
            ? `远程 Provider 预计费用 ¥${displayCost.toFixed(2)}。批准后才会调用远程研究 Provider。`
            : "批准后才会调用远程研究 Provider。"}
        </p>
        {typeof data.question === "string" && data.question.trim() ? (
          <p className="mt-2 text-sm text-foreground/80">
            研究问题：{data.question.trim()}
          </p>
        ) : null}
      </div>
      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <Button
        disabled={status === "submitting"}
        size="sm"
        onClick={async () => {
          setStatus("submitting");
          setError("");
          try {
            await approveResearch(jobId);
            setStatus("approved");
            toast.success("预算已确认，研究任务已启动");
            // Re-drive the agent to fetch the approved job's results via the
            // existing composer auto-send path in chat-pages.
            window.dispatchEvent(
              new CustomEvent("learngraph:compose", {
                detail: {
                  content: `已批准研究任务 ${jobId}，请使用 get_deep_research 工具查询结果。`,
                  autoSend: true,
                },
              }),
            );
          } catch (fetchError) {
            setStatus("pending");
            const message =
              fetchError instanceof Error ? fetchError.message : "预算确认失败";
            setError(message);
            toast.error(message);
          }
        }}
      >
        {status === "submitting" ? "确认中…" : "确认预算并启动"}
      </Button>
    </section>
  );
}

function WebFetchAuthorizationCard({ data }: { data: FetchAuthorizationData }) {
  const requestId = data.authorization_request_id ?? "";
  const [state, setState] = useState<"pending" | "submitting" | "approved" | "denied">(
    data.authorization_status === "approved" || data.decision === "allow_once" || data.decision === "allow_always"
      ? "approved"
      : data.authorization_status === "denied" || data.decision === "deny"
        ? "denied"
        : "pending",
  );
  const [error, setError] = useState("");
  const decide = async (decision: FetchAuthorizationDecision) => {
    if (!requestId) return;
    setState("submitting");
    setError("");
    try {
      await decideFetchAuthorization(requestId, decision);
      if (decision !== "deny" && data.resume_mode === "server") {
        await resumeFetchAuthorization(requestId);
      } else if (decision !== "deny" && data.requested_url && data.resume_mode !== "server") {
        window.dispatchEvent(new CustomEvent("learngraph:compose", {
          detail: { content: `已批准抓取 ${data.requested_url}，请继续使用 fetch_web_page 获取网页内容。`, autoSend: true },
        }));
      }
      // Refresh persisted history after every decision (including deny) so a
      // remount or a second browser shows the terminal card, not a live one.
      window.dispatchEvent(
        new CustomEvent("learngraph:refresh-messages"),
      );
      setState(decision === "deny" ? "denied" : "approved");
      toast.success(
        decision === "allow_always"
          ? "已加入你的网页抓取白名单"
          : decision === "allow_once"
            ? "已允许本次抓取"
            : "已拒绝网页抓取",
      );
    } catch (cause) {
      setState("pending");
      setError(cause instanceof Error ? cause.message : "授权操作失败");
    }
  };
  if (!requestId) return null;
  if (state === "approved" || state === "denied") {
    return <section className="mt-3 rounded-xl border border-muted p-4 text-sm text-muted-foreground">{state === "approved" ? "网页抓取已获授权。" : "已拒绝本次网页抓取。"}</section>;
  }
  return (
    <section aria-label="网页抓取授权" className="mt-3 space-y-3 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:bg-amber-950/20">
      <div>
        <strong className="text-amber-700 dark:text-amber-400">需要网页抓取授权</strong>
        <p className="mt-1 break-words text-sm text-foreground">{data.message_zh || `我将使用${data.tool_label || "网页抓取工具"}抓取${data.requested_url || "该"}网页，是否批准？`}</p>
        {data.hostname ? <p className="mt-1 text-xs text-muted-foreground">域名：{data.hostname}</p> : null}
      </div>
      {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button disabled={state === "submitting"} onClick={() => void decide("allow_once")} size="sm">{state === "submitting" ? "处理中…" : "本次允许"}</Button>
        <Button disabled={state === "submitting"} onClick={() => void decide("allow_always")} size="sm" variant="outline">以后都允许</Button>
        <Button disabled={state === "submitting"} onClick={() => void decide("deny")} size="sm" variant="ghost">拒绝</Button>
      </div>
      <p className="text-[11px] leading-4 text-muted-foreground">
        此授权只放行网页抓取操作（应用层）。若抓取由沙箱容器执行，其网络出站仍受
        「Egress 审批」（网络层）约束，两者相互独立。
      </p>
    </section>
  );
}

function EgressAuthorizationCard({ data }: { data: EgressAuthorizationCardData }) {
  const requestId = data.authorization_request_id ?? "";
  const [state, setState] = useState<"pending" | "submitting" | "approved" | "denied">(
    data.authorization_status === "approved" || data.decision === "allow_once" || data.decision === "allow_always"
      ? "approved"
      : data.authorization_status === "denied" || data.decision === "deny"
        ? "denied"
        : "pending",
  );
  const [error, setError] = useState("");
  const decide = async (decision: EgressAuthorizationDecision) => {
    if (!requestId) return;
    setState("submitting");
    setError("");
    try {
      await decideEgressApproval(requestId, decision);
      if (decision !== "deny" && data.resume_mode === "server") {
        // Server-side resume: re-inject the approval into the suspended Agent
        // turn and re-run the model (D2.1 T4.1).
        await resumeEgressApproval(requestId);
      } else if (decision !== "deny" && data.hostname) {
        // Agent-mode fallback: prompt the model to retry the exact tool request.
        // Trusted downloads bind allow_once to request_spec_sha256 server-side.
        const isAcquisition = data.tool_name === "download_external_image" || data.tool_name === "download_github_source";
        window.dispatchEvent(new CustomEvent("learngraph:compose", {
          detail: {
            content: isAcquisition
              ? `已批准${data.tool_label || "外部下载工具"}访问主机 ${data.hostname}。请使用与刚才完全相同的参数继续该下载。`
              : `已批准沙箱访问主机 ${data.hostname}。请继续执行刚才需要该主机的命令。`,
            autoSend: true,
          },
        }));
      }
      // Refresh persisted history after every decision (including deny) so a
      // remount or a second browser shows the terminal card, not a live one.
      window.dispatchEvent(new CustomEvent("learngraph:refresh-messages"));
      setState(decision === "deny" ? "denied" : "approved");
      toast.success(
        decision === "allow_always"
          ? "已加入 Egress 允许列表"
          : decision === "allow_once"
            ? "已允许本次出站访问"
            : "已拒绝出站访问",
      );
    } catch (cause) {
      setState("pending");
      setError(cause instanceof Error ? cause.message : "授权操作失败");
    }
  };
  const isAcquisition = data.tool_name === "download_external_image" || data.tool_name === "download_github_source";
  const capabilityLabel = isAcquisition ? (data.tool_label || "外部下载工具") : "沙箱出站访问";
  if (!requestId) return null;
  if (state === "approved" || state === "denied") {
    return <section className="mt-3 rounded-xl border border-muted p-4 text-sm text-muted-foreground">{state === "approved" ? `${capabilityLabel}已获授权。` : `已拒绝${capabilityLabel}。`}</section>;
  }
  return (
    <section aria-label={isAcquisition ? "外部下载授权" : "沙箱出站授权"} className="mt-3 space-y-3 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:bg-amber-950/20">
      <div>
        <strong className="text-amber-700 dark:text-amber-400">需要{capabilityLabel}授权</strong>
        <p className="mt-1 break-words text-sm text-foreground">{data.message_zh || `智能体需要访问主机 ${data.hostname || "该"}主机，是否批准？`}</p>
        {data.hostname ? <p className="mt-1 text-xs text-muted-foreground">主机：{data.hostname}</p> : null}
        {data.destination_path ? <p className="mt-1 break-all text-xs text-muted-foreground">写入：{data.destination_path}</p> : null}
      </div>
      {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button disabled={state === "submitting"} onClick={() => void decide("allow_once")} size="sm">{state === "submitting" ? "处理中…" : "允许一次"}</Button>
        <Button disabled={state === "submitting"} onClick={() => void decide("allow_always")} size="sm" variant="outline">总是允许</Button>
        <Button disabled={state === "submitting"} onClick={() => void decide("deny")} size="sm" variant="ghost">拒绝</Button>
      </div>
      <p className="text-[11px] leading-4 text-muted-foreground">
        {isAcquisition
          ? "下载由宿主侧受控网关执行；沙箱仍保持断网。允许一次会绑定本次资源请求摘要，重定向到新主机时会再次审批。"
          : "此授权只放行沙箱容器访问该主机的网络流量（网络层）。搜索与网页抓取的应用层白名单相互独立。"}
      </p>
    </section>
  );
}

function FetchSetupNoticePart({ data }: { data: PartData }) {
  const { workspaceId } = useAuth();
  const navigate = useNavigate();
  const storageKey = `learngraph:fetch-setup-dismissed:${workspaceId}`;
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(storageKey) === "1";
    } catch {
      return false;
    }
  });
  if (dismissed) return null;
  const settingsPath =
    typeof data?.settings_path === "string" && data.settings_path
      ? data.settings_path
      : `/w/${workspaceId}/settings/providers`;
  return (
    <section
      aria-label="网页抓取未配置提示"
      className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-muted bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
    >
      <span className="min-w-0 flex-1">
        本轮已用联网搜索回答。配置网页抓取工具后，可直接读取你发送的网页全文，回答更精准。
      </span>
      <Button
        onClick={() => navigate(settingsPath)}
        size="sm"
        variant="outline"
        className="h-7 px-2 text-xs"
      >
        去配置
      </Button>
      <Button
        onClick={() => {
          try {
            localStorage.setItem(storageKey, "1");
          } catch {
            /* ignore quota/security errors */
          }
          setDismissed(true);
        }}
        size="sm"
        variant="ghost"
        className="h-7 px-2 text-xs"
      >
        不再提示
      </Button>
    </section>
  );
}

function AcknowledgementPart({
  content,
  streaming,
}: {
  content: string;
  streaming: boolean;
}) {
  const text = content.trim();
  if (!text) return null;
  return (
    <div
      className={cn(
        "chat-acknowledgement",
        streaming && "chat-acknowledgement--streaming",
      )}
      data-message-selectable-text
    >
      <p>{text}</p>
    </div>
  );
}

function SubappEventPart({
  data,
}: {
  data: Record<string, unknown> | null | undefined
}) {
  const eventType = typeof data?.event_type === 'string' ? data.event_type : ''
  const eventId = typeof data?.subapp_event_id === 'string' ? data.subapp_event_id : ''
  return (
    <div className="chat-subapp-event" role="note">
      <span>子应用事件</span>
      <code>{eventType || eventId || '已接收'}</code>
    </div>
  )
}

export function MessagePartRenderer({
  interactive = true,
  onAction,
  part,
  siblingParts,
  streaming = false,
}: {
  /** When false, trusted component actions stay disabled (e.g. mid-stream). */
  interactive?: boolean;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  part: MessagePart;
  /** Sibling parts of the same assistant message (used to resolve citation tooltips). */
  siblingParts?: MessagePart[];
  streaming?: boolean;
}) {
  const content = part.content ?? part.content_delta ?? "";
  const { workspaceId } = useAuth();
  const citationLookup = useMemo(
    () => buildCitationLookup(siblingParts, workspaceId),
    [siblingParts, workspaceId],
  );
  switch (part.type) {
    case "acknowledgement":
      return (
        <AcknowledgementPart content={content} streaming={streaming} />
      );
    case "text":
      return content ? (
        <TextWithCitations
          codeHighlight={streaming ? "plain" : "shiki"}
          content={content}
          lookup={citationLookup}
          streaming={streaming}
        />
      ) : null;
    case "reasoning_summary":
    case "reasoning_content": {
      const isReasoningSummary = part.type === "reasoning_summary";
      // These parts live inside the outer ThinkingChain fold. Render as a plain
      // step (no nested collapsible) so expanding "正在思考" shows the text.
      if (!content && !streaming) return null;
      return (
        <div
          className="chat-reasoning-step"
          data-reasoning-type={part.type}
          role="status"
        >
          <div className="chat-reasoning-step__label">
            {streaming
              ? isReasoningSummary
                ? "正在生成推理摘要…"
                : "思考过程"
              : isReasoningSummary
                ? "推理摘要"
                : "思考过程"}
          </div>
          {content ? (
            <div className="chat-reasoning-step__body whitespace-pre-wrap">
              {content}
            </div>
          ) : streaming ? (
            <div className="chat-reasoning-step__body text-muted-foreground">…</div>
          ) : null}
        </div>
      );
    }
    case "agent_step":
      return (
        <AgentStepPart
          content={content}
          data={part.data}
          status={part.status}
          streaming={streaming}
        />
      );
    case "tool_call":
      return <ToolCallPart content={content} part={part} streaming={streaming} />;
    case "fetch_authorization":
      return <WebFetchAuthorizationCard data={(part.data ?? {}) as FetchAuthorizationData} />;
    case "fetch_setup_notice":
      return <FetchSetupNoticePart data={part.data} />;
    case "egress_authorization":
      return <EgressAuthorizationCard data={(part.data ?? {}) as EgressAuthorizationCardData} />;
    case "graph_context":
      return <GraphContextPart data={part.data} />;
    case "quiz":
      return <QuizPart data={part.data} onAction={onAction} partId={part.id} />;
    case "source_list":
      return <SourceListPart data={part.data} />;
    case "chart":
      return (
        <Suspense fallback={null}>
          <ChartPart data={part.data} />
        </Suspense>
      );
    case "user_confirmation":
      return <UserConfirmationPart data={part.data} />;
    case "component":
      return (
        <TrustedComponentRenderer
          data={part.data ?? {}}
          fallbackId={part.id}
          interactive={interactive && !streaming}
          onAction={onAction}
        />
      );
    case "subapp_event":
      return <SubappEventPart data={part.data} />;
    case "magic_card":
      return <MagicCardHost data={part.data ?? {}} />;
    case "sandbox": {
      // Published image files render as inline embedded previews (no manual
      // 预览 click needed); the strip groups adjacent ones side by side.
      if (isSandboxImageArtifactPart(part)) {
        return <SandboxImageArtifact part={part} />;
      }
      const kind = part.data?.kind;
      if (kind === "file" || typeof part.data?.file_id === "string") {
        return <SandboxFileArtifact data={part.data ?? {}} />;
      }
      // Generative card previews prefer the dedicated host so failures stay local.
      if (
        part.data?.runtime === "react-sandbox-v1" ||
        typeof part.data?.card_instance_id === "string"
      ) {
        return <MagicCardHost data={part.data ?? {}} />;
      }
      return <SandboxArtifact data={part.data ?? {}} />;
    }
    case "subapp_artifact":
      return <SandboxArtifact data={part.data ?? {}} />;
    case "sandbox_artifact":
      if (isSandboxImageArtifactPart(part)) {
        return <SandboxImageArtifact part={part} />;
      }
      return <SandboxFileArtifact data={part.data ?? {}} />;
    case "skill_trigger": {
      const skillName =
        typeof part.data?.skill_name === "string" && part.data.skill_name
          ? part.data.skill_name
          : typeof part.data?.skill_key === "string"
            ? part.data.skill_key
            : "Skill";
      const skillKey =
        typeof part.data?.skill_key === "string" ? part.data.skill_key : "";
      const origin =
        typeof part.data?.origin === "string" ? part.data.origin : "";
      const originLabel =
        origin === "declarative_invoke"
          ? "声明式调用"
          : origin === "catalog_read"
            ? "按需加载指令"
            : "技能指令生效";
      return (
        <div className="chat-skill-trigger flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs">
          <Sparkles className="size-3.5 shrink-0 text-primary" />
          <span className="font-medium">触发了 Skill · {skillName}</span>
          {skillKey && skillKey !== skillName ? (
            <span className="truncate font-mono text-[10px] text-muted-foreground">
              {skillKey}
            </span>
          ) : null}
          <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
            {originLabel}
          </span>
        </div>
      );
    }
    case "graph_progress":
      return (
        <div
          className="chat-graph-progress flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
          role="status"
        >
          <LoaderCircle
            aria-hidden="true"
            className={
              part.status === "completed"
                ? "size-4 text-primary"
                : "size-4 animate-spin text-primary"
            }
          />
          <span>
            {content ||
              (part.status === "failed"
                ? "图谱提案生成失败"
                : "正在生成图谱提案")}
          </span>
        </div>
      );
    case "sandbox_status":
      return <SandboxStatusPart data={part.data} status={part.status} />;
    case "image":
      return <ImagePart data={part.data} status={part.status} />;
    case "attachment":
      return <AttachmentPart data={part.data} status={part.status} />;
    case "document_selection":
      return <DocumentSelectionPart content={content} data={part.data} />;
    case "selection_quote":
      return <SelectionQuotePart content={content} data={part.data} />;
    case "error":
      return (
        <p className="message-part-error" role="alert">
          {content || "消息处理失败"}
        </p>
      );
    default:
      return (
        <div className="message-part-unknown">
          <p>
            <ShieldAlert className="size-4" />未知 Message Part 已安全降级
          </p>
          <pre>{JSON.stringify(part, null, 2)}</pre>
        </div>
      );
  }
}

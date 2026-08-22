import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Check,
  Copy,
  Maximize2,
  Minimize2,
  Moon,
  Sun,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { LazyStreamdown } from "@/components/ai-elements/lazy-streamdown";
import { fetchSharedSession, type SessionSharePublicView } from "@/api/session-sharing";

type Part = Record<string, unknown>;

const TEXT_KINDS = new Set(["text", "acknowledgement"]);
const REASONING_KINDS = new Set([
  "reasoning_summary",
  "reasoning_content",
  "agent_step",
]);

interface SourceItem {
  title?: string;
  url?: string;
  locator?: string;
  quote?: string;
}

function PartRenderer({ part }: { part: Part }) {
  const type = String(part.type ?? "");
  const content = String(part.content ?? "");
  const data = (part.data ?? {}) as Record<string, unknown>;

  if (TEXT_KINDS.has(type)) {
    return (
      <div className="share-part share-part--text">
        <LazyStreamdown codeHighlight="plain">{content}</LazyStreamdown>
      </div>
    );
  }

  if (REASONING_KINDS.has(type)) {
    return (
      <details className="share-reasoning">
        <summary>思考过程</summary>
        <div className="share-reasoning__body">{content}</div>
      </details>
    );
  }

  if (type === "source_list") {
    const items = (Array.isArray(data.items) ? data.items : []) as SourceItem[];
    return (
      <div className="share-sources">
        {items.map((item, index) => (
          <div className="share-source" key={index}>
            {item.url ? (
              <a href={item.url} rel="noopener noreferrer" target="_blank">
                [{index + 1}] {item.title || item.url}
              </a>
            ) : (
              <span>
                [{index + 1}] {item.title || item.locator || "来源"}
              </span>
            )}
            {item.quote ? <blockquote>{item.quote}</blockquote> : null}
          </div>
        ))}
      </div>
    );
  }

  if (type === "magic_card" || type === "component") {
    const snapshot = data.preview_snapshot as
      | { preview_html?: string }
      | undefined;
    if (snapshot?.preview_html) {
      return (
        <div className="share-card">
          <iframe
            sandbox="allow-scripts"
            srcDoc={snapshot.preview_html}
            title="共享组件"
          />
        </div>
      );
    }
  }

  // Degraded placeholder for reference-bearing / interactive parts.
  const name = String(data.name ?? "");
  const reason = String(data.reason ?? "该附件或交互内容未随分享公开");
  return (
    <div className="share-degraded">
      {name ? `${name} · ${reason}` : reason}
    </div>
  );
}

export function SessionShareViewPage() {
  const { token = "" } = useParams();
  const [data, setData] = useState<SessionSharePublicView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [fontScale, setFontScale] = useState(1);
  const [dark, setDark] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSharedSession(token)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "分享不存在、已撤销或已过期",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen();
      setIsFullscreen(false);
    } else {
      void document.documentElement.requestFullscreen?.();
      setIsFullscreen(true);
    }
  }, []);

  const toggleDark = useCallback(() => {
    setDark((value) => {
      const next = !value;
      document.documentElement.classList.toggle("dark", next);
      return next;
    });
  }, []);

  const copyAll = useCallback(async () => {
    if (!data) return;
    const text = data.messages
      .map((message) => {
        const role = message.role === "user" ? "我" : "AI";
        const body =
          message.content ||
          (message.parts
            .filter((part) => TEXT_KINDS.has(String(part.type)))
            .map((part) => String(part.content ?? ""))
            .join("\n"));
        return `【${role}】\n${body}`;
      })
      .join("\n\n---\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard unavailable in this context; ignore.
    }
  }, [data]);

  const fullText = useMemo(() => {
    if (!data) return "";
    return data.messages
      .map(
        (message) =>
          message.content ||
          message.parts
            .filter((part) => TEXT_KINDS.has(String(part.type)))
            .map((part) => String(part.content ?? ""))
            .join("\n"),
      )
      .join("\n\n");
  }, [data]);

  return (
    <div className="share-view">
      <header className="share-view__header">
        <div className="share-view__title">
          <h1>{data?.title || "对话分享"}</h1>
          <span>
            {data
              ? `${data.message_count} 条消息 · 只读分享 · 不含记忆与文件`
              : "LearnGraph"}
          </span>
        </div>
        <div className="share-view__toolbar">
          <button onClick={() => setFontScale((value) => Math.max(0.75, value - 0.1))} type="button">
            <ZoomOut className="size-4" />
          </button>
          <span className="share-view__scale">{Math.round(fontScale * 100)}%</span>
          <button onClick={() => setFontScale((value) => Math.min(1.5, value + 0.1))} type="button">
            <ZoomIn className="size-4" />
          </button>
          <button onClick={toggleDark} type="button">
            {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
          <button onClick={toggleFullscreen} type="button">
            {isFullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
          </button>
          <button onClick={() => void copyAll()} type="button">
            {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
            {copied ? "已复制" : "复制全文"}
          </button>
        </div>
      </header>

      {loading ? (
        <div className="share-view__state">正在加载分享…</div>
      ) : error ? (
        <div className="share-view__state share-view__state--error">
          <p>{error}</p>
          <p className="share-view__hint">
            这个链接可能不存在、已被撤销，或已达到查看次数上限。
          </p>
        </div>
      ) : (
        <div
          className="share-view__thread"
          style={{ fontSize: `${fontScale}rem` }}
        >
          {data?.messages.map((message) => (
            <div
              className={
                message.role === "user"
                  ? "share-message share-message--user"
                  : "share-message share-message--assistant"
              }
              key={message.id}
            >
              <div className="share-message__role">
                {message.role === "user" ? "我" : "AI"}
              </div>
              {message.content ? (
                <div className="share-part share-part--text">
                  <LazyStreamdown codeHighlight="plain">
                    {message.content}
                  </LazyStreamdown>
                </div>
              ) : null}
              {message.parts.map((part, index) => (
                <PartRenderer
                  key={String(part.id ?? `part-${index}`)}
                  part={part}
                />
              ))}
            </div>
          ))}
          {data && !fullText.trim() ? (
            <div className="share-view__state">该分享没有可显示的文本内容。</div>
          ) : null}
        </div>
      )}

      <footer className="share-view__footer">
        由 LearnGraph 生成 · 只读分享
      </footer>
    </div>
  );
}

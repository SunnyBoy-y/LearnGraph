import { Component, createContext, createElement, memo, useContext, useEffect, useId, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { TextWithCitations } from "./message-part-renderer";
import { mathMarkdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";
import { ImageLightbox } from "./image-lightbox";
import { SvgIllustration } from "./svg-illustration";
import { useFileStreamUrl } from "@/hooks/use-file-stream-url";
import { StreamUI } from "./stream-ui";
import type { DocumentNode, UISnapshot } from "@/lib/stream-ui";
import type { MessagePart } from "@/types/sessions";
import { StreamUIStateScope, useStreamUIState } from "@/lib/stream-ui-local-state";
import { documentMarkdown, downloadDocument } from "@/genui/document-export";
import { simplifyDocument } from "@/genui/presentation-simplifier";
import { DocumentTOC } from "@/genui/document-toc";
import { RailList } from "@/genui/rail-list";
import "./rich-document.css";

// Rich documents use the existing Markdown/math/code pipeline. Images are
// exclusively resource nodes; raw HTML and HTML previews are never executed.
const InterruptedContext = createContext(false);
const SiblingPartsContext = createContext<MessagePart[] | undefined>(undefined);
const BlockActionContext = createContext<((blockId: string, payload: Record<string, unknown>) => void) | undefined>(undefined);
class NodeBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">此内容暂时无法显示。</p> : this.props.children; }
}
const Markdown = memo(function Markdown({ text }: { text: string }) {
  const siblingParts = useContext(SiblingPartsContext);
  return <TextWithCitations safeDocument content={text} siblingParts={siblingParts} />;
});

function AccordionItem({ node, children }: {node: DocumentNode; children: ReactNode}) {
  const [open, setOpen] = useStreamUIState(`${node.id}:open`, false);
  return <details open={open} onToggle={event => { if (event.currentTarget.open !== open) setOpen(event.currentTarget.open); }}>
    <summary>{node.label || "展开内容"}</summary>{children}
  </details>;
}

function ResourceImage({ media, fallbackAlt, fallbackSvg }: { media: DocumentNode["media"][number]; fallbackAlt: string; fallbackSvg: string }) {
  const { source, failed: fileFailed } = useFileStreamUrl(media.file_id ?? undefined);
  const src = media.file_id ? source : media.url;
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  // Also protects historical snapshots whose pre-search alt/caption described
  // an intended scene. Every actual photo, including a gallery item, owns its
  // accessible label and caption through the metadata returned with that file.
  const alt = media.title || "检索图片";
  if ((failed || fileFailed) && fallbackSvg) return <SvgIllustration svg={fallbackSvg} alt={fallbackAlt || "内容示意"} />;
  return <figure className="rich-doc__image">
    {failed || fileFailed ? <p className="rich-doc__fallback">{alt || media.title} · 图片暂时无法加载</p> :
      <button type="button" disabled={!src} className="rich-doc__image-button" onClick={() => setOpen(true)} aria-label={`放大图片：${alt || media.title}`}>
        {!loaded && <span className="rich-doc__image-loading" role="status">正在加载图片…</span>}
        {src && <img src={src} alt={alt || media.title} loading="lazy" decoding="async" referrerPolicy="no-referrer"
          onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />}
      </button>}
    {(media.title || media.attribution || media.source_url) && <figcaption>
      <span>来源标题：</span>
      {media.source_url ? <a href={media.source_url} target="_blank" rel="noopener noreferrer">{media.title || "图片来源"}</a> : media.title}
      {media.attribution && <span> · {media.attribution}</span>}
    </figcaption>}
    {open && src && <ImageLightbox src={src} alt={alt || media.title} dialogTitle="图片预览" open={open} onOpenChange={setOpen} />}
  </figure>;
}

function Images({ node }: { node: DocumentNode }) {
  const interrupted = useContext(InterruptedContext);
  const pending = node.resource_state === "pending" && !interrupted;
  const reason = {
    no_results: "未找到相关图片", search_failed: "图片检索暂时失败", download_failed: "候选图片暂时无法获取",
    timeout: "图片检索或获取超时", disabled: "本轮未开启联网检索", provider_unavailable: "尚未配置图片搜索",
    capacity: "图片任务繁忙", storage_failed: "图片暂时无法保存", ready: "", "": "暂无可用图片",
  }[node.resource_reason];
  if (!node.media.length && !pending && node.fallback_svg) return <div>
    <SvgIllustration svg={node.fallback_svg} alt={node.alt || node.label || "内容示意"} />
  </div>;
  if (!node.media.length) return <figure className="rich-doc__resource" aria-busy={pending}>
    <p role="status">{pending ? "正在查找相关图片…" : node.alt || node.label || "暂无可用图片"}</p>
    {!pending && <p className="rich-doc__fallback">{reason}</p>}
    {!pending && node.caption && <figcaption>{node.caption}</figcaption>}
  </figure>;
  const media = node.type === "image" ? node.media.slice(0, 1) : node.media;
  return <div>
    <div className={node.type === "gallery" ? "rich-doc__gallery" : "rich-doc__image-group"}>
      {media.map(image => <ResourceImage key={`${image.file_id ?? ""}:${image.url}`} media={image} fallbackAlt={node.alt} fallbackSvg={node.fallback_svg} />)}
    </div>
  </div>;
}

/** Predefined calculations only: no eval, parser-supplied code or network calls. */
function Simulation({ node }: { node: DocumentNode }) {
  const [parameter, setParameter] = useStreamUIState(`${node.id}:parameter`, node.engine === "chase" ? 2 : 1);
  const [step, setStep] = useStreamUIState(`${node.id}:step`, 0);
  const [playing, setPlaying] = useState(false);
  const sorting = node.engine === "sort_steps";
  const frames = useMemo(() => {
    const values = node.values.length ? [...node.values] : [5, 2, 8, 1, 4];
    const states = [[...values]];
    // Bounded insertion-sort trace, intentionally named in the UI.
    for (let i = 1; i < values.length; i++) {
      for (let j = i; j > 0 && values[j - 1] > values[j]; j--) {
        [values[j - 1], values[j]] = [values[j], values[j - 1]];
        states.push([...values]);
      }
    }
    return states;
  }, [node.values]);
  const maxStep = sorting ? frames.length - 1 : 100;
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setStep(value => value >= maxStep ? 0 : value + 1), 180);
    return () => window.clearInterval(timer);
  }, [playing, maxStep, setStep]);
  const currentStep = Math.min(step, maxStep);
  const time = currentStep / 10;
  const path = Array.from({ length: 81 }, (_, i) => {
    const x = (i - 40) / 10;
    return `${i ? "L" : "M"}${40 + i * 7},${Math.max(12, Math.min(228, 190 - parameter * x * x * 8))}`;
  }).join(" ");
  return <figure className="rich-doc__simulation">
    {node.label && <figcaption>{node.label}</figcaption>}
    {sorting ? <>
      <p className="rich-doc__caption">插入排序 · 第 {currentStep} / {maxStep} 步{!node.values.length && " · 示例数组"}</p>
      <ol className="rich-doc__array" aria-label="当前数组">{frames[currentStep].map((value, index) => <li key={index}>{value}</li>)}</ol>
    </> : node.engine === "quadratic" ? <>
      <p aria-live="polite">y = {parameter.toFixed(1)}x²</p>
      <svg viewBox="0 0 640 240" role="img" aria-label="二次函数图像"><path d="M40 190H610 M320 12V228" stroke="currentColor" opacity=".25" /><path d={path} fill="none" stroke="var(--chart-1)" strokeWidth="3" /></svg>
    </> : <>
      <p className="rich-doc__caption">示意模型：领先距离 10，慢者速度 1，追赶者速度可调。</p>
      <svg viewBox="0 0 640 160" role="img" aria-label="追赶演示">
        <path d="M32 54H600 M32 112H600" stroke="currentColor" opacity=".2" />
        <circle cx={32 + Math.min(560, time * parameter * 12)} cy="54" r="10" fill="var(--chart-1)" />
        <circle cx={32 + Math.min(560, (10 + time) * 12)} cy="112" r="10" fill="var(--chart-2)" />
        <text x="32" y="28" fill="currentColor">追赶者</text><text x="32" y="91" fill="currentColor">领先者</text>
      </svg>
      <p aria-live="polite">时间 {time.toFixed(1)} · {parameter > 1 ? `相遇时间 ${(10 / (parameter - 1)).toFixed(2)}` : "当前速度无法追上"}</p>
    </>}
    <div className="rich-doc__controls">
      {!sorting && <label>{node.engine === "quadratic" ? "系数 a" : "追赶速度"}
        <input type="range" min={node.engine === "quadratic" ? -3 : 1} max={node.engine === "quadratic" ? 3 : 5} step="0.1" value={parameter}
          onChange={event => setParameter(Number(event.target.value))} />
      </label>}
      {node.engine !== "quadratic" && <>
        <Button type="button" variant="outline" size="sm" onClick={() => setPlaying(value => !value)}>{playing ? "暂停" : "播放"}</Button>
        <Button type="button" variant="ghost" size="sm" disabled={currentStep >= maxStep} onClick={() => setStep(value => Math.min(maxStep, value + 1))}>下一步</Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => { setPlaying(false); setStep(0); }}>重置</Button>
        <label>进度<input type="range" min="0" max={maxStep} value={currentStep} onChange={event => setStep(Number(event.target.value))} /></label>
      </>}
    </div>
    {node.caption && <p className="rich-doc__caption">{node.caption}</p>}
  </figure>;
}

function Tabs({ node, nodes, render }: { node: DocumentNode; nodes: DocumentNode[]; render: (node: DocumentNode) => React.ReactNode }) {
  const domId = useId();
  const [selected, setSelected] = useStreamUIState(`${node.id}:tab`, "");
  const active = nodes.some(child => child.id === selected) ? selected : nodes[0]?.id;
  return <div><div className="rich-doc__tablist" role="tablist" aria-label={node.label || "内容分组"}>
    {nodes.map(child => <button type="button" role="tab" id={`${domId}-${child.id}-tab`} aria-selected={active === child.id}
      aria-controls={`${domId}-${child.id}-panel`} tabIndex={active === child.id ? 0 : -1} key={child.id}
      onClick={() => setSelected(child.id)} onKeyDown={event => {
        const index = nodes.findIndex(item => item.id === active);
        const next = event.key === "ArrowRight" ? (index + 1) % nodes.length : event.key === "ArrowLeft" ? (index + nodes.length - 1) % nodes.length : event.key === "Home" ? 0 : event.key === "End" ? nodes.length - 1 : -1;
        if (next >= 0) { event.preventDefault(); setSelected(nodes[next].id); event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus(); }
      }}>{child.label || "内容"}</button>)}
  </div>{nodes.map(child => <div key={child.id} role="tabpanel" id={`${domId}-${child.id}-panel`} aria-labelledby={`${domId}-${child.id}-tab`} hidden={active !== child.id}>{render(child)}</div>)}</div>;
}

const NodeView = memo(function NodeView({ node, childrenByParent, scope }: {
  node: DocumentNode; childrenByParent: Map<string | null, DocumentNode[]>; scope: string;
}) {
  const onBlockExplain = useContext(BlockActionContext);
  const interrupted = useContext(InterruptedContext);
  const children = childrenByParent.get(node.id) ?? [];
  const render = (child: DocumentNode) => <div key={child.id} className="rich-doc__node" data-width={child.width}>
    <NodeBoundary><NodeView node={child} childrenByParent={childrenByParent} scope={scope} /></NodeBoundary>
  </div>;
  const body = children.map(render);
  if (node.state === "error") return <p role="alert">{node.label || node.alt || "此内容生成失败，请重试。"}</p>;
  if (node.state === "pending" && !node.text && !body.length && !["feature_list", "step_list", "status_list"].includes(node.type)) return <p className={`rich-doc__resource rich-doc__resource--${node.type}`} role="status">{node.label || "正在准备内容…"}</p>;
  switch (node.type) {
    case "feature_list": case "step_list": case "status_list":
      return <RailList node={node} items={children} interrupted={interrupted} renderText={text => <Markdown text={text} />} />;
    case "heading": return createElement(`h${node.level}`, {id: `${scope}-${node.id}`, tabIndex: -1}, node.text || node.label);
    case "paragraph": case "list": return <Markdown text={node.text} />;
    case "quote": return <blockquote><Markdown text={node.text} /></blockquote>;
    case "code": {
      const fence = "`".repeat(Math.max(3, ...Array.from(node.text.matchAll(/`+/g), match => match[0].length + 1)));
      return <Markdown text={`${fence}${node.language}\n${node.text}\n${fence}`} />;
    }
    case "math": return <Markdown text={mathMarkdown(node.text, node.inline)} />;
    case "divider": return <hr />;
    case "image": case "image_group": case "gallery": return <Images node={node} />;
    case "simulation": case "interactive": return <Simulation node={node} />;
    case "chart": case "table": case "graph": case "diagram": case "timeline": case "metric":
      return node.visual ? <StreamUI embedded partId={`${scope}:${node.id}`} onAction={onBlockExplain ? action => onBlockExplain(node.id, action.payload) : undefined} data={{...node.visual, protocol: "suip/0.1", component: node.type === "diagram" ? "graph" : node.type, block_id: node.id, revision: 0, state: "completed"}} /> : <p role="status">{node.label || "等待数据填充…"}</p>;
    case "tabs": return <Tabs node={node} nodes={children} render={render} />;
    case "accordion": return <div className="rich-doc__stack">{children.map(child => <AccordionItem key={child.id} node={child}>{render(child)}</AccordionItem>)}</div>;
    case "figure": {
      const hasImage = (child: DocumentNode): boolean => ["image", "image_group", "gallery"].includes(child.type)
        || (childrenByParent.get(child.id) ?? []).some(hasImage);
      return <figure>{body}{node.caption && !children.some(hasImage) && <figcaption>{node.caption}</figcaption>}</figure>;
    }
    default: return <section className={`rich-doc__layout rich-doc__${node.type}`} data-align={node.align} data-width={node.width}
      style={{ "--rich-columns": Math.min(node.columns, Math.max(1, children.filter(child => child.width !== "full").length)) } as CSSProperties}>
      {node.label && <h3 className="rich-doc__section-title">{node.label}</h3>}{node.text && <Markdown text={node.text} />}{body}
    </section>;
  }
});

export default function RichDocument({ snapshot, siblingParts, failed = false, settled = false, onBlockExplain }: { snapshot: UISnapshot; siblingParts?: MessagePart[]; failed?: boolean; settled?: boolean; onBlockExplain?: (blockId: string, payload: Record<string, unknown>) => void }) {
  const scope = useContext(StreamUIStateScope);
  const [copyStatus, setCopyStatus] = useState("");
  const blocks = useMemo(() => simplifyDocument(snapshot), [snapshot]);
  const headings = blocks.filter(node => node.type === "heading" && node.state !== "pending");
  const childrenByParent = useMemo(() => {
    const result = new Map<string | null, DocumentNode[]>();
    for (const node of blocks) {
      const key = node.parent_id ?? null;
      const siblings = result.get(key) ?? [];
      siblings.push(node); result.set(key, siblings);
    }
    return result;
  }, [blocks]);
  const roots = childrenByParent.get(null) ?? [];
  return <BlockActionContext.Provider value={onBlockExplain}><SiblingPartsContext.Provider value={siblingParts}><InterruptedContext.Provider value={failed || settled}><div className="rich-doc rich-doc__flow">
    {(snapshot.props.toc || snapshot.props.density === "editorial") && <DocumentTOC headings={headings} scope={scope} />}
    {snapshot.props.description && <p>{snapshot.props.description}</p>}
    {roots.length ? roots.map(node => <div key={node.id} className="rich-doc__node" data-width={node.width}>
      <NodeBoundary><NodeView node={node} scope={scope} childrenByParent={childrenByParent} /></NodeBoundary>
    </div>) :
    <p className="rich-doc__resource" role="status">{snapshot.state === "streaming" && !failed && !settled ? "正在组织内容…" : "暂无可显示的内容"}</p>}
    {snapshot.props.source_note && <p className="rich-doc__caption">{snapshot.props.source_note}</p>}
    {snapshot.props.data_kind === "example" && <p className="rich-doc__caption">示例数据，仅用于说明</p>}
    {roots.length > 0 && snapshot.state === "completed" && <footer className="rich-doc__tools" aria-label="文档操作">
      <Button size="sm" variant="ghost" onClick={async () => { try { await navigator.clipboard.writeText(documentMarkdown(snapshot)); setCopyStatus("已复制"); } catch { setCopyStatus("复制失败，请使用导出"); } }}>复制文档</Button>
      <Button size="sm" variant="ghost" onClick={() => downloadDocument(snapshot)}>导出 Markdown</Button>
      <span role="status">{copyStatus}</span>
    </footer>}
  </div></InterruptedContext.Provider></SiblingPartsContext.Provider></BlockActionContext.Provider>;
}

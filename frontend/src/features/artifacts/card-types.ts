import { extractSvgMarkup } from "@/features/chat/response-composer";
import type { ArtifactCard, ArtifactCardKind } from "@/types/artifacts";

/**
 * 「产物库」页的卡片类型口径。
 *
 * 声明式组件（会话里的问答 / 选择题控件）仍然照常索引，版本与分享记录也全部保留，
 * 但它按产品口径不算「页面」：产物页的列表查询一律把它排除掉，因此产物库和
 * 「已分享」都不会出现组件卡，类型筛选里也不再提供「声明式组件」选项。
 *
 * 排除在服务端完成（`exclude_card_type` 参数），这样组件卡不会挤占列表上限、
 * 把真正的产物卡挤出结果集。
 */
export const DECLARATIVE_COMPONENT_CARD_TYPE = "component";

export type ArtifactGalleryKind = ArtifactCardKind;

const BIDIRECTIONAL_RUNTIMES = new Set(["react-sandbox-v1", "opaque-origin-subapp-v1"]);
const INTERACTIVE_CONTROL_RE =
  /<(?:button|input|select|textarea|form)\b|\bon(?:click|change|input|submit|pointerdown|keydown)\s*=|addEventListener/i;
const SCRIPT_TAG_RE = /<script\b/i;
const ANIMATION_RE =
  /<(?:video|animate|animateTransform|animateMotion)\b|requestAnimationFrame|@keyframes|\banimation\s*:/i;
const CANVAS_TAG_RE = /<canvas\b/i;

/**
 * Classify an artifact card into one of the four gallery categories:
 * - `svg`: static vector graphic / diagram
 * - `interactive`: interactive Magic Card / sub-app
 * - `html`: static HTML page / report
 * - `animation`: CSS / SMIL / canvas / video animation
 */
export function resolveArtifactGalleryKind(
  card: Pick<ArtifactCard, "card_type" | "interactive" | "artifact_kind">,
  snapshot?: Record<string, unknown> | null,
): ArtifactGalleryKind {
  const data = snapshot && typeof snapshot === "object" ? snapshot : undefined;

  if (data) {
    if (card.interactive) return "interactive";
    const runtime = typeof data.runtime === "string" ? data.runtime : "";
    if (BIDIRECTIONAL_RUNTIMES.has(runtime)) return "interactive";
    if (
      data.subapp_mode === true ||
      Boolean(data.bundle_id) ||
      (data.origin_verified === true &&
        typeof data.artifact_url === "string" &&
        data.artifact_url.length > 0)
    ) {
      return "interactive";
    }
    const previewHtml = typeof data.preview_html === "string" ? data.preview_html.trim() : "";
    if (previewHtml) {
      const hasSvg = /<svg\b/i.test(previewHtml);
      const hasControls = INTERACTIVE_CONTROL_RE.test(previewHtml);
      const hasScript = SCRIPT_TAG_RE.test(previewHtml);
      const hasAnim = ANIMATION_RE.test(previewHtml);
      const hasCanvas = CANVAS_TAG_RE.test(previewHtml);

      if (hasSvg && !hasControls && !hasScript && !hasCanvas) {
        return hasAnim ? "animation" : "svg";
      }
      if ((hasAnim || hasCanvas) && !hasControls) {
        return "animation";
      }
      if (hasControls || hasScript) {
        return "interactive";
      }
      return "html";
    }
  }

  if (
    card.artifact_kind === "svg" ||
    card.artifact_kind === "interactive" ||
    card.artifact_kind === "html" ||
    card.artifact_kind === "animation"
  ) {
    return card.artifact_kind;
  }
  if (card.card_type === DECLARATIVE_COMPONENT_CARD_TYPE || card.interactive) {
    return "interactive";
  }
  return "html";
}

export interface ArtifactKindMeta {
  kind: ArtifactGalleryKind;
  badgeLabel: string;
  filterLabel: string;
  actionLabel: string;
  modeLabel: string;
}

const ARTIFACT_KIND_META: Record<ArtifactGalleryKind, ArtifactKindMeta> = {
  interactive: {
    kind: "interactive",
    badgeLabel: "交互应用",
    filterLabel: "交互应用",
    actionLabel: "运行应用",
    modeLabel: "交互应用 · 独立运行",
  },
  svg: {
    kind: "svg",
    badgeLabel: "SVG",
    filterLabel: "SVG 图形",
    actionLabel: "查看预览",
    modeLabel: "静态图形 · 放大查看",
  },
  html: {
    kind: "html",
    badgeLabel: "HTML",
    filterLabel: "HTML 网页",
    actionLabel: "打开页面",
    modeLabel: "HTML 页面 · 完整阅读",
  },
  animation: {
    kind: "animation",
    badgeLabel: "动画",
    filterLabel: "动画",
    actionLabel: "播放动画",
    modeLabel: "动态演示 · 悬停或点击播放",
  },
};

export function artifactKindMeta(kind: ArtifactGalleryKind): ArtifactKindMeta {
  return ARTIFACT_KIND_META[kind];
}

/**
 * True when `previewHtml` consists solely of an `<svg>` (plus boilerplate
 * html/head/body/meta/title wrapper), with no extra visible HTML or external
 * `<style>` rules outside `<svg>`. Pure SVGs can be rendered directly as an
 * `<img>` data URI in the gallery without creating an iframe.
 */
export function extractPureSvgDataUrl(previewHtml: string): {
  svgMarkup: string | undefined;
  pureSvgDataUrl: string | undefined;
} {
  const svgMarkup = extractSvgMarkup(previewHtml);
  if (!svgMarkup) return { svgMarkup: undefined, pureSvgDataUrl: undefined };

  const withoutSvg = previewHtml.replace(svgMarkup, "");
  const stripped = withoutSvg
    .replace(/<!doctype[^>]*>/gi, "")
    .replace(/<\/?(?:html|head|body|main|div|section|figure)\b[^>]*>/gi, "")
    .replace(/<(?:meta|title)\b[^>]*\/?>/gi, "")
    .replace(/<title\b[^>]*>[\s\S]*?<\/title>/gi, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .trim();

  if (stripped.length > 0) {
    return { svgMarkup, pureSvgDataUrl: undefined };
  }

  let normalizedSvg = svgMarkup;
  if (!/\bxmlns\s*=/i.test(normalizedSvg)) {
    normalizedSvg = normalizedSvg.replace(/^<svg\b/i, '<svg xmlns="http://www.w3.org/2000/svg"');
  }
  return {
    svgMarkup: normalizedSvg,
    pureSvgDataUrl: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(normalizedSvg)}`,
  };
}

/**
 * True when an HTML document needs scripts enabled just to paint its initial
 * cover frame (e.g. `<canvas>` or empty mount root `<div id="app"></div>`).
 * Static/SSR-style HTML cards render cleanly with `scripts: false` (`sandbox=""`).
 */
export function needsScriptForInitialCover(previewHtml: string): boolean {
  if (!previewHtml.trim()) return false;
  if (/<canvas\b/i.test(previewHtml)) return true;
  if (!SCRIPT_TAG_RE.test(previewHtml)) return false;

  const visibleText = previewHtml
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const hasSvg = /<svg\b/i.test(previewHtml);
  return !hasSvg && visibleText.length < 12;
}

export function formatArtifactSource(
  card: Pick<ArtifactCard, "chat_session_title" | "project_name">,
): string {
  const sessionTitle = card.chat_session_title?.trim();
  const projectName = card.project_name?.trim();
  if (sessionTitle && sessionTitle !== "新会话") {
    return sessionTitle;
  }
  if (projectName) {
    return projectName;
  }
  return sessionTitle || "会话产物";
}

export function formatRelativeUpdateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfTarget = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const dayDiff = Math.round((startOfToday - startOfTarget) / 86_400_000);

  if (dayDiff === 0) return "今天更新";
  if (dayDiff === 1) return "昨天更新";
  if (dayDiff > 1 && dayDiff <= 7) return `${dayDiff} 天前更新`;
  return `${date.toLocaleDateString()} 更新`;
}

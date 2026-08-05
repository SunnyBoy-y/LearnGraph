// Provider brand marks bundled into the app.
//
// The provider catalog used to reference remote logos (cdn.simpleicons.org,
// models.dev, assorted favicons). Under restrictive networks, proxies, or CDN
// outages those requests failed and every provider row rendered a blank icon
// tile. The marks are now shipped as build-time assets so icon rendering
// never depends on the network.

import alibabaMark from "@/assets/brands/alibaba.svg";
import anysearchMark from "@/assets/brands/anysearch.ico";
import crawl4aiMark from "@/assets/brands/crawl4ai.ico";
import exaMark from "@/assets/brands/exa.ico";
import firecrawlMark from "@/assets/brands/firecrawl.ico";
import jinaMark from "@/assets/brands/jina.ico";
import longcatMark from "@/assets/brands/longcat.svg";
import mem0Mark from "@/assets/brands/mem0.ico";
import anthropicMark from "@/assets/brands/si-anthropic.svg";
import baiduMark from "@/assets/brands/si-baidu.svg";
import braveMark from "@/assets/brands/si-brave.svg";
import bytedanceMark from "@/assets/brands/si-bytedance.svg";
import deepseekMark from "@/assets/brands/si-deepseek.svg";
import githubMark from "@/assets/brands/si-github.svg";
import googleGeminiMark from "@/assets/brands/si-googlegemini.svg";
import minimaxMark from "@/assets/brands/si-minimax.svg";
import modelscopeMark from "@/assets/brands/si-modelscope.svg";
import moonshotMark from "@/assets/brands/si-moonshot.svg";
import ollamaMark from "@/assets/brands/si-ollama.svg";
import openrouterMark from "@/assets/brands/si-openrouter.svg";
import perplexityMark from "@/assets/brands/si-perplexity.svg";
import qwenMark from "@/assets/brands/si-qwen.svg";
import searxngMark from "@/assets/brands/si-searxng.svg";
import tavilyMark from "@/assets/brands/tavily.ico";
import xiaomiMark from "@/assets/brands/si-xiaomi.svg";

/**
 * Bundled marks keyed by brand id. Keys cover both the backend catalog
 * `brand_id` values (backend/app/providers/catalog.py) and the quick-preset
 * `brandId` values in provider-pages.tsx, so a single lookup serves both.
 */
const BRAND_ICONS: Record<string, string> = {
  alibaba: alibabaMark,
  anthropic: anthropicMark,
  anysearch: anysearchMark,
  baidu: baiduMark,
  brave: braveMark,
  bytedance: bytedanceMark,
  crawl4ai: crawl4aiMark,
  deepseek: deepseekMark,
  exa: exaMark,
  firecrawl: firecrawlMark,
  gemini: googleGeminiMark,
  github: githubMark,
  googlegemini: googleGeminiMark,
  jina: jinaMark,
  kimi: moonshotMark,
  kimi_coding: moonshotMark,
  longcat: longcatMark,
  mem0: mem0Mark,
  mimo: xiaomiMark,
  minimax: minimaxMark,
  modelscope: modelscopeMark,
  moonshot: moonshotMark,
  ollama: ollamaMark,
  openrouter: openrouterMark,
  perplexity: perplexityMark,
  qianfan: baiduMark,
  qwen: qwenMark,
  searxng: searxngMark,
  tavily: tavilyMark,
  volc_agentplan: bytedanceMark,
  xiaomi: xiaomiMark,
};

/** Bundled mark for a brand id, or undefined when no local asset exists. */
export function brandIcon(brandId: string | null | undefined): string | undefined {
  if (!brandId) return undefined;
  return BRAND_ICONS[brandId.toLowerCase()];
}

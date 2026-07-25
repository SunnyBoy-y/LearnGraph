import type { WorkspaceSetting } from "@/types/settings";

export const CHAT_RESPONSE_STYLE_SETTING_KEY = "chat.response_style";

export type BaseStyle =
  | "default"
  | "professional"
  | "friendly"
  | "candid"
  | "efficient"
  | "exploratory"
  | "quirky"
  | "cynical";

export type StyleLevel = -2 | -1 | 0 | 1 | 2;

export interface ResponseStyleConfig {
  base_style: BaseStyle;
  warmth: StyleLevel;
  enthusiasm: StyleLevel;
  headings_and_lists: StyleLevel;
  emoji: StyleLevel;
  verbosity: StyleLevel;
}

export const DEFAULT_RESPONSE_STYLE: ResponseStyleConfig = {
  base_style: "default",
  warmth: 0,
  enthusiasm: 0,
  headings_and_lists: 0,
  emoji: 0,
  verbosity: 0,
};

export const BASE_STYLE_OPTIONS: Array<{
  value: BaseStyle;
  label: string;
  description: string;
}> = [
  {
    value: "default",
    label: "默认",
    description: "清晰中立，随上下文自然调整",
  },
  {
    value: "professional",
    label: "专业可靠",
    description: "严谨、精确、有条理",
  },
  {
    value: "friendly",
    label: "友好陪伴",
    description: "温和自然，善于倾听",
  },
  {
    value: "candid",
    label: "坦率直接",
    description: "务实建设性，不粉饰",
  },
  {
    value: "efficient",
    label: "高效简洁",
    description: "直接给答案，少寒暄",
  },
  {
    value: "exploratory",
    label: "探索学习",
    description: "易懂有启发，促进理解",
  },
  {
    value: "quirky",
    label: "活泼创意",
    description: "轻松幽默，适度想象",
  },
  {
    value: "cynical",
    label: "冷幽默",
    description: "克制讽刺，仍提供实用帮助",
  },
];

export const LEVEL_OPTIONS: Array<{
  value: StyleLevel;
  label: string;
}> = [
  { value: -2, label: "很少 / 很低" },
  { value: -1, label: "较少 / 较低" },
  { value: 0, label: "默认" },
  { value: 1, label: "较多 / 较高" },
  { value: 2, label: "很多 / 很高" },
];

export const CHARACTERISTIC_FIELDS: Array<{
  key: keyof Omit<ResponseStyleConfig, "base_style">;
  label: string;
  description: string;
}> = [
  {
    key: "warmth",
    label: "温和体贴",
    description: "调节安慰、共情与支持性语气，不改变回答完整度",
  },
  {
    key: "enthusiasm",
    label: "热情洋溢",
    description: "调节活跃度与兴奋表达，不改变信息密度",
  },
  {
    key: "headings_and_lists",
    label: "标题和列表",
    description: "控制 Markdown 标题、列表与表格的使用频率",
  },
  {
    key: "emoji",
    label: "表情符号",
    description: "控制表情符号出现频率；正式与高风险内容会自动降低",
  },
  {
    key: "verbosity",
    label: "回答详细度",
    description: "控制解释深度；不得省略完成任务所必需的信息",
  },
];

function isBaseStyle(value: unknown): value is BaseStyle {
  return (
    typeof value === "string" &&
    BASE_STYLE_OPTIONS.some((option) => option.value === value)
  );
}

function isStyleLevel(value: unknown): value is StyleLevel {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= -2 &&
    value <= 2
  );
}

export function parseResponseStyle(
  value: unknown,
): ResponseStyleConfig {
  if (!value || typeof value !== "object") {
    return { ...DEFAULT_RESPONSE_STYLE };
  }
  const record = value as Record<string, unknown>;
  return {
    base_style: isBaseStyle(record.base_style)
      ? record.base_style
      : DEFAULT_RESPONSE_STYLE.base_style,
    warmth: isStyleLevel(record.warmth)
      ? record.warmth
      : DEFAULT_RESPONSE_STYLE.warmth,
    enthusiasm: isStyleLevel(record.enthusiasm)
      ? record.enthusiasm
      : DEFAULT_RESPONSE_STYLE.enthusiasm,
    headings_and_lists: isStyleLevel(record.headings_and_lists)
      ? record.headings_and_lists
      : DEFAULT_RESPONSE_STYLE.headings_and_lists,
    emoji: isStyleLevel(record.emoji)
      ? record.emoji
      : DEFAULT_RESPONSE_STYLE.emoji,
    verbosity: isStyleLevel(record.verbosity)
      ? record.verbosity
      : DEFAULT_RESPONSE_STYLE.verbosity,
  };
}

export function getResponseStyleFromSettings(
  settings: WorkspaceSetting[] | undefined,
): ResponseStyleConfig {
  const value = settings?.find(
    (setting) => setting.key === CHAT_RESPONSE_STYLE_SETTING_KEY,
  )?.value;
  return parseResponseStyle(value);
}

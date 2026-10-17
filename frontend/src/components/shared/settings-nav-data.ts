import type { ComponentType } from "react";

import {
  Bot,
  Brain,
  Database,
  MessageSquareText,
  Info,
  Palette,
  RadioTower,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from "lucide-react";

export type SettingsNavItem = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  path: string;
  /** Optional section header the item is grouped under in the sidebar. */
  section?: string;
};

/** Navigation for the settings secondary window. Mirrors the /settings/* routes. */
export const settingsNav: SettingsNavItem[] = [
  { label: "通用与个性化", icon: Palette, path: "settings/general", section: "使用体验" },
  { label: "对话与学习", icon: MessageSquareText, path: "settings/conversation", section: "使用体验" },
  { label: "记忆与检索", icon: Brain, path: "settings/memory", section: "使用体验" },
  { label: "语音与音频", icon: RadioTower, path: "settings/voice", section: "使用体验" },
  { label: "模型与服务", icon: Bot, path: "settings/models", section: "能力配置" },
  { label: "工具与扩展", icon: Sparkles, path: "settings/tools", section: "能力配置" },
  { label: "工作区与协作", icon: UsersRound, path: "settings/workspace", section: "管理" },
  { label: "账户与安全", icon: ShieldCheck, path: "settings/account", section: "管理" },
  { label: "数据与存储", icon: Database, path: "settings/data", section: "管理" },
  { label: "关于", icon: Info, path: "settings/about" },
];

export const legacySettingsRoutes: Record<string, string> = {
  updates: "about?tab=updates",
  providers: "models", usage: "models?tab=usage",
  "voice-relay": "voice?tab=relay", personalization: "general",
  extensions: "tools", "search-fetch": "tools?tab=search", research: "tools?tab=search",
  egress: "tools?tab=search", "access-approvals": "tools?tab=search",
  access: "workspace?tab=members", audit: "workspace?tab=audit",
  "session-sharing": "workspace?tab=sharing", sharing: "workspace?tab=sharing", "session-shares": "workspace?tab=sharing",
  "storage/migrations": "data?tab=migration",
};

export function isSettingsNavActive(pathname: string, item: SettingsNavItem) {
  return pathname.endsWith(`/${item.path}`);
}

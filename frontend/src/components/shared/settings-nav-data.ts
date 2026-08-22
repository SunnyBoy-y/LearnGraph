import type { ComponentType } from "react";

import {
  Bot,
  CircleDollarSign,
  Database,
  Globe,
  Info,
  Palette,
  ShieldCheck,
  SlidersHorizontal,
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
  { label: "工作区设置", icon: SlidersHorizontal, path: "settings/workspace" },
  { label: "模型 Provider", icon: Bot, path: "settings/providers" },
  { label: "用量与预算", icon: CircleDollarSign, path: "settings/usage" },
  { label: "个性化", icon: Palette, path: "settings/personalization" },
  { label: "扩展中心", icon: Sparkles, path: "settings/extensions" },
  { label: "搜索与抓取", icon: Globe, path: "settings/search-fetch" },
  { label: "账户与访问", icon: UsersRound, path: "settings/access" },
  { label: "权限审计", icon: ShieldCheck, path: "settings/audit" },
  { label: "存储迁移", icon: Database, path: "settings/storage/migrations" },
  { label: "关于", icon: Info, path: "settings/about" },
];

export function isSettingsNavActive(pathname: string, item: SettingsNavItem) {
  return pathname.endsWith(`/${item.path}`);
}

import { Bell, BookOpen, Briefcase, CalendarDays, ChartNoAxesColumn, CodeXml, FileText, Languages, Newspaper, Plus, Radar, ShoppingBag, Sparkles } from "lucide-react";
import type { ScheduleSuggestion } from "@/api/schedules";

const icons = {
  newspaper: Newspaper, "book-open": BookOpen, briefcase: Briefcase,
  "chart-no-axes-column": ChartNoAxesColumn, "code-xml": CodeXml, bell: Bell,
  "shopping-bag": ShoppingBag, radar: Radar, "file-text": FileText,
  languages: Languages, "calendar-days": CalendarDays, sparkles: Sparkles,
} as const;

export function TaskRecommendationCard({ suggestion, disabled, onAdd }: {
  suggestion: ScheduleSuggestion;
  disabled: boolean;
  onAdd: () => void;
}) {
  const name = suggestion.icon;
  const Icon = typeof name === "string" && Object.hasOwn(icons, name) ? icons[name as keyof typeof icons] : Sparkles;
  // Legacy drafts start with the cadence. Only surface a recognized time phrase.
  const legacySchedule = suggestion.prompt.match(/^\s*(?:每个?工作日|每天|每日|每周[一二三四五六日天]?|每月(?:\d{1,2}[日号])?)(?:\s*(?:早上|上午|中午|下午|晚上|晚间|晚)?\s*\d{1,2}(?:[:：]\d{2}|点(?:半|\d{1,2}分)?))?/u)?.[0]?.trim();
  return <li>
    <button type="button" className="task-recommendation-card" disabled={disabled} aria-label={`安排${suggestion.name}`} title="填入任务草稿" onClick={onAdd}>
      <span className="task-recommendation-card__top">
        <Icon size={19} aria-hidden="true" /><Plus size={17} aria-hidden="true" />
      </span>
      <span className="task-recommendation-card__name">{suggestion.name}</span>
      <span className="task-recommendation-card__schedule">{suggestion.schedule?.trim() || legacySchedule || "周期见任务草稿"}</span>
    </button>
  </li>;
}

import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Languages, Loader2 } from "lucide-react";
import { FEATURE_MODEL_DEFAULT, SearchableFeatureModelSelect, parseFeatureModelValue, useFeatureModelChoices } from "@/components/shared/feature-model-select";
import { Button } from "@/components/ui/button";
import { currentWorkspaceQueryKey } from "@/lib/query-keys";

const LANGUAGES = [["zh-CN", "简体中文"], ["zh-TW", "繁體中文"], ["en", "English"], ["ja", "日本語"], ["ko", "한국어"], ["fr", "Français"], ["de", "Deutsch"], ["es", "Español"], ["ru", "Русский"], ["pt-BR", "Português"], ["custom", "自定义语言"]];
export interface MarketTranslationSelection { target_locale: string; provider_id: string | null; model_id: string | null }
interface TranslationResult { target_locale: string; translator_model_id: string; cached: boolean }

export function useMarketTranslation<T extends TranslationResult>({ queryKey, request }: {
  queryKey: readonly unknown[];
  request: (selection: MarketTranslationSelection) => Promise<T>;
}) {
  const [language, setLanguage] = useState("zh-CN");
  const [customLocale, setCustomLocale] = useState("");
  const [showTranslation, setShowTranslation] = useState(true);
  const [model, setModel] = useState(FEATURE_MODEL_DEFAULT);
  const { choices } = useFeatureModelChoices();
  const targetLocale = (language === "custom" ? customLocale.trim() : language).replaceAll("_", "-");
  const translation = useQuery({
    queryKey: currentWorkspaceQueryKey(...queryKey, targetLocale.toLowerCase(), model),
    queryFn: async () => {
      const result = await request({ target_locale: targetLocale, ...parseFeatureModelValue(model) });
      if (result.target_locale.toLowerCase() !== targetLocale.toLowerCase()) throw new Error("翻译语言与当前选择不一致，请重试。");
      return result;
    },
    enabled: false, retry: false, staleTime: Infinity,
  });
  return { language, setLanguage, customLocale, setCustomLocale, showTranslation, setShowTranslation,
    model, setModel, choices, targetLocale, translation, translated: showTranslation ? translation.data : undefined };
}

export function MarketTranslationControls<T extends TranslationResult>({ controller: c, disabled, description }: {
  controller: ReturnType<typeof useMarketTranslation<T>>;
  disabled?: boolean;
  description: string;
}) {
  const id = useId();
  return <div className="space-y-2 rounded-xl border bg-muted/30 p-3">
    <div className="max-w-full sm:max-w-md [&_button]:mt-0"><SearchableFeatureModelSelect ariaLabel="翻译模型" value={c.model} onValueChange={(value) => { c.setModel(value); c.setShowTranslation(true); }} placeholder="选择翻译模型" options={[{ value: FEATURE_MODEL_DEFAULT, label: "工作区默认文本模型" }, ...c.choices]} /></div>
    <div className="flex flex-wrap items-center gap-2">
      <Languages className="size-4 text-muted-foreground" />
      <label htmlFor={id} className="text-xs font-medium">翻译为</label>
      <select id={id} value={c.language} onChange={(event) => { c.setLanguage(event.target.value); c.setShowTranslation(true); }} className="h-8 max-w-full rounded-md border bg-background px-2 text-xs">
        {LANGUAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      {c.language === "custom" && <input aria-label="自定义目标语言代码" placeholder="语言代码，如 it 或 ar" value={c.customLocale} onChange={(event) => { c.setCustomLocale(event.target.value); c.setShowTranslation(true); }} className="h-8 w-40 max-w-full rounded-md border bg-background px-2 text-xs" />}
      <Button size="sm" variant="outline" disabled={disabled || c.translation.isFetching || !/^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$/.test(c.targetLocale) || c.targetLocale.length > 32} onClick={() => { c.setShowTranslation(true); void c.translation.refetch(); }}>
        {c.translation.isFetching && <Loader2 className="size-3 animate-spin" />}{c.translation.isFetching ? "模型翻译中…" : c.translation.isError ? "重试翻译" : "模型翻译"}
      </Button>
      {c.translation.data && <Button size="sm" variant="ghost" onClick={() => c.setShowTranslation((value) => !value)}>{c.showTranslation ? "查看原文" : "查看译文"}</Button>}
    </div>
    <p className="text-xs leading-5 text-muted-foreground" aria-live="polite">{c.translation.isFetching ? "正在翻译说明，长文可能需要一些时间。" : c.translated ? `模型译文 · ${c.translated.translator_model_id}${c.translated.cached ? " · 已缓存" : ""} · 可切回原文核对` : description}</p>
    {c.translation.isError && <p role="alert" className="text-xs text-destructive">{c.translation.error.message}</p>}
  </div>;
}

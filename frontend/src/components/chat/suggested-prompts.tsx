import { LoaderCircle, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { SuggestedPrompt } from "@/types/sessions";

export function SuggestedPromptContent({
  prompts,
  onSelect,
  disabled,
  isPending,
  isUnavailable,
  onConfigureProvider,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  disabled: boolean;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
}) {
  // 即便模型/Provider 不可用，也展示记忆或中文默认提示，避免空白或英文占位。
  // 生成失败由页面级 toast 提示；推荐问题区域不渲染错误文案。
  if (!isPending && !isUnavailable && !prompts.length) return null;

  return (
    <div
      aria-busy={isPending}
      aria-live="polite"
      className="chat-suggestions"
    >
      {isPending && !prompts.length ? (
        <div className="chat-suggestions__status">
          <LoaderCircle
            aria-hidden="true"
            className="chat-suggestions__spinner size-3.5 shrink-0"
          />
          <span>正在生成问题提示…</span>
        </div>
      ) : isUnavailable && !prompts.length ? (
        <div className="chat-suggestions__status chat-suggestions__status--unavailable">
          <span>需配置真实模型 Provider</span>
          <Button onClick={onConfigureProvider} size="xs" variant="outline">
            前往设置
          </Button>
        </div>
      ) : (
        <div aria-label="推荐问题" className="chat-empty-state__prompts">
          {prompts.map((prompt) => (
            <button
              className="chat-empty-state__prompt"
              disabled={disabled}
              key={prompt.id}
              onClick={() => onSelect(prompt.content)}
              type="button"
            >
              {prompt.content}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function FollowUpPrompts({
  prompts,
  onSelect,
  isPending,
  isUnavailable,
  onConfigureProvider,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
}) {
  if (!prompts.length && !isPending && !isUnavailable) return null;

  return (
    <section aria-label="对话问题提示" className="chat-follow-up-prompts">
      <div className="chat-follow-up-prompts__label">
        <Sparkles aria-hidden="true" className="size-3.5" />
        <span>接下来可以问</span>
      </div>
      <SuggestedPromptContent
        disabled={isPending}
        isPending={isPending}
        isUnavailable={isUnavailable}
        onConfigureProvider={onConfigureProvider}
        onSelect={onSelect}
        prompts={prompts}
      />
    </section>
  );
}

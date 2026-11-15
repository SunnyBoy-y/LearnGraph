import { ArrowUpRight, Compass, FlaskConical, Lightbulb, RotateCw } from "lucide-react";

import type { SuggestedPrompt } from "@/types/sessions";
import "./new-chat-welcome.css";

const promptIcons = [Compass, Lightbulb, FlaskConical];

export function NewChatSuggestions({
  prompts,
  disabled,
  pending = false,
  onRefresh,
  onSelect,
}: {
  prompts: SuggestedPrompt[];
  disabled: boolean;
  pending?: boolean;
  onRefresh?: () => void;
  onSelect: (content: string) => void;
}) {
  if (!prompts.length) return null;

  return (
    <section aria-label="新对话推荐" aria-busy={pending} className="new-chat-suggestions">
      <div className="new-chat-suggestions__header">
        <span>试着问问</span>
        {onRefresh ? (
          <button
            aria-label="换一组推荐问题"
            className="new-chat-suggestions__refresh"
            disabled={disabled || pending}
            onClick={onRefresh}
            type="button"
          >
            <RotateCw aria-hidden="true" className={pending ? "animate-spin" : undefined} />
            换一组
          </button>
        ) : null}
      </div>
      <div aria-label="推荐问题" aria-live="polite" className="new-chat-suggestions__list">
        {pending && !prompts.length ? (
          <div aria-label="正在生成推荐问题" role="status" className="new-chat-suggestions__loading" data-motion="lockstep">
            {promptIcons.map((_, index) => <div className="new-chat-suggestions__skeleton" key={index} />)}
          </div>
        ) : prompts.slice(0, 3).map((prompt, index) => {
          const Icon = promptIcons[index];
          return (
            <button
              className="new-chat-suggestions__prompt"
              disabled={disabled}
              key={prompt.id}
              onClick={() => onSelect(prompt.content)}
              type="button"
            >
              <Icon aria-hidden="true" />
              <span>{prompt.content}</span>
              <ArrowUpRight aria-hidden="true" className="new-chat-suggestions__arrow" />
            </button>
          );
        })}
      </div>
    </section>
  );
}

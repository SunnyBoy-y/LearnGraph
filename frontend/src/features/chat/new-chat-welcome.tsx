import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUpRight, Compass, FlaskConical, Lightbulb, RotateCw } from "lucide-react";

import type { SuggestedPrompt } from "@/types/sessions";
import "./new-chat-welcome.css";

const promptIcons = [Compass, Lightbulb, FlaskConical];

export function NewChatSuggestions({
  prompts,
  disabled,
  pending = false,
  hidden = false,
  onRefresh,
  refreshHint,
  onSelect,
  mode = "question",
}: {
  prompts: SuggestedPrompt[];
  disabled: boolean;
  pending?: boolean;
  hidden?: boolean;
  onRefresh?: () => void;
  refreshHint?: string;
  onSelect: (content: string) => void;
  mode?: "question" | "goal";
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const [stableListMinHeight, setStableListMinHeight] = useState<number | undefined>(undefined);
  const visiblePrompts = prompts.slice(0, 3);
  const promptKey = visiblePrompts.map((item) => item.id).join("|");

  // Keep container height stable when rotating groups; reset if viewport width or mode changes.
  useEffect(() => {
    setStableListMinHeight(undefined);
  }, [mode]);

  useEffect(() => {
    let lastWidth = window.innerWidth;
    const handleResize = () => {
      if (window.innerWidth !== lastWidth) {
        lastWidth = window.innerWidth;
        setStableListMinHeight(undefined);
      }
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useLayoutEffect(() => {
    const element = listRef.current;
    if (!element || !visiblePrompts.length) return;
    const measured = Math.ceil(element.getBoundingClientRect().height);
    if (measured > 0) {
      setStableListMinHeight((current) => (current && current >= measured ? current : measured));
    }
  }, [promptKey, visiblePrompts.length]);

  if (!prompts.length) return null;
  const goal = mode === "goal";
  const listLabel = goal ? "推荐学习目标" : "推荐问题";

  return (
    <section
      aria-busy={pending}
      aria-hidden={hidden || undefined}
      aria-label={goal ? listLabel : "新对话推荐"}
      className={hidden ? "new-chat-suggestions is-hidden" : "new-chat-suggestions"}
      inert={hidden || undefined}
    >
      <div className="new-chat-suggestions__header">
        <span className="new-chat-suggestions__title">{goal ? "试着设定一个目标" : "试着问问"}</span>
        {onRefresh ? (
          <button
            aria-label={`换一组${listLabel}`}
            className="new-chat-suggestions__refresh"
            disabled={disabled || pending || Boolean(refreshHint)}
            onClick={onRefresh}
            tabIndex={hidden ? -1 : undefined}
            type="button"
          >
            <RotateCw aria-hidden="true" className={pending ? "animate-spin" : undefined} />
            {refreshHint ?? "换一组"}
          </button>
        ) : null}
      </div>
      <div
        aria-label="推荐问题"
        aria-live="polite"
        className="new-chat-suggestions__list"
        ref={listRef}
        style={stableListMinHeight ? { minHeight: `${stableListMinHeight}px` } : undefined}
      >
        {pending && !prompts.length ? (
          <div aria-label="正在生成推荐问题" role="status" className="new-chat-suggestions__loading" data-motion="lockstep">
            {promptIcons.map((_, index) => <div className="new-chat-suggestions__skeleton" key={index} />)}
          </div>
        ) : visiblePrompts.map((prompt, index) => {
          const Icon = promptIcons[index];
          const displayText = prompt.display_text?.trim() || prompt.content;
          return (
            <button
              className="new-chat-suggestions__prompt"
              disabled={disabled}
              key={prompt.id}
              onClick={() => onSelect(prompt.content)}
              tabIndex={hidden ? -1 : undefined}
              type="button"
            >
              <Icon aria-hidden="true" className="new-chat-suggestions__icon" />
              <span className="new-chat-suggestions__text">{displayText}</span>
              <ArrowUpRight aria-hidden="true" className="new-chat-suggestions__arrow" />
            </button>
          );
        })}
      </div>
    </section>
  );
}

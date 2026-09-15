"use client";

import { Button } from "@/components/ui/button";
import { saveBlobViaNative } from "@/lib/native-download";
import { cn } from "@/lib/utils";
import type { UIMessage } from "ai";
import { ArrowDownIcon, DownloadIcon } from "lucide-react";
import type { ComponentProps } from "react";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  useConversationScrollController,
  type ConversationScrollController,
} from "@/features/chat/use-conversation-scroll-controller";

interface ConversationContextValue {
  controller: ConversationScrollController;
}

const ConversationContext = createContext<ConversationContextValue | null>(
  null,
);

interface ConversationProps extends Omit<ComponentProps<"div">, "children"> {
  children?: ReactNode | ((context: ConversationContextValue) => ReactNode);
  controller?: ConversationScrollController;
}

export const Conversation = ({
  className,
  children,
  controller: suppliedController,
  ...props
}: ConversationProps) => {
  const internalController = useConversationScrollController();
  const controller = suppliedController ?? internalController;
  const context = useMemo(() => ({ controller }), [controller]);

  return (
    <ConversationContext.Provider value={context}>
      <div
        className={cn("relative flex-1 overflow-y-hidden", className)}
        data-scroll-mode={controller.mode}
        role="log"
        {...props}
      >
        {typeof children === "function" ? children(context) : children}
      </div>
    </ConversationContext.Provider>
  );
};

function useConversationScrollContext() {
  const context = useContext(ConversationContext);
  if (!context) {
    throw new Error(
      "useConversationScrollContext must be used inside Conversation",
    );
  }
  return context;
}

export type ConversationContentProps = ComponentProps<"div"> & {
  scrollClassName?: string;
};

export const ConversationContent = ({
  className,
  scrollClassName,
  style,
  ...props
}: ConversationContentProps) => {
  const { controller } = useConversationScrollContext();
  return (
    <div
      className={scrollClassName}
      data-conversation-scroll
      // 页面级滚动容器：这里起手仍然允许抽屉手势（内部横向可滚动控件不受影响，
      // 它们在向上查找时先命中，依旧会拦掉手势）。
      data-drawer-swipe="allow"
      onKeyDown={controller.handleKeyDown}
      onPointerDown={controller.handlePointerDown}
      onScroll={controller.handleScroll}
      onTouchEnd={controller.handleTouchEnd}
      onTouchStart={controller.handleTouchStart}
      onWheel={controller.handleWheel}
      ref={controller.scrollRef}
      style={{
        height: "100%",
        overflow: "auto",
        overflowAnchor: "none",
        scrollbarGutter: "stable both-edges",
        width: "100%",
      }}
    >
      <div
        className={cn("flex flex-col gap-8 p-4", className)}
        ref={controller.contentRef}
        style={
          {
            ...style,
            // Tail space reserved by the scroll controller so the active turn
            // anchor stays reachable. Owned by the controller (mode-independent),
            // never by a scroll-mode-scoped CSS rule: that rule moved the content
            // height by ~70dvh on every mode change and jumped the canvas.
            "--conversation-tail-space": `${controller.tailSpace}px`,
          } as CSSProperties
        }
        {...props}
      />
    </div>
  );
};

export type ConversationEmptyStateProps = ComponentProps<"div"> & {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
};

export const ConversationEmptyState = ({
  className,
  title = "No messages yet",
  description = "Start a conversation to see messages here",
  icon,
  children,
  ...props
}: ConversationEmptyStateProps) => (
  <div
    className={cn(
      "flex size-full flex-col items-center justify-center gap-3 p-8 text-center",
      className
    )}
    {...props}
  >
    {children ?? (
      <>
        {icon && <div className="text-muted-foreground">{icon}</div>}
        <div className="space-y-1">
          <h3 className="font-medium text-sm">{title}</h3>
          {description && (
            <p className="text-muted-foreground text-sm">{description}</p>
          )}
        </div>
      </>
    )}
  </div>
);

export type ConversationScrollButtonProps = ComponentProps<typeof Button>;

export const ConversationScrollButton = ({
  className,
  children,
  ...props
}: ConversationScrollButtonProps) => {
  const { controller } = useConversationScrollContext();
  const {
    hasCommittedAnswer,
    hasNewContent,
    isAtBottom,
    mode,
    returnToLatest,
  } = controller;

  const handleScrollToBottom = useCallback(() => {
    returnToLatest();
  }, [returnToLatest]);

  const visible =
    hasNewContent || (!isAtBottom && mode === "MANUAL_READING");
  if (!visible) return null;

  // 按钮只保留向下箭头（悬浮态文案会让按钮宽度随状态跳动）；
  // 状态语义仍通过 aria-label 暴露给读屏与测试。
  const label = hasNewContent
    ? hasCommittedAnswer
      ? "正文仍在生成"
      : "还有新内容"
    : "回到最新";

  return (
    <Button
      aria-label={label}
      className={cn(
        "chat-scroll-to-bottom absolute bottom-4 left-[50%] size-8 shrink-0 translate-x-[-50%] rounded-full p-0 shadow-none dark:bg-background dark:hover:bg-muted",
        className
      )}
      onClick={handleScrollToBottom}
      size="sm"
      type="button"
      variant="outline"
      {...props}
    >
      {children ?? <ArrowDownIcon className="size-3.5" />}
    </Button>
  );
};

const getMessageText = (message: UIMessage): string =>
  message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");

export type ConversationDownloadProps = Omit<
  ComponentProps<typeof Button>,
  "onClick"
> & {
  messages: UIMessage[];
  filename?: string;
  formatMessage?: (message: UIMessage, index: number) => string;
};

const defaultFormatMessage = (message: UIMessage): string => {
  const roleLabel =
    message.role.charAt(0).toUpperCase() + message.role.slice(1);
  return `**${roleLabel}:** ${getMessageText(message)}`;
};

const messagesToMarkdown = (
  messages: UIMessage[],
  formatMessage: (
    message: UIMessage,
    index: number
  ) => string = defaultFormatMessage
): string => messages.map((msg, i) => formatMessage(msg, i)).join("\n\n");

export const ConversationDownload = ({
  messages,
  filename = "conversation.md",
  formatMessage = defaultFormatMessage,
  className,
  children,
  ...props
}: ConversationDownloadProps) => {
  const handleDownload = useCallback(() => {
    const markdown = messagesToMarkdown(messages, formatMessage);
    const blob = new Blob([markdown], { type: "text/markdown" });
    // 移动端 WebView：纯前端生成的 blob 交给原生 base64 通道
    void saveBlobViaNative(blob, filename).then((handled) => {
      if (handled) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    });
  }, [messages, filename, formatMessage]);

  return (
    <Button
      className={cn(
        "absolute top-4 right-4 rounded-full dark:bg-background dark:hover:bg-muted",
        className
      )}
      onClick={handleDownload}
      size="icon"
      type="button"
      variant="outline"
      {...props}
    >
      {children ?? <DownloadIcon className="size-4" />}
    </Button>
  );
};

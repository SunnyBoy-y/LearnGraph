"use client";

import { Button } from "@/components/ui/button";
import {
  ButtonGroup,
  ButtonGroupText,
} from "@/components/ui/button-group";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { normalizeLatexDelimiters } from "@/lib/markdown";
import { LazyStreamdown } from "@/components/ai-elements/lazy-streamdown";
import { decodeUrlForDisplay } from "@/lib/url-display";
import type { UIMessage } from "ai";
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import type { ComponentProps, HTMLAttributes, ReactElement, ReactNode } from "react";
import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export type MessageProps = HTMLAttributes<HTMLDivElement> & {
  from: UIMessage["role"];
};

export const Message = ({ className, from, ...props }: MessageProps) => (
  <div
    className={cn(
      "group flex flex-col gap-2",
      from === "user"
        ? "is-user ml-auto w-[60%] max-w-[60%] justify-end"
        : "is-assistant w-full max-w-[95%]",
      className
    )}
    {...props}
  />
);

export type MessageContentProps = HTMLAttributes<HTMLDivElement>;

export const MessageContent = ({
  children,
  className,
  ...props
}: MessageContentProps) => (
  <div
    className={cn(
      "is-user:dark flex w-fit min-w-0 max-w-full flex-col gap-2 overflow-hidden text-sm",
      "group-[.is-user]:ml-auto group-[.is-user]:rounded-lg group-[.is-user]:bg-secondary group-[.is-user]:px-4 group-[.is-user]:py-3 group-[.is-user]:text-foreground",
      "group-[.is-assistant]:text-foreground",
      className
    )}
    {...props}
  >
    {children}
  </div>
);

export type MessageActionsProps = ComponentProps<"div">;

export const MessageActions = ({
  className,
  children,
  ...props
}: MessageActionsProps) => (
  <div className={cn("flex items-center gap-1", className)} {...props}>
    {children}
  </div>
);

export type MessageActionProps = ComponentProps<typeof Button> & {
  tooltip?: string;
  label?: string;
};

export const MessageAction = ({
  tooltip,
  children,
  label,
  variant = "ghost",
  size = "icon-sm",
  ...props
}: MessageActionProps) => {
  const button = (
    <Button size={size} type="button" variant={variant} {...props}>
      {children}
      <span className="sr-only">{label || tooltip}</span>
    </Button>
  );

  if (tooltip) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            {props.disabled ? (
              <span className="inline-flex cursor-not-allowed">{button}</span>
            ) : (
              button
            )}
          </TooltipTrigger>
          <TooltipContent>
            <p>{tooltip}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return button;
};

interface MessageBranchContextType {
  currentBranch: number;
  totalBranches: number;
  goToPrevious: () => void;
  goToNext: () => void;
  branches: ReactElement[];
  setBranches: (branches: ReactElement[]) => void;
}

const MessageBranchContext = createContext<MessageBranchContextType | null>(
  null
);

const useMessageBranch = () => {
  const context = useContext(MessageBranchContext);

  if (!context) {
    throw new Error(
      "MessageBranch components must be used within MessageBranch"
    );
  }

  return context;
};

export type MessageBranchProps = HTMLAttributes<HTMLDivElement> & {
  defaultBranch?: number;
  onBranchChange?: (branchIndex: number) => void;
};

export const MessageBranch = ({
  defaultBranch = 0,
  onBranchChange,
  className,
  ...props
}: MessageBranchProps) => {
  const [currentBranch, setCurrentBranch] = useState(defaultBranch);
  const [branches, setBranches] = useState<ReactElement[]>([]);

  const handleBranchChange = useCallback(
    (newBranch: number) => {
      setCurrentBranch(newBranch);
      onBranchChange?.(newBranch);
    },
    [onBranchChange]
  );

  const goToPrevious = useCallback(() => {
    const newBranch =
      currentBranch > 0 ? currentBranch - 1 : branches.length - 1;
    handleBranchChange(newBranch);
  }, [currentBranch, branches.length, handleBranchChange]);

  const goToNext = useCallback(() => {
    const newBranch =
      currentBranch < branches.length - 1 ? currentBranch + 1 : 0;
    handleBranchChange(newBranch);
  }, [currentBranch, branches.length, handleBranchChange]);

  const contextValue = useMemo<MessageBranchContextType>(
    () => ({
      branches,
      currentBranch,
      goToNext,
      goToPrevious,
      setBranches,
      totalBranches: branches.length,
    }),
    [branches, currentBranch, goToNext, goToPrevious]
  );

  return (
    <MessageBranchContext.Provider value={contextValue}>
      <div
        className={cn("grid w-full gap-2 [&>div]:pb-0", className)}
        {...props}
      />
    </MessageBranchContext.Provider>
  );
};

export type MessageBranchContentProps = HTMLAttributes<HTMLDivElement>;

export const MessageBranchContent = ({
  children,
  ...props
}: MessageBranchContentProps) => {
  const { currentBranch, setBranches, branches } = useMessageBranch();
  const childrenArray = useMemo(
    () => (Array.isArray(children) ? children : [children]),
    [children]
  );

  // Use useEffect to update branches when they change
  useEffect(() => {
    if (branches.length !== childrenArray.length) {
      setBranches(childrenArray);
    }
  }, [childrenArray, branches, setBranches]);

  return childrenArray.map((branch, index) => (
    <div
      className={cn(
        "grid gap-2 overflow-hidden [&>div]:pb-0",
        index === currentBranch ? "block" : "hidden"
      )}
      key={branch.key}
      {...props}
    >
      {branch}
    </div>
  ));
};

export type MessageBranchSelectorProps = ComponentProps<typeof ButtonGroup>;

export const MessageBranchSelector = ({
  className,
  ...props
}: MessageBranchSelectorProps) => {
  const { totalBranches } = useMessageBranch();

  // Don't render if there's only one branch
  if (totalBranches <= 1) {
    return null;
  }

  return (
    <ButtonGroup
      className={cn(
        "[&>*:not(:first-child)]:rounded-l-md [&>*:not(:last-child)]:rounded-r-md",
        className
      )}
      orientation="horizontal"
      {...props}
    />
  );
};

export type MessageBranchPreviousProps = ComponentProps<typeof Button>;

export const MessageBranchPrevious = ({
  children,
  ...props
}: MessageBranchPreviousProps) => {
  const { goToPrevious, totalBranches } = useMessageBranch();

  return (
    <Button
      aria-label="Previous branch"
      disabled={totalBranches <= 1}
      onClick={goToPrevious}
      size="icon-sm"
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? <ChevronLeftIcon size={14} />}
    </Button>
  );
};

export type MessageBranchNextProps = ComponentProps<typeof Button>;

export const MessageBranchNext = ({
  children,
  ...props
}: MessageBranchNextProps) => {
  const { goToNext, totalBranches } = useMessageBranch();

  return (
    <Button
      aria-label="Next branch"
      disabled={totalBranches <= 1}
      onClick={goToNext}
      size="icon-sm"
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? <ChevronRightIcon size={14} />}
    </Button>
  );
};

export type MessageBranchPageProps = HTMLAttributes<HTMLSpanElement>;

export const MessageBranchPage = ({
  className,
  ...props
}: MessageBranchPageProps) => {
  const { currentBranch, totalBranches } = useMessageBranch();

  return (
    <ButtonGroupText
      className={cn(
        "border-none bg-transparent text-muted-foreground shadow-none",
        className
      )}
      {...props}
    >
      {currentBranch + 1} of {totalBranches}
    </ButtonGroupText>
  );
};

export type MessageResponseComponents = {
  a?: (props: {
    href?: string;
    children?: ReactNode;
    [key: string]: unknown;
  }) => ReactNode;
  img?: (props: {
    src?: string;
    alt?: string;
    [key: string]: unknown;
  }) => ReactNode;
  [key: string]: unknown;
};

export type MessageResponseProps = {
  className?: string;
  components?: MessageResponseComponents;
  linkSafety?: Record<string, unknown>;
  translations?: Record<string, unknown>;
  children?: ReactNode;
  isAnimating?: boolean;
  [key: string]: unknown;
};

export const MessageResponse = memo(
  ({ className, linkSafety, translations, ...props }: MessageResponseProps) => {
    const children =
      typeof props.children === "string"
        ? normalizeLatexDelimiters(props.children)
        : props.children;
    // Decode percent-encoded hrefs in the safety modal so Chinese titles
    // (#2 - 知识图谱全景) are readable instead of %E7%9F%A5… garbled text.
    const resolvedLinkSafety = linkSafety ?? {
      enabled: true,
      renderModal: ({
        url,
        isOpen,
        onClose,
        onConfirm,
      }: {
        url: string;
        isOpen: boolean;
        onClose: () => void;
        onConfirm: () => void;
      }) => {
        if (!isOpen) return null;
        const displayUrl = decodeUrlForDisplay(url);
        return (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-background/50 backdrop-blur-sm"
            data-streamdown="link-safety-modal"
            onClick={onClose}
            onKeyDown={(event) => {
              if (event.key === "Escape") onClose();
            }}
            role="button"
            tabIndex={0}
          >
            <div
              className="relative mx-4 flex w-full max-w-md flex-col gap-4 rounded-xl border bg-background p-6 shadow-lg"
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
              role="presentation"
            >
              <button
                className="absolute top-4 right-4 rounded-md p-1 text-muted-foreground transition-all hover:bg-muted hover:text-foreground"
                onClick={onClose}
                title="关闭"
                type="button"
              >
                ×
              </button>
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 font-semibold text-lg">
                  <span>打开外部链接？</span>
                </div>
                <p className="text-muted-foreground text-sm">
                  你即将访问外部网站。
                </p>
              </div>
              <div
                className={cn(
                  "break-all rounded-md bg-muted p-3 font-mono text-sm",
                  displayUrl.length > 100 && "max-h-32 overflow-y-auto",
                )}
              >
                {displayUrl}
              </div>
              <div className="flex gap-2">
                <button
                  className="flex flex-1 items-center justify-center gap-2 rounded-md border bg-background px-4 py-2 font-medium text-sm transition-all hover:bg-muted"
                  onClick={() => {
                    void navigator.clipboard?.writeText(url);
                  }}
                  type="button"
                >
                  复制链接
                </button>
                <button
                  className="flex flex-1 items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 font-medium text-primary-foreground text-sm transition-all hover:bg-primary/90"
                  onClick={() => {
                    onConfirm();
                    onClose();
                  }}
                  type="button"
                >
                  打开链接
                </button>
              </div>
            </div>
          </div>
        );
      },
    };
    return (
      <LazyStreamdown
        className={cn(
          "size-full [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
          className,
        )}
        linkSafety={resolvedLinkSafety}
        translations={{
          openExternalLink: "打开外部链接？",
          externalLinkWarning: "你即将访问外部网站。",
          copyLink: "复制链接",
          openLink: "打开链接",
          close: "关闭",
          ...translations,
        }}
        {...props}
      >
        {children}
      </LazyStreamdown>
    );
  },
  (prevProps, nextProps) =>
    prevProps.children === nextProps.children &&
    nextProps.isAnimating === prevProps.isAnimating
);

MessageResponse.displayName = "MessageResponse";

export type MessageToolbarProps = ComponentProps<"div">;

export const MessageToolbar = ({
  className,
  children,
  ...props
}: MessageToolbarProps) => (
  <div
    className={cn(
      "mt-4 flex w-full items-center justify-between gap-4",
      className
    )}
    {...props}
  >
    {children}
  </div>
);

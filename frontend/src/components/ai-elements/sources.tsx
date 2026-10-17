"use client";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { BookIcon, ChevronRightIcon, Globe2 } from "lucide-react";
import type { ComponentProps } from "react";

export type SourcesProps = ComponentProps<typeof Collapsible>;

export const Sources = ({ className, ...props }: SourcesProps) => (
  <Collapsible
    className={cn("not-prose mb-4 text-primary text-xs", className)}
    {...props}
  />
);

export type SourcesTriggerProps = ComponentProps<typeof CollapsibleTrigger> & {
  count: number;
};

export const SourcesTrigger = ({
  className,
  count,
  children,
  ...props
}: SourcesTriggerProps) => (
  <CollapsibleTrigger
    type="button"
    className={cn(
      "group inline-flex h-7 w-fit cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-[6px] border-0 bg-transparent px-1.5 py-0 text-[13px] font-normal text-muted-foreground shadow-none transition-colors duration-150 hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      className
    )}
    {...props}
  >
    <Globe2 aria-hidden="true" className="size-3.5 shrink-0" />
    {children ?? <span>Used {count} sources</span>}
    <ChevronRightIcon aria-hidden="true" className="size-3 shrink-0 transition-transform duration-150 group-hover:translate-x-0.5 group-data-[state=open]:rotate-90 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0" />
  </CollapsibleTrigger>
);

export type SourcesContentProps = ComponentProps<typeof CollapsibleContent>;

export const SourcesContent = ({
  className,
  ...props
}: SourcesContentProps) => (
  <CollapsibleContent
    className={cn(
      "mt-3 flex w-fit flex-col gap-2",
      "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 outline-none data-[state=closed]:animate-out data-[state=open]:animate-in",
      className
    )}
    {...props}
  />
);

export type SourceProps = ComponentProps<"a">;

export const Source = ({ href, title, children, ...props }: SourceProps) => (
  <a
    className="flex items-center gap-2"
    href={href}
    rel="noreferrer"
    target="_blank"
    {...props}
  >
    {children ?? (
      <>
        <BookIcon className="h-4 w-4" />
        <span className="block font-medium">{title}</span>
      </>
    )}
  </a>
);

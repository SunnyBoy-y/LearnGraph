"use client";

import { cn } from "@/lib/utils";
import type { CSSProperties, ElementType } from "react";
import { createElement, memo, useMemo } from "react";
import { useReducedMotionPreference } from "@/lib/motion-preferences";

export interface TextShimmerProps {
  children: string;
  as?: ElementType;
  className?: string;
  duration?: number;
  spread?: number;
}

const ShimmerComponent = ({
  children,
  as: Component = "p",
  className,
  duration = 2,
  spread = 2,
}: TextShimmerProps) => {
  const reducedMotion = useReducedMotionPreference();

  const dynamicSpread = useMemo(
    () => (children?.length ?? 0) * spread,
    [children, spread]
  );

  if (reducedMotion) {
    return createElement(
      Component,
      { className: cn("relative inline-block text-muted-foreground", className) },
      children
    );
  }

  return createElement(
    Component,
    {
      className: cn(
        "lg-shimmer-text relative inline-block bg-[length:250%_100%,auto] bg-clip-text text-transparent",
        "[--bg:linear-gradient(90deg,#0000_calc(50%-var(--spread)),var(--color-background),#0000_calc(50%+var(--spread)))] [background-repeat:no-repeat,padding-box]",
        className
      ),
      style: {
        "--spread": `${dynamicSpread}px`,
        "--shimmer-duration": `${duration}s`,
        backgroundImage:
          "var(--bg), linear-gradient(var(--color-muted-foreground), var(--color-muted-foreground))",
      } as CSSProperties,
    },
    children
  );
};

export const Shimmer = memo(ShimmerComponent);

import { createElement, useMemo, type ReactNode } from "react";
import { parseIllustrationSvg, type IllustrationNode } from "@/lib/safe-illustration";

function element(node: IllustrationNode | string, key: number): ReactNode {
  return typeof node === "string" ? node : createElement(node.tag, {...node.attrs, key}, ...node.children.map(element));
}

export function SvgIllustration({svg, alt}: {svg: string; alt: string}) {
  const tree = useMemo(() => parseIllustrationSvg(svg), [svg]);
  if (!tree) return null;
  return <figure className="rich-doc__illustration" aria-label={`${alt}（SVG 示意图）`}>
    {createElement("svg", {...tree.attrs, role: "img", "aria-label": alt || "SVG 示意图", preserveAspectRatio: "xMidYMid meet"}, ...tree.children.map(element))}
  </figure>;
}

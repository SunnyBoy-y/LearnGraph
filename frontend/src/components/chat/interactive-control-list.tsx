import { createContext, useContext, useState, type ComponentProps } from "react";
import type { Element, ElementContent } from "hast";
import { cn } from "@/lib/utils";
import "./interactive-control-list.css";

const TaskLabel = createContext("清单项");
const text = (node: ElementContent): string => node.type === "text" ? node.value
  : node.type === "element" && !["ul", "ol"].includes(node.tagName) ? node.children.map(text).join("") : "";

export function MarkdownTaskListItem({ node, children, className, ...props }: ComponentProps<"li"> & { node?: Element }) {
  const label = node?.children.map(text).join("").replace(/\s+/g, " ").trim() || "清单项";
  return <TaskLabel.Provider value={label}><li {...props} className={cn("py-1 [&>p]:inline", className)} data-streamdown="list-item">{children}</li></TaskLabel.Provider>;
}

/** GFM emits disabled checkboxes by default. Keep the initial mark but let the user change it locally. */
export function MarkdownTaskCheckbox({ node: _node, checked, disabled: _disabled, className, ...props }: ComponentProps<"input"> & { node?: Element }) {
  const label = useContext(TaskLabel);
  const [selected, setSelected] = useState(Boolean(checked));
  if (props.type !== "checkbox") return <input {...props} className={className} readOnly />;
  return <input {...props} type="checkbox" checked={selected}
    aria-label={props["aria-label"] ?? label}
    className={[className, "markdown-task-checkbox"].filter(Boolean).join(" ")}
    onChange={event => setSelected(event.currentTarget.checked)} />;
}

import { useEffect, useState, type CSSProperties } from "react";
import { ChevronDown, List } from "lucide-react";
import type { DocumentNode } from "@/lib/stream-ui";
import { useStreamUIState } from "@/lib/stream-ui-local-state";

/** A reading aid, with stable anchors and scoped, persisted disclosure state. */
export function DocumentTOC({ headings, scope }: {headings: DocumentNode[]; scope: string}) {
  const [open, setOpen] = useStreamUIState("toc:open", false);
  const [active, setActive] = useState(headings[0]?.id ?? "");
  useEffect(() => {
    const fromHash = () => {
      const match = headings.find(node => `#${scope}-${node.id}` === window.location.hash);
      if (match) setActive(match.id);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      const heading = visible[0]?.target;
      if (heading) {
        const match = headings.find(node => `${scope}-${node.id}` === heading.id);
        if (match) setActive(match.id);
      }
    }, {rootMargin: "-10% 0px -65% 0px", threshold: 0});
    headings.forEach(node => {
      const target = document.getElementById(`${scope}-${node.id}`);
      if (target) observer?.observe(target);
    });
    return () => {window.removeEventListener("hashchange", fromHash); observer?.disconnect();};
  }, [headings, scope]);
  if (!headings.length) return null;
  const baseLevel = Math.min(...headings.map(node => node.level));
  const current = headings.some(node => node.id === active) ? active : headings[0].id;
  return <nav className="rich-doc__toc" aria-label="文档目录">
    <details open={open} onToggle={event => {if (event.currentTarget.open !== open) setOpen(event.currentTarget.open);}}>
      <summary>
        <List size={17} aria-hidden="true" />
        <span className="rich-doc__toc-title">目录</span>
        <span className="rich-doc__toc-count">{headings.length} 个章节</span>
        <ChevronDown className="rich-doc__toc-chevron" size={16} aria-hidden="true" />
      </summary>
      <ol>{headings.map((node, index) => <li key={node.id} style={{"--toc-indent": Math.min(node.level - baseLevel, 3)} as CSSProperties}>
        <a href={`#${scope}-${node.id}`} aria-current={current === node.id ? "location" : undefined} onClick={() => setActive(node.id)}>
          <span className="rich-doc__toc-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
          <span>{node.text || node.label}</span>
        </a>
      </li>)}</ol>
    </details>
  </nav>;
}

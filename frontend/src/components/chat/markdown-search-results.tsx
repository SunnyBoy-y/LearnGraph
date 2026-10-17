import type { ComponentProps } from "react";
import type { Element, ElementContent } from "hast";
import { Globe } from "lucide-react";
import { citationHttpUrl } from "@/lib/citation-url";
import { decodeUrlForDisplay } from "@/lib/url-display";
import { cn } from "@/lib/utils";
import { LinkSiteIcon, MessageLink } from "./message-link";

type SearchResult = { title: string; href: string; description: string };
type ListProps = ComponentProps<"ol"> & { node?: Element };

const nonEmpty = (nodes: ElementContent[]) => nodes.filter((node) => node.type !== "text" || node.value.trim());
const plainText = (node: ElementContent): string => node.type === "text" ? node.value
  : node.type === "element" ? (node.tagName === "br" ? "\n" : node.children.map(plainText).join("")) : "";
const allowedTags = new Set(["p", "a", "strong", "em", "span", "br", "blockquote"]);

/** Accept only resource-shaped lists; keep procedural, task and nested lists intact. */
function searchResult(item: Element): SearchResult | undefined {
  const links: Element[] = [];
  function supported(node: ElementContent): boolean {
    if (node.type === "text") return true;
    if (node.type !== "element" || !allowedTags.has(node.tagName)) return false;
    if (node.tagName === "a") links.push(node);
    return node.children.every(supported);
  }
  if (!item.children.every(supported) || links.length !== 1) return undefined;
  const link = links[0];
  const href = citationHttpUrl(typeof link.properties.href === "string" ? link.properties.href : undefined);
  if (!href) return undefined;
  const blocks = nonEmpty(item.children);
  const first = blocks[0];
  const loose = first?.type === "element" && first.tagName === "p";
  const headline = loose ? first.children : item.children;
  const linkPosition = headline.findIndex((child) => child === link ||
    (child.type === "element" && child.children.includes(link)));
  if (linkPosition < 0) return undefined;
  const prefix = nonEmpty(headline.slice(0, linkPosition));
  const suffix = headline.slice(linkPosition + 1).map(plainText).join("");
  // A prose sentence containing a link is not a search result.
  if (suffix.trim() && !/^(?:\s*[-—–:：·|]|[ \t]*\n)/.test(suffix)) return undefined;
  const rawLabel = link.children.map(plainText).join("").trim();
  const bareUrl = decodeUrlForDisplay(rawLabel).replace(/\/$/, "") === decodeUrlForDisplay(href).replace(/\/$/, "");
  if (prefix.length && (!bareUrl || prefix.some((node) => node.type !== "element" || node.tagName !== "strong"))) {
    return undefined;
  }
  const title = (prefix.length ? prefix.map(plainText).join("") : rawLabel).trim();
  if (!title || (bareUrl && !prefix.length)) return undefined;
  const trailing = loose ? blocks.slice(1) : [];
  if (trailing.some((node) => node.type !== "element" || !["p", "blockquote"].includes(node.tagName))) return undefined;
  const description = [suffix.replace(/^\s*[-—–:：·|]\s*/, "").trim(), ...trailing.map(plainText)]
    .filter(Boolean).join("\n").trim();
  return { title, href, description };
}

function MarkdownResourceList({ ordered, node, children, className, ...props }: ListProps & { ordered: boolean }) {
  const listItems = node ? nonEmpty(node.children) : [];
  const results = listItems.map((item) => item.type === "element" && item.tagName === "li" ? searchResult(item) : undefined);
  if (results.length < 2 || results.some((item) => !item)) {
    const Tag = ordered ? "ol" : "ul";
    return <Tag {...props} data-streamdown={ordered ? "ordered-list" : "unordered-list"}
      className={cn("list-inside whitespace-normal [li_&]:pl-6", ordered ? "list-decimal" : "list-disc", className)}>{children}</Tag>;
  }
  const items = results as SearchResult[];
  const domains = [...new Set(items.map((item) => new URL(item.href).hostname.replace(/^www\./, "")))];
  return (
    <section className="message-search-results" aria-label="相关参考资料">
      <header className="message-search-results-heading"><Globe aria-hidden="true" /><strong>相关参考资料</strong></header>
      <ul className="message-search-results-list">
        {items.map((item, index) => (
          <li className="message-search-result" key={`${index}:${item.href}`}>
            <LinkSiteIcon href={item.href} />
            <div className="message-search-result-body">
              <MessageLink href={item.href} variant="result">{item.title}</MessageLink>
              {item.description ? <p className="message-search-result-description">{item.description}</p> : null}
            </div>
          </li>
        ))}
      </ul>
      <footer className="message-search-results-footer"><span title={domains.join("、")}>来源：{domains.join("、")}</span><span>共 {items.length} 个链接</span></footer>
    </section>
  );
}

export const MarkdownOrderedList = (props: ListProps) => <MarkdownResourceList {...props} ordered />;
export const MarkdownUnorderedList = (props: ListProps) => <MarkdownResourceList {...props} ordered={false} />;

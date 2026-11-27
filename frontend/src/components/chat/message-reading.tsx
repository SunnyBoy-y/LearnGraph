import { ChevronDown } from "lucide-react";
import { Children, createContext, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type Ref } from "react";
import "./message-reading.css";

/** Rich documents rendered here contribute to the reply instead of rendering their own TOC. */
export const MessageReadingContext = createContext(false);

const headingSelector = ".message-text-block :is(h1,h2,h3,h4,h5,h6), [data-outline-heading]";
const longReplyLength = 450;
type Heading = { target: HTMLElement; title: string; level: number };

function readHeadings(body: HTMLElement): Heading[] {
  return Array.from(body.querySelectorAll<HTMLElement>(headingSelector)).flatMap(target => {
    if (target.closest('[hidden], [aria-hidden="true"], details:not([open]), .katex, .rich-doc__toc')) return [];
    // Ignore decorative text, icons and copies of formula markup used for accessibility.
    const label = target.cloneNode(true) as HTMLElement;
    label.querySelectorAll('[aria-hidden="true"], svg, .katex-html').forEach(node => node.remove());
    const title = (label.textContent ?? "").replace(/\s+/g, " ").trim();
    return title ? [{ target, title, level: Number(target.tagName.slice(1)) }] : [];
  });
}

export function MessageReading({ process, children, answerRef }: {
  process?: ReactNode;
  children: ReactNode;
  answerRef?: Ref<HTMLDivElement>;
}) {
  const root = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const outline = useRef<HTMLElement>(null);
  const panelId = useId();
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [active, setActive] = useState<HTMLElement>();

  useLayoutEffect(() => {
    const node = body.current;
    if (!node) return;
    let lastHeadingCount = 0;
    const collect = () => {
      const found = readHeadings(node);
      lastHeadingCount = found.length;
      const next = found.length >= 3 || (found.length === 2 && (
        (node.textContent ?? "").trim().length >= longReplyLength || node.offsetHeight >= 380
      )) ? found : [];
      setHeadings(previous => previous.length === next.length && previous.every((item, i) =>
        item.target === next[i].target && item.title === next[i].title && item.level === next[i].level) ? previous : next);
      if (!next.length) setExpanded(false);
    };
    collect();
    // One update per frame across streamed patches and lazy/hydrated renderers.
    let frame = 0;
    const schedule = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; collect(); }); };
    const observer = new MutationObserver(schedule);
    observer.observe(node, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["hidden", "aria-hidden", "open"] });
    // Resize only affects the 2-heading height-threshold fallback; skip DOM
    // scans on resize for all other replies.
    const resize = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(() => {
      if (lastHeadingCount === 2) schedule();
    });
    resize?.observe(node);
    return () => { observer.disconnect(); resize?.disconnect(); cancelAnimationFrame(frame); };
  }, []);

  useEffect(() => {
    if (!expanded || !root.current) return;
    const row = root.current;
    const scroll = row.closest<HTMLElement>("[data-conversation-scroll]");
    const measure = () => {
      const top = scroll ? scroll.getBoundingClientRect().top + scroll.clientTop : 0;
      const bottom = scroll ? top + scroll.clientHeight : window.innerHeight;
      const bounds = row.getBoundingClientRect();
      const trigger = button.current?.getBoundingClientRect();
      // Keep the panel inside the scroller even when opened near its bottom.
      if (bounds.height > 0 && bottom > top && (bounds.bottom < top + 44 || bounds.top >= bottom)) {
        setExpanded(false);
        return;
      }
      if (trigger && outline.current && bottom > top) {
        const below = bottom - trigger.bottom - 8;
        const above = trigger.top - top - 8;
        const opensUp = below < 160 && above > below;
        outline.current.dataset.side = opensUp ? "top" : "bottom";
        outline.current.style.setProperty("--outline-panel-room", `${Math.max(0, opensUp ? above : below)}px`);
      }
      const readingLine = top + 62;
      let current = headings[0]?.target;
      for (const { target } of headings) {
        if (target.getBoundingClientRect().top <= readingLine) current = target;
      }
      setActive(current);
    };
    measure();
    let frame = 0;
    const schedule = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; measure(); }); };
    const scrollTarget = scroll ?? window;
    scrollTarget.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !outline.current?.contains(event.target)) setExpanded(false);
    };
    document.addEventListener("pointerdown", outside);
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && event.target instanceof Node && outline.current?.contains(event.target)) {
        event.preventDefault();
        setExpanded(false);
        button.current?.focus({ preventScroll: true });
      }
    };
    document.addEventListener("keydown", escape);
    return () => {
      scrollTarget.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
      cancelAnimationFrame(frame);
    };
  }, [expanded, headings]);

  const jump = (target: HTMLElement) => {
    if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
    target.focus({ preventScroll: true });
    const behavior = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth";
    const scroll = root.current?.closest<HTMLElement>("[data-conversation-scroll]");
    if (scroll) {
      scroll.scrollTo?.({ top: scroll.scrollTop + target.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 54, behavior });
    } else {
      target.scrollIntoView?.({ block: "start", behavior });
    }
    setExpanded(false);
  };
  const baseLevel = Math.min(...headings.map(item => item.level));
  return <MessageReadingContext.Provider value={true}>
    <div className="message-reading" ref={root}>
      {headings.length > 0 && <div className="message-outline-track">
        <nav className="rich-doc__toc message-outline" aria-label="回复目录" ref={outline}>
          <button className="message-outline__trigger" type="button" aria-expanded={expanded} aria-controls={panelId}
            ref={button} onClick={() => setExpanded(value => !value)}>
            目录 <ChevronDown size={13} aria-hidden="true" />
          </button>
          <ol id={panelId} className="message-outline__panel" hidden={!expanded}>
            {headings.map(({ target, title, level }, index) => <li key={index} style={{ "--outline-depth": Math.min(level - baseLevel, 3) } as CSSProperties}>
              <button type="button" aria-current={target === (active ?? headings[0].target) ? "location" : undefined}
                onClick={() => jump(target)}>{title}</button>
            </li>)}
          </ol>
        </nav>
      </div>}
      {Children.count(process) > 0 && <div className="message-reading__process">{process}</div>}
      <div className="message-reading__body" ref={node => {
        body.current = node;
        if (typeof answerRef === "function") answerRef(node);
        else if (answerRef) answerRef.current = node;
      }}>{children}</div>
    </div>
  </MessageReadingContext.Provider>;
}

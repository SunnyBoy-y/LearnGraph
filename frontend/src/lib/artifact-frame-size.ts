import { useEffect, useRef, useState, type RefObject } from "react";

export type HeightMode = "auto" | "aspect-ratio" | "fixed" | "bounded";

export interface ArtifactFramePolicy {
  mode: HeightMode;
  initialHeight: number;
  minHeight: number;
  maxHeight: number;
  aspectRatio?: string;
}

export interface ArtifactFrameSizeOptions {
  mode?: HeightMode;
  minHeight?: number;
  maxHeight?: number;
  /** Changes when streaming finishes or document content updates, triggering re-measure. */
  measureKey?: string | number | boolean;
}

const VALID_HEIGHT_MODES = new Set<HeightMode>([
  "auto",
  "aspect-ratio",
  "fixed",
  "bounded",
]);

const DEFAULT_MIN_HEIGHT = 96;
const DEFAULT_MAX_HEIGHT = 8192;
const DEFAULT_INITIAL_HEIGHT = 320;
const DEFAULT_BOUNDED_MAX_HEIGHT = 560;

export function normalizeHeightMode(value: unknown): HeightMode | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().toLowerCase() as HeightMode;
  return VALID_HEIGHT_MODES.has(normalized) ? normalized : undefined;
}

export function normalizeAspectRatio(value: unknown): string | undefined {
  if (typeof value === "number" && Number.isFinite(value) && value > 0.1 && value < 20) {
    return String(Number(value.toFixed(4)));
  }
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  const ratioMatch = /^(\d+(?:\.\d+)?)\s*(?:\/|:)\s*(\d+(?:\.\d+)?)$/.exec(trimmed);
  if (ratioMatch) {
    const w = Number(ratioMatch[1]);
    const h = Number(ratioMatch[2]);
    if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0 && w / h > 0.1 && w / h < 20) {
      return `${w} / ${h}`;
    }
    return undefined;
  }
  const numeric = Number(trimmed);
  if (Number.isFinite(numeric) && numeric > 0.1 && numeric < 20) {
    return String(Number(numeric.toFixed(4)));
  }
  return undefined;
}

function clampPositiveHeight(value: unknown, fallback: number, min = 120, max = 1200): number {
  return typeof value === "number" && Number.isFinite(value) && value >= min
    ? Math.min(max, Math.round(value))
    : fallback;
}

/**
 * Resolve the sizing strategy for a Stream UI / HTML artifact frame.
 *
 * Interactive HTML defaults to `auto` even when legacy `preferred_height` /
 * `viewport.max_height` hints exist; `preferred_height` acts as the initial
 * placeholder height before the first live measurement arrives (or as the
 * explicit viewport height when `mode === "fixed"`).
 */
export function resolveArtifactFramePolicy(
  data: Record<string, unknown> | undefined,
  defaults: {
    defaultMode?: HeightMode;
    defaultInitialHeight?: number;
    defaultBoundedMaxHeight?: number;
    defaultAspectRatio?: string;
  } = {},
): ArtifactFramePolicy {
  const viewport =
    data?.viewport && typeof data.viewport === "object" && !Array.isArray(data.viewport)
      ? (data.viewport as Record<string, unknown>)
      : undefined;

  const explicitMode =
    normalizeHeightMode(data?.height_mode) ??
    normalizeHeightMode(viewport?.height_mode) ??
    normalizeHeightMode(viewport?.mode);
  const mode: HeightMode = explicitMode ?? defaults.defaultMode ?? "auto";

  const preferredRaw = data?.preferred_height ?? viewport?.preferred_height;
  const initialFallback = defaults.defaultInitialHeight ?? DEFAULT_INITIAL_HEIGHT;
  const initialHeight = clampPositiveHeight(preferredRaw, initialFallback, 120, 960);

  if (mode === "fixed") {
    return {
      mode,
      initialHeight,
      minHeight: initialHeight,
      maxHeight: initialHeight,
    };
  }

  if (mode === "aspect-ratio") {
    const aspectRatio =
      normalizeAspectRatio(data?.aspect_ratio) ??
      normalizeAspectRatio(viewport?.aspect_ratio) ??
      defaults.defaultAspectRatio ??
      "16 / 9";
    return {
      mode,
      initialHeight,
      minHeight: DEFAULT_MIN_HEIGHT,
      maxHeight: DEFAULT_MAX_HEIGHT,
      aspectRatio,
    };
  }

  if (mode === "bounded") {
    const maxRaw = data?.max_height ?? viewport?.max_height;
    const boundedMax = clampPositiveHeight(
      maxRaw,
      defaults.defaultBoundedMaxHeight ?? DEFAULT_BOUNDED_MAX_HEIGHT,
      160,
      1600,
    );
    return {
      mode,
      initialHeight: Math.min(initialHeight, boundedMax),
      minHeight: DEFAULT_MIN_HEIGHT,
      maxHeight: boundedMax,
    };
  }

  return {
    mode: "auto",
    initialHeight,
    minHeight: DEFAULT_MIN_HEIGHT,
    maxHeight: DEFAULT_MAX_HEIGHT,
  };
}

/**
 * Injected into opaque-origin srcDoc previews that use auto/bounded height.
 *
 * Overrides viewport-coupled html/body height rules while inline (not expanded)
 * so the body shrinks naturally when content narrows or collapses.
 */
export const artifactAutoSizeStyle = `<style data-lg-auto-size>html:not([data-lg-expanded="true"]),html:not([data-lg-expanded="true"]) body{height:auto!important;min-height:0!important}</style>`;

/**
 * Runs inside the opaque-origin srcDoc iframe.
 *
 * Measures the true rendered content height rather than viewport-constrained
 * `documentElement.scrollHeight`, handles inner `100vh` wrappers, avoids
 * `MutationObserver({ attributes: true })` so 60fps animations never starve or
 * spam measurements, and responds to host re-measure requests on resize /
 * sidebar toggle / fullscreen exit.
 */
export const artifactSizeShim = `<script>(function(){
  var send=parent.postMessage.bind(parent),delay=setTimeout.bind(window),cancel=clearTimeout.bind(window);
  var raf=window.requestAnimationFrame?window.requestAnimationFrame.bind(window):function(fn){return delay(fn,16);};
  var SKIP={SCRIPT:1,STYLE:1,LINK:1,META:1,TITLE:1,NOSCRIPT:1,TEMPLATE:1};
  var LEAF={CANVAS:1,SVG:1,IMG:1,VIDEO:1,AUDIO:1,IFRAME:1,OBJECT:1};
  var timer=0,maxTimer=0,last='',observer,body;
  function num(v){var n=parseFloat(v);return isFinite(n)?n:0;}
  function visibleFlowChildren(parent){
    var out=[];
    for(var i=0;i<parent.children.length;i++){
      var el=parent.children[i];
      if(SKIP[el.tagName])continue;
      var st=getComputedStyle(el);
      if(st.display==='none'||st.position==='fixed')continue;
      out.push({el:el,st:st});
    }
    return out;
  }
  function measureContainer(container,viewport,depth){
    var cRect=container.getBoundingClientRect();
    var cSt=getComputedStyle(container);
    var padTop=num(cSt.paddingTop)+num(cSt.borderTopWidth);
    var padBottom=num(cSt.paddingBottom)+num(cSt.borderBottomWidth);
    var children=visibleFlowChildren(container);
    if(!children.length){
      var range=document.createRange();
      range.selectNodeContents(container);
      var rRect=range.getBoundingClientRect();
      if(rRect.height>0){
        return {height:rRect.height+padTop+padBottom,bottom:rRect.bottom+window.scrollY+padBottom,stretched:false};
      }
      return {height:cRect.height,bottom:cRect.bottom+window.scrollY,stretched:false};
    }
    // Single viewport-stretched wrapper (e.g. .app { min-height: 100vh; justify-content: center }):
    // unwrap its inner content span so the iframe can shrink instead of locking to 100vh.
    if(depth<2&&children.length===1){
      var only=children[0].el;
      var onlySt=children[0].st;
      var onlyRect=only.getBoundingClientRect();
      if(!LEAF[only.tagName]&&viewport>0&&onlyRect.height>=viewport-2&&onlySt.position!=='absolute'){
        var inner=measureContainer(only,viewport,depth+1);
        var marginY=num(onlySt.marginTop)+num(onlySt.marginBottom);
        if(inner.height>0&&inner.height<onlyRect.height-6){
          return {
            height:inner.height+marginY+padTop+padBottom,
            bottom:cRect.top+window.scrollY+inner.height+marginY+padTop+padBottom,
            stretched:true
          };
        }
      }
    }
    var minTop=Infinity,maxBottom=-Infinity,hasFlow=false;
    for(var i=0;i<children.length;i++){
      var item=children[i];
      var rect=item.el.getBoundingClientRect();
      if(rect.width===0&&rect.height===0&&item.el.children.length===0)continue;
      var mTop=num(item.st.marginTop);
      var mBottom=num(item.st.marginBottom);
      var top=rect.top+window.scrollY-mTop;
      var bottom=rect.bottom+window.scrollY+mBottom;
      if(top<minTop)minTop=top;
      if(bottom>maxBottom)maxBottom=bottom;
      hasFlow=true;
    }
    if(!hasFlow)return {height:0,bottom:0,stretched:false};
    var span=(maxBottom-minTop)+padTop+padBottom;
    var fromTop=maxBottom+padBottom;
    return {height:span,bottom:fromTop,stretched:false};
  }
  function measureHeight(){
    if(!body||!window.innerWidth)return 0;
    if(document.documentElement.dataset.lgExpanded==='true')return 0;
    var viewport=document.documentElement.clientHeight||window.innerHeight||0;
    var bSt=getComputedStyle(body);
    var mBottom=num(bSt.marginBottom);
    var mTop=num(bSt.marginTop);
    var measured=measureContainer(body,viewport,0);
    // Pure fixed-position/full-viewport canvas apps with zero flow height keep initialHeight.
    if(measured.height<24&&measured.bottom<24)return 0;
    var candidate=measured.stretched?measured.height+mTop+mBottom:Math.max(measured.bottom+mBottom,measured.height+mTop+mBottom);
    // Include genuine document overflow beyond the viewport, except when unwrapping a 100vh shell.
    if(!measured.stretched){
      var docScroll=document.documentElement.scrollHeight;
      if(docScroll>viewport+2&&docScroll>candidate)candidate=docScroll;
    }
    return Math.ceil(candidate);
  }
  function report(force){
    var height=measureHeight();
    if(height<1)return;
    var viewport=document.documentElement.clientHeight||0;
    if(!force&&Math.abs(height-viewport)<=2)return;
    var key=window.innerWidth+':'+height;
    if(!force&&last===key)return;
    last=key;
    send({kind:'lg.artifact-size',width:window.innerWidth,height:height},'*');
  }
  function runReport(){
    cancel(timer);timer=0;
    cancel(maxTimer);maxTimer=0;
    report(false);
  }
  function schedule(){
    cancel(timer);
    timer=delay(runReport,50);
    if(!maxTimer)maxTimer=delay(runReport,160);
  }
  function observeTargets(){
    if(!observer||!body)return;
    observer.disconnect();
    observer.observe(body);
    var kids=visibleFlowChildren(body);
    for(var i=0;i<kids.length;i++){
      observer.observe(kids[i].el);
    }
    if(kids.length===1&&!LEAF[kids[0].el.tagName]){
      var inner=visibleFlowChildren(kids[0].el);
      for(var j=0;j<inner.length;j++)observer.observe(inner[j].el);
    }
    schedule();
  }
  function start(){
    body=document.body;if(!body)return;
    if(typeof ResizeObserver==='function')observer=new ResizeObserver(schedule);
    if(typeof MutationObserver==='function'){
      new MutationObserver(function(records){
        var reobserve=false;
        for(var i=0;i<records.length;i++){
          if(records[i].type==='childList'&&(records[i].target===body||records[i].target.parentElement===body)){
            reobserve=true;break;
          }
        }
        if(reobserve)observeTargets();else schedule();
      }).observe(body,{childList:true,subtree:true});
    }
    window.addEventListener('resize',schedule);
    window.addEventListener('load',schedule,true);
    window.addEventListener('toggle',schedule,true);
    window.addEventListener('transitionend',schedule,true);
    if(document.fonts&&document.fonts.ready)document.fonts.ready.then(schedule);
    window.addEventListener('message',function(event){
      var data=event.data;
      if(event.source!==parent||!data||data.lg!==1)return;
      if(data.kind==='lg.artifact-state'){
        document.documentElement.dataset.lgExpanded=data.expanded?'true':'false';
        if(!data.expanded){last='';raf(function(){report(true);});delay(function(){report(false);},90);}
      }else if(data.kind==='lg.artifact-measure'){
        last='';raf(function(){report(true);});
      }
    });
    observeTargets();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();</script>`;

export function readArtifactFrameSize(
  event: MessageEvent,
  frame: HTMLIFrameElement | null,
  bounds?: { minHeight?: number; maxHeight?: number },
): number | null {
  if (!frame?.contentWindow || event.source !== frame.contentWindow || event.origin !== "null") {
    return null;
  }
  const data = event.data;
  if (
    data?.kind !== "lg.artifact-size" ||
    typeof data.height !== "number" ||
    !Number.isFinite(data.height) ||
    data.height <= 0 ||
    typeof data.width !== "number" ||
    !Number.isFinite(data.width) ||
    Math.abs(data.width - frame.clientWidth) > 3
  ) {
    return null;
  }
  const minHeight = bounds?.minHeight ?? DEFAULT_MIN_HEIGHT;
  const maxHeight = bounds?.maxHeight ?? DEFAULT_MAX_HEIGHT;
  return Math.min(maxHeight, Math.max(minHeight, Math.ceil(data.height)));
}

function postFrameMessage(
  frame: HTMLIFrameElement | null,
  payload: Record<string, unknown>,
): void {
  try {
    frame?.contentWindow?.postMessage({ lg: 1, ...payload }, "*");
  } catch {
    // Frame may have detached during navigation or unmount.
  }
}

export function useArtifactFrameHeight(
  enabled: boolean,
  expanded: boolean,
  frameRef: RefObject<HTMLIFrameElement | null>,
  options: ArtifactFrameSizeOptions = {},
): number | undefined {
  const mode = options.mode ?? "auto";
  const minHeight = options.minHeight ?? DEFAULT_MIN_HEIGHT;
  const maxHeight =
    options.maxHeight ??
    (mode === "bounded" ? DEFAULT_BOUNDED_MAX_HEIGHT : DEFAULT_MAX_HEIGHT);
  const measureKey = options.measureKey;
  const active = enabled && (mode === "auto" || mode === "bounded");

  const [height, setHeight] = useState<number>();
  const currentHeightRef = useRef<number | undefined>(undefined);
  const prevExpandedRef = useRef<boolean>(expanded);

  useEffect(() => {
    currentHeightRef.current = height;
  }, [height]);

  // Synchronize expanded/fullscreen state with the iframe and force a fresh
  // re-measurement when exiting fullscreen back to inline layout.
  useEffect(() => {
    const frame = frameRef.current;
    if (active && frame) {
      postFrameMessage(frame, { kind: "lg.artifact-state", expanded });
    }
    let rafId = 0;
    let timerId = 0;
    if (active && prevExpandedRef.current && !expanded && frame) {
      rafId = window.requestAnimationFrame(() => {
        postFrameMessage(frameRef.current, { kind: "lg.artifact-measure" });
      });
      timerId = window.setTimeout(() => {
        postFrameMessage(frameRef.current, { kind: "lg.artifact-measure" });
      }, 100);
    }
    prevExpandedRef.current = expanded;
    return () => {
      if (rafId) window.cancelAnimationFrame(rafId);
      if (timerId) window.clearTimeout(timerId);
    };
  }, [active, expanded, frameRef]);

  useEffect(() => {
    if (!active || expanded) return;
    let commitTimer = 0;
    let maxCommitTimer = 0;
    let resizeTimer = 0;
    let pending: { next: number; width: number } | null = null;
    let trackedWidth = -1;
    let history: number[] = [];
    let consecutiveGrowths = 0;
    let lastCommitAt = 0;

    const resetLoopState = (nextWidth: number) => {
      trackedWidth = nextWidth;
      history = [];
      consecutiveGrowths = 0;
    };

    const requestMeasure = () => {
      postFrameMessage(frameRef.current, { kind: "lg.artifact-measure" });
    };

    const flush = () => {
      window.clearTimeout(commitTimer);
      commitTimer = 0;
      window.clearTimeout(maxCommitTimer);
      maxCommitTimer = 0;
      const item = pending;
      pending = null;
      const frame = frameRef.current;
      if (!item || !frame || Math.abs(item.width - frame.clientWidth) > 3) return;

      const now = performance.now();
      if (trackedWidth !== frame.clientWidth || now - lastCommitAt > 350) {
        resetLoopState(frame.clientWidth);
        if (currentHeightRef.current !== undefined) {
          history = [currentHeightRef.current];
        }
      }

      const current = currentHeightRef.current;
      if (current !== undefined && Math.abs(current - item.next) <= 2) {
        return;
      }

      // Guard against rapid A -> B -> A feedback loops (< 350ms at the same width)
      // while still allowing legitimate user expand/collapse toggles afterwards.
      if (history.length >= 2 && history.at(-2) === item.next) {
        const stable = Math.max(history.at(-1) ?? item.next, item.next);
        if (current !== stable) {
          currentHeightRef.current = stable;
          setHeight(stable);
        }
        return;
      }

      // Guard against runaway viewport-coupled growth at a fixed width.
      const prev = history.at(-1);
      if (prev !== undefined && item.next > prev) {
        consecutiveGrowths += 1;
        if (consecutiveGrowths >= 6) return;
      } else {
        consecutiveGrowths = 0;
      }

      history.push(item.next);
      if (history.length > 8) history.shift();
      lastCommitAt = now;
      currentHeightRef.current = item.next;
      setHeight(item.next);
    };

    const onMessage = (event: MessageEvent) => {
      const frame = frameRef.current;
      const next = readArtifactFrameSize(event, frame, { minHeight, maxHeight });
      if (!frame || next === null) return;
      pending = { next, width: frame.clientWidth };
      window.clearTimeout(commitTimer);
      commitTimer = window.setTimeout(flush, 45);
      if (!maxCommitTimer) {
        maxCommitTimer = window.setTimeout(flush, 150);
      }
    };

    window.addEventListener("message", onMessage);

    // Watch host iframe width changes (window resize, left/right sidebar toggle
    // or drag) so the iframe always re-measures at the settled width.
    const frame = frameRef.current;
    let lastObservedWidth = frame?.clientWidth ?? -1;
    let resizeObserver: ResizeObserver | null = null;
    if (frame && typeof ResizeObserver === "function") {
      resizeObserver = new ResizeObserver(() => {
        const currentWidth = frame.clientWidth;
        if (!currentWidth || Math.abs(currentWidth - lastObservedWidth) <= 1) return;
        lastObservedWidth = currentWidth;
        resetLoopState(currentWidth);
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(requestMeasure, 55);
      });
      resizeObserver.observe(frame);
    }

    const onLoad = () => {
      postFrameMessage(frameRef.current, { kind: "lg.artifact-state", expanded: false });
      requestMeasure();
    };
    frame?.addEventListener("load", onLoad);

    // Also request measurement when measureKey changes (e.g. streaming finished).
    const settleTimer = window.setTimeout(requestMeasure, 40);

    return () => {
      window.removeEventListener("message", onMessage);
      frame?.removeEventListener("load", onLoad);
      resizeObserver?.disconnect();
      window.clearTimeout(commitTimer);
      window.clearTimeout(maxCommitTimer);
      window.clearTimeout(resizeTimer);
      window.clearTimeout(settleTimer);
    };
  }, [active, expanded, frameRef, minHeight, maxHeight, measureKey]);

  return active ? height : undefined;
}

import { sandboxRuntimeShimInlineTag } from "./sandbox-runtime-shim";
import { subappClientInlineTag } from "./subapp-client-shim";

export const SANDBOXED_HTML_PREVIEW_CSP = [
  "default-src 'none'",
  "img-src data: blob: https: http:",
  "media-src data: blob: https: http:",
  "font-src data: https: http:",
  "style-src 'unsafe-inline' https: http:",
  "script-src 'unsafe-inline' 'unsafe-eval' blob: https: http:",
  "worker-src blob:",
  "connect-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

const DEFAULT_PREVIEW_STYLE =
  "html,body{margin:0;min-height:100%;font-family:system-ui,sans-serif;color:#171717;background:#fff}*{box-sizing:border-box}";

/** Inline control channel used by the host preview pause controller. */
const PREVIEW_CONTROL_SHIM = `<script>(function(){
'use strict';
var paused=false, nextId=1, timers=new Map(), frames=new Map(), contexts=new Set(), animations=[], playing=new Set();
var nativeTimeout=window.setTimeout.bind(window), nativeClear=window.clearTimeout.bind(window);
var nativeFrame=window.requestAnimationFrame.bind(window), nativeCancel=window.cancelAnimationFrame.bind(window);
function invoke(callback,args){if(typeof callback==='function')callback.apply(window,args);else (0,eval)(String(callback));}
function scheduleTimer(id,entry){entry.due=performance.now()+entry.remaining;entry.native=nativeTimeout(function(){if(paused)return;entry.remaining=entry.delay;if(!entry.repeat)timers.delete(id);invoke(entry.callback,entry.args);if(entry.repeat&&timers.has(id)&&!paused)scheduleTimer(id,entry);},entry.remaining);}
function addTimer(callback,delay,repeat,args){var id=nextId++,entry={callback:callback,args:args,delay:Math.max(0,Number(delay)||0),remaining:Math.max(0,Number(delay)||0),repeat:repeat,native:null,due:0};timers.set(id,entry);if(!paused)scheduleTimer(id,entry);return id;}
window.setTimeout=function(callback,delay){return addTimer(callback,delay,false,Array.prototype.slice.call(arguments,2));};
window.setInterval=function(callback,delay){return addTimer(callback,delay,true,Array.prototype.slice.call(arguments,2));};
window.clearTimeout=window.clearInterval=function(id){var entry=timers.get(id);if(entry){nativeClear(entry.native);timers.delete(id);}else nativeClear(id);};
function scheduleFrame(id,callback){var nativeId=nativeFrame(function(time){if(paused)return;frames.delete(id);callback(time);});frames.set(id,{callback:callback,native:nativeId});}
window.requestAnimationFrame=function(callback){var id=nextId++;frames.set(id,{callback:callback,native:null});if(!paused)scheduleFrame(id,callback);return id;};
window.cancelAnimationFrame=function(id){var entry=frames.get(id);if(entry){nativeCancel(entry.native);frames.delete(id);}};
['AudioContext','webkitAudioContext'].forEach(function(name){var Original=window[name];if(!Original)return;window[name]=new Proxy(Original,{construct:function(target,args){var context=Reflect.construct(target,args);contexts.add(context);if(paused)void context.suspend().catch(function(){});return context;}});});
function holdMedia(){document.querySelectorAll('audio,video').forEach(function(media){if(!media.paused&&!media.ended){playing.add(media);media.pause();}});}
document.addEventListener('play',function(event){if(paused&&event.target&&event.target.pause){playing.add(event.target);event.target.pause();}},true);
function setPaused(next){
  if(paused===next)return;paused=next;
  if(next){
    timers.forEach(function(entry){nativeClear(entry.native);entry.remaining=Math.max(0,entry.due-performance.now());});
    frames.forEach(function(entry){nativeCancel(entry.native);});
    holdMedia();contexts.forEach(function(context){if(context.state==='running'){context.__lgResume=true;void context.suspend().catch(function(){});}});
    animations=document.getAnimations?document.getAnimations().filter(function(animation){return animation.playState==='running';}):[];animations.forEach(function(animation){animation.pause();});
  }else{
    timers.forEach(function(entry,id){scheduleTimer(id,entry);});frames.forEach(function(entry,id){scheduleFrame(id,entry.callback);});
    playing.forEach(function(media){void media.play().catch(function(){});});playing.clear();
    contexts.forEach(function(context){if(context.__lgResume){context.__lgResume=false;void context.resume().catch(function(){});}});
    animations.forEach(function(animation){try{animation.play();}catch(e){}});animations=[];
  }
  document.documentElement.dataset.lgPaused=next?'true':'false';
  window.dispatchEvent(new CustomEvent('learngraph:preview-control',{detail:{action:next?'pause':'resume'}}));
}
window.addEventListener('message',function(event){var data=event.data;if(event.source!==parent||!data||data.lg!==1||data.kind!=='preview.control')return;if(data.action==='pause'||data.action==='resume'){setPaused(data.action==='pause');parent.postMessage({lg:1,kind:'preview.control.ack',action:data.action,requestId:data.requestId},'*');}});
})();</script>`;

function isAllowedEmbeddedUrl(value: string) {
  return value.startsWith("#") || value.startsWith("blob:") || value.startsWith("data:");
}

/** http(s) absolute or protocol-relative network URL (static assets load directly). */
function isNetworkUrl(value: string) {
  return /^(https?:)?\/\//i.test(value);
}

/** Resource URL that may load directly: embedded or network. */
function isAllowedResourceUrl(value: string) {
  return isAllowedEmbeddedUrl(value) || isNetworkUrl(value);
}

export interface SandboxedHtmlPreviewOptions {
  /**
   * Inject the browser-sandbox runtime shim (`window.__lg` + `fetch` relay).
   * The shim talks to the host bridge over postMessage (`lg:1` protocol) so
   * sandbox code can read multi-file bundle paths (`vfs.read`) and reach the
   * approval-free network relay (`net.fetch`). Network still never leaves the
   * iframe directly — `connect-src 'none'` is unchanged.
   */
  runtimeShim?: boolean
  /**
   * Inject the bidirectional subapp client SDK (`window.__lgSubapp`) for
   * subapp_mode srcDoc previews. Must be paired with runtimeShim on the bundle
   * path; on the gateway path the SDK is a same-origin static file instead.
   */
  subappClient?: boolean
}

/** Build an opaque-origin srcDoc whose executable policy is owned by the host. */
export function sandboxedHtmlPreviewDocument(
  html: string,
  options: SandboxedHtmlPreviewOptions = {},
): string {
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, "text/html");
  // Navigation / embedding / form / base markup is always stripped — the
  // sandbox never navigates the top window, never embeds third-party frames
  // and never lets the preview change the document base. External CSS links
  // (rel=stylesheet) are kept so previews can load stylesheets over the network.
  doc.querySelectorAll("iframe, object, embed, form, base").forEach((node) => node.remove());
  doc.querySelectorAll("link").forEach((node) => {
    const rel = node.getAttribute("rel")?.trim().toLowerCase();
    const href = node.getAttribute("href")?.trim();
    if (rel !== "stylesheet" || !href || !isAllowedResourceUrl(href)) {
      node.remove();
    }
  });
  doc.querySelectorAll("meta[http-equiv]").forEach((meta) => {
    const directive = meta.getAttribute("http-equiv")?.trim().toLowerCase();
    if (directive === "content-security-policy" || directive === "refresh") {
      meta.remove();
    }
  });

  doc.querySelectorAll<HTMLElement>("*").forEach((element) => {
    element.removeAttribute("srcdoc");
    for (const attributeName of ["src", "href", "xlink:href"]) {
      const value = element.getAttribute(attributeName)?.trim();
      if (!value) continue;
      // Keep embedded (data:/blob:/#) and network (http(s)) references so
      // previews can load images, fonts, media and scripts over the network;
      // every other URL scheme is dropped.
      if (!isAllowedEmbeddedUrl(value) && !isNetworkUrl(value)) {
        element.removeAttribute(attributeName);
      }
    }
  });
  doc.querySelectorAll<HTMLScriptElement>("script[src]").forEach((script) => {
    const src = script.getAttribute("src")?.trim();
    if (!src || (!isAllowedEmbeddedUrl(src) && !isNetworkUrl(src))) {
      script.removeAttribute("src");
    }
  });
  doc.querySelectorAll("style").forEach((style) => {
    style.textContent = (style.textContent ?? "")
      // Keep network @imports (https://...), drop every other one.
      .replace(/@import\s+[^;]+;?/giu, (match) => (/(?:https?:)?\/\//i.test(match) ? match : ""))
      // Keep data:/blob:/# and network url() references (external images,
      // fonts, gradients); drop every other URL so nothing hits non-http
      // schemes.
      .replace(/url\(\s*['"]?([^)'"]*)\)/giu, (match, inner: string) => {
        const value = inner.trim();
        if (/^(https?:)?\/\//i.test(value) || /^(data:|blob:|#)/i.test(value)) return match;
        return "none";
      })
      .replace(/expression\s*\(/giu, "invalid(");
  });

  const shimTag = options.runtimeShim ? sandboxRuntimeShimInlineTag() : "";
  const clientTag = options.subappClient ? subappClientInlineTag() : "";
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${SANDBOXED_HTML_PREVIEW_CSP}"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${DEFAULT_PREVIEW_STYLE}</style>${PREVIEW_CONTROL_SHIM}${shimTag}${clientTag}${doc.head?.innerHTML ?? ""}</head><body>${doc.body?.innerHTML ?? ""}</body></html>`;
}

// Sync exact vector geometry from the same installed package that the Web UI imports.
// Run: node mobile/native/tools/sync-web-icons.mjs [--check]
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { createHash } from "node:crypto";

export const ICON_SOURCES = {
  Menu:"menu",Network:"network",Sliders:"sliders-horizontal",Microphone:"mic",ArrowUp:"arrow-up",
  Expand:"maximize-2",Collapse:"minimize-2",Target:"target",Globe:"globe",Chevron:"chevron-down",
  Edit:"pencil",Copy:"copy",More:"ellipsis",Plus:"plus",Close:"x",Search:"search",Folder:"folder",
  FolderPlus:"folder-plus",Trash:"trash-2",Download:"download",Settings:"settings",ListChecks:"list-checks",
  ChevronLeft:"chevron-left",ChevronRight:"chevron-right",ArrowDown:"arrow-down",LoaderCircle:"loader-circle",
  RefreshCcw:"refresh-ccw",MessageSquareText:"message-square-text",Files:"files",Package:"package",Brain:"brain",
  GraduationCap:"graduation-cap",CalendarDays:"calendar-days",Zap:"zap",Check:"check",CheckCheck:"check-check",
  CornerDownRight:"corner-down-right",Clock:"clock",ExternalLink:"external-link",Paperclip:"paperclip",
  Archive:"archive",LogOut:"log-out",Server:"server",Pin:"pin",Share2:"share-2",FolderOpen:"folder-open",
  GitBranch:"git-branch",Link:"link-2",FileText:"file-text",MessageCircle:"message-circle",ArrowUpRight:"arrow-up-right",Camera:"camera",
};
const root = fileURLToPath(new URL("../../../", import.meta.url));
const packageRoot = path.join(root,"frontend/node_modules/lucide-react");
const generated = path.join(root,"mobile/native/app/src/main/java/com/learngraph/mobile/ui/components/WebLucideGeometry.kt");
const license = path.join(root,"mobile/native/app/src/main/assets/licenses/lucide-react.txt");
const manifest = path.join(root,"mobile/native/tools/web-icon-source-manifest.json");
const num = value => { const n = Number(value); if (!Number.isFinite(n)) throw new Error("Non numeric SVG coordinate"); return n; };
const fmt = n => Number(n.toFixed(9)).toString();

export function svgNodeToPath([tag, a]) {
  const f = v => fmt(num(v));
  let d;
  switch(tag) {
    case "path": d=a.d; break;
    case "line": d=`M${f(a.x1)} ${f(a.y1)}L${f(a.x2)} ${f(a.y2)}`; break;
    case "polyline": case "polygon": d="M"+a.points.trim().replace(/\s+/g," L")+(tag==="polygon"?"Z":""); break;
    case "circle": case "ellipse": {
      const cx=num(a.cx),cy=num(a.cy),rx=num(tag==="circle"?a.r:a.rx),ry=num(tag==="circle"?a.r:a.ry);
      d=`M${fmt(cx-rx)} ${fmt(cy)}a${fmt(rx)} ${fmt(ry)} 0 1 0 ${fmt(2*rx)} 0a${fmt(rx)} ${fmt(ry)} 0 1 0 ${fmt(-2*rx)} 0`;break;
    }
    case "rect": {
      const x=num(a.x??0),y=num(a.y??0),w=num(a.width),h=num(a.height);
      const rx=Math.min(num(a.rx??a.ry??0),w/2),ry=Math.min(num(a.ry??a.rx??0),h/2);
      d=rx&&ry?`M${fmt(x+rx)} ${fmt(y)}H${fmt(x+w-rx)}A${fmt(rx)} ${fmt(ry)} 0 0 1 ${fmt(x+w)} ${fmt(y+ry)}V${fmt(y+h-ry)}A${fmt(rx)} ${fmt(ry)} 0 0 1 ${fmt(x+w-rx)} ${fmt(y+h)}H${fmt(x+rx)}A${fmt(rx)} ${fmt(ry)} 0 0 1 ${fmt(x)} ${fmt(y+h-ry)}V${fmt(y+ry)}A${fmt(rx)} ${fmt(ry)} 0 0 1 ${fmt(x+rx)} ${fmt(y)}Z`:`M${fmt(x)} ${fmt(y)}h${fmt(w)}v${fmt(h)}h${fmt(-w)}Z`;break;
    }
    default: throw new Error("Unsupported SVG node "+tag);
  }
  const allowed = new Set(["d","key","x","y","width","height","rx","ry","cx","cy","r","x1","y1","x2","y2","points","fill"]);
  for(const key of Object.keys(a)) if(!allowed.has(key)) throw new Error("SVG attribute needs explicit conversion: "+key);
  if(a.fill && !["none","currentColor"].includes(a.fill)) throw new Error("Unsupported fill");
  return {data:d,filled:a.fill==="currentColor"};
}

export async function collectGeometry() {
  const packageInfo=JSON.parse(await readFile(path.join(packageRoot,"package.json"),"utf8"));
  const shapes={}, evidence={version:packageInfo.version, icons:{}};
  for(const [name,source] of Object.entries(ICON_SOURCES)) {
    const modulePath=path.join(packageRoot,"dist/esm/icons",source+".mjs");
    const text=await readFile(modulePath,"utf8");
    const {__iconNode}=await import(pathToFileURL(modulePath).href);
    if(!__iconNode) throw new Error("No exported icon geometry: "+source);
    shapes[name]=__iconNode.map(svgNodeToPath);
    evidence.icons[name]={source:"frontend/node_modules/lucide-react/dist/esm/icons/"+source+".mjs",sha256:createHash("sha256").update(text).digest("hex")};
  }
  const voice=await readFile(path.join(root,"frontend/src/features/voice/voice-orb.tsx"),"utf8");
  const width=Number(voice.match(/const VOICE_WAVE_BAR_WIDTH = ([\d.]+);/)[1]);
  const center=Number(voice.match(/const VOICE_WAVE_CENTER = ([\d.]+);/)[1]);
  const bars=voice.match(/const VOICE_WAVE_BARS = \[([\s\S]*?)\] as const;/)[1];
  shapes.Wave=[...bars.matchAll(/\{ height: ([\d.]+), x: ([\d.]+) \}/g)].map(([,height,x])=>svgNodeToPath(["rect",{height,width,x,y:center-Number(height)/2,rx:width/2,fill:"currentColor"}]));
  if(shapes.Wave.length!==5) throw new Error("Voice glyph format changed; inspect the Web source.");
  evidence.icons.Wave={source:"frontend/src/features/voice/voice-orb.tsx:VOICE_WAVE_BARS",sha256:createHash("sha256").update(bars+"|"+width+"|"+center).digest("hex")};
  return {shapes,evidence};
}

export function kotlinFor({shapes,evidence}) {
  const keys=Object.keys(shapes);
  return `// GENERATED by mobile/native/tools/sync-web-icons.mjs. Do not hand-edit geometry.
// lucide-react ${evidence.version}: ISC + upstream Feather MIT. Full notices ship in assets/licenses.
// Wave is copied from the application's VoiceWaveGlyph, not substituted with a Lucide waveform.
package com.learngraph.mobile.ui.components

enum class WebIcon { ${keys.join(", ")} }
internal data class WebIconPath(val data: String, val filled: Boolean = false)
internal val WEB_ICON_PATHS: Map<WebIcon, List<WebIconPath>> = mapOf(
${keys.map(name=>`    WebIcon.${name} to listOf(\n${shapes[name].map(node=>`        WebIconPath(${JSON.stringify(node.data)}, ${node.filled}),`).join("\n")}\n    ),`).join("\n")}
)
`;
}

async function main() {
  const data=await collectGeometry();
  const outputs=[[generated,kotlinFor(data)],[manifest,JSON.stringify(data.evidence,null,2)+"\n"],[license,await readFile(path.join(packageRoot,"LICENSE"),"utf8")]];
  for(const [file,content] of outputs) {
    if(process.argv.includes("--check")) {
      const actual=await readFile(file,"utf8");
      if(actual.replace(/\r\n/g,"\n")!==content.replace(/\r\n/g,"\n")) throw new Error(path.relative(root,file)+" is stale; rerun the sync script");
    } else { await mkdir(path.dirname(file),{recursive:true});await writeFile(file,content,"utf8"); }
  }
  process.stdout.write(`Web icon geometry ${process.argv.includes("--check")?"verified":"synced"}: ${Object.keys(data.shapes).length} icons, lucide-react ${data.evidence.version}\n`);
}
if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) await main();

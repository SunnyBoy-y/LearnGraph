import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import path from "node:path";
import {ICON_SOURCES, collectGeometry, kotlinFor, svgNodeToPath} from "./sync-web-icons.mjs";

const root=fileURLToPath(new URL("../../../",import.meta.url));
test("generated native geometry is byte-for-byte current with the installed Web icon source",async()=>{
  const data=await collectGeometry();
  const actual=await readFile(path.join(root,"mobile/native/app/src/main/java/com/learngraph/mobile/ui/components/WebLucideGeometry.kt"),"utf8");
  assert.equal(actual.replace(/\r\n/g,"\n"),kotlinFor(data));
  assert.equal(Object.keys(data.shapes).length,Object.keys(ICON_SOURCES).length+1);
  assert.ok(data.shapes.Menu.some(node=>node.data==="M4 5h16"),"Do not restore the old approximate menu coordinates");
  assert.ok(data.shapes.Network.some(node=>node.data.startsWith("M17 16")),"The actual Web Network icon uses rounded rectangular nodes");
});
test("voice uses the real filled VoiceWaveGlyph bars, not a similar stroked waveform",async()=>{
  const {shapes}=await collectGeometry();
  assert.equal(shapes.Wave.length,5);
  assert.ok(shapes.Wave.every(node=>node.filled));
  assert.match(shapes.Wave[2].data,/^M12 2\.1H12A1\.1 1\.1/);
  assert.match(shapes.Wave[2].data,/21\.9/);
});
test("SVG primitive conversion preserves circles, asymmetric rounded corners and rejects unhandled styling",()=>{
  assert.equal(svgNodeToPath(["circle",{cx:"12",cy:"12",r:"1"}]).data,"M11 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0");
  assert.match(svgNodeToPath(["rect",{x:"2",y:"3",width:"8",height:"10",rx:"2",ry:"3"}]).data,/A2 3 0 0 1 10 6/);
  assert.throws(()=>svgNodeToPath(["path",{d:"M0 0",transform:"scale(2)"}]),/explicit conversion/);
  assert.throws(()=>svgNodeToPath(["text",{children:"x"}]),/Unsupported SVG node/);
});
test("shipped icon and font notices keep upstream license terms",async()=>{
  const icons=await readFile(path.join(root,"mobile/native/app/src/main/assets/licenses/lucide-react.txt"),"utf8");
  assert.match(icons,/ISC License/);
  assert.match(icons,/The MIT License/);
  const font=await readFile(path.join(root,"mobile/native/app/src/main/assets/licenses/geist-OFL.txt"),"utf8");
  assert.match(font,/SIL OPEN FONT LICENSE Version 1.1/);
});

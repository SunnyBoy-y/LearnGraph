import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { collectGeometry, svgNodeToPath } from './sync-web-icons.mjs'

const preview = fileURLToPath(new URL('../../../docs/previews/native-mobile-before-after.html', import.meta.url))
const { shapes } = await collectGeometry()
const { __iconNode } = await import('../../../frontend/node_modules/lucide-react/dist/esm/icons/sparkles.mjs')
shapes.Sparkles = __iconNode.map(svgNodeToPath)
const names = { menu: 'Menu', network: 'Network', sliders: 'Sliders', chevron: 'Chevron', plus: 'Plus', mic: 'Microphone', wave: 'Wave', up: 'ArrowUp', expand: 'Expand', target: 'Target', globe: 'Globe', copy: 'Copy', edit: 'MessageSquareText', more: 'More', sparkle: 'Sparkles', files: 'Files', package: 'Package', search: 'Search', sync: 'ListChecks' }
const icons = Object.fromEntries(Object.entries(names).map(([name, source]) => [name, shapes[source].map(node => `<path d="${node.data}"${node.filled ? ' fill="currentColor" stroke="none"' : ''}/>`).join('')]))
let html = await readFile(preview, 'utf8')
html = html.replace(/^const icons=.+;$/m, `const icons=${JSON.stringify(icons)};`)
html = html.replace('stroke-width:1.8;', 'stroke-width:2;')
html = html.replace('Compose 控件；复杂页面仍通过兼容视图保留能力', 'Compose 宿主；消息和图谱使用本地打包的同源 Web 组件')
html = html.replace('原生从左侧边缘开启拖拽，Web 支持排除输入/横滚控件后的全屏拖拽；', '原生支持全画布左右抽屉与消息 DOM 手势桥，沿用 Web 的方向锁、半程吸附和控件排除；')
html = html.replace('原生复杂图谱、资料、产物、语音及部分工作流进入系统 WebView；', '原生消息与右侧图谱已接入同源组件；资料、产物、语音及部分完整业务页面沿用系统 WebView；')
html = html.replace('系统字体、字号缩放、系统安全区与弹层/阴影渲染', 'Geist 拉丁字体已同步；中文字体回退、字号缩放、系统安全区与弹层/阴影渲染')
html = html.replace("'+svg('copy')+'资料", "'+svg('files')+'资料").replace("'+svg('expand')+'产物", "'+svg('package')+'产物")
html = html.replace('>⌕</button>', ">'" + "+svg('search')+" + "'</button>").replace('>↻</button>', ">'" + "+svg('sync')+" + "'</button>")
html = html.replace('实际原生 App 会进入系统 WebView 的对应兼容功能。', '消息与右侧图谱在原生宿主中运行同源 Web 组件，完整业务页面沿用系统 WebView。')
html = html.replace('现有语音通过兼容视图继续提供。', '现有语音页面通过系统 WebView 继续提供。')
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1]
if (!script) throw new Error('Preview script missing')
new Function(script)
await writeFile(preview, html)
console.log('Comparison preview synchronized with current source icons and migration boundaries.')

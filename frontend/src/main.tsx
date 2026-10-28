import { StrictMode } from 'react'
import { MotionConfig } from 'motion/react'
import { createRoot } from 'react-dom/client'
import App from '@/App'
import { isNativeApp } from '@/lib/native-bridge'
import { initializeAppearance } from '@/lib/appearance'
import '@xyflow/react/dist/style.css'
import 'katex/dist/katex.min.css'
import 'streamdown/styles.css'
import '@/index.css'
import '@/styles/motion-feedback.css'
import { registerWebShellCache, updateWebShellCache } from '@/lib/web-shell-cache'

try {
  initializeAppearance()
  // APK 内强制手机 UI 风格：不依赖宽度（折叠屏/平板展开也走手机布局）
  if (isNativeApp()) {
    document.documentElement.classList.add('is-native-app')
  }
} catch {
  document.documentElement.dataset.colorMode = 'mono'
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user"><App /></MotionConfig>
  </StrictMode>,
)

if (document.readyState === 'complete') {
  void registerWebShellCache()
} else {
  window.addEventListener('load', () => { void registerWebShellCache() }, { once: true })
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return
  void updateWebShellCache()
})

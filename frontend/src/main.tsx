import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from '@/App'
import { isNativeApp } from '@/lib/native-bridge'
import '@xyflow/react/dist/style.css'
import 'katex/dist/katex.min.css'
import 'streamdown/styles.css'
import '@/index.css'

try {
  document.documentElement.dataset.colorMode = 'mono'
  // Restore theme before first paint so the workspace does not flash light → dark.
  const savedTheme = window.localStorage.getItem('lg-theme')
  document.documentElement.classList.toggle('dark', savedTheme === 'dark')
  // APK 内强制手机 UI 风格：不依赖宽度（折叠屏/平板展开也走手机布局）
  if (isNativeApp()) {
    document.documentElement.classList.add('is-native-app')
  }
} catch {
  document.documentElement.dataset.colorMode = 'mono'
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from '@/App'
import '@xyflow/react/dist/style.css'
import 'katex/dist/katex.min.css'
import 'streamdown/styles.css'
import '@/index.css'

try {
  document.documentElement.dataset.colorMode = 'mono'
  // Restore theme before first paint so the workspace does not flash light → dark.
  const savedTheme = window.localStorage.getItem('lg-theme')
  document.documentElement.classList.toggle('dark', savedTheme === 'dark')
} catch {
  document.documentElement.dataset.colorMode = 'mono'
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

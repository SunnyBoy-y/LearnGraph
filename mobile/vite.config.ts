import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Capacitor 打包要求相对 base（WebView 从 https://localhost 加载 dist 资源）。
// 浏览器开发态：走 /api/v1 代理到本机后端（127.0.0.1:18000），避免 CORS 摩擦；
// 真机/APK 态：连接配置必须填写服务器地址（见 src/screens/Connect.tsx 校验）。
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    // 5174 与本机忆伴 dev server 冲突（IPv4 占用），改用 5175
    port: 5175,
    strictPort: true,
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:18000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})

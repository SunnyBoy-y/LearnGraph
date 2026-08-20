import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'com.learngraph.mobile',
  appName: 'LearnGraph',
  webDir: 'dist',
  // https://localhost 是 Capacitor WebView 的本地 origin；
  // 后端 CORS 需要放行该 origin（LEARNGRAPH_CORS_ORIGINS 加 "https://localhost"）。
  server: {
    androidScheme: 'https',
  },
}

export default config

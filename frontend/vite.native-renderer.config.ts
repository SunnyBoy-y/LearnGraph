import path from 'node:path'
import { defineConfig, mergeConfig } from 'vite'
import applicationConfig from './vite.config'

// Reuse the production dependency groups: splitting React/Streamdown/Mermaid's
// cyclic module graphs differently has previously caused module-init failures.
export default defineConfig(async (environment) => {
  const shared = typeof applicationConfig === 'function'
    ? await applicationConfig(environment)
    : await applicationConfig
  return mergeConfig(shared, {
    base: '/__lg_renderer_v1/',
    publicDir: false,
    // A packaged client must follow the selected server, never a build machine's .env API host.
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('/api/v1') },
    build: {
      outDir: path.resolve(import.meta.dirname, '../mobile/native/app/src/main/assets/renderer'),
      emptyOutDir: true,
      rolldownOptions: { input: {
        renderer: path.resolve(import.meta.dirname, 'native-renderer.html'),
        application: path.resolve(import.meta.dirname, 'native-app.html'),
      } },
    },
  })
})

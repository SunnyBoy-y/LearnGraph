import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// The dev server proxies /api to the backend so the browser always issues
// same-origin requests, no matter which port Vite ends up binding (5173,
// 5174, ...). CORS therefore never applies during development.
const backendOrigin = process.env.LEARNGRAPH_BACKEND_ORIGIN?.trim() || 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: backendOrigin,
        changeOrigin: true,
        // Realtime dictation runs over a same-origin WebSocket.
        ws: true,
      },
    },
  },
  build: {
    // The remaining >500 kB files are isolated Shiki language/WASM payloads
    // loaded on demand. Application entry and route chunks stay well below
    // 500 kB after the vendor split, so warn only above that dependency ceiling.
    chunkSizeWarningLimit: 800,
    // Keep route code separate from large, slow-changing dependencies. Rolldown
    // may split this group further around maxSize while preserving entry-aware
    // loading, so a graph page does not have to download chat-only libraries.
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'vendor',
              test: /node_modules[\\/]/,
              priority: 10,
              entriesAware: true,
              minSize: 20_000,
              maxSize: 320_000,
            },
          ],
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})

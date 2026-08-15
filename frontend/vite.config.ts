import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import { loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const configured = (key: string) => (env[key] || process.env[key] || '').trim()

  // The dev server proxies /api to the backend so the browser always issues
  // same-origin requests, no matter which port Vite ends up binding (5173,
  // 5174, ...). CORS therefore never applies during development.
  const backendOrigin = configured('LEARNGRAPH_BACKEND_ORIGIN') || 'http://127.0.0.1:8000'
  const publicOrigin = configured('LEARNGRAPH_PUBLIC_ORIGIN').replace(/\/+$/, '')
  const allowedHosts = new Set<string>()
  for (const host of configured('LEARNGRAPH_ALLOWED_HOSTS').split(',')) {
    if (host) allowedHosts.add(host)
  }
  if (publicOrigin) {
    try {
      allowedHosts.add(new URL(publicOrigin).hostname)
    } catch {
      // Invalid public origins are ignored; Vite falls back to local hosts.
    }
  }

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: configured('LEARNGRAPH_LISTEN_HOST') || '127.0.0.1',
      ...(allowedHosts.size > 0 ? { allowedHosts: [...allowedHosts] } : {}),
      ...(publicOrigin ? { origin: publicOrigin } : {}),
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
              {
                // Recharts has a cyclic internal module graph; splitting it
                // across chunks (maxSize or entriesAware above) makes a shared
                // constant undefined at module-init time in the browser,
                // crashing the whole app with "Cannot read properties of
                // undefined (reading 'axis')" on any chart page. It MUST stay
                // in exactly ONE chunk, so disable entry-aware splitting.
                name: 'recharts',
                test: /node_modules[\\/]recharts[\\/]/,
                priority: 20,
                entriesAware: false,
                minSize: 0,
                maxSize: Number.POSITIVE_INFINITY,
              },
            ],
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      clearMocks: true,
      mockReset: true,
      restoreMocks: true,
      globals: false,
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
  }
})

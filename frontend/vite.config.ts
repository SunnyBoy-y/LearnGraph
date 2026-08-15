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
                // React and its runtime share module-init-time state
                // (dispatcher, ReactSharedInternals, jsx-runtime, scheduler).
                // Entry-aware splitting scatters those modules across chunks
                // (react even ended up inside the recharts chunk), which can
                // leave shared constants undefined at init time in the browser
                // and white-screen whole routes. Keep the React family in
                // exactly ONE chunk.
                name: 'react',
                test: /node_modules[\\/](react|react-dom|react-is|react-router|react-router-dom|scheduler)[\\/]/,
                priority: 30,
                entriesAware: false,
                minSize: 0,
                maxSize: Number.POSITIVE_INFINITY,
              },
              {
                // motion (framer-motion v12) has the same cyclic module graph
                // problem: rolldown split AnimatePresence into its own chunk
                // while the rest of motion lived in an entriesAware vendor
                // chunk, so providers/document-learning/memory pages could
                // crash at module-init with undefined shared state (the same
                // failure mode as the recharts white screen). Keep the whole
                // motion family in ONE chunk. `motion` is a re-export shim
                // over `framer-motion`, so both packages (plus motion-dom /
                // motion-utils) must be covered.
                name: 'motion',
                test: /node_modules[\\/](motion|framer-motion|motion-dom|motion-utils)[\\/]/,
                priority: 30,
                entriesAware: false,
                minSize: 0,
                maxSize: Number.POSITIVE_INFINITY,
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
              {
                // pptx-preview has an internal module graph with mutual
                // imports; entry-aware splitting scattered it across ~20
                // chunks that import each other in cycles, which can crash
                // at module-init in the browser (same failure mode as the
                // recharts white screen) whenever a PPT attachment is
                // previewed. Keep it in ONE chunk.
                name: 'pptx-preview',
                test: /node_modules[\\/](pptx-preview|echarts|zrender)[\\/]/,
                priority: 30,
                entriesAware: false,
                minSize: 0,
                maxSize: Number.POSITIVE_INFINITY,
              },
              {
                // streamdown's markdown parsing chain (streamdown +
                // micromark + mdast/hast/unist + remark/rehype + unified +
                // vfile) is a cyclic module graph; entry-aware splitting
                // scattered it across ~9 chunks that import each other in
                // cycles, which can crash at module-init (same failure mode
                // as the recharts white screen) whenever a chat message or
                // document renders markdown. Keep the chain in ONE chunk.
                // @streamdown/* code-highlight data is intentionally NOT
                // pinned here (it is not part of the cycle); pinning it drags
                // ~10 MB of shiki grammar data into the chunk.
                name: 'markdown-render',
                test: /node_modules[\\/](streamdown|micromark(-[a-z0-9-]+)?|mdast-util-[a-z0-9-]+|hast-util-[a-z0-9-]+|unist-util-[a-z0-9-]+|remark(-[a-z0-9-]+)?|rehype(-[a-z0-9-]+)?|unified|vfile(-[a-z0-9-]+)?|markdown-table|lowlight|refractor|character-entities(-legacy)?|decode-named-character-reference|property-information|space-separated-tokens|comma-separated-tokens|html-void-elements|web-namespaces|zwitch|trim-lines|ccount|longest-streak|escape-string-regexp|bail|extend|trough|is-plain-obj|inline-style-parser|style-to-object|style-to-js|html-url-attributes|estree-util-[a-z0-9-]+|@ungap|remend|rehype-harden)[\\/]/,
                priority: 30,
                entriesAware: false,
                minSize: 0,
                maxSize: Number.POSITIVE_INFINITY,
              },
              {
                // mermaid (via @streamdown/mermaid) is a huge cyclic module
                // graph (d3/dagre/cytoscape/venn…); entry-aware splitting
                // used to break it into cyclic chunks. Keep mermaid and its
                // dedicated deps in ONE chunk so diagram rendering cannot
                // crash at module-init. Deliberately exclude widely shared
                // deps (dayjs/uuid/dompurify/marked/stylis) so non-diagram
                // pages never pull this chunk in.
                name: 'mermaid',
                test: /node_modules[\\/](mermaid|@mermaid-js|@braintree|@iconify|@upsetjs|cytoscape(-[a-z0-9-]+)?|d3(-[a-z0-9-]+)?|dagre-d3-es|es-toolkit|katex|khroma|non-layered-tidy-tree-layout|roughjs|ts-dedent)[\\/]/,
                priority: 30,
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

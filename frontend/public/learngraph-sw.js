/* LearnGraph Web shell cache.
 *
 * The HTML shell is network-first with conditional revalidation. Hashed Vite
 * resources are cache-first; local caches stay enough to reopen the UI during a
 * short outage, while API data continues to use the app's own persistence.
 */
const SHELL_CACHE = 'learngraph-shell-v1'
const ASSET_CACHE = 'learngraph-assets-v1'
const ROUTES = ['/', '/auth/login', '/auth/change-password']
const CACHEABLE_STATUS = 200

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE)
    const assetCache = await caches.open(ASSET_CACHE)
    const assets = new Set([
      '/learnings-offline-probe',
    ])
    assets.delete('/learnings-offline-probe')
    await Promise.allSettled(ROUTES.map(async (route) => {
      try {
        const response = await fetch(route, { cache: 'no-cache', credentials: 'include' })
        if (response.ok) {
          const html = await response.clone().text()
          if (route === '/') {
            for (const match of html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)) assets.add(match[1])
          }
          await cache.put(route, response)
        }
      } catch {
        // A failed optional route must not discard the shell installed from /.
      }
    }))
    if (assets.size > 0) {
      await Promise.allSettled([...assets].map(async (asset) => {
        try {
          const response = await fetch(asset, {
            cache: 'force-cache',
            credentials: 'include',
            // The HTML declares crossorigin module links. Matching that mode avoids
            // storing a response the page's module loader cannot reuse.
            mode: 'cors',
          })
          if (response.ok) await assetCache.put(asset, response)
        } catch {
          // Optional lazy CSS/worker failures must not invalidate the whole shell.
        }
      }))
    }
    await self.skipWaiting()
  })())
})

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keep = new Set([SHELL_CACHE, ASSET_CACHE])
    const names = await caches.keys()
    await Promise.all(names.filter((name) => name.startsWith('learngraph-') && !keep.has(name))
      .map((name) => caches.delete(name)))
    await self.clients.claim()
  })())
})

function isHashedAsset(pathname) {
  const stem = pathname.split('/').pop()?.replace(/\.[^.]+$/, '') || ''
  return pathname.startsWith('/assets/') && /-[A-Za-z0-9_-]{8,}$/.test(stem)
}

function isCacheableRequest(request) {
  const url = new URL(request.url)
  return request.method === 'GET' && url.origin === self.location.origin &&
    !url.pathname.startsWith('/api/') && !url.pathname.startsWith('/__lg_renderer_v1/')
}

async function cacheResponse(cache, key, response) {
  if (response.status === CACHEABLE_STATUS && ['basic', 'cors'].includes(response.type)) {
    await cache.put(key, response.clone())
  }
  return response
}

async function networkFirst(request) {
  const cache = await caches.open(SHELL_CACHE)
  try {
    const response = await fetch(request, { cache: 'no-cache', credentials: 'include' })
    if (response.ok) {
      await cacheResponse(cache, request, response)
      return response
    }
    throw new Error(`HTTP ${response.status}`)
  } catch {
    return (await cache.match(request)) || (await cache.match(new URL(request.url).pathname)) ||
      Response.error()
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(ASSET_CACHE)
  const hit = (await cache.match(request)) || (await cache.match(new URL(request.url).pathname))
  if (hit) return hit
  const response = await fetch(request)
  // Module requests are often CORS-mode even on the same origin. Cache by URL,
  // not by the original Request, so a precached basic response can be reused.
  if (response.status === CACHEABLE_STATUS && ['basic', 'cors'].includes(response.type)) {
    await cache.put(new URL(request.url).pathname, response.clone())
  }
  return response
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (!isCacheableRequest(request)) return
  const url = new URL(request.url)
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request))
  } else if (isHashedAsset(url.pathname)) {
    event.respondWith(cacheFirst(request))
  }
})


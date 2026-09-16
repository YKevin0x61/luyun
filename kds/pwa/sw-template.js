const CACHE_PREFIX = 'luyun-kds-shell-'
const CACHE_NAME = __LUYUN_KDS_CACHE_NAME__
const APP_VERSION = __LUYUN_KDS_VERSION__
const PRECACHE_URLS = __LUYUN_KDS_PRECACHE_URLS__
const APP_SHELL = '/kds/index.html'

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys()
      await Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
          .map((name) => caches.delete(name)),
      )
      await self.clients.claim()
    })(),
  )
})

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting()
    return
  }
  if (event.data?.type === 'GET_VERSION' && event.ports?.[0]) {
    event.ports[0].postMessage({ version: APP_VERSION })
  }
})

async function cacheFirst(request) {
  const cached = await caches.match(request, { ignoreSearch: true })
  if (cached) return cached

  const response = await fetch(request)
  if (response.ok) {
    const cache = await caches.open(CACHE_NAME)
    await cache.put(request, response.clone())
  }
  return response
}

async function staleWhileRevalidate(request) {
  const cached = await caches.match(request)
  const network = fetch(request)
    .then(async (response) => {
      if (response.ok || response.type === 'opaque') {
        const cache = await caches.open(CACHE_NAME)
        await cache.put(request, response.clone())
      }
      return response
    })
    .catch(() => cached || Response.error())

  return cached || network
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin === self.location.origin) {
    if (!url.pathname.startsWith('/kds/')) return

    if (request.mode === 'navigate') {
      event.respondWith(
        caches
          .match(APP_SHELL, { ignoreSearch: true })
          .then((cached) => cached || fetch(request)),
      )
      return
    }

    if (
      url.pathname.startsWith('/kds/assets/') ||
      url.pathname.startsWith('/kds/static/') ||
      url.pathname === '/kds/manifest.webmanifest'
    ) {
      event.respondWith(cacheFirst(request))
    }
    return
  }

  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(staleWhileRevalidate(request))
  }
})

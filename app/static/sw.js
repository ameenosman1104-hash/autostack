const CACHE = 'autostack-pwa-v1';
const SHELL = [
  '/',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

console.log('[Service Worker] Installing with cache:', CACHE);

self.addEventListener('install', e => {
  console.log('[Service Worker] Install event fired');
  e.waitUntil(
    caches.open(CACHE)
      .then(c => {
        console.log('[Service Worker] Adding shell resources to cache');
        return c.addAll(SHELL);
      })
      .then(() => {
        console.log('[Service Worker] Skipping waiting');
        return self.skipWaiting();
      })
      .catch(err => {
        console.error('[Service Worker] Install error:', err);
      })
  );
});

self.addEventListener('activate', e => {
  console.log('[Service Worker] Activate event fired');
  e.waitUntil(
    caches.keys().then(keys => {
      console.log('[Service Worker] Available caches:', keys);
      return Promise.all(
        keys.filter(k => {
          const shouldDelete = k !== CACHE;
          if (shouldDelete) console.log('[Service Worker] Deleting old cache:', k);
          return shouldDelete;
        }).map(k => caches.delete(k))
      );
    }).then(() => {
      console.log('[Service Worker] Claiming clients');
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', e => {
  // Network-first for API/dynamic routes; cache-first for static assets
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;

  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }))
    );
  } else {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request))
    );
  }
});

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
  const url = new URL(e.request.url);

  // Only handle GET requests; let browser handle others
  if (e.request.method !== 'GET') return;

  // Cache-first for static assets
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request)
        .then(cached => {
          if (cached) {
            console.log('[SW] Cache hit: ' + url.pathname);
            return cached;
          }
          console.log('[SW] Cache miss, fetching: ' + url.pathname);
          return fetch(e.request)
            .then(res => {
              if (!res || res.status !== 200) {
                console.log('[SW] Bad response: ' + url.pathname + ' (' + (res ? res.status : 'null') + ')');
                return res;
              }
              const clone = res.clone();
              caches.open(CACHE).then(c => {
                c.put(e.request, clone);
                console.log('[SW] Cached: ' + url.pathname);
              });
              return res;
            })
            .catch(err => {
              console.error('[SW] Fetch failed: ' + url.pathname + ' - ' + err.message);
              throw err;
            });
        })
        .catch(err => {
          console.error('[SW] Static fetch failed: ' + url.pathname + ' - ' + err.message);
        })
    );
  } else {
    // Network-first for dynamic content
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (!res || res.status !== 200) return res;
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
        .catch(err => {
          console.log('[SW] Network failed: ' + url.pathname + ', trying cache');
          return caches.match(e.request)
            .then(cached => cached || new Response('Offline', { status: 503 }))
            .catch(e => new Response('Service Worker Error', { status: 500 }));
        })
    );
  }
});

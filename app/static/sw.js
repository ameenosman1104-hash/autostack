// AutoStack Service Worker - Fixed Version
// Implements proper fetch error handling and cache strategies

const STATIC_CACHE = 'autostack-static-v1';
const DYNAMIC_CACHE = 'autostack-dynamic-v1';

// Installation: clean up old caches
self.addEventListener('install', event => {
  console.log('[SW] Installing service worker');
  event.waitUntil(self.skipWaiting());
});

// Activation: delete old cache versions
self.addEventListener('activate', event => {
  console.log('[SW] Activating service worker');
  event.waitUntil(
    caches.keys().then(keys => {
      console.log('[SW] Cleaning up old caches:', keys);
      return Promise.all(
        keys
          .filter(key => {
            // Delete old AutoStack caches (but keep current versions)
            const isOldCache = /^autostack-static-v\d+$/.test(key) && key !== STATIC_CACHE;
            const isOldDynamic = /^autostack-dynamic-v\d+$/.test(key) && key !== DYNAMIC_CACHE;
            const isLegacy = /^(autostack|osbro)-/.test(key) &&
                            !key.includes('-static-') &&
                            !key.includes('-dynamic-');
            return isOldCache || isOldDynamic || isLegacy;
          })
          .map(key => {
            console.log('[SW] Deleting cache:', key);
            return caches.delete(key);
          })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event handler with proper error handling
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET requests
  if (request.method !== 'GET') {
    console.log('[SW] Skipping non-GET request:', request.method, url.pathname);
    return;
  }

  // Don't cache API requests, authentication, or dynamic pages
  const isApiRequest = url.pathname.startsWith('/api/');
  const isAuthRequest = url.pathname.includes('/auth/') || url.pathname.includes('/login');
  const isDynamicPage = ['/dashboard/', '/stock/', '/debtors/', '/settings/', '/reports/'].some(path => url.pathname.startsWith(path));

  // Strategy 1: Network-first for navigation requests (HTML pages)
  if (request.mode === 'navigate' || request.destination === 'document') {
    event.respondWith(
      fetch(request, { cache: 'no-cache' })
        .then(response => {
          // Only cache successful responses
          if (response.ok && !isDynamicPage && !isAuthRequest) {
            const responseClone = response.clone();
            caches.open(DYNAMIC_CACHE)
              .then(cache => {
                cache.put(request, responseClone);
              })
              .catch(err => console.warn('[SW] Cache write error:', err));
          }
          return response;
        })
        .catch(error => {
          console.warn('[SW] Network request failed for', url.pathname, error);
          // Try to return cached version for navigation
          return caches.match(request)
            .then(cachedResponse => {
              if (cachedResponse) {
                console.log('[SW] Returning cached page:', url.pathname);
                return cachedResponse;
              }
              // If no cache available, return offline page or error
              console.log('[SW] No cache available, returning error response');
              return new Response('Offline - Page not available', {
                status: 503,
                statusText: 'Service Unavailable',
                headers: new Headers({
                  'Content-Type': 'text/plain'
                })
              });
            })
            .catch(cacheError => {
              console.error('[SW] Cache error:', cacheError);
              // Ensure we always return a response, never a rejection
              return new Response('Error - Cannot load page', {
                status: 500,
                statusText: 'Internal Server Error',
                headers: new Headers({
                  'Content-Type': 'text/plain'
                })
              });
            });
        })
    );
    return;
  }

  // Strategy 2: Cache-first for static assets (CSS, JS, images, fonts)
  const isStaticAsset = /\.(css|js|png|jpg|jpeg|gif|svg|woff|woff2|ttf|eot)$/.test(url.pathname) ||
                        url.pathname.includes('/static/');

  if (isStaticAsset) {
    event.respondWith(
      caches.match(request)
        .then(cachedResponse => {
          if (cachedResponse) {
            console.log('[SW] Returning cached static asset:', url.pathname);
            return cachedResponse;
          }
          // If not in cache, fetch from network
          return fetch(request, { cache: 'no-cache' })
            .then(response => {
              // Cache successful responses
              if (response.ok) {
                const responseClone = response.clone();
                caches.open(STATIC_CACHE)
                  .then(cache => {
                    cache.put(request, responseClone);
                  })
                  .catch(err => console.warn('[SW] Cache write error:', err));
              }
              return response;
            })
            .catch(error => {
              console.warn('[SW] Failed to fetch static asset:', url.pathname, error);
              // Return error response instead of rejecting
              return new Response('Asset not available', {
                status: 503,
                statusText: 'Service Unavailable',
                headers: new Headers({
                  'Content-Type': 'text/plain'
                })
              });
            });
        })
        .catch(cacheError => {
          console.error('[SW] Cache access error:', cacheError);
          return new Response('Error - Cannot load asset', {
            status: 500,
            statusText: 'Internal Server Error',
            headers: new Headers({
              'Content-Type': 'text/plain'
            })
          });
        })
    );
    return;
  }

  // Strategy 3: Network-only for API and auth requests
  if (isApiRequest || isAuthRequest) {
    console.log('[SW] Network-only for:', url.pathname);
    event.respondWith(
      fetch(request)
        .catch(error => {
          console.warn('[SW] API/Auth request failed:', url.pathname, error);
          // Don't cache failed auth/API requests
          return new Response(JSON.stringify({ error: 'Network error' }), {
            status: 503,
            statusText: 'Service Unavailable',
            headers: new Headers({
              'Content-Type': 'application/json'
            })
          });
        })
    );
    return;
  }

  // Strategy 4: Default - Network-first with cache fallback
  event.respondWith(
    fetch(request, { cache: 'no-cache' })
      .then(response => {
        if (response.ok) {
          const responseClone = response.clone();
          caches.open(DYNAMIC_CACHE)
            .then(cache => {
              cache.put(request, responseClone);
            })
            .catch(err => console.warn('[SW] Cache write error:', err));
        }
        return response;
      })
      .catch(error => {
        console.warn('[SW] Network request failed:', url.pathname, error);
        return caches.match(request)
          .then(cachedResponse => {
            if (cachedResponse) {
              console.log('[SW] Returning cached fallback:', url.pathname);
              return cachedResponse;
            }
            console.log('[SW] No cache available');
            return new Response('Content not available', {
              status: 503,
              statusText: 'Service Unavailable',
              headers: new Headers({
                'Content-Type': 'text/plain'
              })
            });
          })
          .catch(cacheError => {
            console.error('[SW] Cache error:', cacheError);
            return new Response('Error', {
              status: 500,
              statusText: 'Internal Server Error',
              headers: new Headers({
                'Content-Type': 'text/plain'
              })
            });
          });
      })
  );
});

console.log('[SW] Service Worker script loaded and initialized');

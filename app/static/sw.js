// Never cache authenticated HTML or API data. Remove caches from earlier versions.
const CACHE = 'autostack-public-v3';
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(
  caches.keys().then(keys => Promise.all(
    keys.filter(key => /^(autostack|osbro)/.test(key)).map(key => caches.delete(key))
  )).then(() => self.clients.claim())
));
self.addEventListener('fetch', event => {
  // Network-only means logging out cannot expose another account through offline fallback.
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request));
});

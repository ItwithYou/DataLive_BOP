/* ITRS Live Report - service worker.
 *
 * Makes the dashboard installable and usable without a connection. What gets
 * cached is the ENCRYPTED page, so an offline copy is no more readable than
 * the published file: the passphrase is still required, and it is never
 * stored here or anywhere else.
 *
 * Bump CACHE when publishing a rebuild, otherwise phones that already have it
 * installed will keep serving the previous copy from cache.
 */
const CACHE = 'itrs-v27';
const SHELL = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/maskable-512.png',
];

self.addEventListener('install', e => {
  // addAll fails the whole install if any single file 404s, so fetch each
  // one independently and keep whatever succeeds.
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.all(SHELL.map(u => c.add(u).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Network first, and with {cache:'no-cache'} so the browser revalidates the
  // file against the server every time (a cheap 304 when unchanged, a full
  // download only when it actually changed). Without this, GitHub Pages' own
  // 10-minute browser cache would keep serving the previous copy even though
  // the service worker asked the network - the cause of "still the old version"
  // after a republish. The cache remains the offline fallback.
  e.respondWith(
    fetch(req, {cache: 'no-cache'})
      .then(res => {
        if (res && res.status === 200 && res.type === 'basic'){
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req).then(hit => hit || caches.match('./index.html')))
  );
});

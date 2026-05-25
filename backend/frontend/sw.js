// OnClock service worker — minimal, network-first.
// We don't aggressively cache because station availability changes frequently.
const CACHE_NAME = 'onclock-shell-v1';
const PRECACHE = ['/', '/manifest.json', '/icon.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((c) => c.addAll(PRECACHE)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  // Don't intercept API, dashboard, or cyber routes — always live.
  if (req.method !== 'GET' || req.url.includes('/api/') || req.url.includes('/dashboard') || req.url.includes('/')) return;
  e.respondWith(
    fetch(req).catch(() => caches.match(req).then((r) => r || caches.match('/')))
  );
});

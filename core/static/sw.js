/**
 * ConCall PWA Service Worker
 * Strategy: Cache static assets, network-first for API calls
 */

const CACHE_NAME = 'concall-v23';
const STATIC_ASSETS = [
    '/',
    '/static/style.css',
    '/static/app.js',
    '/static/processor.js',
    '/static/manifest.json',
    '/static/icons/favicon.svg',
    '/static/icons/icon-192.svg',
    '/static/icons/icon-512.svg',
];

// Install: cache static assets
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
            )
        )
    );
    self.clients.claim();
});

// Fetch: network-first for HTML/API, cache-first for static assets
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Skip WebSocket and non-GET requests
    if (event.request.method !== 'GET' || url.protocol === 'ws:' || url.protocol === 'wss:') {
        return;
    }

    // Static assets: cache-first
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                return cached || fetch(event.request).then((response) => {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    return response;
                });
            })
        );
        return;
    }

    // HTML / API: network-first
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                if (url.pathname === '/') {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                }
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});

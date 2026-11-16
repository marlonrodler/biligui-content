// Service worker mínimo: só para o app ser instalável. Não guarda cache (é um teste).
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()))
self.addEventListener('fetch', () => {})

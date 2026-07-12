/* Intercede service worker — app-shell caching only.
   API responses are NEVER cached here: they carry pastorally sensitive data
   and the server marks them Cache-Control: no-store. Offline behavior for
   actions (e.g. queued "I prayed" taps) lives in the app, not the SW. */

const CACHE = "intercede-shell-v1";
const PRECACHE = [
  "/",
  "/manifest.webmanifest",
  "/icon.svg",
  "/fonts/fraunces-latin.woff2",
  "/fonts/albert-sans-latin.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/")) return;

  // navigations: network first so deploys land, cached shell when offline
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put("/", copy));
          return res;
        })
        .catch(() => caches.match("/"))
    );
    return;
  }

  // static assets (hashed filenames, fonts, icons): cache first
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(request, copy));
          }
          return res;
        })
    )
  );
});

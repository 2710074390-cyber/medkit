/* =========================================================================
   Service Worker：让"添加到主屏"之后**断网也能刷题**
   -------------------------------------------------------------------------
   策略很保守，因为这是学习工具、不是内容站：
   - 预缓存整个 App 外壳（总共不到 100KB），装完即可离线启动。
   - 同源静态资源：cache-first（版本号变了才换）。
   - 页面导航：network-first，失败回落缓存的 index.html（地铁里也能打开）。
   - 只处理 GET；题库数据在 IndexedDB，不经过 SW。

   升级务必改 CACHE 版本号，否则用户会一直看到旧外壳。
   ========================================================================= */
const CACHE = "medkit-mobile-v1";

const SHELL = [
  "./",
  "./index.html",
  "./css/app.css",
  "./js/db.js",
  "./js/sm2.js",
  "./js/model.js",
  "./js/drill.js",
  "./js/app.js",
  "./manifest.webmanifest",
  "./icon.svg",
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // 不外联、不代理

  if (req.mode === "navigate") {
    event.respondWith((async () => {
      try {
        return await fetch(req);
      } catch (e) {
        const cached = await caches.match("./index.html");
        return cached || Response.error();
      }
    })());
    return;
  }

  event.respondWith((async () => {
    const hit = await caches.match(req);
    if (hit) return hit;
    try {
      const res = await fetch(req);
      if (res.ok) {
        const c = await caches.open(CACHE);
        c.put(req, res.clone());
      }
      return res;
    } catch (e) {
      return Response.error();
    }
  })());
});

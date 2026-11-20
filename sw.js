const CACHE='pdj-v7';
const SHELL=['./','index.html','manifest.json','icons/icon-180.png','icons/icon-192.png','icons/favicon.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
// Réseau d'abord (cotes et page à jour), cache en secours hors ligne.
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET' || new URL(e.request.url).origin!==self.location.origin) return;
  e.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(CACHE).then(x=>x.put(e.request.url.split('?')[0],c));return r;})
    .catch(()=>caches.match(e.request.url.split('?')[0]).then(r=>r||caches.match('index.html'))));
});

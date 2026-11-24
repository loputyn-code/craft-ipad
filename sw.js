// Service worker del launcher Craft per iPad.
//
// - L'hub (questa pagina, il catalogo e le icone) è sempre salvato: si apre anche offline,
//   e quando c'è connessione si aggiorna dalla rete.
// - Ogni app scaricata dal launcher vive in una cache sua, "app-<id>-<versione>", riempita
//   dalla pagina dell'hub. Qui la si serve per prima, così l'app funziona senza connessione e
//   non si mescolano file di versioni diverse; l'aggiornamento è esplicito ("Aggiorna").
// - Le app non scaricate funzionano solo online, come un normale sito.
const SHELL = 'hub-v2';
const SHELL_FILES = ['./', 'index.html', 'apps.json', 'manifest.webmanifest', 'icons/hub.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      // elimina le vecchie copie dell'hub (craft-v1, hub-v1...), mai le app scaricate
      .then((keys) => Promise.all(
        keys.filter((k) => !k.startsWith('app-') && k !== SHELL).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

const scope = new URL(self.registration.scope);

function isShell(url) {
  const rel = url.pathname.slice(scope.pathname.length);
  return rel === '' || !rel.includes('/') || rel.startsWith('icons/');
}

const offlinePage = () => new Response(
  '<!DOCTYPE html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" ' +
  'content="width=device-width,initial-scale=1"><title>Offline</title></head>' +
  '<body style="font:17px -apple-system,system-ui;padding:40px;max-width:520px;margin:auto">' +
  '<h2>Questa app non è scaricata</h2><p>Sei offline e questa app non è salvata sul ' +
  'dispositivo. Torna al launcher e usa <b>Scarica</b> quando hai la connessione.</p>' +
  '<p><a href="' + scope.pathname + 'index.html">Torna al launcher</a></p></body></html>',
  { status: 503, headers: { 'content-type': 'text/html; charset=utf-8' } }
);

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  // i download del launcher chiedono esplicitamente la rete: mai rispondere con la copia salvata,
  // altrimenti un aggiornamento salverebbe di nuovo i file vecchi
  if (req.cache === 'reload' || req.cache === 'no-store') return;

  if (isShell(url)) {
    // hub: rete prima, copia salvata se offline
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() =>
          caches.match(req, { cacheName: SHELL, ignoreSearch: true })
            .then((r) => r || caches.match('index.html', { cacheName: SHELL }))
        )
    );
    return;
  }

  // file di un'app: la copia scaricata prima, altrimenti la rete
  event.respondWith(
    caches.match(req, { ignoreSearch: true }).then((hit) =>
      hit || fetch(req).catch(() => {
        if (req.mode === 'navigate') return offlinePage();
        return Response.error();
      })
    )
  );
});

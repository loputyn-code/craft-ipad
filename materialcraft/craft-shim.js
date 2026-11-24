// Caricatore per l'hub Craft: alcuni file delle app (wasm troppo grandi, modelli .onnx, parti
// compresse) sono stati codificati in base64 e divisi in pezzi .txt per poter essere pubblicati.
// Qui intercettiamo fetch() e restituiamo all'app il file originale, ricomposto.
// Funziona sia nella pagina sia nei worker (usa `self`).
(() => {
  const G = self;
  if (G.__craftShim) return;
  G.__craftShim = true;
  const MAP = {};
  const origFetch = G.fetch.bind(G);
  const cache = new Map();

  const b64ToBytes = (s) => {
    s = s.trim();
    if (Uint8Array.fromBase64) return Uint8Array.fromBase64(s);
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  };

  async function assemble(url, ent) {
    const texts = await Promise.all(
      ent.parts.map(async (p) => {
        const r = await origFetch(new URL(p, url));
        if (!r.ok) throw new Error('pezzo mancante ' + p + ' (' + r.status + ')');
        return r.text();
      })
    );
    const chunks = texts.map(b64ToBytes);
    let len = 0;
    for (const c of chunks) len += c.length;
    let bytes = new Uint8Array(len);
    let o = 0;
    for (const c of chunks) { bytes.set(c, o); o += c.length; }
    if (ent.gz) {
      const ds = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      bytes = new Uint8Array(await new Response(ds).arrayBuffer());
    }
    return bytes;
  }

  function match(input) {
    let href;
    try {
      href = typeof input === 'string' ? input : input instanceof URL ? input.href : input && input.url;
      if (!href) return null;
      const u = new URL(href, G.location.href);
      for (const key in MAP) {
        if (u.pathname.endsWith('/' + key)) return { u, ent: MAP[key], key: u.href };
      }
    } catch (_) {}
    return null;
  }

  G.fetch = function (input, init) {
    const m = match(input);
    if (!m) return origFetch(input, init);
    let p = cache.get(m.key);
    if (!p) {
      p = assemble(m.u, m.ent);
      cache.set(m.key, p);
      p.catch(() => cache.delete(m.key));
    }
    return p.then(
      (bytes) => new Response(bytes, { status: 200, headers: { 'content-type': m.ent.type } })
    );
  };
})();

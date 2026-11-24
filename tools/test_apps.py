#!/usr/bin/env python3
"""Prova che le app preparate partano in Chrome headless.

Uso: python3 tools/test_apps.py CARTELLA ID...
CARTELLA contiene una sottocartella per app (per esempio $CRAFT_WORK/stage oppure la radice del sito).
Stampa un JSON {id: true/false, ...}; esce con codice 1 se almeno una app non parte.
Richiede playwright (pip install playwright) e Chrome o Chromium.
"""
import asyncio, functools, http.server, json, shutil, sys, threading
from pathlib import Path
from playwright.async_api import async_playwright

root, ids = Path(sys.argv[1]).resolve(), sys.argv[2:]
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


handler = functools.partial(Quiet, directory=str(root))
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/"

STARTED = ("!!window.wasmBindings || [...document.querySelectorAll('canvas')]"
           ".some(c => c.width > 400 && c.height > 300)")


async def main():
    exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=exe, args=[
            "--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--ignore-gpu-blocklist"])
        result, details = {}, {}
        for i in ids:
            pg = await b.new_page(viewport={"width": 1180, "height": 820})
            errs = []
            pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)[:200]))
            await pg.goto(base + f"{i}/index.html")
            ok = False
            for _ in range(30):
                await pg.wait_for_timeout(2000)
                ok = await pg.evaluate(STARTED)
                if ok or errs:
                    break
            if ok and errs:
                ok = False
            result[i] = ok
            if errs:
                details[i] = errs[:2]
            await pg.close()
        await b.close()
    print(json.dumps({"ok": result, "errors": details}, indent=1))
    srv.shutdown()
    sys.exit(0 if all(result.values()) else 1)

asyncio.run(main())

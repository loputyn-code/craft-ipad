#!/usr/bin/env python3
"""Aggiornamento del launcher Craft per iPad.

Da eseguire nella radice del repository:

  python3 tools/craft_update.py check            # elenca le app con una versione web nuova (JSON)
  python3 tools/craft_update.py stage ID...      # scarica, verifica (SHA-256) e adatta in WORK/stage/ID
  python3 tools/craft_update.py apply ID...      # copia le app preparate nel sito e rigenera apps.json
  python3 tools/craft_update.py exclude ID VERSIONE "motivo"   # segna una versione come non funzionante
  python3 tools/craft_update.py refresh          # rigenera solo apps.json e le icone

Cartella di lavoro: $CRAFT_WORK (predefinita /tmp/craft-work). Le release e il catalogo vengono
da GitHub (storytold/craft-launcher, crates/core/catalog.toml).
"""
import base64, gzip, hashlib, json, os, re, shutil, subprocess, sys, tomllib, urllib.request, zipfile
from pathlib import Path

ROOT = Path.cwd()
TOOLS = Path(__file__).resolve().parent
WORK = Path(os.environ.get("CRAFT_WORK", "/tmp/craft-work"))
LAUNCHER_REPO = "https://github.com/storytold/craft-launcher"
EXCLUDED = TOOLS / "excluded.json"

MAX_FILE = 19 * 1024 * 1024          # i file più grandi vengono divisi (limite web di GitHub: 25 MB)
CHUNK = 18 * 1024 * 1024 // 4 * 4     # caratteri base64 per pezzo
DROP = {"_headers", ".htaccess"}
HOME_BTN = ('<a href="../index.html" id="craft-home" aria-label="Torna al launcher" '
            'style="position:fixed;right:10px;bottom:10px;z-index:2147483647;width:34px;height:34px;'
            'border-radius:17px;background:rgba(30,30,34,.55);color:#fff;display:flex;align-items:center;'
            'justify-content:center;text-decoration:none;font:18px system-ui;-webkit-backdrop-filter:blur(6px);'
            'backdrop-filter:blur(6px)">&#8962;</a>')
SW_REG = ('<script>if("serviceWorker" in navigator){navigator.serviceWorker.register("../sw.js",'
          '{scope:"../"}).catch(()=>{})}</script>')
NOTES = {"pdfcraft": "Il riconoscimento del testo (OCR) scarica anche due modelli."}


# ---------------------------------------------------------------- catalogo e release
def launcher_checkout():
    d = WORK / "craft-launcher"
    if d.exists():
        shutil.rmtree(d)
    WORK.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--depth", "1", LAUNCHER_REPO, str(d)], check=True)
    return d


def catalog(launcher):
    data = tomllib.loads((launcher / "crates/core/catalog.toml").read_text())
    return [{k: a.get(k) for k in ("id", "name", "tagline", "repo", "color", "kind")} for a in data["apps"]]


def vkey(v):
    return tuple(int(x) for x in v.split("."))


def latest_tag(repo):
    out = subprocess.run(["git", "ls-remote", "--tags", "--refs", f"https://github.com/{repo}"],
                         capture_output=True, text=True).stdout
    tags = [l.split("refs/tags/")[1] for l in out.splitlines() if "refs/tags/" in l]
    tags = [t for t in tags if re.fullmatch(r"v?\d+\.\d+\.\d+", t)]
    return max(tags, key=lambda t: vkey(t.lstrip("v")), default=None)


def get(url):
    with urllib.request.urlopen(url, timeout=600) as r:
        return r.read()


def web_asset(repo, tag):
    """(nome zip, sha256) della build web di una release, oppure None."""
    try:
        sums = get(f"https://github.com/{repo}/releases/download/{tag}/SHA256SUMS.txt").decode()
    except Exception:
        return None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r".+-web-[\d.]+\.zip", parts[1].lstrip("*")):
            return parts[1].lstrip("*"), parts[0].lower()
    return None


def site_apps():
    p = ROOT / "apps.json"
    return {a["id"]: a for a in json.loads(p.read_text())["apps"]} if p.exists() else {}


def excluded():
    return json.loads(EXCLUDED.read_text()) if EXCLUDED.exists() else {}


def cmd_check():
    launcher = launcher_checkout()
    current, skip = site_apps(), excluded()
    updates, notes = [], []
    for a in catalog(launcher):
        tag = latest_tag(a["repo"])
        if not tag:
            notes.append(f"{a['id']}: nessuna release")
            continue
        asset = web_asset(a["repo"], tag)
        if not asset:
            notes.append(f"{a['id']} {tag}: nessuna build web")
            continue
        version = re.sub(r"^.*-web-|\.zip$", "", asset[0])
        have = current.get(a["id"], {}).get("version")
        if have and vkey(have) >= vkey(version):
            continue
        if skip.get(a["id"], {}).get("version") == version:
            notes.append(f"{a['id']} {version}: esclusa ({skip[a['id']]['reason']})")
            continue
        updates.append({"id": a["id"], "repo": a["repo"], "tag": tag, "zip": asset[0], "sha256": asset[1],
                        "current": have, "latest": version})
    (WORK / "updates.json").write_text(json.dumps(updates, indent=1))
    print(json.dumps({"updates": updates, "notes": notes}, indent=1))


# ---------------------------------------------------------------- adattamento di una build
def adapt(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    mapping = {}
    for f in sorted(src.rglob("*")):
        if not f.is_file() or f.name in DROP:
            continue
        rel = f.relative_to(src)
        ext = f.suffix[1:].lower()
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if not ext and f.parent == src:                       # LICENSE-MIT, NOTICE...
            shutil.copy2(f, out.with_name(f.name + ".txt"))
            continue
        if ext in ("br", "gz") and f.with_suffix("").exists():  # copie precompresse: non servono
            continue
        if f.stat().st_size <= MAX_FILE:
            shutil.copy2(f, out)
            continue
        data = f.read_bytes()
        gz = not ext.startswith("part") and ext not in ("gz", "br")   # già compressi
        if gz:
            data = gzip.compress(data, 9, mtime=0)
        b64 = base64.b64encode(data).decode()
        parts = []
        for i in range(0, len(b64), CHUNK):
            name = f"{f.name}.b64.{i // CHUNK}.txt"
            (out.parent / name).write_text(b64[i:i + CHUNK])
            parts.append(name)
        mapping[rel.as_posix()] = {"parts": parts, "gz": gz,
                                   "type": "application/wasm" if ext == "wasm" else "application/octet-stream"}
    # PhotoCraft 0.23.1 importa app/snippets/... ma il pacchetto li ha solo nella radice
    if (dst / "app").is_dir() and (dst / "snippets").is_dir() and not (dst / "app" / "snippets").exists():
        shutil.copytree(dst / "snippets", dst / "app" / "snippets")
    shim = (TOOLS / "craft-shim.js").read_text().replace("/*MAP*/{}", json.dumps(mapping, indent=1))
    (dst / "craft-shim.js").write_text(shim)
    idx = dst / "index.html"
    html = idx.read_text()
    for key in mapping:
        html = re.sub(r'<link rel="preload" href="\./' + re.escape(key) + r'"[^>]*>', "", html)
    html = re.sub(r"(<head[^>]*>)", r'\1\n<script src="./craft-shim.js"></script>', html, count=1)
    html = html.replace("</body>", HOME_BTN + "\n" + SW_REG + "\n</body>", 1)
    idx.write_text(html)
    for w in dst.rglob("*_loader.js"):                         # worker che caricano file codificati
        w.write_text("import './craft-shim.js';\n" + w.read_text())


def cmd_stage(ids):
    updates = {u["id"]: u for u in json.loads((WORK / "updates.json").read_text())}
    report = {}
    for i in ids:
        u = updates[i]
        z = WORK / "zips" / u["zip"]
        z.parent.mkdir(parents=True, exist_ok=True)
        data = get(f"https://github.com/{u['repo']}/releases/download/{u['tag']}/{u['zip']}")
        if hashlib.sha256(data).hexdigest() != u["sha256"]:
            report[i] = "ERRORE: SHA-256 non corrisponde, scartata"
            continue
        z.write_bytes(data)
        x = WORK / "extract" / i
        if x.exists():
            shutil.rmtree(x)
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():                            # niente percorsi fuori dalla cartella
                if n.startswith("/") or ".." in Path(n).parts:
                    raise SystemExit(f"{u['zip']}: percorso non valido {n}")
            zf.extractall(x)
        src = next(p for p in x.iterdir() if p.is_dir())
        adapt(src, WORK / "stage" / i)
        (WORK / "stage" / i / ".craft-version").write_text(u["latest"])
        report[i] = f"pronta {u['latest']}"
    print(json.dumps(report, indent=1))


# ---------------------------------------------------------------- pubblicazione nel sito
def regenerate(launcher):
    current = site_apps()
    from PIL import Image
    (ROOT / "icons").mkdir(exist_ok=True)
    apps = []
    for a in catalog(launcher):
        d = ROOT / a["id"]
        if not d.is_dir() or a["id"] not in current and not (d / ".craft-version").exists():
            continue
        vf = d / ".craft-version"
        version = vf.read_text().strip() if vf.exists() else current[a["id"]]["version"]
        files = sorted(p.relative_to(ROOT).as_posix() for p in d.rglob("*")
                       if p.is_file() and p.name != ".craft-version")
        icon = launcher / "assets/app-icons" / f"{a['id']}.png"
        if icon.exists():
            im = Image.open(icon)
            im.thumbnail((192, 192))
            im.save(ROOT / "icons" / f"{a['id']}.png", optimize=True)
        apps.append({"id": a["id"], "name": a["name"], "tagline": a["tagline"], "kind": a["kind"],
                     "repo": a["repo"], "version": version, "color": a["color"],
                     "bytes": sum((ROOT / f).stat().st_size for f in files),
                     "note": NOTES.get(a["id"], ""), "files": files})
    (ROOT / "apps.json").write_text(json.dumps({"apps": apps}, separators=(",", ":")))
    return apps


def cmd_apply(ids):
    for i in ids:
        s = WORK / "stage" / i
        if not (s / ".craft-version").exists():
            raise SystemExit(f"{i}: non preparata")
        d = ROOT / i
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d)
    skip = excluded()
    for i in ids:
        skip.pop(i, None)
    EXCLUDED.write_text(json.dumps(skip, indent=1))
    apps = regenerate(WORK / "craft-launcher" if (WORK / "craft-launcher").exists() else launcher_checkout())
    print(json.dumps({a["id"]: a["version"] for a in apps}, indent=1))


def cmd_exclude(i, version, reason):
    skip = excluded()
    skip[i] = {"version": version, "reason": reason}
    EXCLUDED.write_text(json.dumps(skip, indent=1))
    print(f"{i} {version} esclusa: {reason}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "check":
        cmd_check()
    elif cmd == "stage":
        cmd_stage(args)
    elif cmd == "apply":
        cmd_apply(args)
    elif cmd == "exclude":
        cmd_exclude(*args)
    elif cmd == "refresh":
        print(len(regenerate(launcher_checkout())), "app")
    else:
        raise SystemExit(__doc__)

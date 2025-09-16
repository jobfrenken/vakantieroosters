# updater.py
from __future__ import annotations
import os
import json
import hashlib
import shutil
import subprocess
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

APP_NAME = "VakantieRooster"

def _local_appdata_dir() -> str:
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path

def _updates_dir() -> str:
    d = os.path.join(_local_appdata_dir(), "updates")
    os.makedirs(d, exist_ok=True)
    return d

def _read_text_from_source(src: str) -> str:
    # Ondersteunt:
    #  - http(s)://...
    #  - file://<pad>   (bijv. file://C:/map/manifest.json of file://\\server\share\manifest.json)
    #  - rechtstreeks pad (UNC of lokaal), zoals \\server\share\manifest.json of C:\pad\manifest.json
    s = src.strip()
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        req = Request(s, headers={"User-Agent": f"{APP_NAME}/updater"})
        with urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8")
    if low.startswith("file://"):
        p = s[7:]
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    with open(s, "r", encoding="utf-8") as f:
        return f.read()

def _download_binary(url: str, dest_path: str) -> None:
    u = url.strip()
    low = u.lower()
    if low.startswith("http://") or low.startswith("https://"):
        req = Request(u, headers={"User-Agent": f"{APP_NAME}/updater"})
        with urlopen(req, timeout=60) as r, open(dest_path, "wb") as f:
            shutil.copyfileobj(r, f)
    elif low.startswith("file://"):
        p = u[7:]
        shutil.copyfile(p, dest_path)
    else:
        # Lokaal of UNC pad
        shutil.copyfile(u, dest_path)

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _normalize_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.strip().split("."))

def is_newer(remote: str, current: str) -> bool:
    try:
        return _normalize_version(remote) > _normalize_version(current)
    except Exception:
        return False

def check_for_update(manifest_src: str, current_version: str) -> dict | None:
    # Verwacht JSON met:
    # { "version": "1.4.0", "url": "<installer>", "sha256": "<hex>", "notes": "..." }
    # Retourneert manifest-dict als er een nieuwere versie is, anders None.
    try:
        s = _read_text_from_source(manifest_src)
        manifest = json.loads(s)
        remote_ver = (manifest.get("version") or "").strip()
        if remote_ver and is_newer(remote_ver, current_version):
            return manifest
        return None
    except (URLError, HTTPError, OSError, ValueError):
        return None

def download_update(manifest: dict) -> str | None:
    # Download installer naar %LOCALAPPDATA%\VakantieRooster\updates en verifieer optionele sha256.
    # Retourneert pad naar gedownloade installer of None.
    url = (manifest.get("url") or "").strip()
    if not url:
        return None
    fn = os.path.basename(url.split("?")[0])
    dest = os.path.join(_updates_dir(), fn)
    try:
        _download_binary(url, dest)
        want_sha = (manifest.get("sha256") or "").strip().lower()
        if want_sha:
            calc = _sha256(dest).lower()
            if calc != want_sha:
                try:
                    os.remove(dest)
                except Exception:
                    pass
                return None
        return dest
    except Exception:
        return None

def launch_installer_and_exit(installer_path: str, silent: bool = True) -> None:
    # Start installer en beëindig huidige app zodat bestanden vrijgegeven worden.
    # Inno Setup voorbeeld-flags: /VERYSILENT /NORESTART /SUPPRESSMSGBOXES /SP-
    # Voor MSI zou je msiexec /i <pad> /qn kunnen gebruiken.
    if not os.path.exists(installer_path):
        return
    args = [installer_path]
    if silent:
        args += ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/SP-"]
    try:
        subprocess.Popen(args, close_fds=True)
    except Exception:
        # fallback naar niet-silent
        try:
            subprocess.Popen([installer_path], close_fds=True)
        except Exception:
            pass
    os._exit(0)

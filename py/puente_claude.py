"""Puente entre la web del coche y TU Claude (cuenta Pro), sin API ni pago por uso.

Corre en el portátil-servidor (Acer), donde Claude Code ya está instalado y autenticado con la cuenta Pro.
La web le manda el JSON del chequeo de coherencia; el puente ejecuta `claude -p` con las instrucciones
de taller (coherencia.PROMPT_TALLER) y devuelve el texto. Solo hace eso: no acepta otras preguntas ni
otras instrucciones, y Claude se ejecuta en una carpeta vacía y sin herramientas.

Arranque (en la carpeta py/, junto a coherencia.py):

    python3 puente_claude.py                 # escucha en 127.0.0.1:8091
    tailscale serve --bg 8091                # lo publica con HTTPS dentro de tu red Tailscale

La primera vez crea una llave (puente_llave.txt, no se sube a git): pégala en la web, pestaña 🔧 Chequeo,
junto con la dirección https://<nombre-del-acer>.<tu-red>.ts.net que te dé `tailscale serve status`.

Hace falta HTTPS porque la app del móvil está en GitHub Pages (https) y el navegador no deja llamar a http.

Para que arranque solo al encender el Acer (systemd de usuario):
    ~/.config/systemd/user/puente-claude.service
        [Unit]
        Description=Puente chequeo coche -> Claude Code
        [Service]
        WorkingDirectory=%h/coche-obd            # la carpeta donde estén puente_claude.py y coherencia.py
        ExecStart=/usr/bin/python3 puente_claude.py
        Restart=on-failure
        [Install]
        WantedBy=default.target
    systemctl --user enable --now puente-claude && loginctl enable-linger $USER
"""
import argparse
import hmac
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from coherencia import PROMPT_TALLER

BASE = Path(__file__).parent
LLAVE_F = BASE / "puente_llave.txt"
MAX_CUERPO = 64_000
TIEMPO = 240  # segundos como mucho por análisis
UNO_A_LA_VEZ = threading.Lock()
CFG = {"llave": "", "claude": "claude", "modelo": None}


def llave():
    if LLAVE_F.is_file() and LLAVE_F.read_text().strip():
        return LLAVE_F.read_text().strip()
    k = secrets.token_urlsafe(24)
    LLAVE_F.write_text(k + "\n")
    os.chmod(LLAVE_F, 0o600)
    return k


def preguntar_a_claude(datos):
    """Ejecuta Claude Code en modo no interactivo con el prompt de taller. Devuelve (ok, texto o error, modelo)."""
    mensaje = "Datos OBD2 capturados (JSON):\n" + json.dumps(datos, ensure_ascii=False, indent=1)
    base = [CFG["claude"], "-p", mensaje, "--system-prompt", PROMPT_TALLER, "--output-format", "json", "--max-turns", "2"]
    if CFG["modelo"]:
        base += ["--model", CFG["modelo"]]
    with tempfile.TemporaryDirectory(prefix="chequeo_") as vacia:  # sin archivos ni CLAUDE.md a la vista
        intentos = [base + ["--tools", ""], base]  # sin herramientas; si esta versión no conoce --tools, sin él
        for i, cmd in enumerate(intentos):
            try:
                r = subprocess.run(cmd, cwd=vacia, capture_output=True, text=True, timeout=TIEMPO)
            except FileNotFoundError:
                return False, f"No encuentro el programa «{CFG['claude']}» en el Acer (¿Claude Code instalado?).", None
            except subprocess.TimeoutExpired:
                return False, f"Claude ha tardado más de {TIEMPO} s.", None
            if r.returncode != 0 and i == 0 and "tools" in (r.stderr or "").lower():
                continue
            break
    try:
        j = json.loads(r.stdout)
    except ValueError:
        return False, ("Claude Code no ha contestado bien: " + (r.stderr or r.stdout or "sin salida").strip())[:600], None
    texto = str(j.get("result") or "").strip()
    if r.returncode != 0 or j.get("is_error") or not texto:
        return False, ("Claude Code devolvió un error: " + (texto or r.stderr or j.get("subtype") or "?")).strip()[:600], None
    modelo = j.get("model") or next(iter(j.get("modelUsage") or {}), None) or "Claude (cuenta Pro)"
    return True, texto, modelo


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print("[puente]", self.address_string(), fmt % a)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")

    def _json(self, dato, codigo=200):
        cuerpo = json.dumps(dato, ensure_ascii=False).encode()
        self.send_response(codigo)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _autorizado(self):
        h = self.headers.get("Authorization") or ""
        return h.startswith("Bearer ") and hmac.compare_digest(h[7:].strip().encode(), CFG["llave"].encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.split("?")[0] != "/salud":
            return self._json({"ok": False, "error": "No existe"}, 404)
        if not self._autorizado():
            return self._json({"ok": False, "error": "Llave incorrecta"}, 401)
        return self._json({"ok": True, "claude": bool(shutil.which(CFG["claude"])), "ocupado": UNO_A_LA_VEZ.locked()})

    def do_POST(self):
        if self.path.split("?")[0] != "/analizar":
            return self._json({"ok": False, "error": "No existe"}, 404)
        if not self._autorizado():
            return self._json({"ok": False, "error": "Llave incorrecta"}, 401)
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= MAX_CUERPO:
            return self._json({"ok": False, "error": "Datos vacíos o demasiado grandes"}, 413)
        try:
            datos = json.loads(self.rfile.read(n)).get("datos")
        except (ValueError, AttributeError):
            datos = None
        if not isinstance(datos, dict) or "tabla_ralenti_vs_2500" not in datos:
            return self._json({"ok": False, "error": "Eso no es un chequeo de coherencia"}, 400)
        if not UNO_A_LA_VEZ.acquire(blocking=False):
            return self._json({"ok": False, "error": "Ya hay un análisis en marcha: espera a que termine."}, 409)
        try:
            ok, texto, modelo = preguntar_a_claude(datos)
        finally:
            UNO_A_LA_VEZ.release()
        return self._json({"ok": True, "texto": texto, "modelo": modelo} if ok else {"ok": False, "error": texto},
                          200 if ok else 502)


def main():
    ap = argparse.ArgumentParser(description="Puente web del coche → Claude Code (cuenta Pro)")
    ap.add_argument("--puerto", type=int, default=8091)
    ap.add_argument("--escuchar", default="127.0.0.1", help="127.0.0.1 (con tailscale serve) o la IP de Tailscale")
    ap.add_argument("--claude", default="claude", help="ruta del programa claude si no está en el PATH")
    ap.add_argument("--modelo", help="modelo para Claude Code (por defecto, el de tu cuenta)")
    a = ap.parse_args()
    CFG.update(llave=llave(), claude=a.claude, modelo=a.modelo)
    if not shutil.which(a.claude):
        print(f"OJO: no encuentro «{a.claude}». Instala Claude Code o pasa --claude /ruta/a/claude")
    print(f"Puente escuchando en http://{a.escuchar}:{a.puerto}")
    print(f"Llave (pégala en la web, pestaña 🔧 Chequeo): {CFG['llave']}")
    print("Para HTTPS dentro de Tailscale:  tailscale serve --bg", a.puerto)
    ThreadingHTTPServer((a.escuchar, a.puerto), Manejador).serve_forever()


if __name__ == "__main__":
    main()

"""Servidor de la web del coche.

  python3 servidor.py            → abre http://localhost:8090
  python3 servidor.py --demo     → igual, pero con un coche simulado

Desde el móvil (misma wifi o punto de acceso del móvil): http://<ip-del-portátil>:8090
"""
import argparse
import base64
import csv
import io
import json
import queue
import re
import shutil
import signal
import socket
import threading
import time
import traceback
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import analisis
import diagnostico
import pids
import rutas
from elm327 import ELM327, ErrorLector, abrir_transporte, puertos_candidatos

BASE = Path(__file__).parent
WEB = BASE / "web"
REGISTROS = BASE / "registros"
REGISTROS.mkdir(exist_ok=True)

COCHE = {"modelo": "Dacia Sandero Stepway", "año": 2011, "motor": "K7M 1.6 8V gasolina"}

PRUEBAS = {
    "libre": {"nombre": "Grabación libre"},
    "arranque_frio": {"nombre": "Arranque en frío"},
    "ralenti_caliente": {"nombre": "Ralentí en caliente y acelerones"},
    "trayecto": {"nombre": "Trayecto normal"},
}


# ------------------------------------------------------------------ GPS del móvil

GPS_CLAVES = ("gps_velocidad", "gps_lat", "gps_lon", "gps_alt", "gps_precision")


def _num(x, minimo, maximo):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if minimo <= x <= maximo and x == x else None


def gps_valores(v, precision=None, lat=None, lon=None, alt=None, precision_alt=None):
    """Lo que manda el navegador del móvil, limpio. Lo que no sea creíble queda en None.
    La altura solo se guarda si el móvil dice que es fiable (±20 m o mejor; si no da precisión, se acepta)."""
    v, prec = _num(v, 0, 249.9), _num(precision, 0, 30)
    lat, lon = _num(lat, -90, 90), _num(lon, -180, 180)
    if lat is None or lon is None or (lat == 0 and lon == 0):
        lat = lon = None
    alt, prec_alt = _num(alt, -500, 9000), _num(precision_alt, 0, 1e6)
    if prec_alt is not None and prec_alt > 20:
        alt = None
    return {"gps_velocidad": round(v, 1) if v is not None else None,
            "gps_lat": round(lat, 6) if lat is not None else None,
            "gps_lon": round(lon, 6) if lon is not None else None,
            "gps_alt": round(alt, 1) if alt is not None and lat is not None else None,
            "gps_precision": round(prec) if prec is not None and lat is not None else None}


# ------------------------------------------------------------------ grabación

class Grabacion:
    def __init__(self, prueba, claves, info):
        ahora = datetime.now()
        self.inicio = time.time()
        self.prueba = prueba
        nombre = f"{ahora:%Y-%m-%d_%H%M%S}_{prueba}"
        self.carpeta = REGISTROS / nombre
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.claves = claves
        self.f = open(self.carpeta / "datos.csv", "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["t", "hora"] + claves)
        self.filas = 0
        self.memoria = []  # las mismas filas en memoria, para el diagnóstico en directo
        self.sesion = {"coche": COCHE, "inicio": ahora.isoformat(timespec="seconds"),
                       "inicio_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "prueba": {"tipo": prueba, **PRUEBAS.get(prueba, {})}, "lector": info,
                       "averias": {}, "notas": []}
        self.guardar_sesion()

    def fila(self, valores):
        t = round(time.time() - self.inicio, 2)
        self.memoria.append({"t": t, **{k: valores.get(k) for k in self.claves}})
        self.w.writerow([t, datetime.now().strftime("%H:%M:%S")] +
                        ["" if valores.get(k) is None else valores[k] for k in self.claves])
        self.filas += 1
        if self.filas % 20 == 0:
            self.f.flush()

    def nota(self, texto):
        self.sesion["notas"].append({"t": round(time.time() - self.inicio, 1),
                                     "hora": datetime.now().strftime("%H:%M:%S"), "texto": texto})
        self.guardar_sesion()

    def guardar_sesion(self):
        (self.carpeta / "sesion.json").write_text(json.dumps(self.sesion, ensure_ascii=False, indent=2))

    def cerrar(self, min_filas=10):
        self.sesion["fin"] = datetime.now().isoformat(timespec="seconds")
        self.guardar_sesion()
        self.f.close()
        if self.prueba == "libre" and self.filas < min_filas:  # grabaciones vacías: fuera
            shutil.rmtree(self.carpeta, ignore_errors=True)
            return None
        try:
            return analisis.analizar(self.carpeta)
        except Exception as e:
            traceback.print_exc()
            return {"hallazgos": [{"nivel": "gris", "titulo": "No se pudo analizar", "detalle": str(e)}]}

    def estado(self):
        return {"activa": True, "carpeta": self.carpeta.name, "prueba": self.prueba,
                "nombre": PRUEBAS.get(self.prueba, {}).get("nombre", self.prueba),
                "segundos": round(time.time() - self.inicio), "filas": self.filas,
                "notas": len(self.sesion["notas"])}


# ------------------------------------------------------------------ hilo del lector

class Lector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.ordenes = queue.Queue()
        self.lock = threading.Lock()
        self.elm = None
        self.conexion = {"estado": "desconectado", "mensaje": "Sin conectar"}
        self.valores = {}       # clave -> valor
        self.historial = {}     # clave -> [(t, v)] últimos 40 s
        self.averias = None
        self.grabacion = None
        self.ultimo_resumen = None
        self.lecturas = 0
        self.t_lecturas = time.time()
        self.tasa = 0
        self.diagnostico = None
        self.auto = True  # reconectar solo; se apaga al pulsar Desconectar
        self.ajustes = analisis.ajustes()

    # --- API desde el servidor web (otros hilos)
    def pedir(self, accion, timeout=60, **args):
        resp = queue.Queue()
        self.ordenes.put((accion, args, resp))
        try:
            ok, dato = resp.get(timeout=timeout)
        except queue.Empty:
            return False, "El lector tarda demasiado en responder"
        return ok, dato

    def foto(self):
        with self.lock:
            ctx = dict(self.valores)
            vals = {}
            for k, v in ctx.items():
                estado, mensaje = pids.evaluar(k, v, ctx, self.historial)
                vals[k] = {"v": v, "estado": estado, "mensaje": mensaje, "texto": pids.texto_valor(k, v)}
            return {"conexion": self.conexion, "valores": vals, "tasa": self.tasa,
                    "grabacion": self.grabacion.estado() if self.grabacion else {"activa": False},
                    "averias": self.averias, "resumen": self.ultimo_resumen, "diagnostico": self.diagnostico,
                    "hora": time.time()}

    # --- dentro del hilo
    def run(self):
        ciclo = 0
        while True:
            try:
                accion, args, resp = self.ordenes.get(timeout=0 if self.elm else 0.5)
                self._atender(accion, args, resp)
                continue
            except queue.Empty:
                pass
            if not self.elm:
                continue
            try:
                self._vuelta(ciclo)
                ciclo += 1
            except Exception as e:
                traceback.print_exc()
                self._desconectar(f"Se perdió la conexión: {e}")

    def _atender(self, accion, args, resp):
        try:
            dato = getattr(self, f"_{accion}")(**args)
            resp.put((True, dato))
        except ErrorLector as e:
            resp.put((False, str(e)))
        except Exception as e:
            traceback.print_exc()
            resp.put((False, f"{type(e).__name__}: {e}"))

    def _conectar(self, destino):
        if self.elm and self.conexion.get("estado") == "conectado" and self.tasa:
            return {k: v for k, v in self.conexion.items() if k not in ("estado", "mensaje")}  # ya conectado
        if self.elm:
            es_ble = self.conexion.get("destino", "").startswith("BLE")
            self._desconectar("Reconectando")
            if es_ble:
                time.sleep(3)  # el lector tarda un poco en volver a anunciarse
        intentos = [destino]
        if destino not in ("demo", "ble") and not destino.startswith("ble:"):
            intentos.append("ble")  # lectores de doble modo: en el Mac a veces solo contesta por Bluetooth LE
        error = None
        for i, d in enumerate(intentos):
            try:
                elm, info, t = self._abrir(d, reintento=i > 0)
                break
            except ErrorLector as e:
                error = e
        else:
            self.conexion = {"estado": "error", "mensaje": str(error)}
            raise error
        if destino != "demo":
            self.ajustes["ultimo_lector"] = d
            (BASE / "ajustes.json").write_text(json.dumps(self.ajustes, ensure_ascii=False, indent=2))
        info["destino"] = t.nombre
        self.elm = elm
        self.conexion = {"estado": "conectado", "mensaje": f"Conectado: {info['protocolo']}", **info}
        with self.lock:
            self.valores, self.historial = {}, {}
        self.ultima_ok = time.time()
        self._leer_averias()
        self._empezar(prueba="libre")
        return info

    def _abrir(self, destino, reintento=False):
        nombre = "Bluetooth LE" if destino.startswith("ble") else destino
        self.conexion = {"estado": "conectando", "mensaje":
                         ("No contestó; probando por Bluetooth LE…" if reintento else f"Conectando con {nombre}…")}
        try:
            t = abrir_transporte(destino)
        except ErrorLector:
            raise
        except Exception as e:
            raise ErrorLector(f"No se pudo abrir «{nombre}». ¿Está el lector enchufado y con el contacto puesto? ({e})")
        self.conexion = {"estado": "conectando", "mensaje": "Despertando al coche (puede tardar hasta 15 segundos)…"}
        elm = ELM327(t)
        try:
            return elm, elm.iniciar(), t
        except Exception as e:
            elm.cerrar()
            raise ErrorLector(str(e))

    def _desconectar(self, motivo="Desconectado"):
        if self.grabacion:
            self.ultimo_resumen = self.grabacion.cerrar()
            self.grabacion = None
        if self.elm:
            self.elm.cerrar()
        self.elm = None
        self.conexion = {"estado": "desconectado", "mensaje": motivo}
        return True

    def _claves(self):
        claves = [pids.DEFINICIONES[p][0] for p in dict.fromkeys(pids.RAPIDOS + pids.LENTOS + pids.MUY_LENTOS)
                  if p in self.elm.soportados]
        return claves + ["bateria", "consumo_lh", "consumo_l100"] + list(GPS_CLAVES)

    def _empezar(self, prueba="libre"):
        if not self.elm:
            raise ErrorLector("Primero conecta con el coche")
        if self.grabacion:
            # Si acaba de conectar y en <20 s empieza una prueba guiada, descartar el trocito libre inicial
            min_f = 80 if (self.grabacion.prueba == "libre" and prueba != "libre") else 10
            self.ultimo_resumen = self.grabacion.cerrar(min_filas=min_f)
        self.grabacion = Grabacion(prueba, self._claves(), self.elm.info)
        self.grabacion.sesion["correccion_velocidad"] = self._corr_velocidad()
        self.grabacion.guardar_sesion()
        if self.averias:
            self.grabacion.sesion["averias"] = self.averias
            self.grabacion.guardar_sesion()
        return self.grabacion.estado()

    def _parar(self):
        if not self.grabacion:
            return None
        self.ultimo_resumen = self.grabacion.cerrar()
        self.grabacion = None
        return self.ultimo_resumen

    def _nota(self, texto):
        if not self.grabacion:
            raise ErrorLector("No hay ninguna grabación en marcha")
        self.grabacion.nota(texto)
        return True

    def _leer_averias(self):
        if not self.elm:
            raise ErrorLector("Primero conecta con el coche")
        e = self.elm
        res = {"hora": datetime.now().strftime("%H:%M:%S"),
               "guardadas": e.averias("03"), "pendientes": e.averias("07"),
               "permanentes": e.averias("0A"), "monitores": e.monitores(), "foto": e.foto_averia()}
        for pid in (0x31, 0x30, 0x21, 0x4E):
            if pid in e.soportados:
                res[pids.DEFINICIONES[pid][0]] = e.leer(pid)
        if "vin" not in e.info:
            e.info["vin"] = e.vin()
        res["vin"] = e.info.get("vin")
        self.averias = res
        if self.grabacion:
            self.grabacion.sesion["averias"] = res
            self.grabacion.guardar_sesion()
        return res

    def _borrar_averias(self):
        if not self.elm:
            raise ErrorLector("Primero conecta con el coche")
        if (self.valores.get("rpm") or 0) > 400:
            raise ErrorLector("Para borrar averías el motor tiene que estar APAGADO (solo el contacto puesto).")
        antes = self.averias
        ok = self.elm.borrar_averias()
        if self.grabacion:
            self.grabacion.nota("Averías borradas desde la web")
            self.grabacion.sesion["averias_antes_de_borrar"] = antes
        time.sleep(1)
        self._leer_averias()
        return ok

    def _corr_bateria(self):
        if "Simulador" in (self.conexion.get("destino") or ""):
            return 0.0
        return self.ajustes.get("correccion_bateria", 0)

    def _corr_velocidad(self):
        """La centralita marca menos que el GPS (ruedas más grandes que su calibración): factor en ajustes.json."""
        if "Simulador" in (self.conexion.get("destino") or ""):
            return 1.0
        return self.ajustes.get("correccion_velocidad", 1.0)

    def poner_gps(self, v, precision=None, lat=None, lon=None, alt=None, precision_alt=None):
        """GPS del móvil: velocidad (para comparar con la del coche), posición y altura (para ver el trayecto y las cuestas)."""
        with self.lock:
            self.gps = {"t": time.time(), **gps_valores(v, precision, lat, lon, alt, precision_alt)}

    def _coche_callado(self):
        """Contacto quitado o centralita dormida: no hay respuestas. Reintenta despertarla cada pocos segundos."""
        with self.lock:
            self.valores = {k: v for k, v in self.valores.items() if k == "bateria"}
            self.historial = {}
        self.conexion = {**self.conexion, "mensaje": "El coche no contesta (¿contacto quitado?). En cuanto lo pongas sigo solo…"}
        self.elm.comando("ATPC")  # cerrar el protocolo para que el lector vuelva a despertar a la centralita
        r = self.elm.comando("0100", timeout=12)
        if self.elm._datos_modo01(r, 0x00):
            self.ultima_ok = time.time()
            self.conexion = {**self.conexion, "mensaje": f"Conectado: {self.elm.info.get('protocolo', '')}"}
            if self.grabacion:
                self.grabacion.nota("El coche vuelve a contestar")
        else:
            v = self.elm.bateria()
            self._apuntar("bateria", round(v + self._corr_bateria(), 2) if v is not None else None)
            time.sleep(3)

    def _vuelta(self, ciclo):
        if time.time() - getattr(self, "ultima_ok", time.time()) > 8:
            return self._coche_callado()
        lista = [p for p in pids.RAPIDOS if p in self.elm.soportados]
        if ciclo % 5 == 0:
            lista += [p for p in pids.LENTOS if p in self.elm.soportados]
        if ciclo % 60 == 0:
            lista += [p for p in pids.MUY_LENTOS if p in self.elm.soportados]
        for pid in lista:
            if not self.ordenes.empty():
                break  # atender antes las órdenes de la web
            v = self.elm.leer(pid)
            if v is not None:
                self.ultima_ok = time.time()
            self._apuntar(pids.DEFINICIONES[pid][0], v)
            self._fila()
        if ciclo % 5 == 0:
            v = self.elm.bateria()
            # el lector marca algo menos que el polímetro: corrección medida (ajustes.json)
            self._apuntar("bateria", round(v + self._corr_bateria(), 2) if v is not None else None)

    def _fila(self):
        """Calcula el gasto y guarda una fila del CSV por cada lectura, con el último valor de todo."""
        with self.lock:
            self.valores.update(diagnostico.derivados(self.valores, self.ajustes.get("factor_consumo", 1.0)))
            g = getattr(self, "gps", None)
            fresco = g and time.time() - g["t"] < 4
            for k in GPS_CLAVES:
                if fresco and g.get(k) is not None:
                    self.valores[k] = g[k]
                else:
                    self.valores.pop(k, None)
            if self.grabacion:
                self.grabacion.fila(self.valores)

    def _historial_mezcla(self):
        g = self.grabacion
        if not g or "Simulador" in ((g.sesion.get("lector") or {}).get("destino") or ""):
            return []
        return analisis.historial_mezcla(antes_de=g.carpeta.name)

    def analizar_en_directo(self):
        """Hilo aparte: cada 4 s cruza todos los datos grabados desde que conectaste."""
        while True:
            time.sleep(4)
            try:
                with self.lock:
                    filas = list(self.grabacion.memoria) if self.grabacion else []
                    av = self.averias if self.elm else None
                d = diagnostico.diagnosticar(filas, av, self.ajustes.get("consumo_real"), self.ajustes.get("factor_consumo"),
                                             corr_velocidad=self._corr_velocidad(), historial_mezcla=self._historial_mezcla())
                d["filas"] = len(filas)
                self.diagnostico = d
            except Exception:
                traceback.print_exc()

    def _apuntar(self, clave, v):
        ahora = time.time()
        with self.lock:
            if v is None:
                return
            if clave == "velocidad" and v:
                v = round(v * self._corr_velocidad())  # vale también para la app del móvil (movil_py)
            self.valores[clave] = v
            h = self.historial.setdefault(clave, [])
            h.append((ahora, v))
            while h and h[0][0] < ahora - 40:
                h.pop(0)
            self.lecturas += 1
            if ahora - self.t_lecturas >= 3:
                self.tasa = round(self.lecturas / (ahora - self.t_lecturas), 1)
                self.lecturas, self.t_lecturas = 0, ahora


LECTOR = Lector()


# ------------------------------------------------------------------ web

def recuperar_grabaciones():
    """Grabaciones que se quedaron sin cerrar (cierre brusco): se cierran y se revisan."""
    hechas = []
    for c in sorted(REGISTROS.iterdir()):
        if not c.is_dir():
            continue
        s_p = c / "sesion.json"
        if not s_p.exists():
            continue
        try:
            s = json.loads(s_p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if s.get("fin"):
            continue
        if LECTOR.grabacion and LECTOR.grabacion.carpeta == c:
            continue
        csv_p = c / "datos.csv"
        filas = max(0, sum(1 for _ in open(csv_p)) - 1) if csv_p.exists() else 0
        if filas < 10:
            shutil.rmtree(c, ignore_errors=True)
            continue
        s["fin"] = datetime.fromtimestamp(csv_p.stat().st_mtime).isoformat(timespec="seconds")
        s.setdefault("notas", []).append({"t": 0, "hora": "", "texto": "Grabación cerrada al volver a abrir la app"})
        s_p.write_text(json.dumps(s, ensure_ascii=False, indent=2))
        try:
            analisis.analizar(c)
        except Exception:
            pass
        hechas.append(c.name)
    return hechas


def leer_claude():
    """Pestaña «Claude» de la web: MENSAJES.md (lo último que Claude le dice a Saúl), HISTORIAL.md y BUZON.md."""
    def leer(n):
        p = BASE / n
        return p.read_text() if p.exists() else ""
    return {"mensajes": leer("MENSAJES.md"), "historial": leer("HISTORIAL.md"), "buzon": leer("BUZON.md"),
            "origen": "Leído en el Mac (~/coche-obd). Lo que escribas desde el móvil llega al hacer git pull."}


def escribir_buzon(texto):
    """Mensaje de Saúl para Claude: se añade al final de BUZON.md."""
    texto = str(texto or "").strip()[:4000]
    if not texto:
        return False, "Mensaje vacío"
    p = BASE / "BUZON.md"
    previo = p.read_text() if p.exists() else BUZON_CABECERA
    p.write_text(previo.rstrip() + f"\n\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} · Saúl (desde el Mac)\n{texto}\n")
    return True, "Guardado en el buzón"


BUZON_CABECERA = ("# Buzón: mensajes de Saúl para Claude\n\n"
                  "Lo escribe Saúl desde la web (pestaña 💬 Claude). Claude lo lee al decirle «lee el buzón del coche» "
                  "y contesta en MENSAJES.md.\n")


def lista_sesiones():
    out = []
    for c in sorted(REGISTROS.iterdir(), reverse=True):
        if not c.is_dir() or not (c / "sesion.json").exists():
            continue
        try:
            s = json.loads((c / "sesion.json").read_text())
            r = json.loads((c / "resumen.json").read_text()) if (c / "resumen.json").exists() else None
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"carpeta": c.name, "inicio": s.get("inicio"), "prueba": s.get("prueba"),
                    "lector": (s.get("lector") or {}).get("destino"),
                    "fin": s.get("fin"), "notas": s.get("notas", []), "resumen": r,
                    "archivos": [p.name for p in sorted(c.iterdir()) if p.is_file()]})
    return out


def detalle_sesion(carpeta):
    """Datos resumidos y series temporales (hasta ~360 puntos) para el visor interactivo de una grabación."""
    if not re.fullmatch(r"[\w\-]+", str(carpeta or "")):
        return None
    c = REGISTROS / carpeta
    if not c.is_dir() or c.parent != REGISTROS or not (c / "datos.csv").exists():
        return None
    filas, sesion = analisis.cargar(c)
    resumen = json.loads((c / "resumen.json").read_text()) if (c / "resumen.json").exists() else analisis.analizar(c)
    paso = max(1, len(filas) // 360)
    muestra = filas[::paso]
    if filas and (not muestra or muestra[-1] is not filas[-1]):
        muestra.append(filas[-1])
    claves = ["t", "rpm", "velocidad", "temp_motor", "map", "stft", "ltft", "sonda1", "sonda2",
              "avance", "bateria", "consumo_lh", "consumo_l100", "acelerador", "carga", "temp_admision", "sistema_combustible", "gps_velocidad", "gps_alt"]
    series = {k: [f.get(k) for f in muestra] for k in claves if any(f.get(k) is not None for f in muestra)}
    return {"carpeta": c.name, "sesion": sesion, "resumen": resumen, "series": series, "total_filas": len(filas)}


def borrar_sesion(carpeta):
    if not re.fullmatch(r"[\w\-]+", str(carpeta or "")):
        return False
    c = REGISTROS / carpeta
    if LECTOR.grabacion and LECTOR.grabacion.carpeta == c:
        return False
    if c.is_dir() and c.parent == REGISTROS:
        shutil.rmtree(c)
        return True
    return False


def reanalizar_sesiones(carpeta=None):
    """Vuelve a pasar analisis.analizar sobre una grabación o sobre todas."""
    carpetas = [REGISTROS / carpeta] if carpeta else [
        c for c in sorted(REGISTROS.iterdir()) if c.is_dir() and (c / "datos.csv").exists()
    ]
    n = 0
    for c in carpetas:
        if c.is_dir() and c.parent == REGISTROS and (c / "datos.csv").exists():
            try:
                analisis.analizar(c)
                n += 1
            except Exception:
                traceback.print_exc()
    return n


def importar_archivos(archivos):
    """Importa grabaciones desde archivos .zip o sueltos (<carpeta>__<nombre>).
    archivos: lista de {"nombre": str, "datos": bytes o base64 str}.
    """
    tocadas = set()
    for item in archivos or []:
        nombre = Path(item.get("nombre") or "").name
        crudo = item.get("datos")
        if isinstance(crudo, str):
            crudo = base64.b64decode(crudo)
        if not nombre or not crudo:
            continue
        if nombre.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(crudo)) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    partes = Path(info.filename).parts
                    if len(partes) == 2 and re.fullmatch(r"[\w\-]+", partes[0]) and re.fullmatch(r"[\w\-.]+", partes[1]):
                        dest = REGISTROS / partes[0]
                        dest.mkdir(parents=True, exist_ok=True)
                        (dest / partes[1]).write_bytes(z.read(info.filename))
                        tocadas.add(partes[0])
                    elif len(partes) == 1 and re.fullmatch(r"[\w\-.]+", partes[0]):
                        carp = re.sub(r"^coche_|\.zip$", "", nombre)
                        if re.fullmatch(r"[\w\-]+", carp):
                            dest = REGISTROS / carp
                            dest.mkdir(parents=True, exist_ok=True)
                            (dest / partes[0]).write_bytes(z.read(info.filename))
                            tocadas.add(carp)
        elif "__" in nombre:
            carp, resto = nombre.split("__", 1)
            if resto.endswith(".json.txt"):
                resto = resto[:-4]
            if re.fullmatch(r"[\w\-]+", carp) and re.fullmatch(r"[\w\-.]+", resto):
                dest = REGISTROS / carp
                dest.mkdir(parents=True, exist_ok=True)
                (dest / resto).write_bytes(crudo)
                tocadas.add(carp)
    out = []
    for carp in sorted(tocadas):
        c = REGISTROS / carp
        if (c / "datos.csv").exists():
            if not (c / "sesion.json").exists():
                (c / "sesion.json").write_text(json.dumps({
                    "coche": COCHE, "inicio": datetime.now().isoformat(timespec="seconds"),
                    "fin": datetime.now().isoformat(timespec="seconds"),
                    "prueba": {"tipo": "libre", "nombre": "Grabación importada"},
                    "lector": {"destino": "Móvil"}, "averias": {}, "notas": [],
                }, ensure_ascii=False, indent=2))
            try:
                analisis.analizar(c)
            except Exception:
                traceback.print_exc()
            out.append(carp)
    return out


def definiciones():
    return {"pids": pids.lista_definiciones(), "coche": COCHE, "sistemas": diagnostico.SISTEMAS,
            "nombres_sistema": diagnostico.NOMBRE_SISTEMA}


def guardar_ajustes(cuerpo):
    """Cambia los ajustes que manda la web. Devuelve (ok, ajustes o texto del error)."""
    aj = dict(LECTOR.ajustes)
    for k, lim in (("consumo_real", (2, 30)), ("factor_consumo", (0.5, 2)), ("correccion_bateria", (-2, 2)),
                   ("correccion_velocidad", (0.8, 1.3))):
        if k in cuerpo:
            try:
                v = float(str(cuerpo[k]).replace(",", "."))
            except (TypeError, ValueError):
                v = None
            if cuerpo[k] in ("", None):
                aj.pop(k, None)
            elif v is None:
                return False, "Eso no es un número"
            elif lim[0] <= v <= lim[1]:
                aj[k] = round(v, 3)
            else:
                return False, f"Valor fuera de rango ({lim[0]}-{lim[1]})"
    (BASE / "ajustes.json").write_text(json.dumps(aj, ensure_ascii=False, indent=2))
    antes = LECTOR._corr_velocidad()
    LECTOR.ajustes = aj
    g = LECTOR.grabacion
    if g and abs(LECTOR._corr_velocidad() - antes) > 1e-6:
        # cambiada a mitad de grabación: apuntar desde cuándo, para que la revisión reescale bien cada tramo
        g.sesion.setdefault("cambios_correccion_velocidad", []).append(
            {"t": round(time.time() - g.inicio, 1), "valor": LECTOR._corr_velocidad()})
        g.nota(f"Corrección de velocidad cambiada a {LECTOR._corr_velocidad()}")
    return True, aj


def leer_mantenimiento():
    try:
        return json.loads((BASE / "mantenimiento.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"pendientes_itv_dic2026": [], "historial": []}


def mantenimiento_md(mant):
    """MANTENIMIENTO.md se genera siempre a partir de mantenimiento.json (que es el que manda)."""
    lineas = ["# Historial de Mantenimiento — Dacia Sandero Stepway 2011 (K7M 1.6 8V)", "",
              "_Lo escribe la web a partir de `mantenimiento.json`: lo que se cambie aquí a mano se pierde._", "",
              "## Pendiente antes de la ITV (Diciembre 2026)"]
    for x in mant.get("pendientes_itv_dic2026", []):
        marca = "x" if x.get("hecho") else " "
        est = "Hecho ✅" if x.get("hecho") else ("comprado ✅" if x.get("comprado") else "por comprar")
        lineas.append(f"- [{marca}] **{x.get('tarea', '')}** *({est})*")
    lineas += ["", "---", "", "## Historial realizado"]
    for h in mant.get("historial", []):
        cab = f"### {h.get('fecha', '')}" + (f" — {int(h['km']):,} km".replace(",", ".") if h.get("km") else "")
        lineas += ["", cab] + [f"- {it}" for it in h.get("items", [])]
    return "\n".join(lineas) + "\n"


def _guardar_mantenimiento_md(mant):
    md_p = BASE / "MANTENIMIENTO.md"
    if md_p.exists():  # en el móvil no hay copia: se genera al subirlo a GitHub
        md_p.write_text(mantenimiento_md(mant))


def marcar_mantenimiento(cuerpo):
    mant = leer_mantenimiento()
    pend = mant.setdefault("pendientes_itv_dic2026", [])
    hist = mant.setdefault("historial", [])
    if "indice" in cuerpo and 0 <= int(cuerpo["indice"]) < len(pend):
        item = pend[int(cuerpo["indice"])]
        if "hecho" in cuerpo:
            item["hecho"] = bool(cuerpo["hecho"])
        if "comprado" in cuerpo:
            item["comprado"] = bool(cuerpo["comprado"])
    if "nueva_tarea" in cuerpo:
        texto = str(cuerpo["nueva_tarea"]).strip()[:200]
        if texto:
            pend.append({"tarea": texto, "comprado": bool(cuerpo.get("comprado")), "hecho": False})
    if "borrar_indice" in cuerpo and 0 <= int(cuerpo["borrar_indice"]) < len(pend):
        pend.pop(int(cuerpo["borrar_indice"]))
    if "nuevo_historial" in cuerpo and isinstance(cuerpo["nuevo_historial"], dict):
        nh = cuerpo["nuevo_historial"]
        fecha = str(nh.get("fecha") or datetime.now().strftime("%Y-%m-%d")).strip()[:30]
        km_raw = str(nh.get("km") or "").replace(".", "").replace(",", "").strip()
        km = int(km_raw) if km_raw.isdigit() else None
        items = [str(x).strip()[:200] for x in (nh.get("items") or []) if str(x).strip()]
        if items:
            hist.insert(0, {"fecha": fecha, "km": km, "items": items})
    (BASE / "mantenimiento.json").write_text(json.dumps(mant, ensure_ascii=False, indent=2))
    try:
        _guardar_mantenimiento_md(mant)
    except Exception:
        pass
    return mant


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, dato, codigo=200):
        cuerpo = json.dumps(dato, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _archivo(self, ruta, tipo, descarga=False):
        cuerpo = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        if descarga:
            self.send_header("Content-Disposition", f'attachment; filename="{ruta.parent.name}_{ruta.name}"')
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = urlparse(self.path).path
        if ruta in ("/", "/index.html"):
            return self._archivo(WEB / "index.html", "text/html; charset=utf-8")
        if ruta == "/gps":
            return self._archivo(WEB / "gps.html", "text/html; charset=utf-8")
        if ruta == "/api/estado":
            return self._json(LECTOR.foto())
        if ruta == "/api/definiciones":
            return self._json(definiciones())
        if ruta == "/api/ajustes":
            return self._json(LECTOR.ajustes)
        if ruta == "/api/puertos":
            return self._json({"puertos": puertos_candidatos()})
        if ruta == "/api/sesiones":
            return self._json(lista_sesiones())
        if ruta == "/api/mantenimiento":
            return self._json(leer_mantenimiento())
        if ruta == "/api/claude":
            return self._json(leer_claude())
        if ruta == "/api/rutas":
            return self._json(rutas.lista(REGISTROS, BASE))
        m_ruta = re.fullmatch(r"/api/ruta/([\w\-]+)", ruta)
        if m_ruta:
            d = rutas.detalle(REGISTROS, unquote(m_ruta.group(1)))
            return self._json(d if d else {"error": "No existe"}, 200 if d else 404)
        m_ses = re.fullmatch(r"/api/sesion/([\w\-]+)", ruta)
        if m_ses:
            d = detalle_sesion(unquote(m_ses.group(1)))
            return self._json(d if d else {"error": "No existe"}, 200 if d else 404)
        m = re.fullmatch(r"/registros/([\w\-]+)/([\w\-.]+)", ruta)
        if m:
            p = REGISTROS / unquote(m.group(1)) / unquote(m.group(2))
            if p.is_file() and p.resolve().is_relative_to(REGISTROS.resolve()):
                tipo = {".csv": "text/csv", ".json": "application/json", ".txt": "text/plain",
                        ".gpx": "application/gpx+xml"}.get(p.suffix, "application/octet-stream")
                return self._archivo(p, tipo + "; charset=utf-8", descarga=True)
        if ruta.startswith("/movil"):
            sub = ruta[len("/movil"):].lstrip("/") or "index.html"
            base_m = (BASE / "movil" / "app") if (BASE / "movil" / "app").is_dir() else (BASE / "movil")
            p = (base_m / sub).resolve()
            if p.is_file() and p.is_relative_to(base_m.resolve()):
                tipos = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                         ".json": "application/json; charset=utf-8", ".webmanifest": "application/manifest+json; charset=utf-8",
                         ".png": "image/png", ".wasm": "application/wasm", ".zip": "application/zip", ".py": "text/plain; charset=utf-8"}
                return self._archivo(p, tipos.get(p.suffix, "application/octet-stream"))
        if ruta == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    datos = json.dumps(LECTOR.foto(), ensure_ascii=False)
                    self.wfile.write(f"data: {datos}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                return
        self._json({"error": "No existe"}, 404)

    def _origen_ajeno(self):
        """Una página de otra web abierta en el navegador no puede dar órdenes (borrar averías, grabaciones…).
        El navegador manda siempre «Origin» en esos casos; la propia web manda el suyo, que coincide con «Host»."""
        origen = self.headers.get("Origin")
        if not origen:
            return False
        propios = {self.headers.get("Host"), self.headers.get("X-Forwarded-Host")} - {None}
        return urlparse(origen).netloc not in propios

    def do_POST(self):
        if self._origen_ajeno():
            return self._json({"ok": False, "error": "Orden rechazada: no viene de la web del coche"}, 403)
        ruta = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            cuerpo = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            cuerpo = {}
        acciones = {
            "/api/conectar": ("conectar", {"destino": cuerpo.get("destino", "")}),
            "/api/desconectar": ("desconectar", {}),
            "/api/averias": ("leer_averias", {}),
            "/api/empezar": ("empezar", {"prueba": cuerpo.get("prueba", "libre")}),
            "/api/parar": ("parar", {}),
            "/api/nota": ("nota", {"texto": str(cuerpo.get("texto", ""))[:300] or "Marca"}),
        }
        if ruta == "/api/gps":
            g = gps_valores(cuerpo.get("v"), cuerpo.get("precision"), cuerpo.get("lat"), cuerpo.get("lon"),
                            cuerpo.get("alt"), cuerpo.get("precision_alt"))
            if not any(g.get(k) is not None for k in ("gps_velocidad", "gps_lat")):
                return self._json({"ok": False, "error": "Ni velocidad ni posición válidas"}, 400)
            LECTOR.poner_gps(cuerpo.get("v"), cuerpo.get("precision"), cuerpo.get("lat"), cuerpo.get("lon"),
                             cuerpo.get("alt"), cuerpo.get("precision_alt"))
            return self._json({"ok": True})
        if ruta == "/api/borrar":
            if cuerpo.get("confirmo") is not True:
                return self._json({"ok": False, "error": "Falta confirmar"}, 400)
            ok, dato = LECTOR.pedir("borrar_averias")
            return self._json({"ok": ok, "dato" if ok else "error": dato})
        if ruta == "/api/ajustes":
            ok, dato = guardar_ajustes(cuerpo)
            return self._json({"ok": ok, "dato" if ok else "error": dato}, 200 if ok else 400)
        if ruta == "/api/mantenimiento":
            return self._json({"ok": True, "dato": marcar_mantenimiento(cuerpo)})
        if ruta == "/api/buzon":
            ok, dato = escribir_buzon(cuerpo.get("texto"))
            return self._json({"ok": ok, "dato" if ok else "error": dato}, 200 if ok else 400)
        if ruta == "/api/borrar_sesion":
            ok = borrar_sesion(cuerpo.get("carpeta"))
            return self._json({"ok": ok, "error": None if ok else "No se puede borrar la grabación en marcha"}, 200 if ok else 400)
        if ruta == "/api/ruta_nombre":
            ok, dato = rutas.poner_nombre(BASE, cuerpo.get("id"), cuerpo.get("nombre"))
            return self._json({"ok": ok, "dato" if ok else "error": dato}, 200 if ok else 400)
        if ruta == "/api/reanalizar":
            n_ok = reanalizar_sesiones(cuerpo.get("carpeta"))
            return self._json({"ok": True, "dato": n_ok})
        if ruta == "/api/importar":
            try:
                imp = importar_archivos(cuerpo.get("archivos"))
                return self._json({"ok": bool(imp), "dato": imp, "error": None if imp else "No se encontró ninguna grabación válida en esos archivos"}, 200 if imp else 400)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
        if ruta not in acciones:
            return self._json({"error": "No existe"}, 404)
        if ruta in ("/api/conectar", "/api/desconectar"):
            LECTOR.auto = ruta == "/api/conectar"
        accion, args = acciones[ruta]
        ok, dato = LECTOR.pedir(accion, **args)
        self._json({"ok": ok, "dato" if ok else "error": dato}, 200 if ok else 409)


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description="Lector OBD del Sandero")
    ap.add_argument("--puerto", type=int, default=8090)
    ap.add_argument("--demo", action="store_true", help="Conectar al coche simulado al arrancar")
    ap.add_argument("--lector", help="Conectar al arrancar: /dev/cu.XXX, MAC (Linux), 'ble' o 'demo'")
    a = ap.parse_args()
    try:
        prueba = socket.socket(); prueba.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        prueba.bind(("0.0.0.0", a.puerto)); prueba.close()
    except OSError:
        print(f"La web ya está abierta en otro proceso (puerto {a.puerto}). Abre http://localhost:{a.puerto}")
        return
    recuperar_grabaciones()
    LECTOR.start()
    threading.Thread(target=LECTOR.analizar_en_directo, daemon=True).start()
    destino = "demo" if a.demo else a.lector
    if not destino and LECTOR.ajustes.get("ultimo_lector"):
        # conectar solo con el último lector que funcionó, y volver a conectar si se corta
        # (hasta que alguien pulse Desconectar en la web)
        def reconectar():
            while True:
                if LECTOR.auto and not LECTOR.elm and LECTOR.conexion.get("estado") != "conectando":
                    LECTOR.pedir("conectar", destino=LECTOR.ajustes.get("ultimo_lector", "ble"), timeout=90)
                time.sleep(15)
        threading.Thread(target=reconectar, daemon=True).start()
    elif destino:
        ok, dato = LECTOR.pedir("conectar", destino=destino)
        print("Conectado" if ok else f"No se pudo conectar: {dato}")
    # cerrar bien (guardando la grabación) también con kill / al apagar el Mac
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    signal.signal(signal.SIGINT, signal.default_int_handler)
    srv = ThreadingHTTPServer(("0.0.0.0", a.puerto), Manejador)
    srv.daemon_threads = True
    print(f"\n  Abre en este ordenador:  http://localhost:{a.puerto}")
    print(f"  Desde el móvil:          http://{ip_local()}:{a.puerto}\n  (Ctrl+C para salir)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        LECTOR.pedir("desconectar", timeout=10)
        print("\nGrabación cerrada. ¡Hasta luego!")


if __name__ == "__main__":
    main()

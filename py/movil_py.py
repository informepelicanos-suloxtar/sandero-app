"""Lado Python de la app del móvil (corre dentro del navegador con Pyodide).

Reutiliza tal cual el código del Mac: servidor.Lector (valores, semáforo, grabación, diagnóstico en directo),
servidor.Grabacion, pids, averias, diagnostico y analisis. Lo único distinto es que la conversación con el
lector (Bluetooth LE) la lleva motor.js: aquí llegan ya los bytes leídos y desde aquí no se habla con nadie.

Los archivos viven en /coche (guardado en el propio móvil, IndexedDB), con la misma estructura que en el Mac:
/coche/registros/<fecha>_<prueba>/datos.csv, sesion.json, resumen.json, resumen.txt.
"""
import io
import json
import shutil
import time
import zipfile
from datetime import datetime

import analisis
import averias
import diagnostico
import pids
import rutas
import servidor


class ElmEnJS:
    """Lo que el Lector de Python necesita saber del lector real (que maneja motor.js)."""

    def __init__(self, info, soportados):
        self.info = info
        self.soportados = set(soportados)

    def cerrar(self):
        pass


L = servidor.LECTOR  # el mismo objeto que usa el servidor del Mac (su hilo aquí no se arranca)


def _j(x):
    return json.dumps(x, ensure_ascii=False)


def listas_pids():
    return _j({"rapidos": pids.RAPIDOS, "lentos": pids.LENTOS, "muy_lentos": pids.MUY_LENTOS,
               "claves": {p: d[0] for p, d in pids.DEFINICIONES.items()}})


def estado_conexion(estado, mensaje):
    L.conexion = {**(L.conexion if estado == "conectado" else {}), "estado": estado, "mensaje": mensaje}


def mensaje(texto):
    L.conexion = {**L.conexion, "mensaje": texto}


def conectado(info_json, soportados_json, demo):
    info = json.loads(info_json)
    L.elm = ElmEnJS(info, json.loads(soportados_json))
    if not demo:
        L.ajustes["ultimo_lector"] = "ble"
        (servidor.BASE / "ajustes.json").write_text(_j(L.ajustes))
    L.conexion = {"estado": "conectado", "mensaje": f"Conectado: {info.get('protocolo', '')}", **info}
    L.valores, L.historial = {}, {}
    L._empezar(prueba="libre")


def desconectar(motivo):
    L._desconectar(motivo)


def leido(pid, datos_json):
    """Bytes de un PID del modo 01 → valor apuntado. Devuelve True si el coche contestó."""
    datos = json.loads(datos_json)
    v = pids.decodificar(pid, datos) if datos else None
    L._apuntar(pids.DEFINICIONES[pid][0], v)
    L._fila()
    return v is not None


def bateria(v):
    # Llega como texto («13.60»): pasado como número, Bluefy a veces lo recibía corrompido (1,2 V o 858.993.460 V)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return
    if not 3 <= v <= 20:
        return
    L._apuntar("bateria", round(v + L._corr_bateria(), 2))


def coche_callado():
    with L.lock:
        L.valores = {k: v for k, v in L.valores.items() if k == "bateria"}
        L.historial = {}
    mensaje("El coche no contesta (¿contacto quitado?). En cuanto lo pongas sigo solo…")


def coche_vuelve():
    mensaje(f"Conectado: {L.elm.info.get('protocolo', '') if L.elm else ''}")
    if L.grabacion:
        L.grabacion.nota("El coche vuelve a contestar")


def averias_leidas(crudo_json):
    """crudo: {"03": ["0133", ...], "07": [...], "0A": [...], "monitores": [bytes] | None,
    "foto": {"averia": [b1, b2], "pids": {"5": [bytes], ...}} | None, "pids": {"49": [bytes], ...}, "vin": str|None}"""
    c = json.loads(crudo_json)

    def lista(codigos):
        out = []
        for x in codigos or []:
            if x != "0000":
                cod = averias.decodificar(int(x[:2], 16), int(x[2:], 16))
                if cod not in [y["codigo"] for y in out]:
                    out.append(averias.describir(cod))
        return out

    foto = None
    if c.get("foto"):
        f = c["foto"]
        foto = {"averia": averias.decodificar(*f["averia"])}
        for pid, datos in f.get("pids", {}).items():
            v = pids.decodificar(int(pid), datos)
            if v is not None:
                foto[pids.DEFINICIONES[int(pid)][0]] = v
    mon = c.get("monitores")
    res = {"hora": datetime.now().strftime("%H:%M:%S"),
           "guardadas": lista(c.get("03")), "pendientes": lista(c.get("07")), "permanentes": lista(c.get("0A")),
           "monitores": averias.decodificar_monitores(mon) if mon and len(mon) >= 4 else None, "foto": foto}
    for pid, datos in (c.get("pids") or {}).items():
        res[pids.DEFINICIONES[int(pid)][0]] = pids.decodificar(int(pid), datos)
    if L.elm:
        if c.get("vin") and "vin" not in L.elm.info:
            L.elm.info["vin"] = c["vin"]
        res["vin"] = L.elm.info.get("vin")
    L.averias = res
    if L.grabacion:
        L.grabacion.sesion["averias"] = res
        L.grabacion.guardar_sesion()
    return _j(res)


def antes_de_borrar():
    if L.grabacion:
        L.grabacion.nota("Averías borradas desde la web")
        L.grabacion.sesion["averias_antes_de_borrar"] = L.averias


def rpm():
    return L.valores.get("rpm") or 0


def accion(nombre, args_json):
    """Órdenes de la web que no necesitan hablar con el lector: empezar, parar, nota."""
    try:
        return _j({"ok": True, "dato": getattr(L, f"_{nombre}")(**json.loads(args_json))})
    except servidor.ErrorLector as e:
        return _j({"ok": False, "error": str(e)})


def foto():
    return _j(L.foto())


def diagnostico_en_directo():
    """Lo mismo que Lector.analizar_en_directo hace cada 4 s en el Mac. Devuelve los milisegundos que tardó."""
    t = time.time()
    filas = list(L.grabacion.memoria) if L.grabacion else []
    d = diagnostico.diagnosticar(filas, L.averias if L.elm else None, L.ajustes.get("consumo_real"),
                                 L.ajustes.get("factor_consumo"), corr_velocidad=L._corr_velocidad())
    d["filas"] = len(filas)
    L.diagnostico = d
    return round((time.time() - t) * 1000)


def get(ruta):
    if ruta == "/api/definiciones":
        return _j(servidor.definiciones())
    if ruta == "/api/ajustes":
        return _j(L.ajustes)
    if ruta == "/api/puertos":
        return _j({"puertos": []})
    if ruta == "/api/sesiones":
        return _j(servidor.lista_sesiones())
    if ruta == "/api/mantenimiento":
        return _j(servidor.leer_mantenimiento())
    if ruta == "/api/rutas":
        return _j(rutas.lista(servidor.REGISTROS, servidor.BASE))
    if ruta.startswith("/api/ruta/"):
        d = rutas.detalle(servidor.REGISTROS, ruta.split("/api/ruta/", 1)[1])
        return _j(d if d else {"error": "No existe"})
    if ruta.startswith("/api/sesion/"):
        d = servidor.detalle_sesion(ruta.split("/api/sesion/", 1)[1])
        return _j(d if d else {"error": "No existe"})
    return _j({"error": "No existe"})


def post(ruta, cuerpo_json):
    cuerpo = json.loads(cuerpo_json or "{}")
    if ruta == "/api/gps":
        args = [cuerpo.get(k) for k in ("v", "precision", "lat", "lon", "alt", "precision_alt")]
        g = servidor.gps_valores(*args)
        if g["gps_velocidad"] is not None or g["gps_lat"] is not None:
            L.poner_gps(*args)
            return _j({"ok": True})
        return _j({"ok": False})
    if ruta == "/api/ajustes":
        ok, dato = servidor.guardar_ajustes(cuerpo)
        return _j({"ok": ok, "dato" if ok else "error": dato})
    if ruta == "/api/mantenimiento":
        return _j({"ok": True, "dato": servidor.marcar_mantenimiento(cuerpo)})
    if ruta == "/api/borrar_sesion":
        ok = servidor.borrar_sesion(cuerpo.get("carpeta"))
        return _j({"ok": ok, "error": None if ok else "No se puede borrar la grabación en marcha"})
    if ruta == "/api/ruta_nombre":
        ok, dato = rutas.poner_nombre(servidor.BASE, cuerpo.get("id"), cuerpo.get("nombre"))
        return _j({"ok": ok, "dato" if ok else "error": dato})
    if ruta == "/api/reanalizar":
        return _j({"ok": True, "dato": servidor.reanalizar_sesiones(cuerpo.get("carpeta"))})
    if ruta == "/api/importar":
        imp = servidor.importar_archivos(cuerpo.get("archivos"))
        return _j({"ok": bool(imp), "dato": imp, "error": None if imp else "No se encontró ninguna grabación válida"})
    return _j({"ok": False, "error": "No existe"})


def mantenimiento_md():
    """Texto de MANTENIMIENTO.md, para subirlo a GitHub junto con mantenimiento.json."""
    return servidor.mantenimiento_md(servidor.leer_mantenimiento())


def recargar_ajustes():
    """Tras bajar ajustes.json del repositorio (móvil recién instalado)."""
    L.ajustes = analisis.ajustes()


def volcar():
    """Escribe a disco lo pendiente de la grabación (antes de guardar en el móvil)."""
    g = L.grabacion
    if g:
        g.f.flush()
        g.guardar_sesion()


def recuperar():
    """Grabaciones que se quedaron sin cerrar (app cerrada de golpe, móvil sin batería): se cierran y se revisan."""
    return _j(servidor.recuperar_grabaciones())


def zip_grabacion(carpeta):
    """Una grabación entera en un .zip (con su carpeta dentro), para mandarla al Mac.
    En el Mac: ~/coche-obd/importar.sh la mete en registros/."""
    c = servidor.REGISTROS / carpeta
    if not c.is_dir() or c.parent != servidor.REGISTROS:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(c.iterdir()):
            if p.is_file():
                z.write(p, f"{c.name}/{p.name}")
    return buf.getvalue()


def borrar_grabacion(carpeta):
    c = servidor.REGISTROS / carpeta
    if L.grabacion and L.grabacion.carpeta == c:
        return False
    if c.is_dir() and c.parent == servidor.REGISTROS:
        shutil.rmtree(c)
        return True
    return False

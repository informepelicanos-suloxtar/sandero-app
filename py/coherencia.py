"""Chequeo de coherencia y prediagnóstico (multimarca, solo PIDs OBD-II universales).

Dos capturas de 5 s con el motor caliente: ralentí y 2.500 rpm en vacío. Las medias salen del historial que
el Lector ya va guardando (los últimos 40 s de cada valor), así que funciona igual en el Mac (servidor.py) y
en el móvil (movil_py.py, Pyodide). Mientras dura una captura, el bucle de lectura pide todos los PIDs en
cada vuelta (ver capturando()).

Cada chequeo se guarda en chequeos/<id>.json, con el vehículo (VIN o matrícula) para poder comparar
antes y después de una reparación. El análisis con IA lo hace la web (index.html); aquí solo se prepara
el JSON que se le manda y se guarda la respuesta.
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
CARPETA = BASE / "chequeos"

DURACION = 5.0
RPM_2500 = (2250, 2750)  # margen para dar por buena la fase de 2.500 rpm

# pid, clave del Lector, nombre corto, unidad, decimales
PARAMETROS = [
    (0x05, "temp_motor", "ECT (refrigerante)", "°C", 0),
    (0x0F, "temp_admision", "IAT (aire admisión)", "°C", 0),
    (0x0C, "rpm", "RPM", "rpm", 0),
    (0x04, "carga", "Carga calculada", "%", 0),
    (0x06, "stft", "STFT B1", "%", 1),
    (0x07, "ltft", "LTFT B1", "%", 1),
    (0x0B, "map", "MAP", "kPa", 0),
    (0x10, "maf", "MAF", "g/s", 2),
    (0x11, "acelerador", "Mariposa", "%", 0),
    (0x14, "sonda1", "Lambda B1S1 (pre-cat)", "V", 2),
    (0x15, "sonda2", "Lambda B1S2 (post-cat)", "V", 2),
    (0x42, "voltaje_ecu", "Tensión ECU (01 42)", "V", 2),
    (None, "bateria", "Batería (AT RV)", "V", 2),
]
FASES = {"ralenti": "Ralentí", "2500": "2.500 rpm"}

_captura = {"fase": None, "t0": 0.0}
_actual = None  # chequeo en curso (dict) hasta que se guarda


def capturando():
    """True mientras dura una captura: el bucle lee también los PIDs lentos en cada vuelta."""
    return _captura["fase"] is not None and time.time() - _captura["t0"] < DURACION + 3


def _r(x, dec):
    return None if x is None else round(x, dec)


def _soportado(L, pid):
    if pid is None:
        return True
    elm = L.elm
    return bool(elm and pid in (elm.soportados or ()))


def _estadistica(serie, dec):
    vals = [v for _, v in serie if isinstance(v, (int, float))]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    return {"media": _r(m, dec), "min": _r(min(vals), dec), "max": _r(max(vals), dec), "n": len(vals)}


def _cruces(vals, umbral=0.45):
    return sum(1 for a, b in zip(vals, vals[1:]) if (a - umbral) * (b - umbral) < 0)


def _cerrar_fase(L, fase, t0, t1):
    with L.lock:
        hist = {k: list(v) for k, v in L.historial.items()}
        modo = L.valores.get("sistema_combustible")
    datos, na = {}, []
    for pid, clave, _, _, dec in PARAMETROS:
        if not _soportado(L, pid):
            datos[clave] = "N/A"
            na.append(clave)
            continue
        serie = [(t, v) for t, v in hist.get(clave, []) if t0 <= t <= t1]
        e = _estadistica(serie, dec)
        if e and clave in ("sonda1", "sonda2"):
            vals = [v for _, v in serie]
            e["cruces_045V"] = _cruces(vals)
            e["amplitud"] = _r(max(vals) - min(vals), 2)
        datos[clave] = e if e else "sin datos"
    return {"fase": fase, "inicio": datetime.fromtimestamp(t0).strftime("%H:%M:%S"),
            "segundos": round(t1 - t0, 1), "datos": datos, "no_soportados": na, "modo_mezcla": modo}


def _media(fase, clave):
    if not fase:
        return None
    x = fase["datos"].get(clave)
    return x["media"] if isinstance(x, dict) else None


def _clave_vehiculo(v):
    vin = re.sub(r"[^A-Z0-9]", "", str(v.get("vin") or "").upper())
    if len(vin) == 17:
        return vin
    mat = re.sub(r"[^A-Z0-9]", "", str(v.get("matricula") or "").upper())
    if mat:
        return "MAT-" + mat
    resto = "-".join(re.sub(r"[^a-z0-9]", "", str(v.get(k) or "").lower()) for k in ("marca", "modelo", "anio"))
    return "SINID-" + resto.strip("-") if resto.strip("-") else "SINID"


def _dtcs(L):
    a = L.averias or {}
    simple = lambda l: [{"codigo": x.get("codigo"), "descripcion": x.get("descripcion")} for x in (l or [])]
    return {"leidos_a_las": a.get("hora"), "guardados_03": simple(a.get("guardadas")),
            "pendientes_07": simple(a.get("pendientes"))}


def comprobaciones(ch):
    """Reglas fijas (sin IA) para avisar en el momento. La IA recibe solo los datos, no estas conclusiones."""
    r, a = ch.get("ralenti"), ch.get("2500")
    comb = (ch.get("vehiculo") or {}).get("combustible", "")
    out = []

    def aviso(nivel, texto):
        out.append({"nivel": nivel, "texto": texto})

    ect = _media(r, "temp_motor") or _media(a, "temp_motor")
    iat = _media(r, "temp_admision") or _media(a, "temp_admision")
    if ect is not None and ect < 80:
        aviso("rojo", f"ECT {ect:.0f} °C: el motor no está a temperatura de servicio. Si lleva rato en marcha, "
                      "sospecha de termostato abierto o sensor de temperatura que miente.")
    if ect is not None and iat is not None and ect >= 80 and iat > ect:
        aviso("amarillo", f"IAT ({iat:.0f} °C) por encima de ECT ({ect:.0f} °C) con el motor caliente: incoherente.")

    tot = {}
    for nombre, f in (("ralenti", r), ("2500", a)):
        s, l = _media(f, "stft"), _media(f, "ltft")
        if s is not None and l is not None:
            tot[nombre] = s + l
    for nombre, t in tot.items():
        if abs(t) > 10:
            aviso("rojo" if abs(t) > 20 else "amarillo",
                  f"Corrección total (STFT+LTFT) a {FASES[nombre]}: {t:+.1f} %. Fuera de ±10 %.")
    if len(tot) == 2 and abs(tot["ralenti"] - tot["2500"]) >= 5:
        if tot["ralenti"] > tot["2500"]:
            aviso("amarillo", f"La corrección baja al acelerar ({tot['ralenti']:+.1f} → {tot['2500']:+.1f} %): "
                              "patrón típico de toma de aire (fuga de vacío).")
        else:
            aviso("amarillo", f"La corrección sube al acelerar ({tot['ralenti']:+.1f} → {tot['2500']:+.1f} %): "
                              "patrón de falta de combustible o lectura de aire (MAF/MAP) baja.")

    for nombre, f in (("ralenti", r), ("2500", a)):
        if not f:
            continue
        s1, s2 = f["datos"].get("sonda1"), f["datos"].get("sonda2")
        lazo = f.get("modo_mezcla") == 2
        if isinstance(s1, dict) and s1["n"] >= 6 and lazo and (s1["amplitud"] < 0.4 or s1["cruces_045V"] < 2):
            aviso("amarillo", f"Lambda B1S1 casi no oscila a {FASES[nombre]} (amplitud {s1['amplitud']} V, "
                              f"{s1['cruces_045V']} cruces de 0,45 V en {f['segundos']} s): sonda lenta o mezcla fija.")
        if isinstance(s1, dict) and isinstance(s2, dict) and s2["n"] >= 4 and s2["amplitud"] >= 0.5 \
                and s2["cruces_045V"] >= max(2, s1.get("cruces_045V", 0) // 2):
            aviso("amarillo", f"Lambda B1S2 oscila casi como la delantera a {FASES[nombre]}: "
                              "posible catalizador con poca eficiencia.")

    v = _media(a, "bateria") or _media(r, "bateria")
    if v is not None and not 13.8 <= v <= 14.5:
        aviso("rojo" if v < 13.2 or v > 15 else "amarillo",
              f"Tensión de carga {v:.2f} V con el motor en marcha (lo normal, 13,8–14,5 V).")

    if "diés" not in comb.lower() and "dies" not in comb.lower():
        m = _media(r, "map")
        if m is not None and m > 45:
            aviso("amarillo", f"MAP al ralentí {m:.0f} kPa (gasolina en caliente suele estar en 25–45): "
                              "posible toma de aire o sensor MAP.")

    rpm = _media(a, "rpm")
    if a and rpm is not None and not RPM_2500[0] <= rpm <= RPM_2500[1]:
        aviso("amarillo", f"En la fase de 2.500 rpm la media fue {rpm:.0f} rpm: la comparación pierde fiabilidad.")
    if r and _media(r, "rpm") is not None and _media(r, "rpm") > 1100:
        aviso("amarillo", f"Ralentí a {_media(r, 'rpm'):.0f} rpm: alto para un motor caliente (o no estaba al ralentí).")
    return out


def para_ia(ch):
    """El JSON que se manda a la IA: vehículo, síntoma, averías y tabla ralentí vs 2.500 rpm."""
    tabla, detalle = [], {}
    for _, clave, nombre, unidad, _ in PARAMETROS:
        fila = {"parametro": nombre, "unidad": unidad}
        for f in FASES:
            x = (ch.get(f) or {}).get("datos", {}).get(clave)
            fila[f] = x["media"] if isinstance(x, dict) else (x or "sin capturar")
            if isinstance(x, dict):
                detalle.setdefault(nombre, {})[f] = {k: v for k, v in x.items() if k != "media"}
        tabla.append(fila)
    v = dict(ch.get("vehiculo") or {})
    return {
        "vehiculo": {k: v.get(k) for k in ("vin", "marca", "modelo", "anio", "combustible", "motor", "matricula") if v.get(k)},
        "sintoma_observado": ch.get("sintoma") or "(no indicado)",
        "dtc": ch.get("dtc"),
        "condiciones": {"duracion_captura_s": DURACION, "fase_2500_en_vacio": True,
                        "modo_mezcla_pid_0103": {f: (ch.get(f) or {}).get("modo_mezcla") for f in FASES},
                        "nota_modo": "1 = lazo abierto (frío), 2 = lazo cerrado, 4 = lazo abierto por carga/corte, "
                                     "8 = lazo abierto por avería, 16 = lazo cerrado con fallo de sonda"},
        "tabla_ralenti_vs_2500": tabla,
        "detalle_min_max_n": detalle,
        "no_soportados_por_la_centralita": sorted({c for f in FASES for c in (ch.get(f) or {}).get("no_soportados", [])}),
    }


# ------------------------------------------------------------------ guardado e historial

def _guardar(ch):
    CARPETA.mkdir(exist_ok=True)
    (CARPETA / f"{ch['id']}.json").write_text(json.dumps(ch, ensure_ascii=False, indent=1))


def _leer(id_):
    if not re.fullmatch(r"[\w\-]+", str(id_ or "")):
        return None
    p = CARPETA / f"{id_}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def lista():
    """Chequeos guardados, agrupados por vehículo (lo más nuevo primero)."""
    grupos = {}
    if CARPETA.is_dir():
        for p in sorted(CARPETA.glob("*.json"), reverse=True):
            try:
                ch = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            v = ch.get("vehiculo") or {}
            g = grupos.setdefault(ch.get("clave_vehiculo") or "SINID", {
                "clave": ch.get("clave_vehiculo"), "vehiculo": v, "chequeos": []})
            g["chequeos"].append({
                "id": ch["id"], "fecha": ch.get("fecha"), "sintoma": ch.get("sintoma"), "ia": bool(ch.get("ia")),
                "ltft": {f: _media(ch.get(f), "ltft") for f in FASES},
                "stft": {f: _media(ch.get(f), "stft") for f in FASES},
                "dtc": len((ch.get("dtc") or {}).get("guardados_03", [])) + len((ch.get("dtc") or {}).get("pendientes_07", []))})
    return list(grupos.values())


def detalle(id_):
    ch = _leer(id_)
    if not ch:
        return None
    return {**ch, "para_ia": para_ia(ch), "comprobaciones": comprobaciones(ch), "parametros": _defs()}


def _defs():
    return [{"clave": c, "nombre": n, "unidad": u} for _, c, n, u, _ in PARAMETROS]


def _resp(dato=None, error=None):
    return {"ok": error is None, "dato": dato} if error is None else {"ok": False, "error": error}


def accion(L, cuerpo):
    """POST /api/chequeo — {accion: empezar | capturar | cerrar_fase | guardar_ia | borrar | estado, ...}"""
    global _actual
    a = cuerpo.get("accion")
    if a == "estado":
        return _resp({"actual": _actual, "capturando": capturando(), "fase": _captura["fase"],
                      "t0": _captura["t0"], "duracion": DURACION, "parametros": _defs()})
    if a == "empezar":
        v = cuerpo.get("vehiculo") or {}
        v = {k: str(v.get(k) or "").strip()[:60] for k in ("vin", "marca", "modelo", "anio", "combustible", "motor", "matricula")}
        if L.elm and L.elm.info.get("vin") and not v["vin"]:
            v["vin"] = L.elm.info["vin"]
        v["vin_leido_del_coche"] = bool(L.elm and L.elm.info.get("vin") and v["vin"] == L.elm.info.get("vin"))
        if not v["vin"] and not (v["marca"] and v["modelo"]):
            return _resp(error="Sin VIN: pon al menos marca y modelo (y mejor la matrícula, para el historial).")
        ahora = datetime.now()
        _actual = {"id": ahora.strftime("%Y-%m-%d_%H%M%S"), "fecha": ahora.strftime("%Y-%m-%d %H:%M"),
                   "vehiculo": v, "clave_vehiculo": _clave_vehiculo(v),
                   "sintoma": str(cuerpo.get("sintoma") or "").strip()[:300], "dtc": _dtcs(L),
                   "lector": {k: (L.elm.info.get(k) if L.elm else None) for k in ("protocolo", "destino")}}
        return _resp(_actual)
    if a == "capturar":
        fase = cuerpo.get("fase")
        if fase not in FASES:
            return _resp(error="Fase desconocida")
        if not _actual:
            return _resp(error="Primero pulsa «Iniciar chequeo de coherencia»")
        if not L.elm:
            return _resp(error="Primero conecta con el coche")
        rpm = L.valores.get("rpm") or 0
        if rpm < 400:
            return _resp(error="El motor no está en marcha (no hay revoluciones).")
        _captura.update(fase=fase, t0=time.time())
        return _resp({"t0": _captura["t0"], "duracion": DURACION})
    if a == "cerrar_fase":
        fase, t0 = _captura["fase"], _captura["t0"]
        if not fase or not _actual:
            return _resp(error="No hay ninguna captura en marcha")
        t1 = min(time.time(), t0 + DURACION)
        if t1 - t0 < DURACION - 0.3:
            return _resp(error="La captura aún no ha terminado")
        _captura.update(fase=None, t0=0.0)
        _actual[fase] = _cerrar_fase(L, fase, t0, t1)
        _actual["dtc"] = _dtcs(L)  # por si se volvieron a leer entre medias
        _guardar(_actual)
        return _resp({**_actual, "comprobaciones": comprobaciones(_actual), "para_ia": para_ia(_actual)})
    if a == "cancelar":
        _captura.update(fase=None, t0=0.0)
        return _resp(True)
    if a == "guardar_ia":
        ch = _leer(cuerpo.get("id"))
        if not ch:
            return _resp(error="No encuentro ese chequeo")
        ch["ia"] = {"texto": str(cuerpo.get("texto") or "")[:20000], "modelo": str(cuerpo.get("modelo") or "")[:60],
                    "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")}
        _guardar(ch)
        if _actual and _actual.get("id") == ch["id"]:
            _actual["ia"] = ch["ia"]
        return _resp(True)
    if a == "borrar":
        ch = _leer(cuerpo.get("id"))
        if not ch:
            return _resp(error="No encuentro ese chequeo")
        (CARPETA / f"{ch['id']}.json").unlink()
        if _actual and _actual.get("id") == ch["id"]:
            _actual = None
        return _resp(True)
    return _resp(error="Acción desconocida")

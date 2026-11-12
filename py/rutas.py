"""Rutas con el GPS del móvil: el GPX de cada salida y la comparación de las veces que se repite un recorrido.

Al terminar (o recalcular) una grabación que tiene posición GPS, analisis.analizar llama a guardar() y deja en
su carpeta:
  - ruta.gpx  : el recorrido en GPX 1.1 (se abre en BaseCamp, Garmin Connect, Google Earth…), con la altura,
                la velocidad y, aparte, lo que gastaba el coche en cada punto.
  - ruta.json : lo necesario para el mapa y para comparar, sin volver a leer el CSV: puntos simplificados,
                tramos de ~250 m con su pendiente y su consumo, y el gasto subiendo, en llano y bajando.

lista() junta las salidas que son el mismo recorrido (salen y llegan al mismo sitio y van por el mismo
camino, en el mismo sentido: ida y vuelta son rutas distintas, porque una sube lo que la otra baja).
Los nombres que pone Saúl a cada ruta van en rutas.json (raíz del repositorio).
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import diagnostico

VERSION = 1
MIN_PUNTOS = 30        # menos que esto no es un recorrido
MIN_KM = 0.8
TRAMO_M = 250          # largo de cada tramo para la pendiente y el consumo
PENDIENTE = 2.0        # % a partir del cual un tramo «sube» o «baja»
MAX_PUNTOS_MAPA = 700  # puntos que van en ruta.json para dibujar (el GPX lleva todos)
# mismo recorrido: salida y llegada a menos de 500 m, largo parecido y el 80 % del camino a menos de 200 m
MISMO_SITIO_M, MISMO_CAMINO_M, PARTE_COMUN = 500, 200, 0.8
CLASES = ("subida", "llano", "bajada")


# ------------------------------------------------------------------ geometría

def metros(a, b):
    """Distancia en metros entre (lat, lon) y (lat, lon)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    x = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, x)))


def _metros_rapido(a, b):
    """Aproximación plana, de sobra para comparar caminos a pocos cientos de metros."""
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return 111195 * math.hypot(a[0] - b[0], (a[1] - b[1]) * k)


# ------------------------------------------------------------------ de las filas del CSV a los puntos

def _litros_por_fila(filas, factor):
    """Litros gastados desde la fila anterior: el mismo cálculo que el consumo del diagnóstico."""
    out = [0.0]
    for a, b in zip(filas, filas[1:]):
        dt = min((b.get("t") or 0) - (a.get("t") or 0), 5)
        lh = diagnostico.consumo_lh(a, factor) if factor is not None else a.get("consumo_lh")
        if lh is None and factor is None:
            lh = diagnostico.consumo_lh(a)
        out.append(lh * dt / 3600 if dt > 0 and lh is not None else 0.0)
    return out


def puntos(filas, factor=None):
    """Puntos del recorrido: uno cada ~10 m o cada 10 s, sin los saltos imposibles del GPS.
    Cada punto: t, lat, lon, alt (o None), litros y metros acumulados, velocidad del coche y temperatura."""
    litros_fila = _litros_por_fila(filas, factor)
    out, litros, dist, ultimo = [], 0.0, 0.0, None
    for f, l in zip(filas, litros_fila):
        litros += l
        la, lo = f.get("gps_lat"), f.get("gps_lon")
        if la is None or lo is None:
            continue
        t = f.get("t") or 0.0
        if ultimo:
            d = metros((ultimo["lat"], ultimo["lon"]), (la, lo))
            dt = max(t - ultimo["t"], 0.1)
            if d / dt > 70 or d > 2000:  # más de 250 km/h o 2 km de golpe: salto del GPS
                continue
            if d < 10 and dt < 10:
                continue
            dist += d
        out.append({"t": t, "lat": la, "lon": lo, "alt": f.get("gps_alt"), "litros": litros, "m": dist,
                    "vel": f.get("velocidad"), "temp": f.get("temp_motor"), "rpm": f.get("rpm")})
        ultimo = out[-1]
    return out


def _alisar_altura(pts, ventana_s=20):
    """Mediana de la altura en ±20 s: el GPS baila ±5-10 m en vertical."""
    alts = [(p["t"], p["alt"]) for p in pts if p["alt"] is not None]
    if len(alts) < MIN_PUNTOS:
        for p in pts:
            p["alt_lisa"] = None
        return False
    j0 = j1 = 0
    for p in pts:
        while j0 < len(alts) and alts[j0][0] < p["t"] - ventana_s:
            j0 += 1
        while j1 < len(alts) and alts[j1][0] <= p["t"] + ventana_s:
            j1 += 1
        v = sorted(a for _t, a in alts[j0:j1])
        p["alt_lisa"] = v[len(v) // 2] if v else None
    # huecos sin altura: la última conocida (o la primera, al principio)
    ant = next((p["alt_lisa"] for p in pts if p["alt_lisa"] is not None), None)
    for p in pts:
        if p["alt_lisa"] is None:
            p["alt_lisa"] = ant
        ant = p["alt_lisa"]
    return True


def _clase(pendiente):
    if pendiente is None:
        return None
    return "subida" if pendiente >= PENDIENTE else "bajada" if pendiente <= -PENDIENTE else "llano"


def tramos(pts, con_altura):
    """Cortes de ~250 m con su pendiente (%), su consumo y su velocidad media."""
    out, ini = [], 0
    for i in range(1, len(pts)):
        largo = pts[i]["m"] - pts[ini]["m"]
        if largo >= TRAMO_M or (i == len(pts) - 1 and largo >= 100):
            a, b = pts[ini], pts[i]
            seg = b["t"] - a["t"]
            litros = b["litros"] - a["litros"]
            pend = round((b["alt_lisa"] - a["alt_lisa"]) / largo * 100, 1) if con_altura else None
            out.append({"km": round(a["m"] / 1000, 3), "m": round(largo), "s": round(seg),
                        "litros": round(litros, 4), "l100": round(litros / largo * 100000, 1),
                        "kmh": round(largo / seg * 3.6, 1) if seg > 0 else None,
                        "pendiente": pend, "clase": _clase(pend),
                        "alt": round(b["alt_lisa"]) if con_altura else None})
            ini = i
    return out


def _desnivel(pts):
    sube = baja = 0.0
    ref = pts[0]["alt_lisa"]
    for p in pts:
        a = p["alt_lisa"]
        if a - ref >= 5:
            sube += a - ref; ref = a
        elif ref - a >= 5:
            baja += ref - a; ref = a
    return round(sube), round(baja)


def _por_clase(trs):
    out = {}
    for c in CLASES:
        ts = [t for t in trs if t["clase"] == c]
        m, litros, s = sum(t["m"] for t in ts), sum(t["litros"] for t in ts), sum(t["s"] for t in ts)
        out[c] = {"km": round(m / 1000, 1), "litros": round(litros, 2), "min": round(s / 60, 1),
                  "l100": round(litros / m * 100000, 1) if m >= 300 else None}
    return out


def _inicio_utc(sesion):
    """Hora de salida en UTC: la que guarda la grabación o, en las viejas, la local de Canarias
    (UTC+1 del último domingo de marzo al último de octubre, como en el resto de Europa)."""
    if sesion.get("inicio_utc"):
        try:
            return datetime.fromisoformat(sesion["inicio_utc"]).astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        local = datetime.fromisoformat(sesion.get("inicio") or "")
    except ValueError:
        return None
    if local.tzinfo:
        return local.astimezone(timezone.utc)

    def ultimo_domingo(año, mes):
        d = datetime(año, mes + 1, 1) - timedelta(days=1)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    verano = (ultimo_domingo(local.year, 3) + timedelta(hours=1) <= local
              < ultimo_domingo(local.year, 10) + timedelta(hours=1))
    return (local - timedelta(hours=1 if verano else 0)).replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ por qué gasta lo que gasta

FRIO_C = 70          # por debajo, el motor enriquece y gasta más
RAPIDO_KMH = 90
RPM_ALTO = 3000


def factores(filas, factor=None):
    """Lo que explica el gasto de una salida. Las categorías de litros no se solapan:
    parado (motor en marcha y coche quieto), en marcha con el motor frío, y el resto (en marcha y caliente)."""
    litros_fila = _litros_por_fila(filas, factor)
    f = {"min_parado": 0.0, "litros_parado": 0.0, "paradas": 0, "min_frio": 0.0, "litros_frio": 0.0,
         "km_frio": 0.0, "litros_caliente": 0.0, "km_caliente": 0.0, "min_marcha": 0.0, "min_rapido": 0.0,
         "min_rpm_alto": 0.0, "acelerones": 0, "kmh_max": 0}
    rpm_suma = rpm_t = 0.0
    rpm_kmh, moviendose, ace_en = [], False, False
    temp_aire = [x.get("temp_admision") for x in filas[:200] if x.get("temp_admision") is not None]
    for a, b, l in zip(filas, filas[1:], litros_fila[1:]):
        dt = min((b.get("t") or 0) - (a.get("t") or 0), 5)
        rpm, vel = a.get("rpm") or 0, a.get("velocidad") or 0
        if dt <= 0 or rpm < 300:
            continue
        frio = a.get("temp_motor") is not None and a["temp_motor"] < FRIO_C
        if vel < 3:
            f["min_parado"] += dt; f["litros_parado"] += l
            if moviendose:
                f["paradas"] += 1
            moviendose = False
            continue
        if vel >= 10:
            moviendose = True
        km = vel * dt / 3600
        f["min_marcha"] += dt
        if frio:
            f["min_frio"] += dt; f["litros_frio"] += l; f["km_frio"] += km
        else:
            f["litros_caliente"] += l; f["km_caliente"] += km
        f["kmh_max"] = max(f["kmh_max"], vel)
        if vel > RAPIDO_KMH:
            f["min_rapido"] += dt
        if rpm > RPM_ALTO:
            f["min_rpm_alto"] += dt
        rpm_suma += rpm * dt; rpm_t += dt
        if 45 <= vel <= 55:  # revoluciones a 50 km/h: dice en qué marcha se suele ir
            rpm_kmh.append(rpm / vel * 50)
        fuerte = (a.get("acelerador") or 0) >= 60 or ((a.get("carga") or 0) >= 90 and rpm > 2000)
        if fuerte and not ace_en:
            f["acelerones"] += 1
        ace_en = fuerte
    for k in ("min_parado", "min_frio", "min_marcha", "min_rapido", "min_rpm_alto"):
        f[k] = round(f[k] / 60, 1)
    for k in ("litros_parado", "litros_frio", "litros_caliente"):
        f[k] = round(f[k], 3)
    for k in ("km_frio", "km_caliente"):
        f[k] = round(f[k], 2)
    f["l100_caliente"] = round(f["litros_caliente"] / f["km_caliente"] * 100, 1) if f["km_caliente"] >= 1 else None
    f["l100_frio"] = round(f["litros_frio"] / f["km_frio"] * 100, 1) if f["km_frio"] >= 0.5 else None
    # lo que gastó de más por ir frío: lo de frío menos lo que habría gastado esos km ya caliente
    f["extra_frio"] = (round(max(0.0, f["litros_frio"] - f["km_frio"] * f["l100_caliente"] / 100), 3)
                       if f["l100_caliente"] else None)
    f["rpm_media"] = round(rpm_suma / rpm_t) if rpm_t else None
    f["rpm_a_50"] = round(sorted(rpm_kmh)[len(rpm_kmh) // 2]) if len(rpm_kmh) >= 20 else None
    f["temp_aire"] = round(min(temp_aire)) if temp_aire else None
    f["kmh_max"] = round(f["kmh_max"])
    return f


def _fmt(x, dec=1):
    return f"{x:.{dec}f}".replace(".", ",")


def por_que(v, ref):
    """Por qué el viaje v gastó más que ref (la vez que menos gastó en esa ruta). Lista de motivos,
    de más a menos litros; los que no se pueden medir en litros van al final."""
    fv, fr = v.get("factores") or {}, ref.get("factores") or {}
    if not fv or not fr or not v.get("litros") or not ref.get("l100"):
        return {"litros_de_mas": None, "motivos": []}
    # lo que habría gastado v con el consumo de la mejor vez, por sus km
    de_mas = v["litros"] - ref["l100"] * (v.get("km") or 0) / 100
    motivos = []
    if de_mas < 0.05:  # gastó lo mismo (o menos): no hay nada que explicar
        return {"litros_de_mas": round(de_mas, 2), "motivos": []}

    def m(litros, texto, tipo):
        motivos.append({"litros": round(litros, 2) if litros is not None else None, "texto": texto, "tipo": tipo})

    d = fv["litros_parado"] - fr["litros_parado"]
    if d >= 0.03:
        m(d, f"Más rato parado con el motor en marcha (semáforos, atasco, esperas): {_fmt(fv['min_parado'])} min y "
             f"{fv['paradas']} paradas, frente a {_fmt(fr['min_parado'])} min y {fr['paradas']} la mejor vez.", "parado")
    # extra por frío: lo de frío menos lo que habrían gastado esos km en caliente (con el consumo en caliente de
    # ese mismo viaje o, si no llegó a calentar, con el de la mejor vez)
    l100_cal = fv.get("l100_caliente") or fr.get("l100_caliente")
    if l100_cal:
        extra_v = max(0.0, fv["litros_frio"] - fv["km_frio"] * l100_cal / 100)
        extra_r = fr.get("extra_frio") or 0.0
        d = extra_v - extra_r
        if d >= 0.03:
            m(d, f"Motor frío más rato: {_fmt(fv['min_frio'])} min por debajo de {FRIO_C} °C en marcha "
                 f"({_fmt(fv['km_frio'])} km), frente a {_fmt(fr['min_frio'])} min la mejor vez. En frío inyecta más.",
              "frio")
    # dónde de la ruta gastó más: el mismo kilómetro del camino en los dos viajes
    kv, kr = _por_km(v.get("tramos") or []), _por_km(ref.get("tramos") or [])
    difs = sorted(((a["l100"] - kr[k]["l100"]) * a["m"] / 100000, k) for k, a in kv.items() if k in kr)
    for litros, k in sorted(difs, key=lambda x: -x[0])[:3]:
        if litros < 0.04:
            break
        a, b = kv[k], kr[k]
        donde = {"subida": f", subiendo un {_fmt(abs(a['pendiente']))} %",
                 "bajada": f", bajando un {_fmt(abs(a['pendiente']))} %", "llano": ", en llano"}.get(a.get("clase"), "")
        vel = (f", a {a['kmh']} km/h de media frente a {b['kmh']}" if a.get("kmh") and b.get("kmh")
               and abs(a["kmh"] - b["kmh"]) >= 8 else "")
        m(litros, f"Del km {k} al {k + 1}{donde}{vel}: {_fmt(a['l100'])} L/100 frente a {_fmt(b['l100'])} la mejor vez.",
          "tramo")
    # sin litros: forma de conducir y estado del coche
    if fv["min_marcha"] and fr["min_marcha"]:
        pv, pr = fv["min_rapido"] / fv["min_marcha"] * 100, fr["min_rapido"] / fr["min_marcha"] * 100
        if pv - pr >= 10:
            m(None, f"Más rápido: un {round(pv)} % del rato a más de {RAPIDO_KMH} km/h (la mejor vez, {round(pr)} %); "
                    f"máxima {fv['kmh_max']} km/h.", "rapido")
        pv, pr = fv["min_rpm_alto"] / fv["min_marcha"] * 100, fr["min_rpm_alto"] / fr["min_marcha"] * 100
        if pv - pr >= 8:
            m(None, f"Más revolucionado: un {round(pv)} % del rato por encima de {RPM_ALTO} rpm (la mejor vez, {round(pr)} %).",
              "rpm")
    if fv.get("rpm_a_50") and fr.get("rpm_a_50") and fv["rpm_a_50"] >= fr["rpm_a_50"] * 1.12:
        m(None, f"Marchas más cortas: a 50 km/h iba a {fv['rpm_a_50']} rpm de media, frente a {fr['rpm_a_50']} la mejor vez.",
          "marchas")
    if fv["acelerones"] - fr["acelerones"] >= 3:
        m(None, f"Más acelerones: {fv['acelerones']} pisando a fondo, frente a {fr['acelerones']} la mejor vez.", "acelerones")
    if v.get("correccion_mezcla") is not None and ref.get("correccion_mezcla") is not None \
            and v["correccion_mezcla"] - ref["correccion_mezcla"] >= 4:
        m(None, f"El coche corrigió más la mezcla: +{_fmt(v['correccion_mezcla'])} % de gasolina, frente a "
                f"+{_fmt(ref['correccion_mezcla'])} % la mejor vez.", "mezcla")
    if fv.get("temp_aire") is not None and fr.get("temp_aire") is not None and fr["temp_aire"] - fv["temp_aire"] >= 6:
        m(None, f"Hacía más fresco al salir: aire a {fv['temp_aire']} °C, frente a {fr['temp_aire']} °C la mejor vez "
                f"(tarda más en calentar).", "aire")
    motivos.sort(key=lambda x: -(x["litros"] if x["litros"] is not None else -1))
    return {"litros_de_mas": round(de_mas, 2), "motivos": motivos}


def _por_km(trs):
    """Los tramos de 250 m juntados de kilómetro en kilómetro (uno solo baila mucho con un semáforo)."""
    out = {}
    for t in trs:
        k = int(t["km"])
        g = out.setdefault(k, {"km": k, "m": 0, "s": 0, "litros": 0.0, "subida_m": 0.0})
        g["m"] += t["m"]; g["s"] += t["s"]; g["litros"] += t["litros"]
        if t.get("pendiente") is not None:
            g["subida_m"] += t["pendiente"] * t["m"] / 100
    for g in out.values():
        g["l100"] = round(g["litros"] / g["m"] * 100000, 1) if g["m"] else 0
        g["kmh"] = round(g["m"] / g["s"] * 3.6) if g["s"] else None
        g["pendiente"] = round(g["subida_m"] / g["m"] * 100, 1) if g["m"] else None
        g["clase"] = _clase(g["pendiente"])
    return {k: g for k, g in out.items() if g["m"] >= 400}


def en_que_se_fue(v):
    """Dónde se fue la gasolina de un viaje: parado, extra por frío, subiendo, en llano y bajando."""
    f, c = v.get("factores") or {}, v.get("clases") or {}
    if not f:
        return None
    out = {"parado": f.get("litros_parado"), "extra_frio": f.get("extra_frio")}
    for k in CLASES:
        out[k] = (c.get(k) or {}).get("litros")
    return out


# ------------------------------------------------------------------ GPX

def gpx(pts, sesion, nombre):
    t0 = _inicio_utc(sesion)
    lineas = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<gpx version="1.1" creator="Mi Sandero" xmlns="http://www.topografix.com/GPX/1/1"'
              ' xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2"'
              ' xmlns:sandero="https://informepelicanos-suloxtar.github.io/sandero-app/gpx/1"'
              ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
              ' xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">',
              f'  <metadata><name>{escape(nombre)}</name>'
              + (f'<time>{t0:%Y-%m-%dT%H:%M:%SZ}</time>' if t0 else "") + '</metadata>',
              f'  <trk><name>{escape(nombre)}</name><type>driving</type><trkseg>']
    for a, b in zip([None] + pts, pts):
        extra = []
        if b.get("alt") is not None:
            extra.append(f'<ele>{b["alt"]:.1f}</ele>')
        if t0:
            extra.append(f'<time>{t0 + timedelta(seconds=b["t"]):%Y-%m-%dT%H:%M:%SZ}</time>')
        ext = []
        if b.get("vel") is not None:
            ext.append(f'<gpxtpx:TrackPointExtension><gpxtpx:speed>{b["vel"] / 3.6:.2f}</gpxtpx:speed>'
                       '</gpxtpx:TrackPointExtension>')
        if a is not None and b["t"] > a["t"]:
            lh = (b["litros"] - a["litros"]) / (b["t"] - a["t"]) * 3600
            ext.append(f'<sandero:litros_hora>{lh:.2f}</sandero:litros_hora>')
        if b.get("rpm") is not None:
            ext.append(f'<sandero:rpm>{b["rpm"]:.0f}</sandero:rpm>')
        if b.get("temp") is not None:
            ext.append(f'<sandero:temp_motor>{b["temp"]:.0f}</sandero:temp_motor>')
        if ext:
            extra.append("<extensions>" + "".join(ext) + "</extensions>")
        lineas.append(f'    <trkpt lat="{b["lat"]:.6f}" lon="{b["lon"]:.6f}">{"".join(extra)}</trkpt>')
    lineas += ["  </trkseg></trk>", "</gpx>", ""]
    return "\n".join(lineas)


# ------------------------------------------------------------------ lo que se guarda en cada grabación

def resumir(filas, sesion, resumen, factor=None):
    """ruta.json (dict) y la lista de puntos para el GPX. None si no hay recorrido con GPS."""
    pts = puntos(filas, factor)
    if len(pts) < MIN_PUNTOS or pts[-1]["m"] < MIN_KM * 1000:
        return None, None
    con_altura = _alisar_altura(pts)
    trs = tramos(pts, con_altura)
    est = (resumen or {}).get("estadisticas") or {}
    cons = est.get("consumo") or {}
    km_gps = pts[-1]["m"] / 1000
    litros_gps = pts[-1]["litros"] - pts[0]["litros"]
    paso = max(1, math.ceil(len(pts) / MAX_PUNTOS_MAPA))
    mapa = pts[::paso] + ([pts[-1]] if (len(pts) - 1) % paso else [])
    # consumo de cada punto del mapa: el del tramo en el que cae
    def l100_en(m):
        for t in trs:
            if t["km"] * 1000 <= m < t["km"] * 1000 + t["m"]:
                return t["l100"]
        return trs[-1]["l100"] if trs else None
    sube, baja = _desnivel(pts) if con_altura else (None, None)
    t_ini = _inicio_utc(sesion)
    temps = [p["temp"] for p in pts if p["temp"] is not None]
    r = {"version": VERSION, "carpeta": (resumen or {}).get("carpeta"), "inicio": sesion.get("inicio"),
         "inicio_utc": t_ini.isoformat() if t_ini else None,
         "ini": [round(pts[0]["lat"], 5), round(pts[0]["lon"], 5)],
         "fin": [round(pts[-1]["lat"], 5), round(pts[-1]["lon"], 5)],
         "km_gps": round(km_gps, 2), "min": round((pts[-1]["t"] - pts[0]["t"]) / 60, 1),
         "litros_gps": round(litros_gps, 2),
         "l100_gps": round(litros_gps / km_gps * 100, 1) if km_gps >= 1 else None,
         # lo de la grabación entera (incluye lo que se gastó antes de que el GPS cogiera señal)
         "km": cons.get("km"), "litros": cons.get("litros"), "l100": cons.get("l100"),
         "pct_frio": cons.get("pct_frio"), "pct_ralenti": cons.get("pct_ralenti"),
         "temp_motor_media": round(sum(temps) / len(temps)) if temps else None,
         "correccion_mezcla": est.get("correccion_total"),
         "con_altura": con_altura, "sube_m": sube, "baja_m": baja,
         "alt_min": round(min(p["alt_lisa"] for p in pts)) if con_altura else None,
         "alt_max": round(max(p["alt_lisa"] for p in pts)) if con_altura else None,
         "clases": _por_clase(trs) if con_altura else None,
         "kmh_media": round(km_gps / ((pts[-1]["t"] - pts[0]["t"]) / 3600), 1) if pts[-1]["t"] > pts[0]["t"] else None,
         "factores": factores(filas, factor),
         "puntos": [[round(p["lat"], 5), round(p["lon"], 5),
                     round(p["alt_lisa"]) if con_altura else None, round(p["m"] / 1000, 3), l100_en(p["m"])]
                    for p in mapa],
         "tramos": trs}
    return r, pts


def guardar(carpeta, filas, sesion, resumen, factor=None):
    """Escribe ruta.json y ruta.gpx en la carpeta de la grabación (o los quita si ya no hay recorrido)."""
    carpeta = Path(carpeta)
    r, pts = resumir(filas, sesion, resumen, factor)
    if r is None:
        for n in ("ruta.json", "ruta.gpx"):
            (carpeta / n).unlink(missing_ok=True)
        return None
    try:
        fecha = datetime.fromisoformat(sesion.get("inicio") or "").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        fecha = carpeta.name
    (carpeta / "ruta.json").write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
    (carpeta / "ruta.gpx").write_text(gpx(pts, sesion, f"Sandero {fecha} · {r['km_gps']:.1f} km"))
    return r


# ------------------------------------------------------------------ juntar las veces que se repite un recorrido

def _parte_comun(a, b, n=30):
    """Fracción de n puntos repartidos por el camino de a que quedan a menos de 200 m del camino de b."""
    pa, pb = a["puntos"], b["puntos"]
    if not pa or not pb:
        return 0.0
    muestras = [pa[round(i * (len(pa) - 1) / (n - 1))] for i in range(n)]
    paso = max(1, len(pb) // 250)
    pb = pb[::paso] + [pb[-1]]
    cerca = sum(1 for p in muestras if min(_metros_rapido(p, q) for q in pb) < MISMO_CAMINO_M)
    return cerca / n


def mismo_recorrido(a, b):
    if metros(a["ini"], b["ini"]) > MISMO_SITIO_M or metros(a["fin"], b["fin"]) > MISMO_SITIO_M:
        return False
    if not 0.8 <= (a["km_gps"] or 0) / max(b["km_gps"] or 0, 0.01) <= 1.25:
        return False
    return _parte_comun(a, b) >= PARTE_COMUN and _parte_comun(b, a) >= PARTE_COMUN


def _sin_puntos(r):
    return {k: v for k, v in r.items() if k not in ("puntos", "tramos")}


def nombres(base):
    try:
        return json.loads((Path(base) / "rutas.json").read_text()).get("nombres", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def poner_nombre(base, id_ruta, nombre):
    if not re.fullmatch(r"[\w\-]+", str(id_ruta or "")):
        return False, "Ruta desconocida"
    nombre = " ".join(str(nombre or "").split())[:60]
    p = Path(base) / "rutas.json"
    try:
        dato = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        dato = {}
    noms = dato.setdefault("nombres", {})
    if nombre:
        noms[id_ruta] = nombre
    else:
        noms.pop(id_ruta, None)
    p.write_text(json.dumps(dato, ensure_ascii=False, indent=2) + "\n")
    return True, noms


def lista(registros, base):
    """Rutas (las salidas del mismo recorrido juntas), de la más usada a la menos."""
    viajes = []
    for c in sorted(Path(registros).iterdir()):
        f = c / "ruta.json"
        if c.is_dir() and f.exists():
            try:
                r = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            r["carpeta"] = c.name
            viajes.append(r)
    grupos = []
    for v in viajes:  # en orden de fecha: el id de cada ruta es su primera salida y no cambia
        g = next((g for g in grupos if mismo_recorrido(g[0], v)), None)
        if g:
            g.append(v)
        else:
            grupos.append([v])
    noms = nombres(base)
    out = []
    for g in grupos:
        con_l100 = [v for v in g if v.get("l100")]
        mejor = min(con_l100, key=lambda v: v["l100"]) if con_l100 else None
        for v in g:
            v["mejor"] = v is mejor and len(con_l100) > 1
            v["por_que"] = por_que(v, mejor) if mejor and v is not mejor else None
            v["en_que"] = en_que_se_fue(v)
        l100s = [v["l100"] for v in g if v.get("l100")]
        out.append({"id": g[0]["carpeta"], "nombre": noms.get(g[0]["carpeta"]),
                    "veces": len(g), "km": g[-1]["km_gps"], "sube_m": g[-1].get("sube_m"),
                    "baja_m": g[-1].get("baja_m"), "ini": g[0]["ini"], "fin": g[0]["fin"],
                    "l100_media": round(sum(l100s) / len(l100s), 1) if l100s else None,
                    "l100_min": min(l100s) if l100s else None, "l100_max": max(l100s) if l100s else None,
                    "ultima": g[-1].get("inicio"),
                    "viajes": [_sin_puntos(v) for v in reversed(g)]})
    out.sort(key=lambda r: (r["veces"], r["ultima"] or ""), reverse=True)
    return {"rutas": out, "viajes": len(viajes)}


def detalle(registros, carpeta):
    if not re.fullmatch(r"[\w\-]+", str(carpeta or "")):
        return None
    f = Path(registros) / carpeta / "ruta.json"
    if not f.is_file() or f.parent.parent != Path(registros):
        return None
    try:
        r = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    r["carpeta"] = carpeta
    return r

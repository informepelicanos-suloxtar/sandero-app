"""⛽ Depósito: lo que marca el cuadro, los repostajes y el consumo de verdad entre unos y otros.

Saúl apunta (Mantenimiento → ⛽ Depósito, o por el buzón) lo que ve:
  - los litros que quedan en el depósito según el cuadro (menú oculto del cuadro: dejar pulsado el botón del
    reloj/parcial y dar el contacto). Mejor con el coche en llano: en cuesta el aforador marca de más o de menos.
  - los km del cuadro (los totales; el parcial, si lo pone a 0 al llenar),
  - y al repostar, los litros que entran y si llenó hasta el primer salto del surtidor.

deposito.json (raíz del repositorio; el móvil lo sube y lo baja como el mantenimiento):
  {"capacidad_litros": 50,
   "entradas": [{"id", "fecha" ("AAAA-MM-DDTHH:MM", hora de Canarias), "km", "km_parcial", "litros_deposito",
                 "litros_echados", "lleno", "km_centralita", "nota"}]}
  litros_deposito es lo que marcaba ANTES de echar gasolina, si en esa misma parada se repostó.
  km_centralita lo pone la app (contador «km desde el borrado de averías», PID 0x31): vale si faltan los del cuadro.

tramos() saca el consumo real entre entradas: de lleno a lleno (lo más fiable: lo mide el surtidor) o entre dos
lecturas del cuadro. vista() lo compara con lo que calculó la app en las grabaciones de esas fechas (todas, cortas y
largas, como pidió Saúl el 20/11/2026) y propone ajustar factor_consumo.
"""
import json
import random
import re
from datetime import datetime
from pathlib import Path

ARCHIVO = "deposito.json"
CAPACIDAD = 50          # litros del depósito del Sandero I
KM_CENTRALITA = 1.09    # km reales por cada km del contador de la centralita (medido con el GPS el 20/11/2026)
MIN_KM_TRAMO = 20       # menos que esto no es un tramo (un error de 1 L lo descuadra todo)
MIN_KM_AJUSTE = 150     # km de un tramo para proponer ajustar el cálculo de la app
MIN_COBERTURA = 0.6     # parte de esos km que tiene que estar grabada para compararlo con la app
VERSION_ANALISIS = 2    # = analisis.VERSION_ANALISIS (aquí sin importar analisis: lo usa también el móvil)


# ------------------------------------------------------------------ leer y guardar

def cargar(base):
    try:
        d = json.loads((Path(base) / ARCHIVO).read_text())
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("capacidad_litros", CAPACIDAD)
    d["entradas"] = sorted((e for e in d.get("entradas") or [] if isinstance(e, dict) and e.get("id")),
                           key=lambda e: str(e.get("fecha") or ""))
    return d


def _guardar(base, d):
    (Path(base) / ARCHIVO).write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")


def _numero(x, nombre, minimo, maximo, km=False):
    """Número escrito a la española («6,5», «212.480» en km) o None si está vacío. ValueError con un texto claro."""
    if x is None or (isinstance(x, str) and not x.strip()):
        return None
    if isinstance(x, bool):
        raise ValueError(f"{nombre}: no es un número")
    if isinstance(x, (int, float)):
        v = float(x)
    else:
        t = str(x).strip().replace(" ", "")
        if "," in t:                      # coma decimal: los puntos son de miles
            t = t.replace(".", "").replace(",", ".")
        elif km and re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
            t = t.replace(".", "")         # 212.480 km
        try:
            v = float(t)
        except ValueError:
            raise ValueError(f"{nombre}: «{x}» no es un número")
    if not minimo <= v <= maximo or v != v:
        raise ValueError(f"{nombre}: {x} no parece bien (entre {minimo:g} y {maximo:g})")
    return round(v, 2)


def anadir(base, cuerpo, km_centralita=None):
    """Nueva lectura del cuadro o repostaje. Devuelve (True, entrada) o (False, texto del error)."""
    cuerpo = cuerpo or {}
    try:
        fecha = str(cuerpo.get("fecha") or "").strip().replace(" ", "T")[:16]
        if not fecha:
            fecha = datetime.now().strftime("%Y-%m-%dT%H:%M")
        elif len(fecha) == 10:
            fecha += "T12:00"
        try:
            datetime.fromisoformat(fecha)
        except ValueError:
            raise ValueError(f"La fecha «{cuerpo.get('fecha')}» no se entiende")
        e = {"fecha": fecha,
             "km": _numero(cuerpo.get("km"), "Los km totales", 1000, 2_000_000, km=True),
             "km_parcial": _numero(cuerpo.get("km_parcial"), "Los km del parcial", 0, 3000, km=True),
             "litros_deposito": _numero(cuerpo.get("litros_deposito"), "Los litros del depósito", 0, 80),
             "litros_echados": _numero(cuerpo.get("litros_echados"), "Los litros echados", 0.1, 90),
             "lleno": bool(cuerpo.get("lleno")),
             "nota": " ".join(str(cuerpo.get("nota") or "").split())[:200]}
    except ValueError as err:
        return False, str(err)
    if e["litros_deposito"] is None and e["litros_echados"] is None:
        return False, "Pon los litros que marca el cuadro o los que has echado"
    if e["lleno"] and e["litros_echados"] is None:
        return False, "Si has llenado, pon también los litros que han entrado"
    if km_centralita is not None:
        try:
            e["km_centralita"] = int(km_centralita)
        except (TypeError, ValueError):
            pass
    e = {k: v for k, v in e.items() if v not in (None, "")}
    e["id"] = fecha.replace("-", "").replace(":", "").replace("T", "-") + f"-{random.randrange(16 ** 4):04x}"
    d = cargar(base)
    d["entradas"].append(e)
    d["entradas"].sort(key=lambda x: str(x.get("fecha") or ""))
    _guardar(base, d)
    return True, e


def borrar(base, id_entrada):
    d = cargar(base)
    quedan = [e for e in d["entradas"] if e.get("id") != id_entrada]
    if len(quedan) == len(d["entradas"]):
        return False
    d["entradas"] = quedan
    _guardar(base, d)
    return True


# ------------------------------------------------------------------ consumo real

def _km_entre(a, b, parcial_desde_a=False):
    """Km entre dos entradas: el cuentakilómetros total; si no, el parcial; si no, el contador de la centralita."""
    if a.get("km") is not None and b.get("km") is not None and b["km"] > a["km"]:
        return round(b["km"] - a["km"], 1), "cuadro"
    if a.get("km_parcial") is not None and b.get("km_parcial") is not None and b["km_parcial"] > a["km_parcial"]:
        return round(b["km_parcial"] - a["km_parcial"], 1), "parcial"
    if parcial_desde_a and b.get("km_parcial"):  # el parcial se puso a 0 al llenar en «a»
        return b["km_parcial"], "parcial"
    if a.get("km_centralita") is not None and b.get("km_centralita") is not None and b["km_centralita"] > a["km_centralita"]:
        return round((b["km_centralita"] - a["km_centralita"]) * KM_CENTRALITA, 1), "centralita"
    return None, None


def tramos(d):
    """Consumo real hasta cada entrada: de lleno a lleno si se puede y, si no, entre dos lecturas del cuadro (o desde
    un lleno hasta una lectura, contando el depósito lleno como su capacidad: más aproximado).
    Devuelve [{id, desde, hasta, km, litros, l100, como, km_de, desde_km_centralita}] del más viejo al más nuevo."""
    es, cap = d["entradas"], d.get("capacidad_litros") or CAPACIDAD
    out = []
    for j, b in enumerate(es):
        t = None
        # 1) de lleno a lleno: todo lo que se ha echado desde el lleno anterior (este incluido)
        if b.get("lleno") and b.get("litros_echados"):
            i = next((k for k in range(j - 1, -1, -1) if es[k].get("lleno")), None)
            if i is not None:
                km, km_de = _km_entre(es[i], b, parcial_desde_a=True)
                if km:
                    t = {"desde": es[i]["fecha"], "km": km, "como": "de lleno a lleno", "km_de": km_de,
                         "litros": sum(es[k].get("litros_echados") or 0 for k in range(i + 1, j + 1))}
            elif b.get("km_parcial"):
                # el lleno de antes no está apuntado, pero el parcial cuenta desde entonces (si se puso a 0 al llenar)
                desde_c = round(b["km_centralita"] - b["km_parcial"] / KM_CENTRALITA) if b.get("km_centralita") else None
                t = {"desde": None, "km": b["km_parcial"], "litros": b["litros_echados"], "km_de": "parcial",
                     "como": "de lleno a lleno (el anterior sin apuntar)", "desde_km_centralita": desde_c}
        # 2) entre dos lecturas del cuadro (o desde un lleno), sumando lo echado entre medias
        if t is None and b.get("litros_deposito") is not None:
            i = next((k for k in range(j - 1, -1, -1)
                      if es[k].get("litros_deposito") is not None or es[k].get("lleno")), None)
            if i is not None:
                a = es[i]
                if a.get("litros_deposito") is not None:
                    habia, como = a["litros_deposito"] + (a.get("litros_echados") or 0), "entre dos lecturas del cuadro"
                else:
                    habia, como = cap, f"del lleno a una lectura del cuadro (contando el lleno como {cap:g} L)"
                echados = sum(es[k].get("litros_echados") or 0 for k in range(i + 1, j))
                km, km_de = _km_entre(a, b)
                if km:
                    t = {"desde": a["fecha"], "km": km, "litros": round(habia + echados - b["litros_deposito"], 2),
                         "como": como, "km_de": km_de}
        if t and t["km"] >= MIN_KM_TRAMO and t["litros"] > 0:
            t.update(id=b["id"], hasta=b["fecha"], litros=round(t["litros"], 2), l100=round(t["litros"] / t["km"] * 100, 1))
            out.append(t)
    return out


# ------------------------------------------------------------------ lo que calculó la app en esas fechas

def _ultimo_csv(ruta, columna):
    """Último valor de una columna del CSV de una grabación (las filas llevan siempre el último valor leído)."""
    try:
        lineas = Path(ruta).read_text().splitlines()
    except OSError:
        return None
    if not lineas:
        return None
    cab = lineas[0].split(",")
    if columna not in cab:
        return None
    i = cab.index(columna)
    for l in reversed(lineas[1:]):
        partes = l.split(",")
        if len(partes) > i and partes[i]:
            try:
                return float(partes[i])
            except ValueError:
                return None
    return None


def km_centralita_reciente(registros):
    """El último «km desde el borrado» leído del coche (grabación real más nueva), para apuntarlo en cada entrada."""
    try:
        carpetas = sorted((c for c in Path(registros).iterdir() if c.is_dir()), reverse=True)
    except OSError:
        return None
    for c in carpetas[:20]:
        try:
            s = json.loads((c / "sesion.json").read_text())
        except (OSError, ValueError):
            continue
        if "Simulador" in ((s.get("lector") or {}).get("destino") or ""):
            continue
        v = _ultimo_csv(c / "datos.csv", "km_desde_borrado")
        if v is None:
            v = (s.get("averias") or {}).get("km_desde_borrado")
        if v is not None:
            return int(v)
    return None


def _app_en(registros, desde, hasta, desde_km_centralita=None):
    """Lo que calculó la app con TODAS las grabaciones reales entre dos fechas (cortas y largas)."""
    litros = km = 0.0
    n = viejas = 0
    try:
        carpetas = sorted(c for c in Path(registros).iterdir() if c.is_dir())
    except OSError:
        carpetas = []
    for c in carpetas:
        try:
            s = json.loads((c / "sesion.json").read_text())
            r = json.loads((c / "resumen.json").read_text())
        except (OSError, ValueError):
            continue
        if "Simulador" in ((s.get("lector") or {}).get("destino") or ""):
            continue
        ini = (s.get("inicio") or "")[:16]
        if (desde and ini < desde) or (hasta and ini >= hasta):
            continue
        if not desde and desde_km_centralita is not None:
            kc = (s.get("averias") or {}).get("km_desde_borrado")
            if kc is not None and kc < desde_km_centralita - 2:
                continue
        cons = r.get("consumo") or (r.get("estadisticas") or {}).get("consumo") or {}
        if not cons.get("km") and not cons.get("litros"):
            continue
        if (r.get("version_analisis") or 1) < VERSION_ANALISIS:
            viejas += 1
            continue
        litros += cons.get("litros") or 0
        km += cons.get("km") or 0
        n += 1
    return {"litros": round(litros, 2), "km": round(km, 1), "grabaciones": n, "viejas": viejas}


def vista(base, registros, factor=None):
    """Para la web: las entradas (la más nueva arriba) y cada tramo con el consumo real, lo que calculó la app y,
    si hay datos de sobra, el factor de cálculo que haría cuadrar la app con la realidad."""
    d = cargar(base)
    factor = factor or 1.0
    trs = tramos(d)
    for t in trs:
        app = _app_en(registros, t["desde"], t["hasta"], t.get("desde_km_centralita"))
        app["cobertura"] = round(app["km"] / t["km"], 2) if t["km"] else None
        app["l100"] = round(app["litros"] / app["km"] * 100, 1) if app["km"] >= 10 else None
        t["app"] = app
        if (app["l100"] and t["km"] >= MIN_KM_AJUSTE and (app["cobertura"] or 0) >= MIN_COBERTURA
                and app["cobertura"] <= 1.1):
            f = round(factor * t["l100"] / app["l100"], 2)
            if 0.5 <= f <= 2 and abs(f - factor) >= 0.02:
                t["factor_sugerido"] = f
    return {"capacidad_litros": d["capacidad_litros"], "factor_actual": factor,
            "entradas": list(reversed(d["entradas"])), "tramos": list(reversed(trs))}

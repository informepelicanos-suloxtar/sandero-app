"""Diagnóstico cruzando sensores, por sistemas del coche.

Se usa en directo (cada pocos segundos, sobre lo grabado desde que conectaste) y al
terminar una grabación (analisis.py). Recibe filas: dicts con «t» (segundos) y los
valores de cada sensor (o None).

Consumo: el K7M no tiene caudalímetro, así que se estima por «densidad-velocidad»:
aire = presión colector × cilindrada × rendimiento volumétrico × rpm / (R × temperatura).
Gasolina = aire / 14,7 × (1 + correcciones). Error típico ±15 %; se puede ajustar con
el factor de ajustes.json comparando con un repostaje real.
"""
import re
import statistics

CILINDRADA_M3 = 1.598e-3
RENDIMIENTO_VOL = 0.80
DENSIDAD_GASOLINA = 745  # g/L
ESTEQUIOMETRICA = 14.7

SISTEMAS = [
    {"id": "consumo", "nombre": "Consumo de gasolina",
     "descripcion": "Cuánta gasolina gasta. El coche no lo dice directamente: se calcula con el aire que entra y las "
                    "correcciones de mezcla (margen ±15 %). Abajo salen las cosas del coche que pueden estar subiendo el gasto.",
     "sensores": ["consumo_l100", "consumo_lh", "combustible"]},
    {"id": "gasolina", "nombre": "Mezcla aire-gasolina e inyección",
     "descripcion": "El ordenador calcula cuánta gasolina inyectar según el aire que entra y la corrige con la sonda lambda. "
                    "Los inyectores y la presión de gasolina no tienen sensor: se deducen de estas correcciones.",
     "sensores": ["ltft", "stft", "sistema_combustible", "lambda_pedida"]},
    {"id": "aire", "nombre": "Admisión de aire",
     "descripcion": "Por dónde entra el aire: filtro, mariposa y colector, con sus sensores de presión (MAP) y temperatura. "
                    "Una toma de aire o un filtro sucio se notan aquí y en la mezcla.",
     "sensores": ["map", "acelerador", "acelerador_rel", "carga", "carga_abs", "temp_admision", "presion_atm", "maf"]},
    {"id": "escape", "nombre": "Escape: sondas lambda y catalizador",
     "descripcion": "La sonda delantera le dice al coche si la mezcla va rica o pobre; la trasera vigila el catalizador.",
     "sensores": ["sonda1", "sonda2"]},
    {"id": "encendido", "nombre": "Encendido y ralentí",
     "descripcion": "Revoluciones, estabilidad del ralentí (motor paso a paso de ralentí) y el avance de la chispa.",
     "sensores": ["rpm", "avance"]},
    {"id": "refrigeracion", "nombre": "Temperatura del motor",
     "descripcion": "Termostato, sensor de temperatura y ventilador. Un motor que trabaja frío gasta bastante más.",
     "sensores": ["temp_motor", "temp_ambiente"]},
    {"id": "electricidad", "nombre": "Batería y alternador",
     "descripcion": "Si la batería está bien y el alternador carga.",
     "sensores": ["bateria", "voltaje_ecu"]},
    {"id": "marcha", "nombre": "Conducción y contadores",
     "descripcion": "Velocidad, tiempos y contadores desde el último borrado de averías.",
     "sensores": ["velocidad", "tiempo_motor", "km_desde_borrado", "calentamientos_borrado", "km_con_testigo",
                  "min_desde_borrado", "min_con_testigo"]},
]
NOMBRE_SISTEMA = {s["id"]: s["nombre"] for s in SISTEMAS} | {"averias": "Averías y autodiagnósticos"}


# ------------------------------------------------------------------ consumo instantáneo

def consumo_lh(v, factor=1.0):
    """Litros por hora estimados con los últimos valores. None si faltan datos."""
    rpm, presion = v.get("rpm"), v.get("map")
    if rpm is None or presion is None:
        return None
    if rpm < 300:
        return 0.0
    if rpm > 1300 and presion < 27:  # soltando gas: corte de inyección
        return 0.0
    temp_aire = v.get("temp_admision")
    temp_aire = 30 if temp_aire is None else temp_aire
    aire_gs = presion * 1000 * CILINDRADA_M3 * RENDIMIENTO_VOL * rpm / 120 / (287.05 * (temp_aire + 273.15)) * 1000
    corr = 1 + ((v.get("stft") or 0) + (v.get("ltft") or 0)) / 100
    temp = v.get("temp_motor")
    if temp is not None and temp < 60:  # en frío enriquece (aproximado)
        corr *= 1 + (60 - temp) / 100
    return round(aire_gs / ESTEQUIOMETRICA * corr * 3600 / DENSIDAD_GASOLINA * factor, 2)


def derivados(v, factor=1.0):
    lh = consumo_lh(v, factor)
    vel = v.get("velocidad") or 0
    return {"consumo_lh": lh,
            "consumo_l100": round(lh / vel * 100, 1) if lh is not None and vel >= 5 else None}


# ------------------------------------------------------------------ utilidades

def _media(x):
    return statistics.fmean(x) if x else None


def _est(serie):
    if not serie:
        return None
    return {"media": round(statistics.fmean(serie), 2), "min": round(min(serie), 2), "max": round(max(serie), 2),
            "desv": round(statistics.pstdev(serie), 2) if len(serie) > 1 else 0, "n": len(serie)}


def _cruces(serie, umbral=0.45):
    return sum(1 for a, b in zip(serie, serie[1:]) if (a - umbral) * (b - umbral) < 0)


def _n(x, dec=1):
    """Número a la española."""
    return f"{x:.{dec}f}".replace(".", ",")


SISTEMA_DTC = [
    (r"P00[34]", "escape"), (r"P010[0-9]|P011[0-3]", "aire"), (r"P011[5-8]|P012[58]|P048", "refrigeracion"),
    (r"P012[0-3]|P2101|P2135", "aire"), (r"P01[34]|P0420|P2[01]9|P227|P2A", "escape"),
    (r"P017|P02|P0627|P044|P20|P21[78]", "gasolina"), (r"P03|P050[5-9]|P051", "encendido"),
    (r"P05[06]", "electricidad"), (r"P050[01]", "marcha"),
]
DTC_GASTO = re.compile(r"P0171|P0172|P0125|P0128|P011[5-8]|P013|P0300|P030|P032|P2096|P2097|P2187|P2188|P2195|P2196")


def sistema_de(codigo):
    return next((s for patron, s in SISTEMA_DTC if re.match(patron, codigo)), "averias")


# ------------------------------------------------------------------ diagnóstico

# La corrección de mezcla de este coche va a saltos (~4,7 %) y cambia mucho de un viaje a otro (+7,5…+21,8 con el
# mismo coche y las mismas piezas), así que el veredicto se da con la media de varios viajes, no con uno solo.
MEZCLA_MIN_VALIDO = 8     # minutos con el motor caliente y el lazo cerrado para que un viaje cuente
MEZCLA_VIAJES = 5         # cuántos viajes (este incluido) entran en la media
MEZCLA_MIN_VIAJES = 3     # por debajo de esto el veredicto es solo orientativo


def diagnosticar(filas, averias=None, consumo_real=None, factor_consumo=None, corr_velocidad=1.0,
                 historial_mezcla=None):
    """historial_mezcla: viajes anteriores, del más viejo al más nuevo, como {"carpeta", "media", "min"}
    (estadisticas.mezcla_caliente de cada resumen.json). Sirve para dar la mezcla con la media de varios viajes."""
    hallazgos, stats = [], {}

    def h(sistema, nivel, titulo, detalle, gasto=False):
        hallazgos.append({"sistema": sistema, "nivel": nivel, "titulo": titulo, "detalle": detalle, "gasto": gasto})

    # --- averías (no necesitan filas)
    if averias:
        for tipo, txt in (("guardadas", "guardada"), ("pendientes", "pendiente"), ("permanentes", "permanente")):
            for a in averias.get(tipo) or []:
                nivel = "rojo" if tipo != "pendientes" or a["gravedad"] == 3 else "amarillo"
                h(sistema_de(a["codigo"]), nivel, f"Avería {txt} {a['codigo']}",
                  f"{a['descripcion']}. {a['consejo']}", gasto=bool(DTC_GASTO.match(a["codigo"])))
        mon = averias.get("monitores")
        if mon:
            inc = [m["nombre"] for m in mon["monitores"] if not m["completo"]]
            if mon.get("testigo"):
                if (mon.get("num_averias") or 0) > 0 or averias.get("guardadas"):
                    h("averias", "rojo", "Testigo del motor encendido", "El coche tiene una avería confirmada.")
                elif (averias.get("km_con_testigo") or 0) > 0:
                    h("averias", "amarillo", "Testigo marcado sin códigos guardados",
                      "La centralita indica testigo encendido pero dice tener 0 averías guardadas.")
                else:
                    h("averias", "gris", "Testigo encendido al dar el contacto",
                      "Al leer con el contacto puesto (motor parado) la luz del cuadro se enciende como comprobación (0 averías y 0 km con testigo).")
            if inc:
                h("averias", "amarillo", "Autodiagnósticos sin terminar",
                  "Aún no ha comprobado: " + ", ".join(inc) + ". Normal tras borrar averías; se completan conduciendo "
                  "unos días (ciudad y carretera). Para la ITV conviene tenerlos hechos.")
            else:
                h("averias", "verde", "Autodiagnósticos completos", "El coche ha terminado todas sus comprobaciones.")

    filas = [f for f in filas if f.get("t") is not None]
    if len(filas) < 10:
        return _cerrar(hallazgos, stats, None)

    dur = filas[-1]["t"] - filas[0]["t"]
    stats["duracion_min"] = round(dur / 60, 1)
    stats["filas_por_s"] = round(len(filas) / dur, 2) if dur else None

    en_marcha = lambda f: (f.get("rpm") or 0) > 400
    caliente = lambda f: (f.get("temp_motor") or 0) >= 80
    parado = lambda f: (f.get("velocidad") or 0) == 0
    ralenti_cal = lambda f: en_marcha(f) and caliente(f) and parado(f) and f["rpm"] < 1200
    conduciendo = lambda f: en_marcha(f) and (f.get("velocidad") or 0) > 5
    corte = lambda f: (f.get("rpm") or 0) > 1300 and (f.get("map") or 100) < 27
    lazo = lambda f: f.get("sistema_combustible") in (2, 2.0, 16, 16.0)

    def serie(clave, cond=lambda f: True):
        return [f[clave] for f in filas if f.get(clave) is not None and cond(f)]

    marcha = [f for f in filas if en_marcha(f)]
    acels = sorted(f["acelerador"] for f in marcha if f.get("acelerador") is not None)
    acel_min = acels[len(acels) // 20] if acels else None  # posición «pie fuera» (el sensor no marca 0)
    pie_fuera = lambda f: acel_min is None or f.get("acelerador") is None or f["acelerador"] <= acel_min + 3

    # ralentí de verdad: parado y sin pisar >= 12 s seguidos, sin los 5 primeros s (bajando de vueltas)
    # ni los 3 últimos (el acelerador y la velocidad tardan ~2 s en reflejar que ya estás saliendo)
    estables, tramo = set(), []
    for f in filas + [{"t": None}]:
        if f.get("t") is not None and en_marcha(f) and caliente(f) and parado(f) and pie_fuera(f) and f["rpm"] < 1200:
            tramo.append(f)
            continue
        if tramo and tramo[-1]["t"] - tramo[0]["t"] >= 12:
            t_ini, t_fin = tramo[0]["t"], tramo[-1]["t"]
            estables.update(id(x) for x in tramo if x["t"] - t_ini > 5 and t_fin - x["t"] > 3)
        tramo = []
    ralenti_cal = lambda f: id(f) in estables
    t_marcha = sum(min(b["t"] - a["t"], 5) for a, b in zip(filas, filas[1:]) if en_marcha(a))

    # ================= refrigeración
    temps = serie("temp_motor", en_marcha)
    en_carretera = [f["temp_motor"] for f in filas if (f.get("velocidad") or 0) > 40 and f.get("temp_motor") is not None
                    and f["t"] - filas[0]["t"] > 300]
    ya_caliente = bool(temps and max(temps) >= 85)
    enfria_marcha = False
    if len(en_carretera) > 300 and ya_caliente:
        med_carr = statistics.median(en_carretera)
        stats["temp_motor_en_marcha"] = med_carr
        enfria_marcha = med_carr < 84

    if temps:
        stats["temp_motor"] = _est(temps)
        tmax = sorted(temps)[int(len(temps) * 0.99)]  # sin contar picos sueltos de un conector
        if tmax > 106:
            h("refrigeracion", "rojo", "El motor se calentó de más",
              f"Llegó a {tmax:.0f} °C. Revisa el nivel de refrigerante y que salte el ventilador.")
        primera = next(f for f in marcha if f.get("temp_motor") is not None)
        t0 = primera["t"]
        if primera["temp_motor"] < 40:
            iat0 = primera.get("temp_admision")
            if iat0 is not None and primera["temp_motor"] < iat0 - 15:
                h("refrigeracion", "amarillo", "El sensor de temperatura puede marcar de menos",
                  f"Al arrancar en frío marcaba {primera['temp_motor']:.0f} °C con el aire a {iat0:.0f} °C; deberían "
                  "parecerse. Si el sensor cree que el motor está frío, echa gasolina de más.", gasto=True)
            t80 = next((f["t"] for f in marcha if (f.get("temp_motor") or 0) >= 80), None)
            if t80 is not None:
                mins = (t80 - t0) / 60
                stats["min_hasta_80C"] = round(mins, 1)
                prop_parado = sum(1 for f in marcha if f["t"] <= t80 and parado(f)) / \
                    max(1, sum(1 for f in marcha if f["t"] <= t80))
                limite = 20 if prop_parado > 0.7 else 12
                if mins > limite:
                    h("refrigeracion", "amarillo", "Tarda mucho en calentar",
                      f"Tardó {mins:.0f} min en llegar a 80 °C (normal: menos de {limite} min). Suele ser el termostato, "
                      "que se queda abierto. Con el motor frío gasta más.", gasto=True)
                else:
                    h("refrigeracion", "verde", "Calienta a buen ritmo", f"Llegó a 80 °C en {_n(mins)} min.")
            elif (filas[-1]["t"] - t0) > 20 * 60:
                h("refrigeracion", "rojo", "El motor no llega a calentarse",
                  f"Tras {(filas[-1]['t'] - t0) / 60:.0f} min no pasó de {max(temps):.0f} °C. Termostato abierto o sensor "
                  "de temperatura: es una causa muy típica de gastar mucho más.", gasto=True)
            else:
                h("refrigeracion", "gris", "Calentando", "Aún no ha llegado a 80 °C; sigo mirando cuánto tarda.")
        else:
            tarde = [f["temp_motor"] for f in marcha if f["t"] - t0 > 10 * 60 and f.get("temp_motor") is not None]
            if len(tarde) > 30 and statistics.median(tarde) < 78:
                h("refrigeracion", "amarillo", "El motor va frío",
                  f"Después de 10 min sigue a {statistics.median(tarde):.0f} °C de media (normal 85-100). "
                  "Termostato abierto: gasta más.", gasto=True)
            elif max(temps) >= 80:
                h("refrigeracion", "verde",
                  "Alcanza temperatura parado" if enfria_marcha else "Temperatura de trabajo correcta",
                  f"Máxima {max(temps):.0f} °C.")
            if not enfria_marcha and len(en_carretera) <= 300:
                h("refrigeracion", "gris", "Termostato sin revisar del todo",
                  "El motor ya estaba templado al conectar. Para revisarlo bien haz la prueba de arranque en frío o circula en carretera.")

    if len(en_carretera) > 300 and ya_caliente and not any(x["titulo"] in ("El motor va frío", "El motor no llega a calentarse")
                                                             for x in hallazgos):
        med = stats["temp_motor_en_marcha"]
        if enfria_marcha:
            h("refrigeracion", "amarillo", "Se enfría en marcha: termostato",
              f"Parado sube a {max(temps):.0f} °C, pero circulando baja a {med:.0f} °C de media. Con el termostato bien se "
              "mantiene cerca de 89-90 °C también en marcha. Lo típico es un termostato que se queda algo abierto "
              "(pieza barata). Con el motor más frío gasta algo más y la calefacción calienta menos.", gasto=True)
        else:
            h("refrigeracion", "verde", "Mantiene la temperatura en marcha", f"{med:.0f} °C de media circulando.")

    # ================= ralentí y encendido
    rpm_r = serie("rpm", ralenti_cal)
    if len(rpm_r) >= 15:
        m, d = statistics.fmean(rpm_r), statistics.pstdev(rpm_r)
        stats["ralenti_caliente_rpm"] = _est(rpm_r)
        if m > 950:
            h("encendido", "amarillo", "Ralentí alto en caliente",
              f"{m:.0f} rpm de media (normal 750-850). Toma de aire o motor de ralentí/mariposa sucios. Gasta algo más.",
              gasto=True)
        elif m < 650:
            h("encendido", "amarillo", "Ralentí bajo en caliente", f"{m:.0f} rpm de media. Motor de ralentí o mariposa sucios.")
        if d > 60:
            h("encendido", "amarillo", "Ralentí inestable",
              f"Las revoluciones bailan ±{d:.0f} rpm. Típico del motor paso a paso de ralentí o la mariposa sucios; "
              "también bujías o bobina.")
        elif 650 <= m <= 950:
            h("encendido", "verde", "Ralentí estable", f"{m:.0f} rpm de media, bailando ±{d:.0f}.")
        av_r = serie("avance", ralenti_cal)
        if av_r and statistics.fmean(av_r) < 0:
            h("encendido", "amarillo", "Avance muy retrasado al ralentí", f"{_n(statistics.fmean(av_r))}° de media.")
    else:
        h("encendido", "gris", "Ralentí en caliente sin revisar", "Hace falta un rato con el motor caliente y el coche parado.")
    av_c = serie("avance", lambda f: conduciendo(f) and (f.get("carga") or 100) < 50 and (f.get("velocidad") or 0) > 30)
    if len(av_c) > 30:
        m = statistics.fmean(av_c)
        stats["avance_conduciendo_poca_carga"] = _est(av_c)
        if m < 15:
            h("encendido", "amarillo", "Encendido retrasado en marcha",
              f"Con poco gas va a {_n(m)}° de media (normal 25-40°). Puede estar detectando picado (gasolina, "
              "carbonilla, sensor de picado). Con la chispa retrasada el motor rinde menos y gasta más.", gasto=True)
        else:
            h("encendido", "verde", "Avance del encendido correcto en marcha", f"{m:.0f}° de media con poco gas.")

    # ================= mezcla
    lt = serie("ltft", en_marcha)
    st = serie("stft", lambda f: en_marcha(f) and lazo(f))
    if lt and st:
        def total(cond):
            a = [f["ltft"] + f["stft"] for f in filas if cond(f) and lazo(f)
                 and f.get("ltft") is not None and f.get("stft") is not None]
            return (statistics.fmean(a), len(a)) if a else (None, 0)
        tot, _ = total(en_marcha)
        tot_r, n_r = total(ralenti_cal)
        tot_c, n_c = total(conduciendo)
        stats["correccion_total"] = round(tot, 1) if tot is not None else None
        stats["correccion_ralenti"] = round(tot_r, 1) if tot_r is not None else None
        stats["correccion_conduciendo"] = round(tot_c, 1) if tot_c is not None else None
        stats["ltft"] = _est(lt)
        # con el motor caliente y el lazo cerrado: el dato que se compara entre viajes
        cal_m, t_cal = [], 0.0
        for a, b in zip(filas, filas[1:]):
            if en_marcha(a) and lazo(a) and (a.get("temp_motor") or 0) >= 78 \
                    and a.get("ltft") is not None and a.get("stft") is not None:
                cal_m.append(a["ltft"] + a["stft"])
                t_cal += min(b["t"] - a["t"], 5)
        este = None
        if cal_m:
            este = {"media": round(statistics.fmean(cal_m), 1), "min": round(t_cal / 60, 1)}
            stats["mezcla_caliente"] = este
        valido = bool(este and este["min"] >= MEZCLA_MIN_VALIDO)
        prev = [x for x in (historial_mezcla or []) if x.get("media") is not None
                and (x.get("min") or 0) >= MEZCLA_MIN_VALIDO]
        viajes = (prev + [este])[-MEZCLA_VIAJES:] if valido else prev[-MEZCLA_VIAJES:]
        firmar = lambda x: f"{'+' if x > 0 else ''}{_n(x, 1)}"
        minutos = lambda e: "menos de 1 min" if not e or e["min"] < 1 else f"{_n(e['min'], 0)} min"
        if viajes:
            med = statistics.fmean(x["media"] for x in viajes)
            lo, hi = min(x["media"] for x in viajes), max(x["media"] for x in viajes)
            stats["mezcla_media_viajes"] = {"media": round(med, 1), "viajes": len(viajes), "min": lo, "max": hi}
            if len(viajes) == 1 and valido:
                base = (f"En este viaje, con el motor caliente: {firmar(med)} %. Es un solo viaje: este dato va a saltos y "
                        "cambia mucho de un día a otro, así que es orientativo hasta tener al menos "
                        f"{MEZCLA_MIN_VIAJES} viajes de más de {MEZCLA_MIN_VALIDO} min.")
            else:
                base = (f"Media de los últimos {len(viajes)} viajes con el motor caliente: {firmar(med)} % "
                        f"(de {firmar(lo)} a {firmar(hi)}). " if len(viajes) > 1 else
                        f"El último viaje que sirve, con el motor caliente: {firmar(med)} %. ")
                base += (f"Este viaje: {firmar(este['media'])} %." if valido else
                         f"Este viaje no cuenta: solo {minutos(este)} con el motor caliente "
                         f"(hacen falta {MEZCLA_MIN_VALIDO}).")
                if len(viajes) < MEZCLA_MIN_VIAJES:
                    base += f" Aún orientativo: faltan viajes hasta {MEZCLA_MIN_VIAJES}."
            tot_v = med
        else:
            # sin viajes que sirvan: el dato de este viaje, avisando de que no vale para juzgar
            tot_v = None
            if tot is not None:
                h("gasolina", "gris", "Mezcla: viaje corto, no sirve para juzgar",
                  f"Corrección {firmar(tot)} %, pero solo con {minutos(este)} con el motor "
                  f"caliente (hacen falta {MEZCLA_MIN_VALIDO}). Este dato va a saltos y baila mucho en viajes cortos.")
        if tot_v is not None:
            if tot_v < -15:
                h("gasolina", "rojo" if tot_v < -22 else "amarillo", "Mezcla rica: le sobra gasolina",
                  base + " El coche quita gasolina para compensar. Lo que causa esto: un inyector "
                  "que gotea, el sensor MAP o el de temperatura mintiendo, o el canister. Gasta más.", gasto=True)
            elif tot_v > 15:
                h("gasolina", "rojo" if tot_v > 22 else "amarillo", "Mezcla pobre: le falta gasolina o le sobra aire",
                  base + " El coche añade gasolina para compensar.", gasto=True)
            elif abs(tot_v) > 10:
                h("gasolina", "amarillo", "La mezcla necesita bastante corrección", base + " Aceptable, pero vigílalo.")
            else:
                h("gasolina", "verde", "Mezcla correcta", base)
        # cruzar ralentí con marcha: dónde está el problema
        if tot_r is not None and tot_c is not None and n_r >= 15 and n_c > 30:
            if tot_r > 10 and tot_c < tot_r - 7 and n_r >= 100:
                h("gasolina", "gris", "Corrige más al ralentí que en marcha",
                  f"Al ralentí añade {'+' if tot_r > 0 else ''}{_n(tot_r, 0)} % y en marcha "
                  f"{'+' if tot_c > 0 else ''}{_n(tot_c, 0)} %. En paradas cortas es normal que la corrección rápida se dispare. "
                  "Si se repite en paradas largas: fuga de escape antes de la sonda, bujías/cables o inyectores "
                  "(en este motor, que mide el aire por presión, una toma de aire casi no se nota aquí). "
                  "La prueba guiada de ralentí + 2.500 rpm lo aclara.")
            elif tot_c > 10 and tot_c > tot_r + 7:
                h("gasolina", "amarillo", "Le falta gasolina cuando pides fuerza",
                  f"En marcha añade {_n(tot_c, 0)} % y al ralentí solo {_n(tot_r, 0)} %. Apunta a poca presión de gasolina "
                  "(filtro, bomba) o inyectores sucios.")
            elif abs(tot_r) <= 10 and abs(tot_c) <= 10:
                h("gasolina", "verde", "Inyectores y presión de gasolina sin pistas de fallo",
                  "La mezcla está bien tanto al ralentí como en marcha.")
        # lazo abierto en caliente
        # sin contar los cortes de gasolina al soltar gas ni los ~12 s siguientes (el coche tarda en volver a
        # usar la sonda y este dato se lee cada varios segundos); tampoco pisando a fondo
        cal, t_corte = [], None
        for f in marcha:
            if (f.get("rpm") or 0) > 1200 and (f.get("map") or 100) < 30:
                t_corte = f["t"]
            if caliente(f) and f.get("sistema_combustible") is not None and pie_fuera(f) is False \
                    and (f.get("map") or 0) < 80 and (t_corte is None or f["t"] - t_corte > 12):
                cal.append(f)
        if len(cal) > 60:
            prop = sum(1 for f in cal if not lazo(f)) / len(cal)
            stats["prop_lazo_abierto_caliente"] = round(prop, 2)
            if prop > 0.25:
                h("gasolina", "amarillo", "Pasa mucho tiempo sin usar la sonda lambda",
                  f"Con el motor caliente, el {prop * 100:.0f} % del tiempo va «a ciegas» (lazo abierto) y entonces suele "
                  "echar gasolina de más. Sonda lambda delantera o su calefactor.", gasto=True)
    elif marcha and not st:
        if temps and max(temps) >= 80 and len(marcha) > 100:
            h("gasolina", "amarillo", "El coche no llega a usar la sonda lambda",
              "Con el motor caliente sigue en lazo abierto: sonda lambda o su calefactor. Gasta más.", gasto=True)

    # ================= prueba parado: ralentí frente a 2.500 rpm (la firma de una toma de aire)
    alto = [f["ltft"] + f["stft"] for f in filas if en_marcha(f) and caliente(f) and parado(f) and 2200 <= f["rpm"] <= 3000
            and lazo(f) and f.get("ltft") is not None and f.get("stft") is not None]
    bajo = [f["ltft"] + f["stft"] for f in filas if ralenti_cal(f) and lazo(f)
            and f.get("ltft") is not None and f.get("stft") is not None]
    if len(alto) >= 15 and len(bajo) >= 20:
        a_, b_ = statistics.fmean(alto), statistics.fmean(bajo)
        stats["correccion_2500_parado"], stats["correccion_ralenti_parado"] = round(a_, 1), round(b_, 1)
        if b_ - a_ >= 8:
            h("aire", "amarillo", "Prueba de 2.500 rpm: pinta a toma de aire",
              f"Al ralentí corrige {'+' if b_ > 0 else ''}{_n(b_, 0)} % y a 2.500 rpm {'+' if a_ > 0 else ''}{_n(a_, 0)} %. Cuando la falta de "
              "gasolina baja tanto al subir de vueltas, suele ser aire que entra por donde no debe (manguitos, junta del "
              "colector, servofreno).")
        else:
            h("aire", "verde", "Prueba de 2.500 rpm: no parece una toma de aire",
              f"Corrige parecido al ralentí ({'+' if b_ > 0 else ''}{_n(b_, 0)} %) y a 2.500 rpm ({'+' if a_ > 0 else ''}{_n(a_, 0)} %). "
              "Si la mezcla va pobre, el motivo será otro (escape, sensor MAP, inyectores o presión de gasolina).")

    # ================= sonda al soltar gas: con la inyección cortada debe caer a casi 0 V enseguida
    tramos_corte, cur = [], []
    for f in filas:
        if corte(f) and conduciendo(f):
            cur.append(f)
        else:
            if cur:
                tramos_corte.append(cur)
            cur = []
    largos = [t for t in tramos_corte if t[-1]["t"] - t[0]["t"] >= 3]
    if len(largos) >= 3:
        finales = [min(f["sonda1"] for f in t if f.get("sonda1") is not None and f["t"] - t[0]["t"] >= 2)
                   for t in largos if any(f.get("sonda1") is not None and f["t"] - t[0]["t"] >= 2 for f in t)]
        if finales:
            m = statistics.median(finales)
            stats["sonda1_soltando_gas"] = round(m, 2)
            if m > 0.25:
                h("escape", "amarillo", "La sonda delantera no baja al soltar gas",
                  f"Con la inyección cortada debería caer a casi 0 V y se queda en {_n(m, 2)} V. Sonda lenta o vieja: "
                  "mide mal y el coche corrige mal la mezcla.", gasto=True)
            else:
                h("escape", "verde", "La sonda delantera responde rápido",
                  f"Al soltar gas cae a {_n(m, 2)} V, como debe.")

    # ================= admisión
    map_r = serie("map", ralenti_cal)
    if len(map_r) >= 15:
        m = statistics.fmean(map_r)
        stats["map_ralenti"] = _est(map_r)
        if m > 48:
            h("aire", "amarillo", "Poco vacío al ralentí",
              f"{m:.0f} kPa de media (normal 25-45). Toma de aire, sensor MAP o mariposa que no cierra.")
        else:
            h("aire", "verde", "Buen vacío al ralentí", f"{m:.0f} kPa de media.")
    map_p = serie("map", lambda f: not en_marcha(f))
    if len(map_p) > 3:
        m = statistics.fmean(map_p)
        if not 85 <= m <= 106:
            h("aire", "amarillo", "El sensor MAP marca raro con el motor parado", f"{m:.0f} kPa en vez de ~100.",
              gasto=True)
        else:
            h("aire", "verde", "Sensor MAP coherente con el motor parado", f"Marca {m:.0f} kPa (la presión de la calle).")
    umbral_fondo = (acel_min or 15) + 55
    fondo = sorted(serie("map", lambda f: en_marcha(f) and (f.get("acelerador") or 0) >= umbral_fondo and f["rpm"] > 2000))
    if len(fondo) >= 5:
        ref = _media(serie("presion_atm")) or _media(map_p) or 100
        m = fondo[int(len(fondo) * 0.9)]
        stats["map_a_fondo"] = round(m, 1)
        if ref - m > 12:
            h("aire", "amarillo", "Le cuesta respirar a fondo",
              f"Pisando a fondo el colector llega a {m:.0f} kPa y la calle está a {ref:.0f}. Debería acercarse más: "
              "filtro de aire sucio o algo que obstruye la admisión. Pierde fuerza y gasta más.", gasto=True)
        else:
            h("aire", "verde", "Respira bien a fondo", f"A fondo llega a {m:.0f} kPa (calle {ref:.0f}).")
    elif marcha:
        h("aire", "gris", "Filtro de aire sin revisar",
          "Para comprobarlo hace falta pisar a fondo unos segundos por encima de 2.000 rpm (en una cuesta o incorporación, con seguridad).")
    iat_c = serie("temp_admision", conduciendo)
    if len(iat_c) > 60 and statistics.fmean(iat_c) > 60:
        h("aire", "amarillo", "El aire entra muy caliente en marcha",
          f"{statistics.fmean(iat_c):.0f} °C de media conduciendo. Revisa que la toma de aire del filtro no esté chupando aire caliente.")

    # ================= escape
    s1 = serie("sonda1", lambda f: en_marcha(f) and lazo(f))
    if len(s1) > 30:
        stats["sonda1"] = _est(s1)
        if max(s1) - min(s1) < 0.4:
            h("escape", "amarillo", "La sonda lambda delantera apenas se mueve",
              f"Solo va de {_n(min(s1), 2)} a {_n(max(s1), 2)} V (debería ir de ~0,1 a ~0,9). Sonda vieja: el coche "
              "ajusta peor la mezcla y gasta más.", gasto=True)
        else:
            h("escape", "verde", "La sonda lambda delantera trabaja", f"Oscila entre {_n(min(s1), 2)} y {_n(max(s1), 2)} V.")
        if statistics.fmean(s1) > 0.6:
            h("escape", "amarillo", "La sonda delantera marca rico casi siempre",
              f"Media {_n(statistics.fmean(s1), 2)} V. La mezcla tiende a ir rica.", gasto=True)
        s2 = serie("sonda2", lambda f: en_marcha(f) and lazo(f) and caliente(f) and not corte(f))
        s2_cond = serie("sonda2", lambda f: conduciendo(f) and lazo(f) and caliente(f) and not corte(f))
        if len(s2) > 30 and t_marcha >= 120:
            c1, c2 = _cruces(s1) / len(s1), _cruces(s2) / len(s2)
            stats["sonda2"] = {**_est(s2), "ratio_cruces": round(c2 / c1, 2) if c1 else None}
            if c1 and c2 / c1 > 0.6 and max(s2) - min(s2) > 0.4:
                h("escape", "amarillo", "Puede que el catalizador esté gastado",
                  "La sonda trasera oscila casi como la delantera. Si sale el código P0420, es el catalizador.")
            elif len(s2_cond) > 30 and statistics.fmean(s2_cond) < 0.3:
                h("escape", "amarillo", "Sonda trasera muy baja en marcha",
                  "Posible fuga en el escape antes de la sonda o sonda trasera vieja.")
            else:
                h("escape", "verde", "El catalizador trabaja",
                  f"La sonda trasera está estable ({_n(statistics.fmean(s2), 2)} V).")
        else:
            h("escape", "gris", "Catalizador sin revisar", "Hace falta al menos un par de minutos con el motor caliente en marcha.")
    elif marcha:
        h("escape", "gris", "Sondas sin revisar", "Las sondas solo se pueden mirar con el motor caliente (lazo cerrado).")

    # ================= electricidad
    t_arranque = marcha[0]["t"] if marcha else None
    en_arranque = lambda f: (100 <= (f.get("rpm") or 0) <= 400) or (t_arranque is not None and abs(f["t"] - t_arranque) < 8)
    bat_m = serie("bateria", lambda f: en_marcha(f) and (t_arranque is None or f["t"] - t_arranque >= 6))
    bat_p = serie("bateria", lambda f: not en_marcha(f) and not en_arranque(f))
    if bat_m:
        m = statistics.fmean(bat_m)
        stats["bateria_en_marcha"] = _est(bat_m)
        if m < 13.2:
            h("electricidad", "rojo", "El alternador carga poco", f"{_n(m)} V con el motor en marcha (normal 13,8-14,6). Alternador o correa.")
        elif m > 14.9:
            h("electricidad", "rojo", "Voltaje demasiado alto", f"{_n(m)} V: regulador del alternador.")
        else:
            h("electricidad", "verde", "El alternador carga bien", f"{_n(m)} V de media con el motor en marcha.")
    if bat_p:
        m = statistics.fmean(bat_p)
        stats["bateria_parado"] = _est(bat_p)
        if m < 12.1:
            h("electricidad", "amarillo", "Batería algo baja", f"{_n(m)} V con el motor parado. Si cuesta arrancar, pruébala en un taller.")
        else:
            h("electricidad", "verde", "Batería bien con el motor parado", f"{_n(m)} V.")

    # ================= conducción
    crucero = [f for f in filas if conduciendo(f) and 80 <= (f.get("velocidad") or 0) <= 110 and f.get("carga") is not None]
    if len(crucero) > 60:
        c = statistics.fmean(f["carga"] for f in crucero)
        stats["carga_crucero"] = round(c, 1)
        if c > 70:
            h("marcha", "amarillo", "El motor se esfuerza mucho a velocidad de carretera",
              f"A 80-110 km/h va al {c:.0f} % de esfuerzo. Si era llano: mira la presión de los neumáticos y que no "
              "roce ningún freno (rueda que se calienta).", gasto=True)

    # ================= salud de cada sensor (valores imposibles, congelados, saltos, incoherencias)
    # --- velocidad del coche frente al GPS del móvil (página /gps)
    _gps_vs_coche(filas, corr_velocidad or 1.0, stats, h)
    _recorrido_gps(filas, stats, h)
    _salud_sensores(filas, marcha, h, en_marcha, parado, caliente, conduciendo, lazo, temps, acel_min)

    # ================= consumo
    consumo = _consumo(filas, en_marcha, parado, lambda f: (f.get("temp_motor") or 99) < 70, stats, factor_consumo)
    if consumo:
        _hallazgos_consumo(consumo, consumo_real, hallazgos, h)

    return _cerrar(hallazgos, stats, consumo)


SISTEMA_SENSOR = {k: s["id"] for s in SISTEMAS for k in s["sensores"]}
# valor imposible (lo que da el sensor con el cable cortado o en corto) y rango físico creíble
LIMITES = {
    "temp_motor": ("Sensor de temperatura del motor", -39, 125, 8, "°C"),
    "temp_admision": ("Sensor de temperatura del aire", -39, 95, 10, "°C"),
    "map": ("Sensor de presión de admisión (MAP)", 8, 108, None, "kPa"),
    "sonda1": ("Sonda lambda delantera", None, 1.1, None, "V"),
    "sonda2": ("Sonda lambda trasera", None, 1.1, None, "V"),
    "velocidad": ("Sensor de velocidad", None, 200, 45, "km/h"),
    "rpm": ("Sensor de régimen (cigüeñal)", None, 6800, None, "rpm"),
    "bateria": ("Tensión de batería", 9.5, 16, None, "V"),
}


def _gps_vs_coche(filas, corr, stats, h):
    """Compara la velocidad de la centralita con la del GPS solo a velocidad estable (el GPS va ~1 s retrasado)."""
    ratios = []
    for i in range(10, len(filas) - 10):
        f = filas[i]
        g, v = f.get("gps_velocidad"), f.get("velocidad")
        if not g or not v or g < 25 or v < 20:
            continue
        vecinos = [filas[j].get("velocidad") for j in (i - 10, i - 5, i + 5, i + 10)]
        if any(x is None or abs(x - v) > 2 for x in vecinos):
            continue
        ratios.append(g / v)
    if len(ratios) < 30:
        if ratios:
            stats["gps_vs_coche"] = {"muestras": len(ratios)}
        return
    ratios.sort()
    med = ratios[len(ratios) // 2]
    sugerido = round(corr * med, 3)
    stats["gps_vs_coche"] = {"muestras": len(ratios), "gps_entre_coche": round(med, 3),
                             "correccion_actual": corr, "correccion_sugerida": sugerido}
    dif = (med - 1) * 100
    if abs(dif) <= 2:
        h("marcha", "verde", "La velocidad cuadra con el GPS",
          f"Diferencia {_n(dif)} % en {len(ratios)} momentos a velocidad estable (corrección {_n(corr, 3)}).")
    else:
        h("marcha", "gris", "Ajustar la corrección de velocidad",
          f"Con la corrección {_n(corr, 3)} la centralita aún marca un {_n(abs(dif))} % "
          f"{'menos' if dif > 0 else 'más'} que el GPS ({len(ratios)} momentos a velocidad estable). "
          f"Pon {_n(sugerido, 3)} en «Corrección velocidad». No es avería: suele ser el tamaño de las ruedas.")


def _recorrido_gps(filas, stats, h):
    """Con la posición y la altura del GPS del móvil: km recorridos, cuánto sube y cuánto baja."""
    import math
    puntos = [(f["t"], f["gps_lat"], f["gps_lon"], f.get("gps_alt")) for f in filas
              if f.get("gps_lat") is not None and f.get("gps_lon") is not None]
    if len(puntos) < 30:
        return
    km, ant = 0.0, None
    for _t, la, lo, _a in puntos:
        if ant and (ant[0], ant[1]) != (la, lo):
            dla, dlo = math.radians(la - ant[0]), math.radians(lo - ant[1])
            x = math.sin(dla / 2) ** 2 + math.cos(math.radians(la)) * math.cos(math.radians(ant[0])) * math.sin(dlo / 2) ** 2
            d = 2 * 6371 * math.asin(math.sqrt(x))
            if d < 0.2:  # saltos raros del GPS (más de 200 m entre dos lecturas): fuera
                km += d
        ant = (la, lo)
    stats["gps_recorrido"] = {"puntos": len(puntos), "km_gps": round(km, 1)}
    alts = [a for *_r, a in puntos if a is not None]
    if len(alts) < 30:
        h("marcha", "gris", "Recorrido con GPS", f"{_n(km)} km según el GPS del móvil (sin altura fiable).")
        return
    # alisar (el GPS baila ±5-10 m en altura) y contar subidas y bajadas solo cuando cambian más de 5 m
    ventana = 15
    lisas = [sorted(alts[max(0, i - ventana):i + ventana + 1])[len(alts[max(0, i - ventana):i + ventana + 1]) // 2]
             for i in range(len(alts))]
    sube = baja = 0.0
    ref = lisas[0]
    for a in lisas:
        if a - ref >= 5:
            sube += a - ref; ref = a
        elif ref - a >= 5:
            baja += ref - a; ref = a
    stats["gps_recorrido"].update({"sube_m": round(sube), "baja_m": round(baja),
                                   "alt_min": round(min(lisas)), "alt_max": round(max(lisas)),
                                   "alt_inicio": round(lisas[0]), "alt_fin": round(lisas[-1])})
    h("marcha", "gris", "Recorrido con GPS",
      f"{_n(km)} km según el GPS del móvil. Sube {round(sube)} m y baja {round(baja)} m "
      f"(empieza a {round(lisas[0])} m y acaba a {round(lisas[-1])} m sobre el mar; "
      f"entre {round(min(lisas))} y {round(max(lisas))} m).")


def _cambios(filas, clave):
    """Lecturas en las que el valor cambia: [(t, valor, fila)]."""
    out, previo = [], object()
    for f in filas:
        v = f.get(clave)
        if v is not None and v != previo:
            out.append((f["t"], v, f))
            previo = v
    return out


def _salud_sensores(filas, marcha, h, en_marcha, parado, caliente, conduciendo, lazo, temps, acel_min):
    malos = set()
    for clave, (nombre, vmin, vmax, salto, ud) in LIMITES.items():
        vals = [f[clave] for f in filas if f.get(clave) is not None and (clave != "map" or en_marcha(f))]
        if not vals:
            continue
        sis = SISTEMA_SENSOR.get(clave, "averias")
        raros = [v for v in vals if (vmin is not None and v < vmin) or (vmax is not None and v > vmax)]
        if len(raros) >= 2:
            malos.add(clave)
            h(sis, "rojo", f"{nombre}: valores imposibles",
              f"Ha marcado {_n(raros[0], 1 if ud == 'V' else 0)} {ud} ({len(raros)} veces). Eso no es real: suele ser el sensor "
              "averiado, un cable cortado o en corto, o el conector suelto.")
        if salto:
            cam = _cambios(filas, clave)
            picos = sum(1 for (t0, a, _), (t1, b, _), (t2, c, _) in zip(cam, cam[1:], cam[2:])
                        if t1 - t0 < 6 and t2 - t1 < 6 and (b - a) * (b - c) > 0 and abs(b - a) > salto and abs(b - c) > salto)
            if picos >= 3:
                malos.add(clave)
                h(sis, "amarillo", f"{nombre}: la señal pega saltos",
                  f"{picos} veces ha saltado de golpe y vuelto (más de {salto} {ud} en un par de segundos). Eso no puede pasar "
                  "de verdad: conector flojo o cable rozado. Puede provocar tirones o que el coche haga cosas raras.")

    dur_marcha = (marcha[-1]["t"] - marcha[0]["t"]) if len(marcha) > 1 else 0

    # --- sensores congelados
    def congelado(clave, nombre, cond_otro, texto):
        vals = [f[clave] for f in marcha if f.get(clave) is not None]
        if clave in malos or len(vals) < 100 or dur_marcha < 120:
            return
        if max(vals) - min(vals) <= (1 if clave.startswith("temp") else 0.5) and cond_otro():
            h(SISTEMA_SENSOR[clave], "amarillo", f"{nombre}: parece congelado", texto)

    rango = lambda k: (lambda v: (max(v) - min(v)) if v else 0)([f[k] for f in marcha if f.get(k) is not None])
    congelado("map", "Sensor MAP", lambda: rango("rpm") > 1200,
              "Las revoluciones cambian mucho y la presión de admisión no se mueve. Sin este sensor el coche calcula mal la gasolina.")
    congelado("acelerador", "Sensor de la mariposa", lambda: rango("velocidad") > 30 and rango("map") > 20,
              "El coche acelera y frena pero la mariposa marca siempre lo mismo: potenciómetro de la mariposa o su conector.")
    if dur_marcha > 15 * 60:
        congelado("temp_admision", "Sensor de temperatura del aire", lambda: rango("temp_motor") > 25,
                  "En 15 minutos el motor ha cambiado mucho de temperatura y el aire marca exactamente lo mismo.")
    s1 = [f["sonda1"] for f in marcha if f.get("sonda1") is not None and caliente(f) and lazo(f)]
    if len(s1) > 60 and max(s1) - min(s1) < 0.05:
        h("escape", "rojo", "Sonda lambda delantera muerta",
          f"Con el motor caliente marca siempre {_n(s1[0], 2)} V. Hay que cambiarla: el coche ajusta la mezcla a ciegas.", gasto=True)

    # --- sensor de velocidad
    if marcha and max((f.get("velocidad") or 0) for f in filas) == 0:
        esfuerzo = [f for f in marcha if f["rpm"] > 1800 and (f.get("carga") or 0) > 45 and (f.get("map") or 0) > 55]
        if len(esfuerzo) > 100:
            h("marcha", "amarillo", "El sensor de velocidad marca siempre 0",
              "El motor ha estado trabajando con carga un buen rato como si el coche se moviera. Si el coche se movió, "
              "el sensor de velocidad (en la caja) o su cable fallan; el cuentakilómetros también suele dejar de ir.")

    # --- picado: el avance cae de golpe con el motor estable
    caidas = 0
    avs = [(f["t"], f["avance"]) for f in filas if f.get("avance") is not None]
    def recupera(t1, b):
        return any(t1 < t <= t1 + 4.5 and v > b + 7 for t, v in avs[max(0, _idx.get(t1, 0)):_idx.get(t1, 0) + 12])
    _idx = {t: i for i, (t, _) in enumerate(avs)}
    for f0, f1 in zip(filas, filas[1:]):
        a, b = f0.get("avance"), f1.get("avance")
        if a is None or b is None or not conduciendo(f0) or not conduciendo(f1):
            continue
        ac0, ac1, m0, m1 = f0.get("acelerador"), f1.get("acelerador"), f0.get("map"), f1.get("map")
        if None in (ac0, ac1, m0, m1) or acel_min is None:
            continue
        pedal_quieto = ac0 > acel_min + 4 and abs(ac1 - ac0) <= 3 and m0 >= 35 and abs(m1 - m0) <= 8
        if pedal_quieto and f1["t"] - f0["t"] < 4 and a - b > 7 and abs((f1.get("rpm") or 0) - (f0.get("rpm") or 0)) < 250 \
                and recupera(f1["t"], b):
            caidas += 1
    t_cond = sum(1 for f in filas if conduciendo(f)) / max(1, len(filas)) * (filas[-1]["t"] - filas[0]["t"])
    if caidas >= 6 and t_cond > 120 and caidas / (t_cond / 600) >= 6:
        h("encendido", "amarillo", "Retrasa el encendido de golpe a menudo",
          f"{caidas} veces el avance ha caído de repente sin que cambies el pie. Es lo que hace el coche cuando oye picado: "
          "gasolina de mala calidad, carbonilla, bujías inadecuadas o el sensor de picado/su conector.", gasto=True)
    elif caidas >= 3:
        h("encendido", "gris", "Algún retraso puntual del encendido",
          f"{caidas} veces el coche ha retrasado la chispa un momento con el pedal quieto. Pocas veces es normal "
          "(subiendo cuestas a pocas vueltas). Si notas cascabeleo al subir, baja una marcha.")

    # --- casi se cala
    bajones = 0
    for (t0, a, f0), (t1, b, f1) in zip(*(lambda c: (c, c[1:]))(_cambios([f for f in marcha if parado(f)], "rpm"))):
        if b < 560 and a >= 600:
            bajones += 1
    if bajones >= 2:
        h("encendido", "amarillo", "Casi se cala al ralentí",
          f"{bajones} veces las revoluciones han caído por debajo de 560 rpm estando parado. Típico del motor paso a paso de "
          "ralentí o la mariposa sucios (en el K7M es muy común), o de una toma de aire.")

    # --- ventilador
    seg = t_seguidos = 0
    for a, b in zip(filas, filas[1:]):
        if en_marcha(a) and parado(a) and (a.get("temp_motor") or 0) >= 104:
            t_seguidos += b["t"] - a["t"]
            seg = max(seg, t_seguidos)
        else:
            t_seguidos = 0
    if seg > 120:
        h("refrigeracion", "amarillo", "Parece que el ventilador no salta a tiempo",
          f"Parado al ralentí estuvo {seg / 60:.0f} min por encima de 104 °C sin bajar. Comprueba que el ventilador gira "
          "(relé, fusible, sensor) y el nivel de refrigerante.")

    # --- corrección rápida al tope
    st_par = [(f["stft"], (f.get("ltft") or 0) + f["stft"]) for f in marcha if f.get("stft") is not None and lazo(f)]
    if len(st_par) > 100:
        tope = sum(1 for st_v, tot_v in st_par if abs(st_v) >= 23 and abs(tot_v) >= 20) / len(st_par)
        if tope > 0.1:
            h("gasolina", "amarillo", "La corrección de gasolina va al tope",
              f"El {tope * 100:.0f} % del tiempo la corrección rápida está al máximo: el coche no llega a compensar la mezcla.", gasto=True)

    # --- sensores que el coche dice tener pero no contestan
    if len(filas) > 150:
        claves = {k for f in filas[:5] for k in f if k not in ("t", "consumo_l100", "consumo_lh") and not k.startswith("gps_")}
        mudos = [k for k in claves if all(f.get(k) is None for f in filas)]
        if mudos:
            h("averias", "gris", "Datos que el coche anuncia pero no da",
              "No contestan: " + ", ".join(mudos) + ". Suele ser normal en centralitas antiguas.")


def _consumo(filas, en_marcha, parado, frio, stats, factor_consumo=None):
    litros = km = l_frio = l_ralenti = l_ral_puro = t_ral = t_ral_puro = t_mar = t_frio = 0.0
    for a, b in zip(filas, filas[1:]):
        dt = min(b["t"] - a["t"], 5)
        if dt <= 0:
            continue
        if factor_consumo is not None:
            lh = consumo_lh(a, factor_consumo)
        else:
            lh = a.get("consumo_lh")
            if lh is None:
                lh = consumo_lh(a)
        es_ral_puro = en_marcha(a) and parado(a) and (a.get("rpm") or 0) < 1150
        if en_marcha(a):
            t_mar += dt
            if parado(a):
                t_ral += dt
            if es_ral_puro:
                t_ral_puro += dt
            if frio(a):
                t_frio += dt
        if lh is None:
            continue
        l = lh * dt / 3600
        litros += l
        km += (a.get("velocidad") or 0) * dt / 3600
        if en_marcha(a) and parado(a):
            l_ralenti += l
        if es_ral_puro:
            l_ral_puro += l
        if en_marcha(a) and frio(a):
            l_frio += l
    if t_mar < 30:
        return None
    c = {"litros": round(litros, 2), "km": round(km, 1), "min_motor": round(t_mar / 60, 1),
         "pct_ralenti": round(t_ral / t_mar * 100), "pct_frio": round(t_frio / t_mar * 100),
         "pct_litros_frio": round(l_frio / litros * 100) if litros else 0,
         "pct_litros_ralenti": round(l_ralenti / litros * 100) if litros else 0,
         "l100": round(litros / km * 100, 1) if km >= 1 else None,
         "lh_ralenti": round(l_ral_puro / (t_ral_puro / 3600), 2) if t_ral_puro > 45 else None}
    stats["consumo"] = c
    return c


def _hallazgos_consumo(c, real, hallazgos, h):
    causas = [x for x in hallazgos if x.get("gasto") and x["nivel"] in ("rojo", "amarillo")]
    estilo = []
    if c["pct_litros_frio"] >= 30:
        estilo.append(f"el {c['pct_litros_frio']} % de la gasolina se gastó con el motor frío")
    if c["pct_ralenti"] >= 30:
        estilo.append(f"el {c['pct_ralenti']} % del tiempo estuvo parado al ralentí")
    if c["l100"] is not None:
        m = c["l100"]
        nivel = "verde" if m <= 9 else "amarillo" if m <= 11 else "rojo"
        txt = (f"{_n(m)} L/100 km de media en {_n(c['km'])} km (estimado, ±15 %). En uso real este coche suele "
               "gastar 7,5-9 L/100 km (más en ciudad).")
        if estilo:
            txt += " Ojo: " + " y ".join(estilo) + ", y eso sube la media."
        h("consumo", nivel, "Consumo de esta sesión", txt)
    elif c["lh_ralenti"] is not None:
        m = c["lh_ralenti"]
        if c["pct_frio"] >= 50 and m > 1.2:
            h("consumo", "gris", "Consumo al ralentí en frío",
              f"{_n(m, 2)} L/h parado calentando el motor (en frío sube de vueltas e inyecta más; en caliente baja a 0,7-1,1 L/h).")
        else:
            nivel = "verde" if m <= 1.2 else "amarillo"
            h("consumo", nivel, "Consumo al ralentí",
              f"{_n(m, 2)} L/h parado (estimado). Normal para un 1.6 en caliente: 0,7-1,1 L/h. Para la media en L/100 km hace falta conducir.")
    if c["l100"] is None and c["lh_ralenti"] is None:
        h("consumo", "gris", "Consumo: recogiendo datos",
          "Hace falta algo más de un minuto al ralentí o recorrer al menos 1 km para dar una media.")
    if causas:
        h("consumo", "amarillo", f"{len(causas)} cosa{'s' if len(causas) > 1 else ''} del coche que puede{'n' if len(causas) > 1 else ''} subir el gasto",
          " · ".join(f"{x['titulo']} ({NOMBRE_SISTEMA.get(x['sistema'], '')})" for x in causas))
    elif c["l100"] is not None or c["lh_ralenti"] is not None:
        h("consumo", "verde", "Ningún sensor apunta a un gasto de más",
          "Lo revisado hasta ahora no explica un consumo alto." +
          ("" if c["l100"] is not None else " Falta conducir para revisar la mezcla en marcha."))
    if real and c["l100"] and c["km"] < 30:
        h("consumo", "gris", "Aún pocos km para comparar con tus repostajes",
          f"Llevas {_n(c['km'])} km en esta grabación. Un solo trayecto no es comparable con un depósito entero "
          "(depende mucho de si es ciudad o carretera); con 30 km o más ya se puede.")
    if real and c["l100"] and c["km"] >= 30:
        if real > c["l100"] * 1.3:
            h("consumo", "amarillo", "Gastas más de lo que ve el coche",
              f"Según tus repostajes gastas {_n(real)} L/100 km y el cálculo da {_n(c['l100'])}. "
              + ("Lo que sale arriba puede explicar parte (con el motor frío el cálculo se queda corto). " if causas else "")
              + "Además mira lo que el motor no ve: neumáticos bajos, un freno que roza, fuga de gasolina (olor), mucho "
              "trayecto corto, o que el cálculo necesite ajuste.")
        else:
            h("consumo", "verde", "Tus repostajes cuadran con el cálculo",
              f"Repostajes: {_n(real)} L/100 km; cálculo: {_n(c['l100'])}.")


def _cerrar(hallazgos, stats, consumo):
    orden = {"rojo": 0, "amarillo": 1, "verde": 2, "gris": 3}
    hallazgos.sort(key=lambda x: orden.get(x["nivel"], 9))
    sistemas = {}
    for x in hallazgos:
        actual = sistemas.get(x["sistema"])
        if x["nivel"] == "gris":
            sistemas.setdefault(x["sistema"], "gris")
        elif actual in (None, "gris") or orden[x["nivel"]] < orden[actual]:
            sistemas[x["sistema"]] = x["nivel"]
    return {"hallazgos": hallazgos, "estadisticas": stats, "consumo": consumo, "sistemas": sistemas}

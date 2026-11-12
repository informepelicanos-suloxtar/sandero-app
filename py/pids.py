"""Parámetros OBD-II (modo 01): cómo se decodifican, qué son en castellano llano
y cuándo están bien o mal en un Dacia Sandero 1.6 8V (motor K7M, gasolina).

El K7M no tiene caudalímetro (MAF): calcula el aire con el sensor de presión
del colector (MAP). Tampoco tiene EGR ni distribución variable.
"""


def _u8(d):
    return d[0]


def _u16(d):
    return d[0] * 256 + d[1]


def _pct(d):
    return round(d[0] * 100 / 255, 1)


def _temp(d):
    return d[0] - 40


def _trim(d):
    return round((d[0] - 128) * 100 / 128, 1)


SISTEMA_COMBUSTIBLE = {
    1: ("Lazo abierto: motor frío", "gris"),
    2: ("Lazo cerrado: la sonda lambda está mandando (normal)", "verde"),
    4: ("Lazo abierto: estás soltando gas o pidiendo mucha fuerza", "gris"),
    8: ("Lazo abierto por AVERÍA en el sistema", "rojo"),
    16: ("Lazo cerrado pero con una sonda averiada", "rojo"),
}


def _sistema(d):
    return d[0]


# clave, nombre, unidad, decimales, función, explicación, qué es normal
DEFINICIONES = {
    0x04: ("carga", "Esfuerzo del motor", "%", 0, _pct,
           "Cuánto está trabajando el motor respecto a lo máximo que puede dar en ese momento.",
           "Al ralentí en caliente, entre 20 y 35 %. Sube al acelerar o subir cuestas."),
    0x05: ("temp_motor", "Temperatura del motor", "°C", 0, _temp,
           "Temperatura del líquido refrigerante, medida por un sensor en la culata.",
           "En caliente, entre 85 y 100 °C. El ventilador salta hacia 99-102 °C. "
           "Si tarda muchísimo en pasar de 80 °C, el termostato puede estar abierto."),
    0x06: ("stft", "Corrección rápida de gasolina", "%", 1, _trim,
           "Lo que el ordenador del coche suma (+) o quita (−) de gasolina AHORA MISMO para que la mezcla sea perfecta. "
           "Va bailando todo el rato, es normal.",
           "Entre −10 % y +10 %. Solo cuenta con el motor caliente (lazo cerrado)."),
    0x07: ("ltft", "Corrección aprendida de gasolina", "%", 1, _trim,
           "La corrección que el coche ha ido aprendiendo con el tiempo. Es la que más dice: si está muy alta o "
           "muy baja, algo del motor no está como debería.",
           "Entre −10 % y +10 %. Positiva = le falta gasolina o le entra aire de más (toma de aire, inyectores, "
           "bomba). Negativa = le sobra gasolina."),
    0x0B: ("map", "Presión en la admisión (vacío)", "kPa", 0, _u8,
           "Presión dentro del colector de admisión. Con el motor parado es la presión del aire (~100 kPa). "
           "Al ralentí el motor «chupa» y baja mucho. El K7M usa este sensor para calcular cuánta gasolina echar.",
           "Ralentí en caliente: 25-45 kPa. Si al ralentí está alta, puede haber una toma de aire o el sensor "
           "estar mal."),
    0x0C: ("rpm", "Revoluciones", "rpm", 0, lambda d: round(_u16(d) / 4),
           "Vueltas por minuto que da el motor.",
           "Ralentí en caliente: 700-850 rpm. En frío es normal que esté más alto (900-1300)."),
    0x0D: ("velocidad", "Velocidad", "km/h", 0, _u8,
           "Velocidad del coche según el sensor de la caja.",
           "Debe coincidir con el cuentakilómetros (este suele marcar un poco más)."),
    0x0E: ("avance", "Avance del encendido", "°", 1, lambda d: d[0] / 2 - 64,
           "Cuántos grados antes de que el pistón llegue arriba salta la chispa. El coche lo ajusta solo; "
           "si oye picado (detonación) lo retrasa.",
           "Ralentí: 5-15°. En marcha con poco gas, 25-40°."),
    0x0F: ("temp_admision", "Temperatura del aire que entra", "°C", 0, _temp,
           "Temperatura del aire en la admisión.",
           "Algo por encima de la temperatura de la calle. Parado en caliente puede llegar a 50-60 °C."),
    0x10: ("maf", "Caudal de aire", "g/s", 1, lambda d: _u16(d) / 100,
           "Gramos de aire por segundo que entran al motor.",
           "El K7M normalmente no tiene este sensor."),
    0x11: ("acelerador", "Mariposa de gas", "%", 0, _pct,
           "Lo abierta que está la mariposa del acelerador según su sensor. Con el pie fuera no marca 0: "
           "el sensor tiene un mínimo.",
           "Pie fuera: un valor fijo bajo (suele rondar 10-20 %). Debe subir suave al pisar, sin saltos."),
    0x14: ("sonda1", "Sonda lambda delantera", "V", 2, lambda d: d[0] / 200,
           "Mide el oxígeno de los gases de escape ANTES del catalizador. Es la que usa el coche para "
           "ajustar la mezcla.",
           "Con el motor caliente debe oscilar todo el rato entre ~0,1 V y ~0,9 V. Si se queda quieta, mal."),
    0x15: ("sonda2", "Sonda lambda trasera", "V", 2, lambda d: d[0] / 200,
           "Mide el oxígeno DESPUÉS del catalizador. Sirve para vigilar que el catalizador funciona.",
           "Bastante estable, entre 0,5 y 0,8 V. Si oscila igual que la delantera, el catalizador está gastado."),
    0x03: ("sistema_combustible", "Modo de la mezcla", "", 0, _sistema,
           "Si el coche está corrigiendo la gasolina con la sonda lambda (lazo cerrado) o no (lazo abierto).",
           "En caliente y a velocidad constante o ralentí: lazo cerrado."),
    0x1F: ("tiempo_motor", "Tiempo con el motor encendido", "s", 0, _u16,
           "Segundos desde que arrancaste esta vez.", ""),
    0x21: ("km_con_testigo", "Km con el testigo de avería encendido", "km", 0, _u16,
           "Kilómetros recorridos con el testigo naranja del motor encendido.", "Lo ideal: 0."),
    0x2F: ("combustible", "Nivel de gasolina", "%", 0, _pct,
           "Nivel del depósito.", ""),
    0x30: ("calentamientos_borrado", "Arranques en caliente desde el último borrado", "", 0, _u8,
           "Cuántas veces se ha calentado el motor desde que se borraron las averías.",
           "Tras borrar averías el coche necesita varios calentamientos y trayectos para volver a revisarse."),
    0x31: ("km_desde_borrado", "Km desde que se borraron las averías", "km", 0, _u16,
           "Kilómetros recorridos desde que alguien borró los códigos de avería.", ""),
    0x33: ("presion_atm", "Presión atmosférica", "kPa", 0, _u8,
           "Presión del aire de la calle.", "Unos 100 kPa a nivel del mar; menos en altura."),
    0x42: ("voltaje_ecu", "Tensión en la centralita", "V", 2, lambda d: _u16(d) / 1000,
           "Voltaje que le llega al ordenador del motor.", "Motor en marcha: 13,5-14,8 V."),
    0x43: ("carga_abs", "Carga absoluta", "%", 0, lambda d: round(_u16(d) * 100 / 255, 1), "", ""),
    0x44: ("lambda_pedida", "Mezcla pedida (lambda)", "", 3, lambda d: round(_u16(d) * 2 / 65536, 3),
           "La mezcla que el coche quiere. 1,000 es la mezcla perfecta.", "Casi siempre 1,000."),
    0x45: ("acelerador_rel", "Mariposa (relativa)", "%", 0, _pct, "", ""),
    0x46: ("temp_ambiente", "Temperatura de la calle", "°C", 0, _temp, "", ""),
    0x4D: ("min_con_testigo", "Minutos con el testigo encendido", "min", 0, _u16, "", ""),
    0x4E: ("min_desde_borrado", "Minutos desde el último borrado", "min", 0, _u16, "", ""),
}

# Pseudo-parámetro leído del propio lector (comando ATRV)
BATERIA = ("bateria", "Batería", "V", 1,
           "Voltaje de la batería, medido por el lector en el enchufe OBD.",
           "Motor parado: 12,4-12,8 V. Motor en marcha: 13,8-14,6 V (el alternador está cargando).")

# Qué se lee en cada vuelta. El K7M (KWP2000) es lento: ~5-8 lecturas por segundo.
# La sonda delantera (0x14) va 3 veces por vuelta: oscila ~1 vez por segundo y si no se lee a menudo no se ve.
RAPIDOS = [0x0C, 0x14, 0x0B, 0x0D, 0x14, 0x06, 0x11, 0x14, 0x0E, 0x04]
LENTOS = [0x05, 0x0F, 0x07, 0x15, 0x03, 0x42, 0x2F, 0x46, 0x44, 0x45, 0x10, 0x33]
MUY_LENTOS = [0x1F, 0x21, 0x30, 0x31, 0x4D, 0x4E]


# Calculados (no los da el coche): ver diagnostico.consumo_lh
DERIVADOS = {
    "consumo_lh": ("Gasto ahora (por hora)", "L/h", 1,
                   "Litros por hora que está gastando ahora, calculado con el aire que entra y las correcciones de mezcla "
                   "(tu motor no tiene caudalímetro ni da el dato directamente). Margen ±15 %.",
                   "Al ralentí en caliente: 0,7-1,1 L/h. Soltando gas en marcha: 0 (corte de inyección)."),
    "consumo_l100": ("Gasto ahora (por distancia)", "L/100 km", 1,
                     "Lo que gastaría en 100 km si fueras siempre así. Varía muchísimo segundo a segundo: lo que importa es "
                     "la media de la sesión, arriba en «Consumo».",
                     "A velocidad constante en llano: 5-7 L/100 km. Acelerando sube mucho y soltando gas baja a 0."),
    "gps_velocidad": ("Velocidad GPS (móvil)", "km/h", 0,
                      "La que mide el GPS del iPhone con la página /gps abierta. Sirve para calibrar la velocidad del coche.",
                      "A velocidad estable debe parecerse a «Velocidad» (±2 %)."),
    "gps_lat": ("Latitud GPS (móvil)", "°", 6, "Posición del coche según el GPS del móvil, para ver el trayecto.", ""),
    "gps_lon": ("Longitud GPS (móvil)", "°", 6, "Posición del coche según el GPS del móvil, para ver el trayecto.", ""),
    "gps_alt": ("Altura GPS (móvil)", "m", 0,
                "Altura sobre el mar según el GPS del móvil. Sirve para saber si vas subiendo o bajando una cuesta.",
                "El GPS se equivoca ±10 m en altura: vale para cuestas largas, no para baches."),
    "gps_precision": ("Precisión GPS (móvil)", "m", 0, "Margen de error de la posición del GPS.", "Menos de 15 m es buena señal."),
}


def lista_definiciones():
    """Para la web: nombre, unidad y explicaciones de cada clave."""
    out = {}
    for pid, (clave, nombre, unidad, dec, _f, expl, normal) in DEFINICIONES.items():
        out[clave] = {"pid": f"{pid:02X}", "nombre": nombre, "unidad": unidad, "decimales": dec,
                      "explicacion": expl, "normal": normal}
    clave, nombre, unidad, dec, expl, normal = BATERIA
    out[clave] = {"pid": "ATRV", "nombre": nombre, "unidad": unidad, "decimales": dec,
                  "explicacion": expl, "normal": normal}
    for clave, (nombre, unidad, dec, expl, normal) in DERIVADOS.items():
        out[clave] = {"pid": "calculado", "nombre": nombre, "unidad": unidad, "decimales": dec,
                      "explicacion": expl, "normal": normal}
    return out


def decodificar(pid, datos):
    d = DEFINICIONES.get(pid)
    if not d or not datos:
        return None
    try:
        return d[4](datos)
    except (IndexError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------- semáforo

def _cruces(serie, umbral=0.45):
    n = 0
    for a, b in zip(serie, serie[1:]):
        if (a - umbral) * (b - umbral) < 0:
            n += 1
    return n


def evaluar(clave, v, ctx, historial):
    """Devuelve (estado, mensaje). estado: verde, amarillo, rojo, gris.

    ctx: últimos valores de todo. historial: dict clave -> lista de (t, valor) de los últimos ~30 s.
    """
    rpm = ctx.get("rpm")
    temp = ctx.get("temp_motor")
    vel = ctx.get("velocidad") or 0
    en_marcha = rpm is not None and rpm > 400
    caliente = temp is not None and temp >= 80
    ralenti = en_marcha and vel == 0 and rpm < 1200
    lazo_cerrado = ctx.get("sistema_combustible") in (2, 16)

    if v is None:
        if clave == "consumo_l100":
            return "gris", "Parado: mira el gasto por hora"
        return "gris", "Sin dato"

    if clave == "consumo_lh":
        if not en_marcha:
            return "gris", "Motor parado"
        if v == 0:
            return "verde", "Soltando gas: no gasta nada (corte de inyección)"
        if ralenti and caliente:
            if v > 1.3:
                return "amarillo", "Parado gasta más de lo normal (0,7-1,1 L/h)"
            return "verde", "Gasto normal al ralentí"
        if ralenti:
            return "gris", "En frío gasta más al ralentí: es normal"
        return "gris", "Estimado"

    if clave == "consumo_l100":
        return "gris", "Instantáneo: mira la media de la sesión"

    if clave == "rpm":
        if not en_marcha:
            return "gris", "Motor parado (con el contacto puesto marca 256)" if v == 256 else "Motor parado"
        if ralenti and not caliente:
            return "verde", "Ralentí en frío: es normal que vaya algo alto"
        if ralenti and caliente:
            ahora = max((t for t, _ in historial.get("rpm", [])), default=0)
            hist = [x for t, x in historial.get("rpm", []) if t > ahora - 15]
            parado_15s = all(x == 0 for t, x in historial.get("velocidad", []) if t > ahora - 20)
            acel = [x for t, x in historial.get("acelerador", []) if t > ahora - 15]
            sin_pisar = bool(acel) and max(acel) - min(acel) < 3
            if len(hist) > 10 and parado_15s and sin_pisar and max(hist) - min(hist) > 200:
                return "amarillo", "El ralentí sube y baja bastante (¿motor de ralentí o mariposa sucios?)"
            if v < 620:
                return "amarillo", "Ralentí bajo"
            if v > 1000:
                return "amarillo", "Ralentí alto en caliente: posible toma de aire o motor de ralentí sucio"
            if v > 870:
                return "amarillo", "Ralentí algo alto"
            return "verde", "Ralentí correcto"
        return "verde", "Normal"

    if clave == "temp_motor":
        if v < 70:
            return "gris", "Calentando…"
        if v < 80:
            return "gris", "Casi caliente"
        if v <= 104:
            return "verde", "Temperatura correcta"
        if v <= 110:
            return "amarillo", "Algo caliente: comprueba que salta el ventilador"
        return "rojo", "¡Demasiado caliente! Para el coche y deja que se enfríe"

    if clave == "temp_admision":
        if v > 75:
            return "amarillo", "Aire muy caliente (normal si llevas rato parado con el motor en marcha)"
        return "verde", "Normal"

    if clave == "map":
        if not en_marcha:
            if 85 <= v <= 106:
                return "verde", "Motor parado: marca la presión de la calle (correcto)"
            return "amarillo", "Con el motor parado debería marcar ~100 kPa: el sensor puede estar mal"
        if ralenti and caliente:
            if v <= 45:
                return "verde", "Buen vacío al ralentí"
            if v <= 55:
                return "amarillo", "Vacío algo flojo al ralentí"
            return "rojo", "Poco vacío al ralentí: posible toma de aire o sensor MAP"
        return "verde", "Normal"

    if clave in ("stft", "ltft"):
        if clave == "stft" and not lazo_cerrado:
            return "gris", "Ahora mismo el coche no corrige (motor frío o soltando gas)"
        a = abs(v)
        lado = "le falta gasolina / le entra aire de más" if v > 0 else "le sobra gasolina"
        if a <= 10:
            return "verde", "Mezcla bien"
        if a <= 20:
            return "amarillo", f"Corrigiendo bastante: {lado}"
        return "rojo", f"Corrige muchísimo: {lado}"

    if clave == "sonda1":
        if not en_marcha:
            return "gris", "Motor parado"
        if not lazo_cerrado:
            return "gris", "Aún no está en uso (motor frío o soltando gas)"
        serie = [x for _, x in historial.get("sonda1", [])[-40:]]
        if len(serie) < 12:
            return "gris", "Observando…"
        if _cruces(serie) >= 2:
            return "verde", "Oscila: funciona"
        if max(serie) < 0.45:
            return "amarillo", "Se queda baja (mezcla pobre o sonda perezosa)"
        if min(serie) > 0.45:
            return "amarillo", "Se queda alta (mezcla rica o sonda perezosa)"
        return "amarillo", "Oscila poco"

    if clave == "sonda2":
        if not en_marcha or not caliente:
            return "gris", "Esperando a que el motor y el catalizador estén calientes"
        serie2 = [x for _, x in historial.get("sonda2", [])[-30:]]
        serie1 = [x for _, x in historial.get("sonda1", [])[-60:]]
        if len(serie2) < 8:
            return "gris", "Observando…"
        c1, c2 = _cruces(serie1), _cruces(serie2)
        amplitud = max(serie2) - min(serie2)
        if c2 >= 3 and amplitud > 0.4 and c1 > 0 and c2 / len(serie2) >= 0.6 * c1 / max(len(serie1), 1):
            return "amarillo", "Oscila casi como la delantera: el catalizador puede estar gastado"
        if v < 0.3 and lazo_cerrado and not ralenti:
            return "amarillo", "Muy baja tras el catalizador (posible fuga en el escape o mezcla pobre)"
        return "verde", "Estable: el catalizador trabaja"

    if clave == "bateria":
        if en_marcha:
            if v > 15.0:
                return "rojo", "Demasiado voltaje: regulador del alternador"
            if v >= 13.5:
                return "verde", "El alternador carga bien"
            if v >= 13.0:
                return "amarillo", "Carga algo justa"
            return "rojo", "El alternador no está cargando bien"
        if v >= 12.4:
            return "verde", "Batería bien"
        if v >= 12.0:
            return "amarillo", "Batería algo baja"
        return "rojo", "Batería baja"

    if clave == "sistema_combustible":
        return SISTEMA_COMBUSTIBLE.get(v, ("Desconocido", "gris"))[1], \
            SISTEMA_COMBUSTIBLE.get(v, ("Estado desconocido", "gris"))[0]

    if clave == "avance":
        if ralenti and caliente and v < -2:
            return "amarillo", "Avance muy retrasado al ralentí"
        return "verde", "Normal"

    if clave == "km_con_testigo":
        return ("verde", "Sin km con el testigo") if v == 0 else ("amarillo", "Se ha circulado con el testigo encendido")

    return "verde", ""


def texto_valor(clave, v):
    if clave == "sistema_combustible":
        return SISTEMA_COMBUSTIBLE.get(v, (f"Código {v}", ""))[0].split(":")[0]
    return None

"""Códigos de avería (DTC) en castellano llano.

gravedad: 1 = leve (revisa cuando puedas), 2 = revisar pronto (puede gastar más,
suspender la ITV o ir a peor), 3 = grave (no hagas muchos km así).
"""

CODIGOS = {
    # Sondas lambda: calefactores
    "P0030": ("Calefactor de la sonda lambda delantera: fallo en el circuito", 2, "Revisar conector y cables de la sonda delantera; si están bien, la sonda."),
    "P0031": ("Calefactor de la sonda lambda delantera: señal baja", 2, "Suele ser la propia sonda (resistencia interna cortada) o su fusible/cable."),
    "P0032": ("Calefactor de la sonda lambda delantera: señal alta", 2, "Cable en corto o sonda averiada."),
    "P0036": ("Calefactor de la sonda lambda trasera: fallo en el circuito", 1, "Revisar conector y la sonda trasera."),
    "P0037": ("Calefactor de la sonda lambda trasera: señal baja", 1, "Suele ser la propia sonda trasera."),
    "P0038": ("Calefactor de la sonda lambda trasera: señal alta", 1, "Cable en corto o sonda trasera averiada."),
    # MAP / aire
    "P0100": ("Caudalímetro de aire: fallo en el circuito", 2, "Revisar conector del caudalímetro."),
    "P0105": ("Sensor de presión de admisión (MAP): fallo en el circuito", 2, "Revisar conector y tubo de vacío del sensor MAP (en el colector)."),
    "P0106": ("Sensor de presión de admisión (MAP): valores raros", 2, "Toma de aire, tubo de vacío roto o sensor MAP sucio/averiado."),
    "P0107": ("Sensor de presión de admisión (MAP): señal baja", 2, "Conector suelto, cable cortado o sensor MAP averiado."),
    "P0108": ("Sensor de presión de admisión (MAP): señal alta", 2, "Cable en corto o sensor MAP averiado. También puede salir con tomas de aire grandes."),
    "P0110": ("Sensor de temperatura del aire: fallo en el circuito", 1, "Revisar conector del sensor (en el colector o junto al MAP)."),
    "P0112": ("Sensor de temperatura del aire: señal baja", 1, "Cable en corto o sensor averiado."),
    "P0113": ("Sensor de temperatura del aire: señal alta", 1, "Conector suelto o sensor averiado."),
    # Temperatura del motor
    "P0115": ("Sensor de temperatura del motor: fallo en el circuito", 2, "Revisar conector del sensor de temperatura. Con este fallo el ventilador puede ir siempre."),
    "P0116": ("Sensor de temperatura del motor: valores raros", 2, "Sensor que miente o termostato. Compara con la aguja del cuadro."),
    "P0117": ("Sensor de temperatura del motor: señal baja", 2, "Cable en corto o sensor averiado."),
    "P0118": ("Sensor de temperatura del motor: señal alta", 2, "Conector suelto o sensor averiado (el coche cree que está frío)."),
    "P0125": ("El motor tarda demasiado en calentarse para controlar bien la mezcla", 1, "Termostato abierto o sensor de temperatura."),
    "P0128": ("Termostato: el motor no llega a su temperatura", 1, "Casi siempre es el termostato, que se queda abierto. Gasta más gasolina."),
    # Mariposa
    "P0120": ("Sensor de la mariposa de gas: fallo en el circuito", 2, "Revisar conector del potenciómetro de la mariposa."),
    "P0121": ("Sensor de la mariposa de gas: valores raros", 2, "Potenciómetro gastado o mariposa sucia."),
    "P0122": ("Sensor de la mariposa de gas: señal baja", 2, "Conector o potenciómetro de la mariposa."),
    "P0123": ("Sensor de la mariposa de gas: señal alta", 2, "Conector o potenciómetro de la mariposa."),
    "P2101": ("Motor de la mariposa: fallo de control", 3, "Mariposa motorizada sucia o averiada."),
    "P2135": ("Sensores de la mariposa no coinciden entre sí", 3, "Conector o cuerpo de mariposa."),
    # Sondas lambda: señal
    "P0130": ("Sonda lambda delantera: fallo en el circuito", 2, "Sonda delantera o su cableado."),
    "P0131": ("Sonda lambda delantera: tensión baja", 2, "Sonda, cable o una toma de aire/escape antes de la sonda."),
    "P0132": ("Sonda lambda delantera: tensión alta", 2, "Sonda en corto o mezcla muy rica."),
    "P0133": ("Sonda lambda delantera: responde lenta", 2, "Sonda vieja/perezosa. Es pieza de desgaste (cada ~100.000-150.000 km)."),
    "P0134": ("Sonda lambda delantera: no da señal", 2, "Sonda muerta o cable cortado."),
    "P0135": ("Calefactor de la sonda lambda delantera", 2, "Normalmente hay que cambiar la sonda delantera."),
    "P0136": ("Sonda lambda trasera: fallo en el circuito", 1, "Sonda trasera o su cableado."),
    "P0137": ("Sonda lambda trasera: tensión baja", 1, "Sonda trasera, fuga en el escape o catalizador."),
    "P0138": ("Sonda lambda trasera: tensión alta", 1, "Sonda trasera en corto."),
    "P0139": ("Sonda lambda trasera: responde lenta", 1, "Sonda trasera vieja."),
    "P0140": ("Sonda lambda trasera: no da señal", 1, "Sonda trasera muerta o cable cortado."),
    "P0141": ("Calefactor de la sonda lambda trasera", 1, "Normalmente hay que cambiar la sonda trasera."),
    # Mezcla
    "P0170": ("Mezcla de combustible desajustada", 2, "Ver corrección de gasolina en directo para saber si es pobre o rica."),
    "P0171": ("Mezcla demasiado pobre (le falta gasolina o le sobra aire)", 2, "Lo típico: toma de aire en tubos de vacío o junta del colector, sensor MAP, inyectores sucios o poca presión de gasolina."),
    "P0172": ("Mezcla demasiado rica (le sobra gasolina)", 2, "Inyector que gotea, sensor MAP o de temperatura mintiendo, sonda lambda."),
    "P2096": ("Tras el catalizador la mezcla sale pobre", 1, "Fuga en el escape, sonda trasera o mezcla."),
    "P2097": ("Tras el catalizador la mezcla sale rica", 1, "Sonda trasera, catalizador o mezcla."),
    "P2177": ("Mezcla pobre fuera del ralentí", 2, "Inyectores, presión de gasolina o sensor MAP."),
    "P2178": ("Mezcla rica fuera del ralentí", 2, "Inyectores o sensores."),
    "P2187": ("Mezcla pobre al ralentí", 2, "Casi siempre una toma de aire."),
    "P2188": ("Mezcla rica al ralentí", 2, "Inyector que gotea o canister."),
    "P2195": ("Sonda lambda delantera atascada en pobre", 2, "Sonda o toma de aire."),
    "P2196": ("Sonda lambda delantera atascada en rica", 2, "Sonda o mezcla rica."),
    "P2270": ("Sonda lambda trasera atascada en pobre", 1, "Fuga en el escape o sonda trasera."),
    "P2271": ("Sonda lambda trasera atascada en rica", 1, "Sonda trasera."),
    # Inyectores / bomba
    "P0200": ("Inyectores: fallo en el circuito", 3, "Revisar cableado común de inyectores y relé."),
    "P0201": ("Inyector del cilindro 1: fallo en el circuito", 3, "Conector o inyector 1."),
    "P0202": ("Inyector del cilindro 2: fallo en el circuito", 3, "Conector o inyector 2."),
    "P0203": ("Inyector del cilindro 3: fallo en el circuito", 3, "Conector o inyector 3."),
    "P0204": ("Inyector del cilindro 4: fallo en el circuito", 3, "Conector o inyector 4."),
    "P0230": ("Bomba de gasolina: fallo en el circuito", 3, "Relé o bomba de gasolina. Puede dejarte tirado."),
    "P0627": ("Bomba de gasolina: fallo en el circuito de control", 3, "Relé o cableado de la bomba."),
    # Encendido
    "P0300": ("Fallos de encendido en varios cilindros", 3, "Bujías, bobina o cables. No circules mucho: daña el catalizador."),
    "P0301": ("Fallos de encendido en el cilindro 1", 3, "Bujía, bobina o inyector del cilindro 1. Daña el catalizador si sigue."),
    "P0302": ("Fallos de encendido en el cilindro 2", 3, "Bujía, bobina o inyector del cilindro 2."),
    "P0303": ("Fallos de encendido en el cilindro 3", 3, "Bujía, bobina o inyector del cilindro 3."),
    "P0304": ("Fallos de encendido en el cilindro 4", 3, "Bujía, bobina o inyector del cilindro 4."),
    "P0325": ("Sensor de picado (detonación): fallo en el circuito", 2, "Conector o sensor de picado. El coche retrasa el encendido y pierde fuerza."),
    "P0326": ("Sensor de picado: valores raros", 2, "Sensor de picado o gasolina de mala calidad."),
    "P0327": ("Sensor de picado: señal baja", 2, "Sensor flojo, conector o sensor averiado."),
    "P0328": ("Sensor de picado: señal alta", 2, "Cable en corto o sensor."),
    "P0335": ("Sensor de régimen del cigüeñal (PMS): fallo", 3, "Sin este sensor el motor no arranca o se para. Revisar conector y sensor (junto al volante motor)."),
    "P0336": ("Sensor de régimen del cigüeñal: señal rara", 3, "Sensor sucio, holgura o corona dentada dañada."),
    "P0340": ("Sensor del árbol de levas: fallo", 2, "Conector o sensor."),
    "P0350": ("Bobina de encendido: fallo en el circuito", 3, "Bobina o su cableado."),
    "P0351": ("Bobina de encendido A (cilindros 1-4): fallo", 3, "Bobina doble, cable o conector."),
    "P0352": ("Bobina de encendido B (cilindros 2-3): fallo", 3, "Bobina doble, cable o conector."),
    # Emisiones
    "P0420": ("El catalizador no limpia lo suficiente", 2, "Catalizador gastado o sonda trasera. Antes de cambiarlo, descarta fugas en el escape y sondas."),
    "P0440": ("Sistema de vapores de gasolina (canister): fallo", 1, "Tapón del depósito, tubos o electroválvula del canister."),
    "P0441": ("Canister: caudal de purga incorrecto", 1, "Electroválvula del canister o tubos."),
    "P0443": ("Electroválvula del canister: fallo en el circuito", 1, "Conector o electroválvula del canister."),
    "P0444": ("Electroválvula del canister: circuito abierto", 1, "Conector o electroválvula del canister."),
    "P0445": ("Electroválvula del canister: cortocircuito", 1, "Electroválvula del canister."),
    # Ventiladores / velocidad / ralentí
    "P0480": ("Ventilador del radiador (velocidad 1): fallo", 3, "Relé, fusible o motor del ventilador. Vigila la temperatura."),
    "P0481": ("Ventilador del radiador (velocidad 2): fallo", 2, "Relé, resistencia o motor del ventilador."),
    "P0500": ("Sensor de velocidad del coche: fallo", 2, "Sensor en la caja o su cable. Puede fallar el cuentakilómetros."),
    "P0501": ("Sensor de velocidad del coche: señal rara", 2, "Sensor de velocidad."),
    "P0505": ("Control del ralentí: fallo", 2, "En el K7M es muy típico: motor paso a paso de ralentí sucio o averiado. Limpiar mariposa y motor de ralentí."),
    "P0506": ("Ralentí más bajo de lo esperado", 1, "Motor de ralentí o mariposa sucios."),
    "P0507": ("Ralentí más alto de lo esperado", 1, "Toma de aire o motor de ralentí sucio."),
    "P0508": ("Motor de ralentí: señal baja", 2, "Conector o motor paso a paso de ralentí."),
    "P0509": ("Motor de ralentí: señal alta", 2, "Conector o motor paso a paso de ralentí."),
    "P0511": ("Motor de ralentí: fallo en el circuito", 2, "Conector o motor paso a paso de ralentí."),
    "P0530": ("Sensor de presión del aire acondicionado: fallo", 1, "Sensor o falta de gas del aire acondicionado."),
    "P0532": ("Sensor de presión del aire acondicionado: señal baja", 1, "Falta de gas o sensor."),
    "P0533": ("Sensor de presión del aire acondicionado: señal alta", 1, "Sensor o exceso de presión."),
    # Electricidad / centralita
    "P0560": ("Tensión de la batería: valores raros", 2, "Batería, alternador o bornes sueltos."),
    "P0562": ("Tensión de la batería baja", 2, "Batería gastada o alternador que no carga."),
    "P0563": ("Tensión de la batería alta", 2, "Regulador del alternador."),
    "P0601": ("Centralita del motor: error de memoria", 3, "Fallo interno de la centralita."),
    "P0602": ("Centralita del motor: error de programación", 3, "Centralita."),
    "P0604": ("Centralita del motor: error de memoria RAM", 3, "Centralita."),
    "P0605": ("Centralita del motor: error de memoria ROM", 3, "Centralita."),
    "P0606": ("Centralita del motor: fallo del procesador", 3, "Centralita o alimentación de la centralita."),
    "P0645": ("Relé del compresor del aire acondicionado: fallo", 1, "Relé del compresor."),
    "P0650": ("Testigo de avería del cuadro: fallo en el circuito", 1, "Bombilla/led o cableado del testigo."),
    "P0685": ("Relé principal de la centralita: fallo", 3, "Relé principal o su cableado."),
    "P0700": ("Caja de cambios automática: avería", 2, "Leer la centralita de la caja."),
    # Comunicaciones
    "U0001": ("Red de comunicaciones del coche: fallo", 2, "Cableado o una centralita."),
    "U0100": ("Sin comunicación con la centralita del motor", 3, "Cableado, alimentación o centralita."),
}

RANGOS = [
    ("P00", "Control auxiliar de aire y gasolina"),
    ("P01", "Sensores de aire y gasolina (MAP, temperaturas, sondas lambda, mezcla)"),
    ("P02", "Inyectores y circuito de gasolina"),
    ("P03", "Encendido y fallos de encendido"),
    ("P04", "Control de emisiones (catalizador, canister)"),
    ("P05", "Velocidad, ralentí y entradas auxiliares"),
    ("P06", "Centralita del motor y sus salidas"),
    ("P07", "Caja de cambios"),
    ("P08", "Caja de cambios"),
    ("P09", "Caja de cambios"),
    ("P1", "Código propio de Renault/Dacia"),
    ("P2", "Código genérico adicional de motor"),
    ("P3", "Código propio del fabricante o genérico adicional"),
    ("B", "Carrocería (airbag, cierre, luces…)"),
    ("C", "Chasis (ABS, frenos, dirección…)"),
    ("U", "Comunicaciones entre centralitas"),
]

GRAVEDAD_TEXTO = {1: "Leve", 2: "Revisar pronto", 3: "Grave"}


def describir(codigo):
    if codigo in CODIGOS:
        desc, grav, consejo = CODIGOS[codigo]
        return {"codigo": codigo, "descripcion": desc, "gravedad": grav,
                "gravedad_texto": GRAVEDAD_TEXTO[grav], "consejo": consejo, "conocido": True}
    grupo = next((t for pref, t in RANGOS if codigo.startswith(pref)), "Desconocido")
    return {"codigo": codigo, "descripcion": f"Código no incluido en la lista. Grupo: {grupo}",
            "gravedad": 2, "gravedad_texto": "Por mirar", "consejo": "Pásaselo a Claude para buscarlo.",
            "conocido": False}


def decodificar(b1, b2):
    letra = "PCBU"[b1 >> 6]
    return f"{letra}{(b1 >> 4) & 3}{b1 & 0x0F:X}{b2:02X}"


MONITORES_B = [(0, "Fallos de encendido"), (1, "Sistema de gasolina"), (2, "Componentes")]
MONITORES_C = [(0, "Catalizador"), (1, "Catalizador calefactado"), (2, "Vapores de gasolina (canister)"),
               (3, "Aire secundario"), (4, "Gas del aire acondicionado"), (5, "Sondas lambda"),
               (6, "Calefactor de las sondas"), (7, "EGR")]


def decodificar_monitores(d):
    """PID 01 01. Devuelve testigo, nº de averías y lista de autodiagnósticos."""
    a, b, c, dd = d[:4]
    mon = []
    for bit, nombre in MONITORES_B:
        if b & (1 << bit):
            mon.append({"nombre": nombre, "completo": not (b & (1 << (bit + 4)))})
    diesel = bool(b & 0x08)
    if not diesel:
        for bit, nombre in MONITORES_C:
            if c & (1 << bit):
                mon.append({"nombre": nombre, "completo": not (dd & (1 << bit))})
    return {"testigo": bool(a & 0x80), "num_averias": a & 0x7F, "monitores": mon}

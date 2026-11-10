"""Revisión de una grabación guardada (usa el mismo diagnóstico que la web en directo).

Uso: python3 analisis.py registros/<carpeta>   (sin carpeta: la última)
Escribe resumen.json y resumen.txt dentro de la carpeta.
"""
import csv
import json
import sys
from pathlib import Path

import diagnostico

BASE = Path(__file__).parent
REGISTROS = BASE / "registros"


def ajustes():
    try:
        return json.loads((BASE / "ajustes.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def cargar(carpeta):
    sesion = json.loads((carpeta / "sesion.json").read_text())
    es_sim = "Simulador" in ((sesion.get("lector") or {}).get("destino") or "")
    corr_actual = 1.0 if es_sim else (ajustes().get("correccion_velocidad") or 1.0)
    # corrección con la que se grabó cada tramo (puede cambiarse a mitad, p. ej. desde la página /gps)
    tramos = [(0.0, sesion.get("correccion_velocidad") or 1.0)]
    tramos += sorted((c["t"], c["valor"]) for c in sesion.get("cambios_correccion_velocidad", []))
    filas = []
    with open(carpeta / "datos.csv", newline="") as f:
        for r in csv.DictReader(f):
            fila = {}
            for k, v in r.items():
                if k == "hora":
                    continue
                try:
                    fila[k] = float(v) if v not in ("", None) else None
                except ValueError:
                    fila[k] = None
            # tensiones imposibles (fallo antiguo de la app en el iPhone, 08/11/2026): no son del coche
            if fila.get("bateria") is not None and not 3 <= fila["bateria"] <= 20:
                fila["bateria"] = None
            t = fila.get("t") or 0.0
            corr_grabada = [c for desde, c in tramos if desde <= t][-1]
            escala_vel = corr_actual / corr_grabada
            if fila.get("velocidad") and abs(escala_vel - 1.0) > 1e-4:
                fila["velocidad"] = round(fila["velocidad"] * escala_vel)
                if fila.get("consumo_lh") is not None and fila["velocidad"] >= 5:
                    fila["consumo_l100"] = round(fila["consumo_lh"] / fila["velocidad"] * 100, 1)
            filas.append(fila)
    return filas, sesion


def analizar(carpeta):
    carpeta = Path(carpeta)
    filas, sesion = cargar(carpeta)
    aj = ajustes()
    es_sim = "Simulador" in ((sesion.get("lector") or {}).get("destino") or "")
    corr_vel = 1.0 if es_sim else (aj.get("correccion_velocidad") or sesion.get("correccion_velocidad") or 1.0)
    d = diagnostico.diagnosticar(filas, sesion.get("averias"), aj.get("consumo_real"), aj.get("factor_consumo"),
                                 corr_velocidad=corr_vel)
    resumen = {"carpeta": carpeta.name, "prueba": sesion.get("prueba"), "inicio": sesion.get("inicio"),
               "filas": len(filas), "notas": sesion.get("notas", []), **d}
    (carpeta / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2))
    icono = {"rojo": "[!!]", "amarillo": "[! ]", "verde": "[ok]", "gris": "[--]"}
    lineas = [f"Revisión: {carpeta.name}  ({(sesion.get('prueba') or {}).get('nombre', 'libre')})", ""]
    for s in [{"id": "averias"}] + diagnostico.SISTEMAS:
        del_sis = [x for x in d["hallazgos"] if x["sistema"] == s["id"]]
        if del_sis:
            lineas.append(f"== {diagnostico.NOMBRE_SISTEMA[s['id']]}")
            lineas += [f"{icono[x['nivel']]} {x['titulo']}: {x['detalle']}" for x in del_sis]
            lineas.append("")
    lineas += ["Estadísticas:", json.dumps(d["estadisticas"], ensure_ascii=False, indent=2)]
    if sesion.get("notas"):
        lineas += ["", "Marcas «Ha pasado algo»:"] + [f"  {n['t']:.0f}s ({n['hora']}) {n['texto']}" for n in sesion["notas"]]
    (carpeta / "resumen.txt").write_text("\n".join(lineas))
    return resumen


if __name__ == "__main__":
    if len(sys.argv) > 1:
        c = Path(sys.argv[1])
    else:
        c = sorted(p for p in REGISTROS.iterdir() if (p / "datos.csv").exists())[-1]
    analizar(c)
    print((c / "resumen.txt").read_text())

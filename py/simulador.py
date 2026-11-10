"""Coche falso (Sandero K7M en frío) que contesta como un ELM327 por KWP2000.

Sirve para probar la web sin estar en el coche: python3 servidor.py --demo
"""
import math
import os
import random
import time

SOPORTADOS = {0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11,
              0x13, 0x14, 0x15, 0x1C, 0x1F, 0x20, 0x21, 0x30, 0x31, 0x33}


def _mascara(base):
    m = 0
    for i in range(32):
        if base + i + 1 in SOPORTADOS:
            m |= 1 << (31 - i)
    return f"{m:08X}"


class Coche:
    def __init__(self):
        self.t0 = time.time()
        self.acelerar = float(os.environ.get("OBD_DEMO_X", "1"))  # 1 s real = acelerar s de motor (para que caliente rápido en la demo)

    def estado(self):
        t = (time.time() - self.t0) * self.acelerar
        temp = min(90, 18 + t * 0.5)  # calienta en ~2,5 min
        caliente = temp >= 80
        # A partir de 3 min: conduce un rato y vuelve al ralentí
        ciclo = (t - 180) % 120 if t > 180 else -1
        vel = 0
        if 0 <= ciclo < 70:
            rampa = min(1.0, ciclo / 10.0, (70.0 - ciclo) / 10.0)
            vel = rampa * (50 + 30 * math.sin(ciclo / 70 * math.pi))
        idle = 1150 - (temp - 18) * 5 if temp < 80 else 790
        rpm = idle + random.uniform(-25, 25) if vel == 0 else 1600 + vel * 25 + random.uniform(-40, 40)
        lazo = 2 if temp > 45 and not (vel and random.random() < 0.05) else 1
        if lazo == 2:
            sonda1 = 0.48 + 0.4 * math.sin(time.time() * 4.5)
        else:
            sonda1 = 0.1
        return {
            "temp": temp, "rpm": rpm, "vel": vel, "lazo": lazo, "sonda1": max(0.05, sonda1),
            "sonda2": 0.68 + random.uniform(-0.04, 0.04) if caliente else 0.2,
            "map": 33 + random.uniform(-2, 2) if vel == 0 else 55 + vel * 0.3,
            "stft": random.uniform(-6, 6) if lazo == 2 else 0,
            "ltft": 7.8, "carga": 26 if vel == 0 else 45 + vel * 0.2,
            "avance": 10 + random.uniform(-2, 2) if vel == 0 else 30,
            "acel": 12.2 if vel == 0 else 22 + vel * 0.2,
            "iat": 24 + temp * 0.15, "t": t,
            "bat": 14.1 + random.uniform(-0.1, 0.1),
        }


class TransporteSimulado:
    nombre = "Simulador (coche de prueba)"

    def __init__(self):
        self.coche = Coche()
        self.buffer = b""
        self.listo = 0

    def _resp(self, cmd):
        cmd = cmd.strip().upper()
        e = self.coche.estado()
        if cmd == "ATZ":
            return "ELM327 v1.5"
        if cmd == "ATRV":
            return f"{e['bat']:.1f}V"
        if cmd == "ATDPN":
            return "A5"
        if cmd == "ATDP":
            return "AUTO, ISO 14230-4 (KWP FAST)"
        if cmd.startswith("AT"):
            return "OK"
        if cmd == "03":
            return "43 00 00 00 00 00 00"
        if cmd == "07":
            return "43 05 05 00 00 00 00".replace("43", "47", 1)
        if cmd in ("0A", "04"):
            return "NO DATA" if cmd == "0A" else "44"
        if cmd == "0902":
            return "NO DATA"
        if cmd.startswith("02"):
            return "NO DATA"
        if not cmd.startswith("01") or len(cmd) != 4:
            return "?"
        pid = int(cmd[2:], 16)
        if pid in (0x00, 0x20, 0x40):
            return f"41 {pid:02X} {_mascara(pid)}" if pid in (0x00, 0x20) else "NO DATA"
        if pid not in SOPORTADOS:
            return "NO DATA"
        b = lambda x: f"{max(0, min(255, int(round(x)))):02X}"
        w = lambda x: f"{max(0, min(65535, int(round(x)))):04X}"
        valores = {
            0x01: "00076501",
            0x03: b(e["lazo"]) + "00",
            0x04: b(e["carga"] * 255 / 100),
            0x05: b(e["temp"] + 40),
            0x06: b(e["stft"] * 128 / 100 + 128),
            0x07: b(e["ltft"] * 128 / 100 + 128),
            0x0B: b(e["map"]),
            0x0C: w(e["rpm"] * 4),
            0x0D: b(e["vel"]),
            0x0E: b((e["avance"] + 64) * 2),
            0x0F: b(e["iat"] + 40),
            0x11: b(e["acel"] * 255 / 100),
            0x13: "03",
            0x14: b(e["sonda1"] * 200) + "FF",
            0x15: b(e["sonda2"] * 200) + "FF",
            0x1C: "06",
            0x1F: w(e["t"]),
            0x21: w(0),
            0x30: b(3),
            0x31: w(42),
            0x33: b(101),
        }
        return f"41{pid:02X}" + valores[pid]

    def escribir(self, datos):
        cmd = datos.decode().strip()
        r = self._resp(cmd)
        if cmd.upper() == "0100" and time.time() - self.coche.t0 < 5:
            r = "SEARCHING...\r" + r
        self.buffer = (r + "\r\r>").encode()
        self.listo = time.time() + random.uniform(0.04, 0.09)  # el KWP es lento

    def leer(self):
        if self.buffer and time.time() >= self.listo:
            out, self.buffer = self.buffer, b""
            return out
        time.sleep(0.005)
        return b""

    def cerrar(self):
        pass

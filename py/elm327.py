"""Conexión con el lector ELM327 (clónico chino incluido) y lectura OBD-II.

Transportes:
  - Serie: Bluetooth clásico emparejado en el Mac (/dev/cu.OBDII…) o en Linux (/dev/rfcomm0).
  - RFCOMM directo (solo Linux): por la dirección MAC, sin emparejar con rfcomm bind.
  - BLE: lectores Bluetooth Low Energy (necesita el paquete «bleak»).
  - Simulador: un coche falso para probar la web en casa.
"""
import glob
import re
import socket
import sys
import threading
import time

import averias
import pids


class ErrorLector(Exception):
    pass


# ------------------------------------------------------------------ transportes

class TransporteSerie:
    def __init__(self, puerto, baudios=38400):
        import serial  # pyserial
        self.s = serial.Serial(puerto, baudios, timeout=0.05)
        self.nombre = puerto

    def escribir(self, datos):
        self.s.write(datos)

    def leer(self):
        n = self.s.in_waiting
        return self.s.read(n or 1)

    def cerrar(self):
        self.s.close()


class TransporteRFCOMM:
    def __init__(self, mac, canal=1):
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self.s.settimeout(10)
        self.s.connect((mac, canal))
        self.s.settimeout(0.05)
        self.nombre = f"RFCOMM {mac}"

    def escribir(self, datos):
        self.s.sendall(datos)

    def leer(self):
        try:
            return self.s.recv(512)
        except socket.timeout:
            return b""

    def cerrar(self):
        self.s.close()


NOMBRES_BLE = re.compile(r"obd|elm|v-?link|vgate|konnwei|ios|android|car|scan|ble", re.I)


class TransporteBLE:
    """Lectores Bluetooth LE. La conexión va en un proceso aparte (ble_puente.py): si se cuelga, se mata y
    macOS la suelta del todo (si no, el lector se queda «enganchado» al Mac y deja de anunciarse)."""

    def __init__(self, direccion=None):
        import subprocess
        import sys as _sys
        from pathlib import Path
        puente = Path(__file__).with_name("ble_puente.py")
        self.p = subprocess.Popen([_sys.executable, str(puente)] + ([direccion] if direccion else []),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.estado = None
        self.vivo = True
        listo = threading.Event()

        def leer_err():
            for linea in iter(self.p.stderr.readline, b""):
                texto = linea.decode("utf-8", "ignore").strip()
                if texto.startswith(("OK ", "ERROR ")):
                    self.estado = texto
                    listo.set()
            self.vivo = False
            listo.set()

        def leer_out():
            while True:
                datos = self.p.stdout.read1(512) if hasattr(self.p.stdout, "read1") else self.p.stdout.read(1)
                if not datos:
                    break
                with self.lock:
                    self.buffer.extend(datos)
            self.vivo = False

        threading.Thread(target=leer_err, daemon=True).start()
        threading.Thread(target=leer_out, daemon=True).start()
        listo.wait(timeout=45)
        if not self.estado or not self.estado.startswith("OK "):
            self.cerrar()
            raise ErrorLector(self.estado[6:] if self.estado else "El lector Bluetooth LE no contesta.")
        self.nombre = f"BLE {self.estado[3:]}"

    def escribir(self, datos):
        if not self.vivo or self.p.poll() is not None:
            raise ErrorLector("Se perdió la conexión Bluetooth con el lector")
        try:
            self.p.stdin.write(datos)
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ErrorLector("Se perdió la conexión Bluetooth con el lector")

    def leer(self):
        time.sleep(0.01)
        with self.lock:
            out = bytes(self.buffer)
            self.buffer.clear()
        if not out and not self.vivo:
            raise ErrorLector("Se perdió la conexión Bluetooth con el lector")
        return out

    def cerrar(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=6)
        except Exception:
            pass
        if self.p.poll() is None:
            self.p.kill()


def puertos_candidatos():
    """Puertos serie que pueden ser el lector."""
    fuera = re.compile(r"Bluetooth-Incoming-Port|debug-console|wlan|usbmodem.*Garmin", re.I)
    if sys.platform == "darwin":
        lista = glob.glob("/dev/cu.*")
    else:
        lista = glob.glob("/dev/rfcomm*") + glob.glob("/dev/ttyUSB*")
    return [p for p in sorted(lista) if not fuera.search(p)]


def abrir_transporte(destino):
    """destino: 'demo', 'ble', 'ble:AA:BB…', una MAC (Linux RFCOMM) o un puerto serie."""
    if destino == "demo":
        from simulador import TransporteSimulado
        return TransporteSimulado()
    if destino == "ble" or destino.startswith("ble:"):
        try:
            return TransporteBLE(destino[4:] or None)
        except ImportError:
            raise ErrorLector("Para lectores Bluetooth LE falta instalar «bleak» (pip install bleak).")
    if re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", destino):
        return TransporteRFCOMM(destino)
    return TransporteSerie(destino)


# ------------------------------------------------------------------ protocolo ELM327

HEX = re.compile(r"^[0-9A-F]+$")
ERRORES = ("UNABLE TO CONNECT", "BUS INIT: ...ERROR", "BUS ERROR", "CAN ERROR", "ERROR")


class ELM327:
    def __init__(self, transporte, registro=None):
        self.t = transporte
        self.registro = registro  # función para guardar el tráfico (depuración)
        self.es_can = False
        self.soportados = set()
        self.info = {}

    # --- bajo nivel
    def comando(self, cmd, timeout=2.0):
        self.t.leer()  # vaciar restos
        self.t.escribir((cmd + "\r").encode())
        datos = bytearray()
        fin = time.time() + timeout
        while time.time() < fin:
            trozo = self.t.leer()
            if trozo:
                datos.extend(trozo)
                if b">" in trozo:
                    break
        texto = datos.decode("ascii", "ignore").replace(">", "")
        if self.registro:
            self.registro(cmd, texto)
        lineas = [l.strip() for l in re.split(r"[\r\n]+", texto) if l.strip()]
        return [l for l in lineas if l.upper() != cmd.upper()]  # quitar eco si ATE0 no funcionó

    def _hex(self, lineas):
        out = []
        for l in lineas:
            l = l.upper().replace(" ", "")
            l = re.sub(r"SEARCHING\.*|BUSINIT:?\.*(OK)?", "", l).lstrip(".")  # algunos clones pegan «..» delante
            if not l:
                continue
            l = re.sub(r"^[0-9A-F]:", "", l)  # trama múltiple CAN «0:…»
            if HEX.match(l):
                out.append(l)
        return out

    # --- arranque
    def iniciar(self):
        r = self.comando("ATZ", timeout=4)
        if not r:
            r = self.comando("ATI", timeout=3)
        if not r:
            raise ErrorLector("El lector no contesta (ni siquiera a los comandos básicos). Puede estar ocupado con "
                              "otro aparato (cierra la app del móvil o apaga su Bluetooth) o hay que elegir el otro "
                              "modo de conexión: prueba con «Bluetooth LE (buscar)».")
        version = next((l for l in r if "ELM" in l.upper()), " ".join(r))
        for c in ("ATE0", "ATL0", "ATS0", "ATH0", "ATAT1"):
            self.comando(c)
        self.comando("ATSP0")
        self.info["version"] = version or "desconocida"
        r = self.comando("0100", timeout=15)  # el KWP2000 tarda unos segundos en despertar
        texto = " ".join(r).upper()
        if any(e in texto for e in ERRORES) or "NO DATA" in texto or not self._hex(r):
            raise ErrorLector("El lector responde pero el coche no. Pon el contacto (o arranca el motor) y vuelve a probar.")
        proto_n = "".join(self.comando("ATDPN")).upper().lstrip("A")
        self.es_can = proto_n in ("6", "7", "8", "9")
        self.info["protocolo"] = " ".join(self.comando("ATDP"))
        self.soportados = self._leer_soportados(r)
        self.info["pids_soportados"] = [f"{p:02X}" for p in sorted(self.soportados)]
        return self.info

    def _leer_soportados(self, primera):
        soportados = set()
        base, resp = 0x00, primera
        while True:
            datos = self._datos_modo01(resp, base)
            if not datos or len(datos) < 4:
                break
            mascara = int.from_bytes(bytes(datos[:4]), "big")
            for i in range(32):
                if mascara & (1 << (31 - i)):
                    soportados.add(base + i + 1)
            siguiente = base + 0x20
            if siguiente not in soportados or siguiente > 0xC0:
                break
            base = siguiente
            resp = self.comando(f"01{base:02X}", timeout=3)
        return soportados

    def _datos_modo01(self, lineas, pid, modo="41"):
        cab = f"{modo}{pid:02X}"
        for l in self._hex(lineas):
            if l.startswith(cab):
                return list(bytes.fromhex(l[len(cab):][: (len(l) - len(cab)) // 2 * 2]))
        return None

    # --- lecturas
    def leer_pid(self, pid):
        return self._datos_modo01(self.comando(f"01{pid:02X}"), pid)

    def leer(self, pid):
        return pids.decodificar(pid, self.leer_pid(pid))

    def bateria(self):
        r = " ".join(self.comando("ATRV"))
        m = re.search(r"(\d+(?:\.\d+)?)\s*V", r)
        return float(m.group(1)) if m else None

    def averias(self, modo="03"):
        """modo 03 = guardadas, 07 = pendientes, 0A = permanentes."""
        lineas = self._hex(self.comando(modo, timeout=4))
        resp = f"{int(modo, 16) + 0x40:02X}"
        codigos = []
        if self.es_can:
            datos = "".join(l for l in lineas if len(l) > 3)
            i = datos.find(resp)
            while i != -1 and i + 4 <= len(datos):
                n = int(datos[i + 2:i + 4], 16)
                trozo = datos[i + 4:i + 4 + n * 4]
                for j in range(0, len(trozo) - 3, 4):
                    codigos.append(trozo[j:j + 4])
                i = datos.find(resp, i + 4 + n * 4)
        else:
            for l in lineas:
                if l.startswith(resp):
                    cuerpo = l[2:]
                    for j in range(0, len(cuerpo) - 3, 4):
                        codigos.append(cuerpo[j:j + 4])
        out = []
        for c in codigos:
            if c != "0000":
                cod = averias.decodificar(int(c[:2], 16), int(c[2:], 16))
                if cod not in [x["codigo"] for x in out]:
                    out.append(averias.describir(cod))
        return out

    def borrar_averias(self):
        r = " ".join(self.comando("04", timeout=5)).upper().replace(" ", "")
        return "44" in r

    def monitores(self):
        d = self.leer_pid(0x01)
        return averias.decodificar_monitores(d) if d and len(d) >= 4 else None

    def foto_averia(self):
        """Freeze frame: datos guardados en el instante en que saltó la avería."""
        d = self._datos_modo01(self.comando("020200"), 0x02, modo="42")
        if not d or len(d) < 3 or (d[1] == 0 and d[2] == 0):
            return None
        foto = {"averia": averias.decodificar(d[1], d[2])}
        for pid in (0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x03):
            datos = self._datos_modo01(self.comando(f"02{pid:02X}00"), pid, modo="42")
            if datos and len(datos) > 1:
                v = pids.decodificar(pid, datos[1:])
                if v is not None:
                    foto[pids.DEFINICIONES[pid][0]] = v
        return foto

    def vin(self):
        lineas = self._hex(self.comando("0902", timeout=4))
        datos = "".join(re.sub(r"^4902\d\d", "", l) for l in lineas if l.startswith("4902") or len(l) > 6)
        try:
            texto = bytes.fromhex(datos[: len(datos) // 2 * 2]).decode("ascii", "ignore")
        except ValueError:
            return None
        vin = re.sub(r"[^A-HJ-NPR-Z0-9]", "", texto)[-17:]
        return vin if len(vin) == 17 else None

    def cerrar(self):
        try:
            self.t.cerrar()
        except Exception:
            pass

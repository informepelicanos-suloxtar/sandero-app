/* App del móvil: hace dentro del navegador lo que en el Mac hace servidor.py.
 *
 * - Habla con el lector OBD por Bluetooth LE (Web Bluetooth: Chrome en Android, Bluefy en el iPhone).
 * - Sube cada grabación terminada al repositorio privado de GitHub y lee de ahí los mensajes de Claude.
 * - Protocolo ELM327: copia en JavaScript de elm327.py (mismos comandos y mismas comprobaciones).
 * - Todo lo demás (semáforo, grabación, diagnóstico, revisión) es el código Python del Mac, que corre aquí con
 *   Pyodide (ver movil_py.py). Las grabaciones se guardan en el propio móvil con la misma estructura que en el Mac.
 *
 * La web (index.html) es la misma que la del Mac: si existe window.MOTOR, le pide los datos a esto en vez de al servidor.
 */
(() => {
"use strict";
const VERSION = "f8b4a59f41";
const PY = ["pids.py", "averias.py", "diagnostico.py", "analisis.py", "rutas.py", "deposito.py", "servidor.py", "elm327.py", "simulador.py", "movil_py.py"];
const espera = ms => new Promise(r => setTimeout(r, ms));
const h2 = n => n.toString(16).toUpperCase().padStart(2, "0");
const bytesDeHex = s => { const out = []; for (let i = 0; i + 1 < s.length; i += 2) out.push(parseInt(s.slice(i, i + 2), 16)); return out; };
function conTiempo(promesa, ms, texto) {
  let t; return Promise.race([promesa, new Promise((_, no) => { t = setTimeout(() => no(new Error(texto)), ms); })]).finally(() => clearTimeout(t));
}
class ErrorLector extends Error {}

let py = null, M = null;

// ------------------------------------------------------------------ pantalla de arranque

function velo(texto, error) {
  let v = document.getElementById("veloMovil");
  if (!v) {
    v = document.createElement("div"); v.id = "veloMovil";
    v.style.cssText = "position:fixed;inset:0;z-index:99;display:flex;align-items:center;justify-content:center;padding:24px;"
      + "background:var(--bg,#0f1720);color:var(--txt,#e8edf2);font:17px/1.5 system-ui,sans-serif;text-align:center";
    (document.body || document.documentElement).appendChild(v);
  }
  v.innerHTML = `<div style="max-width:420px">${error ? "⚠️" : "🚗"}<br>${texto}</div>`;
}
function quitarVelo() { const v = document.getElementById("veloMovil"); if (v) v.remove(); }

// ------------------------------------------------------------------ arranque: Python y archivos guardados

function cargarScript(src) {
  return new Promise((si, no) => { const s = document.createElement("script"); s.src = src; s.onload = si; s.onerror = () => no(new Error("No se pudo cargar " + src)); document.head.appendChild(s); });
}
const existe = ruta => { try { py.FS.stat(ruta); return true; } catch { return false; } };

async function arrancar() {
  if (document.readyState === "loading") await new Promise(r => document.addEventListener("DOMContentLoaded", r));
  velo("Preparando la app…<br><small>La primera vez tarda un poco más.</small>");
  let indexURL = new URL("pyodide/", location.href).href;
  try {
    await cargarScript("pyodide/pyodide.js");
  } catch {
    indexURL = "https://cdn.jsdelivr.net/pyodide/v0.27.4/full/";
    await cargarScript(indexURL + "pyodide.js");
  }
  py = await loadPyodide({ indexURL });
  py.FS.mkdirTree("/coche");
  py.FS.mount(py.FS.filesystems.IDBFS, {}, "/coche");
  await new Promise((si, no) => py.FS.syncfs(true, e => e ? no(e) : si()));
  for (const f of PY) py.FS.writeFile("/coche/" + f, await (await fetch("py/" + f)).text());
  for (const f of ["mantenimiento.json", "ajustes.json", "deposito.json"])  // la primera vez: copia vacía (los de verdad, del repositorio)
    if (!existe("/coche/" + f)) py.FS.writeFile("/coche/" + f, await (await fetch("py/" + f)).text());
  py.runPython("import sys\nif '/coche' not in sys.path: sys.path.insert(0, '/coche')");
  M = py.pyimport("movil_py");
  const recuperadas = JSON.parse(M.recuperar());
  await guardar();
  setInterval(guardar, 10000);
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
  diagnosticoEnDirecto();
  setTimeout(subirPendientes, 5000);
  setInterval(subirPendientes, 120000);
  setTimeout(() => sincronizarMantenimiento(true), 7000);
  setTimeout(() => sincronizarDeposito(), 9000);
  window.addEventListener("online", () => setTimeout(() => { subirPendientes(); sincronizarMantenimiento(true); sincronizarRutas(); sincronizarDeposito(); }, 3000));
  setInterval(reconectarSolo, 15000);
  setTimeout(reconectarSolo, 1500);
  ajustarTextos();
  quitarVelo();
  if (!navigator.bluetooth) setTimeout(() => tostadaMovil(/iPhone|iPad|iPod/.test(navigator.userAgent)
    ? "Safari no tiene Bluetooth: abre esta misma dirección con la app Bluefy." : "Este navegador no tiene Bluetooth: abre la app con Chrome en Android."), 800);
  else if (recuperadas.length) setTimeout(() => tostadaMovil("Se cerró a medias una grabación: ya está guardada y revisada."), 800);
  else if (otroCoche()) setTimeout(() => tostadaMovil("La última vez hiciste un chequeo a otro coche: pulsa Conectar para volver al Sandero."), 800);
}

let guardando = null, otraVez = false;
async function guardar() {  // del Python (memoria) al almacenamiento del móvil
  if (!py) return;
  if (guardando) { otraVez = true; return guardando; }
  try { M.volcar(); } catch (e) { console.error(e); }
  guardando = new Promise(r => py.FS.syncfs(false, e => { if (e) console.error("syncfs", e); r(); }));
  await guardando; guardando = null;
  if (otraVez) { otraVez = false; return guardar(); }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") guardar();
  else if (Lec.elm) mantenerPantalla();
});
window.addEventListener("pagehide", () => guardar());

function tostadaMovil(t) { if (typeof window.tostada === "function") window.tostada(t); }

// ------------------------------------------------------------------ transporte Bluetooth LE

const U = c => `0000${c}-0000-1000-8000-00805f9b34fb`;
const CANALES = [  // servicio, avisa (notify), escribe — los mismos que ble_puente.py
  [U("fff0"), U("fff1"), U("fff2")],
  [U("ffe0"), U("ffe1"), U("ffe1")],
  [U("18f0"), U("2af0"), U("2af1")],
  ["e7810a71-73ae-499d-8c15-faa9aef0c3f2", "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f", "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"],
];
const NOMBRES = /obd|elm|v-?link|vgate|konnwei|ios|android|car|scan|ble/i;
let dispositivo = null;

async function elegirLector(todos) {
  const opciones = { optionalServices: CANALES.map(c => c[0]) };
  if (todos) opciones.acceptAllDevices = true;
  else opciones.filters = [...["OBD", "obd", "Obd", "ELM", "elm", "V-LINK", "VLINK", "Vgate", "vgate", "KONNWEI", "IOS", "Android", "Car", "Scan"]
    .map(namePrefix => ({ namePrefix })), ...CANALES.map(c => ({ services: [c[0]] }))];
  try {
    dispositivo = await navigator.bluetooth.requestDevice(opciones);
  } catch (e) {
    if (e.name === "NotFoundError") throw new ErrorLector(todos ? "No has elegido ningún aparato." :
      "No has elegido ningún lector. Si no salía en la lista, elige «Buscar cualquier aparato Bluetooth».");
    if (e.name === "SecurityError") throw new ErrorLector("Para elegir el lector hay que pulsar Conectar (Chrome no deja hacerlo solo).");
    throw new ErrorLector("No se pudo buscar por Bluetooth: " + e.message + ". ¿Está el Bluetooth y la ubicación del móvil encendidos?");
  }
  return dispositivo;
}

async function lectorRecordado() {  // el que se eligió otro día (si Chrome lo permite)
  if (dispositivo) return dispositivo;
  if (!navigator.bluetooth || !navigator.bluetooth.getDevices) return null;
  try {
    const ds = await navigator.bluetooth.getDevices();
    dispositivo = ds.find(d => NOMBRES.test(d.name || "")) || ds[0] || null;
  } catch { dispositivo = null; }
  return dispositivo;
}

class TransporteBLE {
  constructor(dev) { this.dev = dev; this.buf = ""; this.vivo = false; this.nombre = `BLE ${dev.name || dev.id}`; }

  async abrir() {
    this._fuera = () => { this.vivo = false; };
    this.dev.addEventListener("gattserverdisconnected", this._fuera);
    let srv;
    try {
      srv = await conTiempo(this.dev.gatt.connect(), 15000, "tarda");
    } catch (e) {
      // un lector recordado de otro día puede necesitar que Chrome lo «vea» antes de conectar
      if (this.dev.watchAdvertisements) {
        try {
          await this.dev.watchAdvertisements();
          await conTiempo(new Promise(r => this.dev.addEventListener("advertisementreceived", r, { once: true })), 10000, "no se anuncia");
          srv = await conTiempo(this.dev.gatt.connect(), 15000, "tarda");
        } catch {}
      }
      if (!srv) throw new ErrorLector("No encuentro el lector por Bluetooth. Si el contacto está puesto, desenchúfalo y vuelve a "
        + "enchufarlo, espera 5 segundos y pulsa Conectar.");
    }
    let notif = null, escr = null;
    for (const [s, n, e] of CANALES) {
      try {
        const serv = await srv.getPrimaryService(s);
        notif = await serv.getCharacteristic(n);
        escr = n === e ? notif : await serv.getCharacteristic(e);
        break;
      } catch { notif = escr = null; }
    }
    if (!notif) {  // canales con otro nombre: el primero que avise y el primero que deje escribir
      for (const serv of await srv.getPrimaryServices().catch(() => [])) {
        const chs = await serv.getCharacteristics().catch(() => []);
        const n = chs.find(c => c.properties.notify || c.properties.indicate);
        const e = chs.find(c => c.properties.write || c.properties.writeWithoutResponse);
        if (n && e) { notif = n; escr = e; break; }
      }
    }
    if (!notif) { this.cerrar(); throw new ErrorLector("El lector Bluetooth no tiene los canales esperados. ¿Seguro que es el lector OBD?"); }
    this.notif = notif; this.escr = escr;
    this._llega = ev => { const v = ev.target.value; this.buf += String.fromCharCode(...new Uint8Array(v.buffer, v.byteOffset, v.byteLength)); };
    notif.addEventListener("characteristicvaluechanged", this._llega);
    await notif.startNotifications();
    this.conRespuesta = !!(escr.properties && escr.properties.write);
    this.vivo = true;
  }

  async escribir(texto) {
    if (!this.vivo) throw new ErrorLector("Se perdió la conexión Bluetooth con el lector");
    const datos = new TextEncoder().encode(texto);
    for (let i = 0; i < datos.length; i += 20) {
      const trozo = datos.slice(i, i + 20);
      try {
        const ch = this.escr;  // Bluefy (iPhone) puede tener solo writeValue
        const p = this.conRespuesta && ch.writeValueWithResponse ? ch.writeValueWithResponse(trozo)
          : !this.conRespuesta && ch.writeValueWithoutResponse ? ch.writeValueWithoutResponse(trozo) : ch.writeValue(trozo);
        await conTiempo(p, 5000, "escribir");
      } catch { this.vivo = false; throw new ErrorLector("Se perdió la conexión Bluetooth con el lector"); }
    }
  }

  leer() {
    const out = this.buf; this.buf = "";
    if (!out && !this.vivo) throw new ErrorLector("Se perdió la conexión Bluetooth con el lector");
    return out;
  }

  cerrar() {
    this.vivo = false;
    try { this.notif && this.notif.removeEventListener("characteristicvaluechanged", this._llega); } catch {}
    try { this.dev.removeEventListener("gattserverdisconnected", this._fuera); } catch {}
    try { if (this.dev.gatt.connected) this.dev.gatt.disconnect(); } catch {}
  }
}

// ------------------------------------------------------------------ coche de prueba (copia de simulador.py)

const SOPORTADOS_DEMO = new Set([0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11,
  0x13, 0x14, 0x15, 0x1C, 0x1F, 0x20, 0x21, 0x30, 0x31, 0x33]);
const azar = (a, b) => a + Math.random() * (b - a);

class TransporteSimulado {
  constructor() {
    this.nombre = "Simulador (coche de prueba)";
    this.t0 = Date.now(); this.buf = ""; this.listo = 0;
    this.acelerar = Number(new URLSearchParams(location.search).get("x")) || 1;
  }
  async abrir() {}
  estado() {
    const t = (Date.now() - this.t0) / 1000 * this.acelerar;
    const temp = Math.min(90, 18 + t * 0.5), caliente = temp >= 80;
    const ciclo = t > 180 ? (t - 180) % 120 : -1;
    const rampa = ciclo >= 0 && ciclo < 70 ? Math.min(1, ciclo / 10, (70 - ciclo) / 10) : 0;
    const vel = rampa ? rampa * (50 + 30 * Math.sin(ciclo / 70 * Math.PI)) : 0;
    const idle = temp < 80 ? 1150 - (temp - 18) * 5 : 790;
    let rpm = vel === 0 ? idle + azar(-25, 25) : 1600 + vel * 25 + azar(-40, 40);
    if (this.rpmFijo) rpm = this.rpmFijo + azar(-40, 40);  // chequeo de prueba: «pisa» a 2.500 rpm
    const lazo = temp > 45 && !(vel && Math.random() < 0.05) ? 2 : 1;
    const sonda1 = lazo === 2 ? 0.48 + 0.4 * Math.sin(Date.now() / 1000 * 4.5) : 0.1;
    return { temp, rpm, vel, lazo, sonda1: Math.max(0.05, sonda1),
      sonda2: caliente ? 0.68 + azar(-0.04, 0.04) : 0.2,
      map: this.rpmFijo ? 22 + azar(-2, 2) : vel === 0 ? 33 + azar(-2, 2) : 55 + vel * 0.3,
      stft: lazo === 2 ? azar(-6, 6) : 0, ltft: 7.8, carga: vel === 0 ? 26 : 45 + vel * 0.2,
      avance: vel === 0 ? 10 + azar(-2, 2) : 30, acel: vel === 0 ? 12.2 : 22 + vel * 0.2,
      iat: 24 + temp * 0.15, t, bat: 14.1 + azar(-0.1, 0.1) };
  }
  respuesta(cmd) {
    cmd = cmd.trim().toUpperCase();
    const e = this.estado();
    if (cmd === "ATZ") return "ELM327 v1.5";
    if (cmd === "ATRV") return `${e.bat.toFixed(1)}V`;
    if (cmd === "ATDPN") return "A5";
    if (cmd === "ATDP") return "AUTO, ISO 14230-4 (KWP FAST)";
    if (cmd.startsWith("AT")) return "OK";
    if (cmd === "03") return "43 00 00 00 00 00 00";
    if (cmd === "07") return "47 05 05 00 00 00 00";
    if (cmd === "0A") return "NO DATA";
    if (cmd === "04") return "44";
    if (cmd === "0902" || cmd.startsWith("02")) return "NO DATA";
    if (!cmd.startsWith("01") || cmd.length !== 4) return "?";
    const pid = parseInt(cmd.slice(2), 16);
    if (pid === 0x00 || pid === 0x20) {
      let m = 0; for (let i = 0; i < 32; i++) if (SOPORTADOS_DEMO.has(pid + i + 1)) m |= 1 << (31 - i);
      return `41 ${h2(pid)} ${(m >>> 0).toString(16).toUpperCase().padStart(8, "0")}`;
    }
    if (!SOPORTADOS_DEMO.has(pid)) return "NO DATA";
    const b = x => h2(Math.max(0, Math.min(255, Math.round(x))));
    const w = x => Math.max(0, Math.min(65535, Math.round(x))).toString(16).toUpperCase().padStart(4, "0");
    const v = { 0x01: "00076501", 0x03: b(e.lazo) + "00", 0x04: b(e.carga * 255 / 100), 0x05: b(e.temp + 40),
      0x06: b(e.stft * 128 / 100 + 128), 0x07: b(e.ltft * 128 / 100 + 128), 0x0B: b(e.map), 0x0C: w(e.rpm * 4),
      0x0D: b(e.vel), 0x0E: b((e.avance + 64) * 2), 0x0F: b(e.iat + 40), 0x11: b(e.acel * 255 / 100), 0x13: "03",
      0x14: b(e.sonda1 * 200) + "FF", 0x15: b(e.sonda2 * 200) + "FF", 0x1C: "06", 0x1F: w(e.t), 0x21: w(0),
      0x30: b(3), 0x31: w(42), 0x33: b(101) };
    return `41${h2(pid)}` + v[pid];
  }
  async escribir(texto) {
    let r = this.respuesta(texto);
    if (texto.trim().toUpperCase() === "0100" && Date.now() - this.t0 < 5000) r = "SEARCHING...\r" + r;
    this.buf = r + "\r\r>"; this.listo = Date.now() + azar(40, 90);  // el KWP es lento
  }
  leer() { if (this.buf && Date.now() >= this.listo) { const o = this.buf; this.buf = ""; return o; } return ""; }
  cerrar() {}
}

// ------------------------------------------------------------------ protocolo ELM327 (copia de elm327.py)

const HEX = /^[0-9A-F]+$/;
const ERRORES = ["UNABLE TO CONNECT", "BUS INIT: ...ERROR", "BUS ERROR", "CAN ERROR", "ERROR"];

class ELM327 {
  constructor(t) { this.t = t; this.es_can = false; this.soportados = new Set(); this.info = {}; }

  async comando(cmd, timeout = 2000) {
    this.t.leer();  // vaciar restos
    await this.t.escribir(cmd + "\r");
    let datos = "";
    const fin = Date.now() + timeout;
    while (Date.now() < fin) {
      const trozo = this.t.leer();
      if (trozo) { datos += trozo; if (trozo.includes(">")) break; }
      else await espera(10);
    }
    const lineas = datos.replaceAll(">", "").split(/[\r\n]+/).map(l => l.trim()).filter(Boolean);
    return lineas.filter(l => l.toUpperCase() !== cmd.toUpperCase());  // quitar eco si ATE0 no funcionó
  }

  hex(lineas) {
    const out = [];
    for (let l of lineas) {
      l = l.toUpperCase().replaceAll(" ", "");
      l = l.replace(/SEARCHING\.*|BUSINIT:?\.*(OK)?/g, "").replace(/^\.+/, "");  // algunos clones pegan «..» delante
      if (!l) continue;
      l = l.replace(/^[0-9A-F]:/, "");  // trama múltiple CAN «0:…»
      if (HEX.test(l)) out.push(l);
    }
    return out;
  }

  async iniciar() {
    let r = await this.comando("ATZ", 4000);
    if (!r.length) r = await this.comando("ATI", 3000);
    if (!r.length) throw new ErrorLector("El lector no contesta (ni siquiera a los comandos básicos). Puede estar ocupado con otro "
      + "aparato: cierra las otras apps del coche del móvil y apaga el Bluetooth del Mac. Luego desenchufa y enchufa el lector.");
    const version = r.find(l => l.toUpperCase().includes("ELM")) || r.join(" ");
    for (const c of ["ATE0", "ATL0", "ATS0", "ATH0", "ATAT1"]) await this.comando(c);
    await this.comando("ATSP0");
    this.info.version = version || "desconocida";
    r = await this.comando("0100", 15000);  // el KWP2000 tarda unos segundos en despertar
    const texto = r.join(" ").toUpperCase();
    if (ERRORES.some(e => texto.includes(e)) || texto.includes("NO DATA") || !this.hex(r).length)
      throw new ErrorLector("El lector responde pero el coche no. Pon el contacto (o arranca el motor) y vuelve a probar.");
    const proto = (await this.comando("ATDPN")).join("").toUpperCase().replace(/^A+/, "");
    this.es_can = ["6", "7", "8", "9"].includes(proto);
    this.info.protocolo = (await this.comando("ATDP")).join(" ");
    this.soportados = await this.leerSoportados(r);
    this.info.pids_soportados = [...this.soportados].sort((a, b) => a - b).map(h2);
    return this.info;
  }

  async leerSoportados(primera) {
    const s = new Set();
    let base = 0x00, resp = primera;
    while (true) {
      const d = this.datosModo01(resp, base);
      if (!d || d.length < 4) break;
      const mascara = ((d[0] << 24) | (d[1] << 16) | (d[2] << 8) | d[3]) >>> 0;
      for (let i = 0; i < 32; i++) if (mascara & (2 ** (31 - i))) s.add(base + i + 1);
      const sig = base + 0x20;
      if (!s.has(sig) || sig > 0xC0) break;
      base = sig;
      resp = await this.comando(`01${h2(base)}`, 3000);
    }
    return s;
  }

  datosModo01(lineas, pid, modo = "41") {
    const cab = `${modo}${h2(pid)}`;
    for (const l of this.hex(lineas)) {
      if (l.startsWith(cab)) { const resto = l.slice(cab.length); return bytesDeHex(resto.slice(0, Math.floor(resto.length / 2) * 2)); }
    }
    return null;
  }

  async leerPid(pid) { return this.datosModo01(await this.comando(`01${h2(pid)}`), pid); }

  async bateria() {
    const m = (await this.comando("ATRV")).join(" ").match(/(\d+(?:\.\d+)?)\s*V/);
    return m ? parseFloat(m[1]) : null;
  }

  async averias(modo) {  // 03 guardadas, 07 pendientes, 0A permanentes → códigos en crudo («0133»)
    const lineas = this.hex(await this.comando(modo, 4000));
    const resp = h2(parseInt(modo, 16) + 0x40);
    const codigos = [];
    if (this.es_can) {
      const datos = lineas.filter(l => l.length > 3).join("");
      let i = datos.indexOf(resp);
      while (i !== -1 && i + 4 <= datos.length) {
        const n = parseInt(datos.slice(i + 2, i + 4), 16);
        const trozo = datos.slice(i + 4, i + 4 + n * 4);
        for (let j = 0; j + 3 < trozo.length; j += 4) codigos.push(trozo.slice(j, j + 4));
        i = datos.indexOf(resp, i + 4 + n * 4);
      }
    } else {
      for (const l of lineas) if (l.startsWith(resp)) {
        const cuerpo = l.slice(2);
        for (let j = 0; j + 3 < cuerpo.length; j += 4) codigos.push(cuerpo.slice(j, j + 4));
      }
    }
    return codigos;
  }

  async borrarAverias() { return (await this.comando("04", 5000)).join(" ").toUpperCase().replaceAll(" ", "").includes("44"); }

  async fotoAveria() {  // freeze frame, en crudo
    const d = this.datosModo01(await this.comando("020200"), 0x02, "42");
    if (!d || d.length < 3 || (d[1] === 0 && d[2] === 0)) return null;
    const foto = { averia: [d[1], d[2]], pids: {} };
    for (const pid of [0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x03]) {
      const x = this.datosModo01(await this.comando(`02${h2(pid)}00`), pid, "42");
      if (x && x.length > 1) foto.pids[pid] = x.slice(1);
    }
    return foto;
  }

  async vin() {
    const lineas = this.hex(await this.comando("0902", 4000));
    const datos = lineas.filter(l => l.startsWith("4902") || l.length > 6).map(l => l.replace(/^4902\d\d/, "")).join("");
    const texto = String.fromCharCode(...bytesDeHex(datos.slice(0, Math.floor(datos.length / 2) * 2)).filter(b => b < 128));
    const vin = texto.replace(/[^A-HJ-NPR-Z0-9]/g, "").slice(-17);
    return vin.length === 17 ? vin : null;
  }
}

// ------------------------------------------------------------------ chequeo de coherencia (cualquier coche)
// Conexión aparte: no llama a M.conectado, así que no abre ninguna grabación del Sandero ni sube nada a registros/.
// Los chequeos se guardan en /coche/chequeos.json (IndexedDB del móvil) y, con la llave, en chequeos/ del repositorio.

const CHQ = { elm: null, t: null, demo: false, info: null };
const CHQ_F = "/coche/chequeos.json", OTRO_K = "modoOtroCoche";
const otroCoche = () => { try { return localStorage.getItem(OTRO_K) === "1"; } catch { return false; } };
const marcarOtro = si => { try { si ? localStorage.setItem(OTRO_K, "1") : localStorage.removeItem(OTRO_K); } catch {} };
const u16 = d => d.length >= 2 ? (d[0] << 8) | d[1] : null;
const CHQ_PIDS = {  // PIDs universales del modo 01: clave y fórmula SAE J1979
  0x03: ["lazo", d => d[0]],
  0x04: ["carga", d => d[0] * 100 / 255],
  0x05: ["ect", d => d[0] - 40],
  0x06: ["stft", d => (d[0] - 128) * 100 / 128],
  0x07: ["ltft", d => (d[0] - 128) * 100 / 128],
  0x0B: ["map", d => d[0]],
  0x0C: ["rpm", d => u16(d) == null ? null : u16(d) / 4],
  0x0D: ["velocidad", d => d[0]],
  0x0F: ["iat", d => d[0] - 40],
  0x10: ["maf", d => u16(d) == null ? null : u16(d) / 100],
  0x11: ["mariposa", d => d[0] * 100 / 255],
  0x14: ["o2_s1", d => d[0] / 200],
  0x15: ["o2_s2", d => d[0] / 200],
  0x42: ["v_ecu", d => u16(d) == null ? null : u16(d) / 1000],
};
// la sonda delantera va 3 veces por vuelta (oscila ~1 vez/s y en KWP solo hay ~5 lecturas/s)
const CHQ_RAPIDOS = [0x0C, 0x14, 0x06, 0x0B, 0x14, 0x10, 0x11, 0x04, 0x14, 0x15, 0x0D];
const CHQ_LENTOS = [0x05, 0x0F, 0x07, 0x42, 0x03];

function chqDecodificar(pid, d) {
  const def = CHQ_PIDS[pid];
  if (!def || !d || !d.length) return null;
  const v = def[1](d);
  return v == null || !isFinite(v) ? null : Math.round(v * 1000) / 1000;
}
function chqCorrBateria() {  // el lector marca algo menos que un polímetro (medido en el Sandero): es cosa del lector
  try { return Number(JSON.parse(M.get("/api/ajustes")).correccion_bateria) || 0; } catch { return 0; }
}

async function chqConectar(destino) {
  if (CHQ.elm) return { ok: true, dato: { ...CHQ.info, soportados: [...CHQ.elm.soportados].map(h2) } };
  const demo = destino === "demo";
  try {
    if (!demo) {
      if (!navigator.bluetooth) throw new ErrorLector(/iPhone|iPad|iPod/.test(navigator.userAgent)
        ? "Safari no tiene Bluetooth. Abre la app con «Bluefy – Web BLE Browser»." : "Este navegador no tiene Bluetooth. Abre la app con Chrome en Android.");
      if (destino === "ble_todos" || (!dispositivo && !(await lectorRecordado()))) await elegirLector(destino === "ble_todos");
    }
    if (Lec.conectando) throw new ErrorLector("La app está conectando con el Sandero: espera unos segundos y vuelve a pulsar.");
    marcarOtro(true); Lec.auto = false;
    if (Lec.elm) { const ble = !Lec.demo; await soltar("Pasa al chequeo de coherencia"); if (ble && !demo) await espera(3000); }
    const t = demo ? new TransporteSimulado() : new TransporteBLE(dispositivo);
    try { await t.abrir(); } catch (e) { t.cerrar(); throw e; }
    const elm = new ELM327(t);
    let info;
    try { info = await elm.iniciar(); } catch (e) { t.cerrar(); throw e; }
    info.destino = t.nombre;
    info.vin = await elm.vin().catch(() => null);
    Object.assign(CHQ, { elm, t, demo, info });
    mantenerPantalla();
    return { ok: true, dato: { ...info, soportados: [...elm.soportados].map(h2) } };
  } catch (e) {
    return { ok: false, error: e instanceof ErrorLector ? e.message : `${e.name || "Error"}: ${e.message || e}` };
  }
}

async function chqSoltar() {
  const t = CHQ.t;
  CHQ.elm = null; CHQ.t = null; CHQ.info = null;
  await cola;
  if (t) t.cerrar();
  soltarPantalla();
  return { ok: true, dato: true };
}

function chqVolverSandero() { marcarOtro(false); Lec.auto = true; return { ok: true, dato: true }; }

async function chqAverias() {
  const e = CHQ.elm;
  if (!e) return { ok: false, error: "Primero conecta con el coche" };
  try {
    const crudo = await enCola(async () => {
      const c = {};
      for (const modo of ["03", "07", "0A"]) c[modo] = await e.averias(modo);
      c.monitores = e.soportados.has(0x01) ? await e.leerPid(0x01) : null;
      return c;
    });
    return { ok: true, dato: JSON.parse(M.describir_averias(JSON.stringify(crudo))) };
  } catch (err) { return { ok: false, error: err.message || String(err) }; }
}

async function chqLeer(lista) {  // unos pocos PIDs para la pantalla (rpm, temperatura) mientras se espera
  const e = CHQ.elm;
  if (!e) return { ok: false, error: "Sin conexión" };
  try {
    const out = {};
    await enCola(async () => {
      for (const pid of lista) if (e.soportados.has(pid)) {
        const v = chqDecodificar(pid, await e.leerPid(pid));
        if (v != null) out[CHQ_PIDS[pid][0]] = v;
      }
    });
    return { ok: true, dato: out };
  } catch (err) { return { ok: false, error: err.message || String(err) }; }
}

async function chqMuestra(segundos, rpmDemo) {  // captura de una fase: series de cada PID durante ≥ «segundos»
  const e = CHQ.elm;
  if (!e) return { ok: false, error: "Se ha perdido la conexión con el coche" };
  if (CHQ.demo) CHQ.t.rpmFijo = rpmDemo || 0;
  const s = e.soportados, series = {}, t0 = Date.now();
  const leer = async pid => {
    if (!s.has(pid)) return;
    const v = chqDecodificar(pid, await e.leerPid(pid));
    if (v != null) (series[CHQ_PIDS[pid][0]] ||= []).push([Math.round((Date.now() - t0) / 10) / 100, v]);
  };
  const bateria = async () => {
    const v = await e.bateria();
    if (v != null) (series.bateria_atrv ||= []).push([Math.round((Date.now() - t0) / 10) / 100, Math.round((v + chqCorrBateria()) * 100) / 100]);
  };
  try {
    let vueltas = 0;
    await enCola(async () => {
      for (const pid of CHQ_LENTOS) await leer(pid);
      await bateria();
      while ((Date.now() - t0 < segundos * 1000 || vueltas < 3) && Date.now() - t0 < 40000) {
        for (const pid of CHQ_RAPIDOS) await leer(pid);
        vueltas++;
      }
      for (const pid of CHQ_LENTOS) await leer(pid);
      await bateria();
    });
    return { ok: true, dato: { segundos: Math.round((Date.now() - t0) / 100) / 10, vueltas, series,
      no_soportados: Object.entries(CHQ_PIDS).filter(([p]) => !s.has(Number(p))).map(([, d]) => d[0]) } };
  } catch (err) { return { ok: false, error: err.message || String(err) }; }
  finally { if (CHQ.t && CHQ.demo) CHQ.t.rpmFijo = 0; }
}

function chqLista() { return leerJSON(CHQ_F, []); }

async function chqGuardar(reg) {
  const lista = chqLista().filter(x => x.id !== reg.id);
  lista.push(reg);
  escribirJSON(CHQ_F, lista);
  await guardar();
  let subido = false, error = "";
  if (nube().token && navigator.onLine) {
    try {
      const carpeta = String(reg.vehiculo_id || "sin-id").toUpperCase().replace(/[^A-Z0-9]+/g, "-").replace(/^-|-$/g, "") || "SIN-ID";
      await commitArchivos([[`chequeos/${carpeta}/${reg.id}.json`, new TextEncoder().encode(JSON.stringify(reg, null, 2) + "\n")]],
        `Chequeo de coherencia ${reg.id} (subido desde el móvil)`);
      subido = true;
    } catch (e) { error = e.message || String(e); }
  }
  return { ok: true, dato: { subido, error } };
}

async function chqBorrar(id) {
  escribirJSON(CHQ_F, chqLista().filter(x => x.id !== id));
  await guardar();
  return { ok: true, dato: true };
}

async function chq(accion, a = {}) {
  await listo;
  switch (accion) {
    case "conectar": return chqConectar(a.destino || "ble");
    case "soltar": return chqSoltar();
    case "volver": await chqSoltar(); return chqVolverSandero();
    case "averias": return chqAverias();
    case "leer": return chqLeer(a.pids || [0x0C, 0x05]);
    case "muestra": return chqMuestra(Number(a.segundos) || 5, a.rpmDemo);
    case "lista": return { ok: true, dato: chqLista() };
    case "guardar": return chqGuardar(a.registro);
    case "borrar": return chqBorrar(a.id);
    case "estado": return { ok: true, dato: { conectado: !!CHQ.elm, otro_coche: otroCoche(), demo: CHQ.demo, info: CHQ.info } };
  }
  return { ok: false, error: "Acción desconocida" };
}

// ------------------------------------------------------------------ el lector (lo que hace el hilo de servidor.py)

const Lec = { elm: null, t: null, demo: false, auto: true, conectando: false, ultimaOk: 0, ciclo: 0, vuelta: 0, listas: null };
let cola = Promise.resolve();
function enCola(fn) {  // una sola conversación con el lector a la vez
  const p = cola.then(() => fn());
  cola = p.catch(() => {});
  return p;
}

let bloqueo = null;
async function mantenerPantalla() {  // si la pantalla se apaga, Android duerme la app y deja de leer
  try { if (navigator.wakeLock && !bloqueo) { bloqueo = await navigator.wakeLock.request("screen"); bloqueo.addEventListener("release", () => { bloqueo = null; }); } } catch {}
}
function soltarPantalla() { try { bloqueo && bloqueo.release(); } catch {} bloqueo = null; }

async function conectar(destino) {
  const c = JSON.parse(M.foto()).conexion;
  if (Lec.elm && c.estado === "conectado" && JSON.parse(M.foto()).tasa) return { ok: true, dato: c };  // ya conectado
  const demo = destino === "demo";
  let mio = false;  // este intento es el que tiene el «conectando»
  try {
    if (!demo) {
      if (!navigator.bluetooth) throw new ErrorLector(/iPhone|iPad|iPod/.test(navigator.userAgent)
        ? "Safari no tiene Bluetooth. En el iPhone abre esta misma dirección con la app «Bluefy – Web BLE Browser» (gratis en la App Store)."
        : "Este navegador no tiene Bluetooth. Abre la app con Chrome en Android (y el Bluetooth encendido).");
      // elegir el lector tiene que ser lo primero tras pulsar Conectar (Chrome solo lo deja justo después de un toque)
      if (destino === "ble_todos" || (destino === "ble" && !dispositivo && !(await lectorRecordado()))) await elegirLector(destino === "ble_todos");
    }
    if (Lec.conectando) return { ok: false, error: "Ya estoy conectando, espera un momento…" };
    Lec.conectando = mio = true;
    if (Lec.elm) { const ble = !Lec.demo; await soltar("Reconectando"); if (ble) await espera(3000); }
    M.estado_conexion("conectando", demo ? "Conectando con el coche de prueba…" : `Conectando con ${dispositivo.name || "el lector"} por Bluetooth…`);
    const t = demo ? new TransporteSimulado() : new TransporteBLE(dispositivo);
    try { await t.abrir(); } catch (e) { t.cerrar(); throw e; }  // que no quede el Bluetooth medio abierto
    M.estado_conexion("conectando", "Despertando al coche (puede tardar hasta 15 segundos)…");
    const elm = new ELM327(t);
    let info;
    try { info = await elm.iniciar(); } catch (e) { t.cerrar(); throw e; }
    info.destino = t.nombre;
    if (!Lec.listas) Lec.listas = JSON.parse(M.listas_pids());
    Object.assign(Lec, { elm, t, demo, ultimaOk: Date.now(), ciclo: 0 });
    M.conectado(JSON.stringify(info), JSON.stringify([...elm.soportados]), demo);
    await leerAverias().catch(e => console.error(e));
    mantenerPantalla();
    bucle(++Lec.vuelta);
    guardar();
    return { ok: true, dato: info };
  } catch (e) {
    const texto = e instanceof ErrorLector ? e.message : `${e.name || "Error"}: ${e.message || e}`;
    M.estado_conexion("error", texto);
    return { ok: false, error: texto };
  } finally {
    if (mio) Lec.conectando = false;
  }
}

async function soltar(motivo) {  // desconectar: cierra y revisa la grabación
  const t = Lec.t;
  Lec.elm = null; Lec.t = null;
  await cola;  // que termine el comando que estuviera en marcha
  M.desconectar(motivo);
  if (t) t.cerrar();
  soltarPantalla();
  await guardar();
  setTimeout(subirPendientes, 2000);
}

async function bucle(vuelta) {
  while (Lec.elm && Lec.vuelta === vuelta) {
    try {
      await unaVuelta();
    } catch (e) {
      if (Lec.vuelta !== vuelta) return;
      console.error(e);
      await soltar(`Se perdió la conexión: ${e.message || e}`);
      return;
    }
  }
}

async function unaVuelta() {
  const elm = Lec.elm;
  if (Date.now() - Lec.ultimaOk > 8000) return cocheCallado(elm);
  const s = elm.soportados, L = Lec.listas, ciclo = Lec.ciclo++;
  const lista = L.rapidos.filter(p => s.has(p));
  if (ciclo % 5 === 0) lista.push(...L.lentos.filter(p => s.has(p)));
  if (ciclo % 60 === 0) lista.push(...L.muy_lentos.filter(p => s.has(p)));
  for (const pid of lista) {
    if (Lec.elm !== elm) return;
    const datos = await enCola(() => elm.leerPid(pid));
    if (M.leido(pid, JSON.stringify(datos || []))) Lec.ultimaOk = Date.now();
  }
  if (ciclo % 5 === 0) {
    const v = await enCola(() => elm.bateria());
    if (v != null) M.bateria(v.toFixed(2));  // el lector marca algo menos que el polímetro: corrección en ajustes.json
  }
}

async function cocheCallado(elm) {  // contacto quitado: reintenta despertar a la centralita cada pocos segundos
  M.coche_callado();
  const vuelve = await enCola(async () => {
    await elm.comando("ATPC");
    const r = await elm.comando("0100", 12000);
    if (elm.datosModo01(r, 0x00)) return true;
    const v = await elm.bateria();
    if (v != null) M.bateria(v.toFixed(2));
    return false;
  });
  if (vuelve) { Lec.ultimaOk = Date.now(); M.coche_vuelve(); } else await espera(3000);
}

async function leerAverias() {
  const e = Lec.elm;
  if (!e) return { ok: false, error: "Primero conecta con el coche" };
  const crudo = await enCola(async () => {
    const c = {};
    for (const modo of ["03", "07", "0A"]) c[modo] = await e.averias(modo);
    c.monitores = await e.leerPid(0x01);
    c.foto = await e.fotoAveria();
    c.pids = {};
    for (const pid of [0x31, 0x30, 0x21, 0x4E]) if (e.soportados.has(pid)) c.pids[pid] = (await e.leerPid(pid)) || [];
    if (!("vin" in e.info)) e.info.vin = await e.vin();
    c.vin = e.info.vin;
    return c;
  });
  const res = JSON.parse(M.averias_leidas(JSON.stringify(crudo)));
  guardar();
  return { ok: true, dato: res };
}

async function borrarAverias() {
  if (!Lec.elm) return { ok: false, error: "Primero conecta con el coche" };
  if (M.rpm() > 400) return { ok: false, error: "Para borrar averías el motor tiene que estar APAGADO (solo el contacto puesto)." };
  M.antes_de_borrar();
  const elm = Lec.elm;
  const ok = await enCola(() => elm.borrarAverias());
  await espera(1000);
  await leerAverias();
  return { ok: true, dato: ok };
}

async function reconectarSolo() {  // como el hilo «reconectar» de servidor.py: vuelve a conectar si se corta
  if (!M || !Lec.auto || Lec.elm || Lec.conectando) return;
  if (CHQ.elm || otroCoche()) return;  // el lector está en otro coche (chequeo): no grabarlo como si fuera el Sandero
  if (JSON.parse(M.get("/api/ajustes")).ultimo_lector !== "ble") return;
  if (!(await lectorRecordado())) return;  // sin lector recordado hay que pulsar Conectar
  await conectar("ble");
}

let diagMs = 0;
async function diagnosticoEnDirecto() {  // cada 4 s (o más si el móvil va justo) cruza todo lo grabado
  try { diagMs = M.diagnostico_en_directo(); } catch (e) { console.error(e); }
  setTimeout(diagnosticoEnDirecto, Math.max(4000, diagMs * 10));
}


// ------------------------------------------------------------------ nube: grabaciones a GitHub y mensajes de Claude
// Cada grabación terminada se sube sola al repositorio privado (registros/<carpeta>/…) en un solo commit.
// La pestaña «💬 Claude» lee de ahí MENSAJES.md, HISTORIAL.md y BUZON.md, y escribe en BUZON.md.
// La llave (token fine-grained solo para ese repositorio, permiso Contents) se guarda únicamente en este móvil.

const REPO_DEFECTO = "informepelicanos-suloxtar/coche-obd";
const NUBE_F = "/coche/nube.json", SUBIDAS_F = "/coche/subidas.json";
const leerJSON = (f, d) => { try { return JSON.parse(py.FS.readFile(f, { encoding: "utf8" })); } catch { return d; } };
const escribirJSON = (f, x) => py.FS.writeFile(f, JSON.stringify(x));
const nube = () => ({ repo: REPO_DEFECTO, token: "", ...leerJSON(NUBE_F, {}) });
let nubeError = "", nubeUltima = "", subiendo = null;

async function gh(ruta, opciones = {}, token = nube().token) {
  const r = await fetch(`https://api.github.com/repos/${nube().repo}${ruta}`, { ...opciones, cache: "no-store", headers: {
    Accept: opciones.crudo ? "application/vnd.github.raw+json" : "application/vnd.github+json",
    Authorization: `Bearer ${token}`, "X-GitHub-Api-Version": "2022-11-28",
    ...(opciones.body ? { "Content-Type": "application/json" } : {}) } });
  if (r.status === 404 && opciones.vacio404) return null;
  if (!r.ok) {
    let m = ""; try { m = (await r.json()).message || ""; } catch {}
    const e = new Error(r.status === 401 ? "La llave de GitHub no vale o ha caducado: pon una nueva." :
      r.status === 403 || r.status === 404 ? `La llave no tiene permiso sobre ${nube().repo} (hace falta Contents: Read and write).` :
      `GitHub contesta ${r.status} ${m}`);
    e.status = r.status; throw e;
  }
  return opciones.crudo ? r.text() : r.json();
}

function base64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function deBase64(b64) {
  const bin = atob(String(b64 || "").replace(/^data:[^,]+,/, "").replace(/\s+/g, ""));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function commitArchivos(archivos, mensaje) {  // archivos: [[ruta en el repo, Uint8Array]] → un solo commit en main
  const blobs = [];
  for (const [ruta, bytes] of archivos)
    blobs.push({ path: ruta, mode: "100644", type: "blob",
      sha: (await gh("/git/blobs", { method: "POST", body: JSON.stringify({ content: base64(bytes), encoding: "base64" }) })).sha });
  for (let intento = 0; intento < 3; intento++) {
    const ref = await gh("/git/ref/heads/main");
    const padre = await gh(`/git/commits/${ref.object.sha}`);
    const arbol = await gh("/git/trees", { method: "POST", body: JSON.stringify({ base_tree: padre.tree.sha, tree: blobs }) });
    if (arbol.sha === padre.tree.sha) return false;  // ya estaba igual en GitHub
    const c = await gh("/git/commits", { method: "POST", body: JSON.stringify({ message: mensaje, tree: arbol.sha, parents: [ref.object.sha] }) });
    try { await gh("/git/refs/heads/main", { method: "PATCH", body: JSON.stringify({ sha: c.sha }) }); return true; }
    catch (e) { if (e.status !== 422 || intento === 2) throw e; await espera(1500); }  // alguien subió algo a la vez: otra vez
  }
}

function grabacionesParaSubir() {  // terminadas, del coche real y cambiadas desde la última subida
  const hechas = leerJSON(SUBIDAS_F, {}), out = [];
  let carpetas = [];
  try { carpetas = py.FS.readdir("/coche/registros").filter(n => !n.startsWith(".")); } catch {}
  for (const c of carpetas.sort()) {
    const dir = `/coche/registros/${c}`;
    const s = leerJSON(`${dir}/sesion.json`, null);
    if (!s || !s.fin || /simulador/i.test((s.lector || {}).destino || "")) continue;
    const nombres = py.FS.readdir(dir).filter(n => !n.startsWith(".") && py.FS.isFile(py.FS.stat(`${dir}/${n}`).mode));
    const firma = nombres.sort().map(n => { const st = py.FS.stat(`${dir}/${n}`); return `${n}:${st.size}:${+st.mtime}`; }).join("|");
    if (hechas[c] !== firma) out.push({ c, nombres, firma });
  }
  return out;
}

async function subirPendientes() {  // devuelve cuántas grabaciones ha subido
  if (subiendo) return subiendo;
  if (!py || !nube().token || !navigator.onLine) return 0;
  subiendo = (async () => {
    let n = 0;
    try {
      await guardar();
      for (const { c, nombres, firma } of grabacionesParaSubir()) {
        const archivos = nombres.map(f => [`registros/${c}/${f}`, py.FS.readFile(`/coche/registros/${c}/${f}`)]);
        await commitArchivos(archivos, `Grabación ${c} (subida desde el móvil)`);
        const hechas = leerJSON(SUBIDAS_F, {}); hechas[c] = firma; escribirJSON(SUBIDAS_F, hechas);
        n++;
      }
      nubeError = ""; if (n) nubeUltima = new Date().toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
      if (n) { await guardar(); tostadaMovil(n === 1 ? "Grabación subida a GitHub ✓" : `${n} grabaciones subidas a GitHub ✓`); }
    } catch (e) { nubeError = "No se pudo subir: " + (e.message || e); console.error(e); }
    return n;
  })().finally(() => { subiendo = null; });
  return subiendo;
}

function estadoNube() {
  const n = nube();
  let pendientes = 0; try { pendientes = grabacionesParaSubir().length; } catch {}
  return { token: !!n.token, repo: n.repo, pendientes, subidas: Object.keys(leerJSON(SUBIDAS_F, {})).length,
    ultima: nubeUltima, error: nubeError,
    enlace_llave: "https://github.com/settings/personal-access-tokens/new?name=" + encodeURIComponent("Mi Sandero (móvil)")
      + "&description=" + encodeURIComponent("Sube las grabaciones del coche y lee los mensajes de Claude")
      + "&expires_in=366&contents=write" };
}

// Mantenimiento y ajustes: la copia buena es la del repositorio privado (la web pública no lleva datos del coche).
// Lo que se marca en el móvil se sube enseguida; lo que cambie en el repositorio (Claude, el Mac) se baja al abrir.
const MANT_F = "/coche/mantenimiento.json", MANT_EST = "/coche/mant_nube.json";
const mantVacio = m => !m || !((m.pendientes_itv_dic2026 || []).length + (m.historial || []).length);
let mantCambios = 0, mantUltima = 0, mantEnMarcha = null, mantError = "";

function mantJuntar(remoto, local) {  // lo del repositorio y, detrás, lo que solo estaba en este móvil
  const clave = x => String(x && x.tarea || "").trim().toLowerCase();
  const out = { ...local, ...remoto };
  const pend = [...(remoto.pendientes_itv_dic2026 || [])], vistas = new Set(pend.map(clave));
  for (const x of local.pendientes_itv_dic2026 || []) if (!vistas.has(clave(x))) { pend.push(x); vistas.add(clave(x)); }
  const hist = [...(remoto.historial || [])], firmas = new Set(hist.map(e => JSON.stringify(e)));
  for (const e of local.historial || []) if (!firmas.has(JSON.stringify(e))) hist.push(e);
  out.pendientes_itv_dic2026 = pend; out.historial = hist;
  return out;
}

// Junta a tres bandas: «base» es lo último en lo que estaban de acuerdo el móvil y el repositorio. Gana el lado que
// cambió algo desde entonces; si los dos cambiaron lo mismo, gana lo marcado en el móvil.
const MANT_BASE = "/coche/mant_base.json", AJ_BASE = "/coche/ajustes_base.json";
const mismo = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function mantJuntar3(base, local, remoto) {
  const clave = x => String(x && x.tarea || "").trim().toLowerCase();
  const porClave = l => new Map((l || []).map(x => [clave(x), x]));
  const out = {};
  for (const k of new Set([...Object.keys(remoto), ...Object.keys(local)])) {
    if (k === "pendientes_itv_dic2026" || k === "historial") continue;
    const v = mismo(local[k], base[k]) ? remoto[k] : local[k];
    if (v !== undefined) out[k] = v;
  }
  const B = porClave(base.pendientes_itv_dic2026), L = porClave(local.pendientes_itv_dic2026), R = porClave(remoto.pendientes_itv_dic2026);
  const pend = [];
  for (const c of new Set([...R.keys(), ...L.keys()])) {
    const b = B.get(c), l = L.get(c), r = R.get(c);
    if (b && (!l || !r)) {  // quitada en un lado: fuera, salvo que el otro lado la haya cambiado
      const queda = l || r;
      if (mismo(queda, b)) continue;
      pend.push(queda); continue;
    }
    if (!l || !r) { pend.push(l || r); continue; }  // nueva en un solo lado
    const x = {};
    for (const f of new Set([...Object.keys(r), ...Object.keys(l)])) {
      const v = (b && mismo(l[f], b[f])) || l[f] === undefined ? r[f] : l[f];
      if (v !== undefined) x[f] = v;
    }
    pend.push(x);
  }
  const firma = e => JSON.stringify(e);
  const fb = new Set((base.historial || []).map(firma)), fl = new Set((local.historial || []).map(firma)),
        fr = new Set((remoto.historial || []).map(firma));
  const hist = [];
  for (const e of [...(remoto.historial || []), ...(local.historial || [])]) {
    const f = firma(e);
    if (hist.some(h => firma(h) === f)) continue;
    const enL = fl.has(f), enR = fr.has(f), enB = fb.has(f);
    if ((enL && enR) || !enB) hist.push(e);  // en los dos, o nueva en uno; si estaba y un lado la quitó, fuera
  }
  out.pendientes_itv_dic2026 = pend; out.historial = hist;
  return out;
}

function ajustesJuntar(local, remoto, base) {  // ultimo_lector es de cada móvil; lo demás se junta a 3 bandas
  const out = { ...local };
  const claves = new Set([...Object.keys(remoto || {}), ...Object.keys(base || local || {})]);
  for (const k of claves) {
    if (k === "ultimo_lector") continue;
    if (base && !mismo(local[k], base[k])) continue;  // cambiado en este móvil después de la última vez
    if (remoto && k in remoto) out[k] = remoto[k]; else delete out[k];
  }
  return out;
}

function mantenimientoTocado() {  // se ha marcado algo en el móvil: queda pendiente de subir
  mantCambios++;
  escribirJSON(MANT_EST, { ...leerJSON(MANT_EST, {}), pendiente: true });
  sincronizarMantenimiento(true);
}

async function sincronizarMantenimiento(forzar) {
  if (mantEnMarcha) return mantEnMarcha;
  if (!py || !nube().token || !navigator.onLine) return;
  if (!forzar && Date.now() - mantUltima < 60000) return;
  mantEnMarcha = (async () => {
    const cambios = mantCambios;
    try {
      // ajustes: sincronización en los dos sentidos (sin tocar ultimo_lector del otro aparato)
      const aj = await gh("/contents/ajustes.json?ref=main", { crudo: true, vacio404: true });
      const remotoAj = aj ? JSON.parse(aj) : {};
      const localAj = leerJSON("/coche/ajustes.json", {}), baseAj = leerJSON(AJ_BASE, null);
      const juntoAj = ajustesJuntar(localAj, remotoAj, baseAj);
      const subirAj = { ...juntoAj };
      if ("ultimo_lector" in remotoAj) subirAj.ultimo_lector = remotoAj.ultimo_lector; else delete subirAj.ultimo_lector;
      if (JSON.stringify(juntoAj) !== JSON.stringify(localAj)) { escribirJSON("/coche/ajustes.json", juntoAj); M.recargar_ajustes(); }
      const hayDatosAj = Object.keys(subirAj).some(k => k !== "ultimo_lector");
      if ((aj || hayDatosAj) && !mismo(subirAj, remotoAj)) {
        await commitArchivos([["ajustes.json", new TextEncoder().encode(JSON.stringify(subirAj, null, 2) + "\n")]],
          "Ajustes: cambios desde el móvil");
      }
      if (aj || hayDatosAj) escribirJSON(AJ_BASE, subirAj);
      const est = leerJSON(MANT_EST, {}), base = leerJSON(MANT_BASE, null);
      const local = leerJSON(MANT_F, null);
      const texto = await gh("/contents/mantenimiento.json?ref=main", { crudo: true, vacio404: true });
      let remoto = null; try { remoto = texto ? JSON.parse(texto) : null; } catch {}
      let junto;
      if (mantVacio(remoto)) junto = local;
      else if (mantVacio(local)) junto = remoto;
      else if (base) junto = mantJuntar3(base, local, remoto);       // lo cambiado en cada lado desde la última vez
      else if (!est.visto) junto = mantJuntar(remoto, local);          // primera vez con llave: se junta sin perder nada
      else if (est.pendiente) junto = mantJuntar(local, remoto);       // sin copia de la última vez: manda lo marcado aquí
      else junto = remoto;
      if (cambios !== mantCambios) return true;  // se marcó algo mientras tanto: otra vuelta con eso dentro
      if (junto && JSON.stringify(junto) !== JSON.stringify(local)) py.FS.writeFile(MANT_F, JSON.stringify(junto, null, 2));
      if (!mantVacio(junto) && JSON.stringify(junto) !== JSON.stringify(remoto)) {
        await commitArchivos([["mantenimiento.json", new TextEncoder().encode(JSON.stringify(junto, null, 2) + "\n")],
          ["MANTENIMIENTO.md", new TextEncoder().encode(M.mantenimiento_md())]], "Mantenimiento: cambios desde el móvil");
      }
      if (cambios === mantCambios) {
        if (junto) escribirJSON(MANT_BASE, junto);
        escribirJSON(MANT_EST, { visto: true, pendiente: false });
      }
      mantUltima = Date.now(); mantError = "";
      guardar();
    } catch (e) { console.error("mantenimiento", e); mantError = e.message || String(e); }
    return cambios !== mantCambios;
  })().finally(() => { mantEnMarcha = null; });
  const otra = await mantEnMarcha;
  if (otra) return sincronizarMantenimiento(true);  // se marcó algo mientras tanto
}

// Nombres de las rutas (pestaña 🗺️ Rutas): rutas.json en la raíz del repositorio, juntado a tres bandas
// para no pisar nombres puestos desde el Mac ni desde el móvil.
const RUTAS_F = "/coche/rutas.json", RUTAS_EST = "/coche/rutas_nube.json", RUTAS_BASE = "/coche/rutas_base.json";
let rutasEnMarcha = null;
function rutasJuntar(base, local, remoto, mandaLocal) {
  const b = (base && base.nombres) || {}, l = (local && local.nombres) || {}, r = (remoto && remoto.nombres) || {};
  const nombres = {};
  for (const k of new Set([...Object.keys(r), ...Object.keys(l)])) {
    const v = base ? (mismo(l[k], b[k]) ? r[k] : l[k]) : (mandaLocal ? (k in l ? l[k] : r[k]) : (k in r ? r[k] : l[k]));
    if (v) nombres[k] = v;
  }
  return { ...(remoto || {}), ...(local || {}), nombres };
}
async function sincronizarRutas(subir) {
  if (rutasEnMarcha) return rutasEnMarcha;
  if (!py || !nube().token || !navigator.onLine) return;
  rutasEnMarcha = (async () => {
    try {
      const pend = !!(subir || leerJSON(RUTAS_EST, {}).pendiente);
      const local = leerJSON(RUTAS_F, {}), base = leerJSON(RUTAS_BASE, null);
      const texto = await gh("/contents/rutas.json?ref=main", { crudo: true, vacio404: true });
      let remoto = null; try { remoto = texto ? JSON.parse(texto) : null; } catch {}
      const junto = rutasJuntar(base, local, remoto, pend);
      if (!mismo(junto, local)) escribirJSON(RUTAS_F, junto);
      if ((pend || Object.keys(junto.nombres).length) && !mismo(junto, remoto || { nombres: {} })) {
        await commitArchivos([["rutas.json", new TextEncoder().encode(JSON.stringify(junto, null, 2) + "\n")]],
          "Rutas: nombres puestos desde el móvil");
      }
      escribirJSON(RUTAS_BASE, junto);
      escribirJSON(RUTAS_EST, { pendiente: false });
      guardar();
    } catch (e) { console.error("rutas", e); }
  })().finally(() => { rutasEnMarcha = null; });
  return rutasEnMarcha;
}

// ⛽ Depósito (lecturas del cuadro y repostajes): deposito.json en la raíz del repositorio, juntado a tres bandas por
// cada anotación (id), para no perder lo apuntado desde el Mac, desde el buzón (Claude) o desde otro móvil.
const DEP_F = "/coche/deposito.json", DEP_EST = "/coche/deposito_nube.json", DEP_BASE = "/coche/deposito_base.json";
let depEnMarcha = null;
function depJuntar(base, local, remoto) {
  const mapa = d => new Map(((d && d.entradas) || []).filter(e => e && e.id).map(e => [e.id, e]));
  const B = mapa(base), L = mapa(local), R = mapa(remoto), entradas = [];
  for (const id of new Set([...R.keys(), ...L.keys()])) {
    const b = B.get(id), l = L.get(id), r = R.get(id);
    if (l && r) { entradas.push(b && mismo(l, b) ? r : l); continue; }  // en los dos: gana el que la cambió (el móvil si los dos)
    const queda = l || r;
    if (b && mismo(queda, b)) continue;  // estaba la última vez y un lado la ha borrado sin que el otro la tocara: fuera
    entradas.push(queda);                // nueva en un lado
  }
  entradas.sort((a, b) => String(a.fecha || "").localeCompare(String(b.fecha || "")));
  const out = { ...(remoto || {}), ...(local || {}) };
  for (const k of Object.keys(out)) if (k !== "entradas" && base && mismo((local || {})[k], base[k]) && remoto && k in remoto) out[k] = remoto[k];
  out.entradas = entradas;
  return out;
}
async function sincronizarDeposito(subir) {
  if (depEnMarcha) { await depEnMarcha.catch(() => {}); if (!subir) return; }
  if (!py || !nube().token || !navigator.onLine) return;
  depEnMarcha = (async () => {
    try {
      const pend = !!(subir || leerJSON(DEP_EST, {}).pendiente);
      const local = leerJSON(DEP_F, {}), base = leerJSON(DEP_BASE, null);
      const texto = await gh("/contents/deposito.json?ref=main", { crudo: true, vacio404: true });
      let remoto = null; try { remoto = texto ? JSON.parse(texto) : null; } catch {}
      const junto = depJuntar(base, local, remoto);
      if (!mismo(junto, local)) escribirJSON(DEP_F, junto);
      const hay = (junto.entradas || []).length || (remoto && (remoto.entradas || []).length);
      if ((pend || hay) && !mismo(junto, remoto || {})) {
        await commitArchivos([["deposito.json", new TextEncoder().encode(JSON.stringify(junto, null, 2) + "\n")]],
          "Depósito: lectura o repostaje apuntado desde el móvil");
      }
      escribirJSON(DEP_BASE, junto);
      escribirJSON(DEP_EST, { pendiente: false });
      guardar();
    } catch (e) { console.error("depósito", e); escribirJSON(DEP_EST, { pendiente: true }); }
  })().finally(() => { depEnMarcha = null; });
  return depEnMarcha;
}

async function bajarArchivo(carpeta, nombre) {  // p. ej. el GPX de una salida: compartir (Garmin, Drive…) o a Descargas
  await listo;
  if (!/^[\w-]+$/.test(carpeta) || !/^[\w.-]+$/.test(nombre)) return;
  let d; try { d = py.FS.readFile(`/coche/registros/${carpeta}/${nombre}`); } catch { return tostadaMovil("No encuentro ese archivo"); }
  const f = new File([d], `sandero_${carpeta}_${nombre}`, { type: nombre.endsWith(".gpx") ? "application/gpx+xml" : "application/octet-stream" });
  if (navigator.canShare && navigator.canShare({ files: [f] })) {
    try { await navigator.share({ files: [f], title: "Ruta del Sandero" }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  descargar(f); tostadaMovil("Guardado en Descargas: " + f.name);
}

async function leerClaude(forzar) {
  const caja = { leer: () => { try { return JSON.parse(localStorage.getItem("claudeCopia") || "null"); } catch { return null; } },
                 poner: x => { try { localStorage.setItem("claudeCopia", JSON.stringify(x)); } catch {} } };
  const copia = caja.leer();
  if (!nube().token) return { nube: estadoNube(), error: "Pon la llave de GitHub (arriba) para ver aquí los mensajes de Claude." };
  if (!forzar && copia && Date.now() - copia.t < 60000) return { ...copia, nube: estadoNube() };
  try {
    const [mensajes, historial, buzon] = await Promise.all(["MENSAJES.md", "HISTORIAL.md", "BUZON.md"]
      .map(f => gh(`/contents/${f}?ref=main`, { crudo: true, vacio404: true }).then(t => t || "")));
    const x = { mensajes, historial, buzon, t: Date.now(), origen: "Leído de GitHub a las " + new Date().toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }) };
    caja.poner(x);
    return { ...x, nube: estadoNube() };
  } catch (e) {
    if (copia) return { ...copia, nube: estadoNube(), origen: `Sin conexión con GitHub (${e.message || e}). Copia guardada del ${new Date(copia.t).toLocaleString("es-ES")}.` };
    return { nube: estadoNube(), error: e.message || String(e) };
  }
}

async function escribirBuzon(texto, fotos) {
  texto = String(texto || "").trim().slice(0, 4000);
  const listaFotos = Array.isArray(fotos) ? fotos.filter(f => f && f.b64).slice(0, 8) : [];
  if (!texto && !listaFotos.length) return { ok: false, error: "Mensaje vacío" };
  if (!nube().token) return { ok: false, error: "Primero pon la llave de GitHub (arriba)." };
  if (!navigator.onLine) return { ok: false, error: "Sin internet: inténtalo luego." };
  try {
    const previo = await gh("/contents/BUZON.md?ref=main", { crudo: true, vacio404: true })
      || "# Buzón: mensajes de Saúl para Claude\n\nLo escribe Saúl desde la web (pestaña 💬 Claude). Claude lo lee al decirle «lee el buzón del coche» y contesta en MENSAJES.md.\n";
    const ahora = new Date(), d = n => String(n).padStart(2, "0");
    const fecha = `${ahora.getFullYear()}-${d(ahora.getMonth() + 1)}-${d(ahora.getDate())} ${d(ahora.getHours())}:${d(ahora.getMinutes())}`;
    const sello = `${ahora.getFullYear()}-${d(ahora.getMonth() + 1)}-${d(ahora.getDate())}_${d(ahora.getHours())}${d(ahora.getMinutes())}${d(ahora.getSeconds())}`;
    const archivos = [], lineasFotos = [];
    for (let i = 0; i < listaFotos.length; i++) {
      const bytes = deBase64(listaFotos[i].b64);
      if (!bytes.length || bytes.length > 3 * 1024 * 1024) continue;
      const ruta = `fotos/${sello}_${i + 1}.jpg`;
      archivos.push([ruta, bytes]);
      lineasFotos.push(`![Foto ${i + 1}](${ruta})`);
    }
    if (!texto && !archivos.length) return { ok: false, error: "No se pudo leer ninguna foto" };
    const cuerpo = [texto, ...lineasFotos].filter(Boolean).join("\n");
    const nuevo = previo.trimEnd() + `\n\n## ${fecha} · Saúl (desde el móvil)\n${cuerpo}\n`;
    archivos.push(["BUZON.md", new TextEncoder().encode(nuevo)]);
    const notaFotos = lineasFotos.length ? ` (${lineasFotos.length === 1 ? "1 foto" : lineasFotos.length + " fotos"})` : "";
    await commitArchivos(archivos, `Buzón: mensaje de Saúl desde el móvil${notaFotos}`);
    try { localStorage.removeItem("claudeCopia"); } catch {}
    return { ok: true, dato: `Enviado ✓${notaFotos} Claude contesta en 1-3 minutos (pulsa ↻ Actualizar)` };
  } catch (e) { return { ok: false, error: e.message || String(e) }; }
}

async function ponerLlave(token) {
  token = String(token || "").trim();
  if (!token) { escribirJSON(NUBE_F, { ...nube(), token: "" }); await guardar(); return { ok: true, dato: true }; }
  try {
    const r = await gh("", {}, token);
    if (!(r.permissions && r.permissions.push)) return { ok: false, error: "La llave solo deja leer: en Contents pon «Read and write»." };
  } catch (e) { return { ok: false, error: e.message || String(e) }; }
  escribirJSON(NUBE_F, { ...nube(), token });
  try { localStorage.removeItem("claudeCopia"); } catch {}
  await guardar();
  sincronizarMantenimiento(true);
  return { ok: true, dato: true };
}

// ------------------------------------------------------------------ «servidor» para index.html

const listo = arrancar().catch(e => { console.error(e); velo(`No se pudo preparar la app: ${e.message || e}<br><small>Cierra y vuelve a abrirla.</small>`, true); throw e; });

async function api(ruta, cuerpo) {
  await listo;
  if (cuerpo === undefined && ruta.startsWith("/api/claude")) return leerClaude(ruta.includes("forzar"));
  if (cuerpo === undefined && ruta === "/api/mantenimiento")  // antes de pintarlo, lo último del repositorio (sin esperar de más)
    await conTiempo(Promise.resolve(sincronizarMantenimiento()), 6000, "tarda").catch(() => {});
  if (cuerpo === undefined && ruta === "/api/rutas")  // los nombres de las rutas, por si se cambiaron desde el Mac
    await conTiempo(Promise.resolve(sincronizarRutas()), 5000, "tarda").catch(() => {});
  if (cuerpo === undefined && ruta === "/api/deposito")  // lo apuntado desde el Mac o el buzón, antes de pintarlo
    await conTiempo(Promise.resolve(sincronizarDeposito()), 5000, "tarda").catch(() => {});
  if (cuerpo === undefined && ruta === "/api/mantenimiento")  // la página explica por qué sale vacío (sin llave, sin internet…)
    return { ...JSON.parse(M.get(ruta)), _nube: { llave: !!nube().token, internet: navigator.onLine, error: mantError } };
  if (cuerpo === undefined) return JSON.parse(M.get(ruta));
  const accion = (n, a) => { const r = JSON.parse(M.accion(n, JSON.stringify(a))); guardar(); return r; };
  switch (ruta) {
    case "/api/conectar":  // Conectar a mano = vuelve al Sandero (cierra el chequeo si estaba abierto)
      if (CHQ.elm) await chqSoltar();
      chqVolverSandero(); return conectar(cuerpo.destino || "ble");
    case "/api/desconectar": Lec.auto = false; await soltar("Desconectado"); return { ok: true, dato: true };
    case "/api/averias": return leerAverias();
    case "/api/borrar": return cuerpo.confirmo === true ? borrarAverias() : { ok: false, error: "Falta confirmar" };
    case "/api/empezar":
      if (!Lec.elm) return { ok: false, error: "Primero conecta con el coche" };
      return accion("empezar", { prueba: cuerpo.prueba || "libre" });
    case "/api/parar": { const r = accion("parar", {}); setTimeout(subirPendientes, 2000); return r; }
    case "/api/nube": return ponerLlave(cuerpo.token);
    case "/api/buzon": return escribirBuzon(cuerpo.texto, cuerpo.fotos);
    case "/api/subir": {
      if (!nube().token) return { ok: false, error: "Primero pon la llave de GitHub" };
      if (!navigator.onLine) return { ok: false, error: "Sin internet: se subirán solas cuando vuelva" };
      const n = await subirPendientes();
      return nubeError ? { ok: false, error: nubeError } : { ok: true, dato: n };
    }
    case "/api/nota": return accion("nota", { texto: String(cuerpo.texto || "").slice(0, 300) || "Marca" });
    default: {
      const r = JSON.parse(M.post(ruta, JSON.stringify(cuerpo)));
      if (ruta !== "/api/gps") guardar();  // el GPS llega 2,5 veces/s: ya se guarda solo cada 10 s
      if (ruta === "/api/mantenimiento") mantenimientoTocado();
      if (ruta === "/api/ajustes" && r.ok) { mantCambios++; sincronizarMantenimiento(true); }
      if (ruta === "/api/ruta_nombre" && r.ok) { escribirJSON(RUTAS_EST, { pendiente: true }); sincronizarRutas(true); }
      if (ruta === "/api/deposito" && r.ok) { escribirJSON(DEP_EST, { pendiente: true }); guardar(); sincronizarDeposito(true); }
      return r;
    }
  }
}

function suscribir(pintar) {
  listo.then(() => setInterval(() => { try { pintar(JSON.parse(M.foto())); } catch (e) { console.error(e); } }, 500));
}

function archivo(carpeta) {
  const px = M.zip_grabacion(carpeta);
  if (!px) return null;
  const bytes = px.toJs(); px.destroy();
  return new File([bytes], `coche_${carpeta}.zip`, { type: "application/zip" });
}

function descargar(f) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(f); a.download = f.name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

async function compartir(carpeta) {
  await listo;
  const zip = archivo(carpeta);
  if (!zip) return tostadaMovil("No encuentro esa grabación");
  // Android no deja compartir .zip: se mandan los archivos sueltos con el nombre de la grabación delante
  const leer = n => { try { return py.FS.readFile(`/coche/registros/${carpeta}/${n}`); } catch { return null; } };
  const sueltos = [["datos.csv", "text/csv"], ["resumen.txt", "text/plain"], ["sesion.json", "text/plain"]]
    .map(([n, tipo]) => { const d = leer(n); return d && new File([d], `${carpeta}__${n}${n.endsWith(".json") ? ".txt" : ""}`, { type: tipo }); })
    .filter(Boolean);
  for (const files of [[zip], sueltos]) {
    if (navigator.canShare && navigator.canShare({ files })) {
      try { await navigator.share({ files, title: "Grabación del Sandero", text: `Grabación del coche ${carpeta}` }); return; }
      catch (e) { if (e.name === "AbortError") return; }
    }
  }
  descargar(zip);
  tostadaMovil("Guardada en Descargas: " + zip.name);
}

async function bajar(carpeta) { await listo; const f = archivo(carpeta); if (f) { descargar(f); tostadaMovil("Guardada en Descargas: " + f.name); } }

async function borrarGrabacion(carpeta) {
  await listo;
  if (!confirm("¿Borrar esta grabación del móvil? (Si no se la has mandado al Mac, se pierde.)")) return;
  if (M.borrar_grabacion(carpeta)) { await guardar(); tostadaMovil("Grabación borrada"); if (window.recargarSesiones) { window.recargarSesiones(); } }
  else tostadaMovil("No se puede borrar la grabación que está en marcha");
}

function ajustarTextos() {  // lo poco que cambia respecto al Mac
  const h = document.getElementById("textoGrabaciones");
  if (h) h.innerHTML = "Se guardan en este móvil y, si has puesto la llave de GitHub (pestaña 💬 Claude), se suben solas al "
    + "terminar cada grabación. Luego dile a Claude: <b>«revisa la última grabación del coche»</b> y su respuesta sale en 💬 Claude.";
  const b = document.getElementById("textoBuzon");
  if (b) b.innerHTML = "Claude lo lee solo (texto y fotos) y contesta aquí arriba en 1-3 minutos (desde el portátil de la azotea). "
    + "Pulsa <b>↻ Actualizar</b> para verlo.";
  const m = document.getElementById("textoMantenimiento");
  if (m) m.innerHTML = "Puedes marcar cada tarea según la vayas haciendo. Se guarda en este móvil y, con la llave de GitHub "
    + "puesta (pestaña 💬 Claude), también en el repositorio, para que lo vean Claude y el Mac.";
}

window.MOTOR = { api, chq, suscribir, compartir, bajar, bajarArchivo, borrarGrabacion, version: VERSION,
  opcionesLector: [["ble", "Lector OBD por Bluetooth"], ["ble_todos", "Buscar cualquier aparato Bluetooth"], ["demo", "Coche de prueba (demostración)"]] };
})();

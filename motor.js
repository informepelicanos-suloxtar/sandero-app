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
const VERSION = "959f33d338";
const PY = ["pids.py", "averias.py", "diagnostico.py", "analisis.py", "rutas.py", "servidor.py", "elm327.py", "simulador.py", "movil_py.py"];
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
  for (const f of ["mantenimiento.json", "ajustes.json"])  // la primera vez: copia de lo que había en el Mac
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
  window.addEventListener("online", () => setTimeout(() => { subirPendientes(); sincronizarMantenimiento(true); }, 3000));
  setInterval(reconectarSolo, 15000);
  setTimeout(reconectarSolo, 1500);
  ajustarTextos();
  quitarVelo();
  if (!navigator.bluetooth) setTimeout(() => tostadaMovil(/iPhone|iPad|iPod/.test(navigator.userAgent)
    ? "Safari no tiene Bluetooth: abre esta misma dirección con la app Bluefy." : "Este navegador no tiene Bluetooth: abre la app con Chrome en Android."), 800);
  else if (recuperadas.length) setTimeout(() => tostadaMovil("Se cerró a medias una grabación: ya está guardada y revisada."), 800);
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
    const rpm = vel === 0 ? idle + azar(-25, 25) : 1600 + vel * 25 + azar(-40, 40);
    const lazo = temp > 45 && !(vel && Math.random() < 0.05) ? 2 : 1;
    const sonda1 = lazo === 2 ? 0.48 + 0.4 * Math.sin(Date.now() / 1000 * 4.5) : 0.1;
    return { temp, rpm, vel, lazo, sonda1: Math.max(0.05, sonda1),
      sonda2: caliente ? 0.68 + azar(-0.04, 0.04) : 0.2,
      map: vel === 0 ? 33 + azar(-2, 2) : 55 + vel * 0.3,
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
    await t.abrir();
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
let mantCambios = 0, mantUltima = 0, mantEnMarcha = null;

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
      if (!("correccion_velocidad" in leerJSON("/coche/ajustes.json", {}))) {  // móvil recién instalado: ajustes del repositorio
        const aj = await gh("/contents/ajustes.json?ref=main", { crudo: true, vacio404: true });
        if (aj) { const nuevos = { ...JSON.parse(aj), ...leerJSON("/coche/ajustes.json", {}) }; escribirJSON("/coche/ajustes.json", nuevos); M.recargar_ajustes(); }
      }
      const est = leerJSON(MANT_EST, {}), local = leerJSON(MANT_F, null);
      const texto = await gh("/contents/mantenimiento.json?ref=main", { crudo: true, vacio404: true });
      let remoto = null; try { remoto = texto ? JSON.parse(texto) : null; } catch {}
      const igual = !!remoto && JSON.stringify(remoto) === JSON.stringify(local);
      // se sube lo del móvil si hay algo marcado sin subir (o es la primera vez y el móvil ya tenía lo suyo)
      if (!igual && !mantVacio(local) && (est.pendiente || !est.visto)) {
        await commitArchivos([["mantenimiento.json", new TextEncoder().encode(JSON.stringify(local, null, 2) + "\n")],
          ["MANTENIMIENTO.md", new TextEncoder().encode(M.mantenimiento_md())]], "Mantenimiento: cambios desde el móvil");
      } else if (!igual && !mantVacio(remoto) && cambios === mantCambios) {
        py.FS.writeFile(MANT_F, JSON.stringify(remoto, null, 2));
      }
      if (cambios === mantCambios) escribirJSON(MANT_EST, { visto: true, pendiente: false });
      mantUltima = Date.now();
      guardar();
    } catch (e) { console.error("mantenimiento", e); }
    return cambios !== mantCambios;
  })().finally(() => { mantEnMarcha = null; });
  const otra = await mantEnMarcha;
  if (otra) return sincronizarMantenimiento(true);  // se marcó algo mientras tanto
}

// Nombres de las rutas (pestaña 🗺️ Rutas): rutas.json en la raíz del repositorio. Lo que se cambia en el móvil se
// sube; si no hay nada pendiente, manda lo del repositorio (por si se le puso nombre desde el Mac).
const RUTAS_F = "/coche/rutas.json", RUTAS_EST = "/coche/rutas_nube.json";
async function sincronizarRutas(subir) {
  if (!py || !nube().token || !navigator.onLine) return;
  try {
    if (subir || leerJSON(RUTAS_EST, {}).pendiente) {
      const local = leerJSON(RUTAS_F, {});
      await commitArchivos([["rutas.json", new TextEncoder().encode(JSON.stringify(local, null, 2) + "\n")]],
        "Rutas: nombres puestos desde el móvil");
      escribirJSON(RUTAS_EST, { pendiente: false });
    } else {
      const texto = await gh("/contents/rutas.json?ref=main", { crudo: true, vacio404: true });
      if (texto) { const dato = JSON.parse(texto); if (dato && typeof dato === "object") escribirJSON(RUTAS_F, dato); }
    }
    guardar();
  } catch (e) { console.error("rutas", e); }
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

async function escribirBuzon(texto) {
  texto = String(texto || "").trim().slice(0, 4000);
  if (!texto) return { ok: false, error: "Mensaje vacío" };
  if (!nube().token) return { ok: false, error: "Primero pon la llave de GitHub (arriba)." };
  if (!navigator.onLine) return { ok: false, error: "Sin internet: inténtalo luego." };
  try {
    const previo = await gh("/contents/BUZON.md?ref=main", { crudo: true, vacio404: true })
      || "# Buzón: mensajes de Saúl para Claude\n\nLo escribe Saúl desde la web (pestaña 💬 Claude). Claude lo lee al decirle «lee el buzón del coche» y contesta en MENSAJES.md.\n";
    const ahora = new Date(), d = n => String(n).padStart(2, "0");
    const fecha = `${ahora.getFullYear()}-${d(ahora.getMonth() + 1)}-${d(ahora.getDate())} ${d(ahora.getHours())}:${d(ahora.getMinutes())}`;
    const nuevo = previo.trimEnd() + `\n\n## ${fecha} · Saúl (desde el móvil)\n${texto}\n`;
    await commitArchivos([["BUZON.md", new TextEncoder().encode(nuevo)]], "Buzón: mensaje de Saúl desde el móvil");
    try { localStorage.removeItem("claudeCopia"); } catch {}
    return { ok: true, dato: "Enviado a Claude ✓" };
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
  if (cuerpo === undefined) return JSON.parse(M.get(ruta));
  const accion = (n, a) => { const r = JSON.parse(M.accion(n, JSON.stringify(a))); guardar(); return r; };
  switch (ruta) {
    case "/api/conectar": Lec.auto = true; return conectar(cuerpo.destino || "ble");
    case "/api/desconectar": Lec.auto = false; await soltar("Desconectado"); return { ok: true, dato: true };
    case "/api/averias": return leerAverias();
    case "/api/borrar": return cuerpo.confirmo === true ? borrarAverias() : { ok: false, error: "Falta confirmar" };
    case "/api/empezar":
      if (!Lec.elm) return { ok: false, error: "Primero conecta con el coche" };
      return accion("empezar", { prueba: cuerpo.prueba || "libre" });
    case "/api/parar": { const r = accion("parar", {}); setTimeout(subirPendientes, 2000); return r; }
    case "/api/nube": return ponerLlave(cuerpo.token);
    case "/api/buzon": return escribirBuzon(cuerpo.texto);
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
      if (ruta === "/api/ruta_nombre" && r.ok) { escribirJSON(RUTAS_EST, { pendiente: true }); sincronizarRutas(true); }
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
  if (b) b.innerHTML = "Se guarda en GitHub (<code>BUZON.md</code>). Claude lo lee cuando le digas «lee el buzón del coche».";
  const m = document.getElementById("textoMantenimiento");
  if (m) m.innerHTML = "Puedes marcar cada tarea según la vayas haciendo. Se guarda en este móvil y, con la llave de GitHub "
    + "puesta (pestaña 💬 Claude), también en el repositorio, para que lo vean Claude y el Mac.";
}

window.MOTOR = { api, suscribir, compartir, bajar, bajarArchivo, borrarGrabacion, version: VERSION,
  opcionesLector: [["ble", "Lector OBD por Bluetooth"], ["ble_todos", "Buscar cualquier aparato Bluetooth"], ["demo", "Coche de prueba (demostración)"]] };
})();

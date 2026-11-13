/* Guarda la app entera en el móvil para que funcione sin internet (en el coche). La genera preparar_movil.sh. */
const CACHE = "mi-sandero-492aaf230b";
const ARCHIVOS = ["./", "index.html", "motor.js", "manifest.webmanifest", "icono-192.png", "icono-512.png", "py/ajustes.json", "py/analisis.py", "py/averias.py", "py/diagnostico.py", "py/elm327.py", "py/mantenimiento.json", "py/movil_py.py", "py/pids.py", "py/rutas.py", "py/servidor.py", "py/simulador.py", "pyodide/pyodide-lock.json", "pyodide/pyodide.asm.js", "pyodide/pyodide.asm.wasm", "pyodide/pyodide.js", "pyodide/python_stdlib.zip"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ARCHIVOS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(caches.open(CACHE).then(c => c.match(e.request, { ignoreSearch: true })
    .then(r => r || fetch(e.request))));
});

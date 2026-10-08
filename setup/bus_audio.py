"""
setup/bus_audio.py
------------------
Bus de audio INTERNO del reproductor. Reemplaza al mezclador de pygame y es lo
que permite tener el ecualizador adentro del programa, sin cable virtual:

    canal A ─┐
             ├─► suma ─► Ecualizador DJ (MotorEfectos) ─► sounddevice ─► parlantes
    canal B ─┘

Los "canales" (CanalBus) imitan la interfaz de pygame.mixer.Channel que usa el
motor de mezcla (play / stop / set_volume / get_volume / get_busy), así
SeamlessMixerEngine casi no cambia. Los "sonidos" (SonidoBus) son arrays int16
estéreo a 44100 Hz, igual que pygame.sndarray.make_sound.

Cómo suena sin cortes:
  * Un hilo productor va mezclando bloques de BLOQUE muestras, los pasa por el
    ecualizador y los deja en un anillo. El callback de sounddevice SOLO lee
    del anillo (no corre DSP adentro: así los picos de CPU de Python no
    producen cortes).
  * El colchón del anillo arranca chico (poca latencia) y crece solo si el
    sistema se atrasa (ver colchon_seg).
  * Un canal "sigue ocupado" hasta que su última muestra salió de verdad por
    el parlante (get_busy usa los contadores del anillo), igual que pygame.
"""
import sys
import threading
import time

import numpy as np

SR_BUS = 44100
BLOQUE = 512                      # muestras por bloque de mezcla (≈ 11,6 ms)
COLCHON_INICIAL_SEG = 0.060       # latencia propia del bus al arrancar
COLCHON_MAX_SEG = 0.300           # tope si el sistema se atrasa
PASO_COLCHON_SEG = 0.020          # cuánto crece el colchón tras un hueco
LATENCIA_SALIDA_SEG = 0.030       # buffer pedido al dispositivo de salida
# Latencia que el bus suma respecto del mezclador de pygame; Principal.py la usa
# para que las barritas/ondas sigan midiendo lo que se oye AHORA.
LATENCIA_EXTRA_SEG = COLCHON_INICIAL_SEG
N_CANALES = 4
# Modo "robusto": mientras corre el análisis de la lista en segundo plano, los hilos de
# Python se pelean el GIL y el hilo de mezcla se atrasa. Con bloques más grandes y un
# colchón mayor se necesita pedir el GIL muchas menos veces por segundo (a costa de unos
# 150 ms más de latencia, que mientras se analiza no se nota).
BLOQUE_ROBUSTO = 2048
COLCHON_ROBUSTO_SEG = 0.20


# ------------------------------------------------------------------
# Sonidos
# ------------------------------------------------------------------
class SonidoBus:
    """Audio int16 estéreo (N, 2) a SR_BUS. No se copia: el que lo crea no debe
    modificar el array después (igual que con un pygame.mixer.Sound ya armado
    el motor de mezcla siempre crea arrays nuevos)."""
    __slots__ = ("datos",)

    def __init__(self, datos):
        self.datos = datos

    def get_length(self):
        return len(self.datos) / float(SR_BUS)


def make_sound(array):
    """Equivalente a pygame.sndarray.make_sound: array (N, 2) int16 (o (N,) mono)."""
    a = np.asarray(array)
    if a.ndim == 1:
        a = np.column_stack((a, a))
    elif a.ndim == 2 and a.shape[1] == 1:
        a = np.repeat(a, 2, axis=1)
    if a.dtype != np.int16:
        if np.issubdtype(a.dtype, np.floating):
            a = np.clip(a, -1.0, 1.0) * 32767.0
        a = a.astype(np.int16)
    return SonidoBus(np.ascontiguousarray(a))


def sonido_desde_archivo(ruta):
    """Equivalente a pygame.mixer.Sound(ruta) para WAV/FLAC/etc. (vía soundfile)."""
    import soundfile as sf
    datos, sr = sf.read(str(ruta), dtype="int16", always_2d=True)
    if sr != SR_BUS:
        import scipy.signal
        g = np.gcd(int(sr), SR_BUS)
        f = scipy.signal.resample_poly(datos.astype(np.float32), SR_BUS // g, int(sr) // g, axis=0)
        datos = np.clip(f, -32768, 32767).astype(np.int16)
    return make_sound(datos)


# ------------------------------------------------------------------
# Anillo de salida
# ------------------------------------------------------------------
class AnilloBus:
    """Cola circular float32 estéreo con contadores acumulados (escritos/leidos)."""

    def __init__(self, segundos=1.0, sr=SR_BUS):
        self.sr = sr
        self.cap = int(segundos * sr)
        self.buf = np.zeros((self.cap, 2), dtype=np.float32)
        self.r = 0
        self.w = 0
        self.n = 0
        self.escritos = 0
        self.leidos = 0
        self.desbordes = 0
        self.vaciados = 0
        self.lock = threading.Lock()

    def llenado(self):
        return self.n

    def escribir(self, x):
        k = len(x)
        if k == 0:
            return
        with self.lock:
            if k > self.cap:
                x = x[-self.cap:]
                k = self.cap
            libre = self.cap - self.n
            if k > libre:                      # no debería pasar: se descarta lo más viejo
                perdidas = k - libre
                self.r = (self.r + perdidas) % self.cap
                self.n -= perdidas
                self.leidos += perdidas
                self.desbordes += 1
            fin = self.w + k
            if fin <= self.cap:
                self.buf[self.w:fin] = x
            else:
                corte = self.cap - self.w
                self.buf[self.w:] = x[:corte]
                self.buf[:fin - self.cap] = x[corte:]
            self.w = fin % self.cap
            self.n += k
            self.escritos += k

    def leer(self, k):
        """Devuelve (bloque (k, 2), faltaron_muestras)."""
        out = np.zeros((k, 2), dtype=np.float32)
        with self.lock:
            k2 = min(k, self.n)
            if k2:
                fin = self.r + k2
                if fin <= self.cap:
                    out[:k2] = self.buf[self.r:fin]
                else:
                    corte = self.cap - self.r
                    out[:corte] = self.buf[self.r:]
                    out[corte:k2] = self.buf[:fin - self.cap]
                self.r = fin % self.cap
                self.n -= k2
                self.leidos += k2
            falto = k2 < k
            if falto:
                self.vaciados += 1
        return out, falto


# ------------------------------------------------------------------
# Canales (interfaz de pygame.mixer.Channel)
# ------------------------------------------------------------------
class CanalBus:
    def __init__(self, bus, indice):
        self._bus = bus
        self.indice = indice
        self.sonido = None
        self.pos = 0
        self.volumen = 1.0
        self._vol_render = 1.0
        self.activo = False
        self.pausado = False
        self.fin_global = None

    def play(self, sonido, loops=0, maxtime=0, fade_ms=0):
        with self._bus.lock:
            self.sonido = sonido
            self.pos = 0
            self.activo = sonido is not None
            self.pausado = False          # como pygame: play() destapa el canal
            self.fin_global = None
            self._vol_render = None       # primer bloque: sin rampa desde un volumen viejo

    def stop(self):
        with self._bus.lock:
            self.activo = False
            self.fin_global = None
            self.sonido = None

    def set_volume(self, v, *_):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        self.volumen = min(1.0, max(0.0, v))

    def get_volume(self):
        return float(self.volumen)

    def get_busy(self):
        if self.activo:
            return True
        f = self.fin_global
        return f is not None and self._bus.anillo.leidos < f

    def pause(self):
        self.pausado = True

    def unpause(self):
        self.pausado = False


# ------------------------------------------------------------------
# Bus
# ------------------------------------------------------------------
class BusAudio:
    def __init__(self, sr=SR_BUS):
        self.sr = sr
        self.lock = threading.RLock()
        self.canales = [CanalBus(self, i) for i in range(N_CANALES)]
        self.anillo = AnilloBus(1.0, sr)
        self.motor = None             # MotorEfectos del ecualizador (o None)
        self.eq_activo = True
        self.colchon_seg = COLCHON_INICIAL_SEG
        self.stream = None
        self.hilo = None
        self.dispositivo_actual = None
        self.activa = False
        self.ultimo_error = ""
        self.xr_salida = 0
        self.t_proc_max = 0.0
        self._corre = False
        self._evento = threading.Event()
        self._timer_alto = False
        self.modo_robusto = False

    def sonando(self):
        """True si algún canal está sacando audio ahora mismo (no pausado)."""
        for c in self.canales:
            if c.activo and not c.pausado and c.sonido is not None:
                return True
        return False

    def set_modo_robusto(self, activo):
        activo = bool(activo)
        if activo == self.modo_robusto:
            return
        self.modo_robusto = activo
        try:
            sys.setswitchinterval(0.001 if activo else 0.002)
        except Exception:
            pass

    # ---- dispositivos ----
    @staticmethod
    def listar_salidas():
        import sounddevice as sd
        apis = sd.query_hostapis()
        sal = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] >= 2:
                sal.append((i, f"{d['name']}  [{apis[d['hostapi']]['name']}]"))
        return sal

    def _resolver_dispositivo(self, sd, dispositivo):
        if isinstance(dispositivo, int):
            return dispositivo
        if isinstance(dispositivo, str) and dispositivo:
            for i, t in self.listar_salidas():
                if t == dispositivo:
                    return i
        # Por defecto: la salida predeterminada de Windows vía WASAPI (poca latencia).
        try:
            for api in sd.query_hostapis():
                if "WASAPI" in api["name"] and api.get("default_output_device", -1) >= 0:
                    return int(api["default_output_device"])
        except Exception:
            pass
        d = sd.default.device
        return int(d[1]) if isinstance(d, (list, tuple)) else int(d)

    @staticmethod
    def _extra(sd, idx):
        try:
            if "WASAPI" in sd.query_hostapis(sd.query_devices(idx)["hostapi"])["name"]:
                return sd.WasapiSettings(auto_convert=True)
        except Exception:
            pass
        return None

    # ---- arranque / parada ----
    def iniciar(self, dispositivo=None):
        """Abre la salida y arranca el hilo de mezcla. Lanza excepción si no puede."""
        import sounddevice as sd
        self.detener()
        idx = self._resolver_dispositivo(sd, dispositivo)
        self._subir_resolucion_timer()
        try:
            # Cambio de hilos más ágil: el hilo de mezcla no espera tanto por el GIL
            # cuando la interfaz está dibujando.
            sys.setswitchinterval(0.002)
        except Exception:
            pass
        self.ultimo_error = ""
        self.colchon_seg = COLCHON_INICIAL_SEG
        self.anillo = AnilloBus(1.0, self.sr)
        self._corre = True
        self.hilo = threading.Thread(target=self._producir, name="bus-audio-mezcla", daemon=True)
        self.hilo.start()
        t_fin = time.monotonic() + 1.0                 # prellenado antes de abrir la salida
        objetivo = int(self.colchon_seg * self.sr)
        while self.anillo.llenado() < objetivo and time.monotonic() < t_fin:
            time.sleep(0.002)
        try:
            self.stream = self._abrir_stream(sd, idx)
            self.stream.start()
        except Exception:
            self._corre = False
            self._evento.set()
            self.hilo.join(timeout=1.0)
            self.hilo = None
            self.stream = None
            raise
        self.dispositivo_actual = idx
        self.activa = True

    def _abrir_stream(self, sd, idx):
        intentos = [self._extra(sd, idx), None]
        ultimo = None
        for extra in intentos:
            try:
                return sd.OutputStream(device=idx, samplerate=self.sr, channels=2, dtype="float32",
                                       blocksize=BLOQUE, latency=LATENCIA_SALIDA_SEG,
                                       callback=self._callback, extra_settings=extra)
            except Exception as e:
                ultimo = e
        raise ultimo

    def cambiar_salida(self, dispositivo):
        """Cambia el parlante sin cortar a los canales (solo se reabre el stream)."""
        import sounddevice as sd
        if not self.activa:
            self.iniciar(dispositivo)
            return
        idx = self._resolver_dispositivo(sd, dispositivo)
        viejo = self.stream
        nuevo = self._abrir_stream(sd, idx)
        nuevo.start()
        self.stream = nuevo
        self.dispositivo_actual = idx
        try:
            if viejo is not None:
                viejo.stop()
                viejo.close()
        except Exception:
            pass

    def detener(self):
        self._corre = False
        self._evento.set()
        s, self.stream = self.stream, None
        try:
            if s is not None:
                s.stop()
                s.close()
        except Exception:
            pass
        h, self.hilo = self.hilo, None
        if h is not None and h is not threading.current_thread():
            h.join(timeout=1.0)
        self.activa = False
        self._restaurar_resolucion_timer()

    def _subir_resolucion_timer(self):
        if sys.platform == "win32" and not self._timer_alto:
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
                self._timer_alto = True
            except Exception:
                pass

    def _restaurar_resolucion_timer(self):
        if sys.platform == "win32" and self._timer_alto:
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
            self._timer_alto = False

    # ---- control global (como pygame.mixer.*) ----
    def detener_todo(self):
        for c in self.canales:
            c.stop()

    def pausar_todo(self):
        for c in self.canales:
            c.pausado = True

    def reanudar_todo(self):
        for c in self.canales:
            c.pausado = False

    # ---- audio ----
    def _callback(self, outdata, frames, tiempo, estado):
        try:
            if estado and estado.output_underflow:
                self.xr_salida += 1
            out, falto = self.anillo.leer(frames)
            outdata[:] = out
            if falto and self.colchon_seg < COLCHON_MAX_SEG:
                self.colchon_seg = min(COLCHON_MAX_SEG, self.colchon_seg + PASO_COLCHON_SEG)
            self._evento.set()
        except Exception:
            outdata.fill(0)

    def _producir(self):
        if sys.platform == "win32":
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                k32.SetThreadPriority(k32.GetCurrentThread(), 2)   # HIGHEST
            except Exception:
                pass
        while self._corre:
            colchon = self.colchon_seg
            if self.modo_robusto and colchon < COLCHON_ROBUSTO_SEG:
                colchon = COLCHON_ROBUSTO_SEG
            objetivo = int(colchon * self.sr)
            if self.anillo.llenado() >= objetivo:
                self._evento.wait(0.02)
                self._evento.clear()
                continue
            try:
                self.renderizar_bloque()
            except Exception as e:                  # que un error no mate el hilo
                self.ultimo_error = f"mezcla: {e}"
                self.anillo.escribir(np.zeros((BLOQUE, 2), dtype=np.float32))  # no cortar el flujo

    def renderizar_bloque(self):
        """Mezcla un bloque de los canales, lo ecualiza y lo escribe en el anillo."""
        N = BLOQUE_ROBUSTO if self.modo_robusto else BLOQUE
        mix = np.zeros((N, 2), dtype=np.float32)
        base = self.anillo.escritos
        with self.lock:
            for c in self.canales:
                v1 = c.volumen
                if c.sonido is None or not c.activo or c.pausado:
                    c._vol_render = v1
                    continue
                datos = c.sonido.datos
                total = len(datos)
                pos = c.pos
                n = min(N, total - pos)
                if n > 0:
                    seg = datos[pos:pos + n].astype(np.float32)
                    seg *= np.float32(1.0 / 32768.0)
                    v0 = c._vol_render if c._vol_render is not None else v1
                    if v0 == v1:
                        seg *= np.float32(v1)
                    else:                              # rampa: sin "zipper noise" al mover volúmenes
                        seg *= np.linspace(v0, v1, n, dtype=np.float32)[:, None]
                    mix[:n] += seg
                    c.pos = pos + n
                c._vol_render = v1
                if c.pos >= total:
                    c.activo = False
                    c.fin_global = base + max(0, n)
        y = mix
        motor = self.motor
        if motor is not None and self.eq_activo:
            t0 = time.perf_counter()
            try:
                y = motor.procesar(mix)
            except Exception as e:
                self.ultimo_error = f"procesado: {e}"
                y = mix
            dt = time.perf_counter() - t0
            if dt > self.t_proc_max:
                self.t_proc_max = dt
        y = np.clip(np.asarray(y, dtype=np.float32), -1.0, 1.0)
        self.anillo.escribir(y)


# ------------------------------------------------------------------
# Instancia única + atajos con la forma de pygame.mixer
# ------------------------------------------------------------------
_BUS = None


def obtener_bus():
    global _BUS
    if _BUS is None:
        _BUS = BusAudio()
    return _BUS


def canal(indice):
    return obtener_bus().canales[indice]


def detener_todo():
    obtener_bus().detener_todo()


def pausar_todo():
    obtener_bus().pausar_todo()


def reanudar_todo():
    obtener_bus().reanudar_todo()

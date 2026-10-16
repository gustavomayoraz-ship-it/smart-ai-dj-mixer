"""
setup/analisis_proceso.py
--------------------------
Análisis de la lista (BPM, tono, downbeat, energía) pensado para correr en PROCESOS
SEPARADOS: así no compite con el audio por el intérprete de Python (GIL) y la música no se
corta aunque se esté analizando una lista enorme.

Es un módulo liviano a propósito (solo numpy/scipy/librosa/soundfile): el proceso hijo no
tiene que cargar la interfaz ni el resto del programa.
"""
import os
import sys
import time

# Dentro de un proceso hijo cada trabajador usa UN solo hilo de BLAS/OpenMP: la
# paralelización ya la da la cantidad de procesos y así no se pisan entre sí. Tiene que
# fijarse ANTES de importar numpy.
try:
    import multiprocessing as _mp
    if _mp.current_process().name != "MainProcess":
        for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                   "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            os.environ.setdefault(_v, "1")
except Exception:
    pass

import numpy as np
import scipy.signal
import librosa
import soundfile as sf

ANALISIS_LISTA_SR = 22050
ANALISIS_LISTA_SEG_FRAGMENTO = 90.0


def detectar_downbeat(y_mono, sr, beat_times):
    beat_times = np.asarray(beat_times, dtype=float)
    if beat_times.size < 8 or y_mono.size == 0:
        return 0, beat_times[::4] if beat_times.size > 0 else np.array([])
    try:
        nyquist = sr / 2.0
        corte_kick = float(np.clip(150.0 / nyquist, 0.001, 0.99))
        sos_kick = scipy.signal.butter(4, corte_kick, btype="low", output="sos")
        y_kick = scipy.signal.sosfiltfilt(sos_kick, y_mono)
    except Exception:
        return 0, beat_times[::4] if beat_times.size > 0 else np.array([])
    ventana_muestras = max(1, int(0.05 * sr))
    energia_por_beat = np.zeros(beat_times.size)
    for i, t in enumerate(beat_times):
        idx = int(t * sr)
        ini = max(0, idx - ventana_muestras // 2)
        fin = min(len(y_kick), idx + ventana_muestras // 2)
        if fin > ini:
            segmento = y_kick[ini:fin]
            energia_por_beat[i] = np.sqrt(np.mean(segmento ** 2))
    if energia_por_beat.max() <= 1e-9:
        return 0, beat_times[::4]
    sumas = []
    for fase in range(4):
        indices = np.arange(fase, beat_times.size, 4)
        if indices.size == 0:
            sumas.append(0.0)
            continue
        sumas.append(float(energia_por_beat[indices].sum()))
    mejor_fase = int(np.argmax(sumas))
    if sumas[0] > 0 and sumas[mejor_fase] > 0:
        if (sumas[mejor_fase] - sumas[0]) / sumas[mejor_fase] < 0.01:
            mejor_fase = 0
    downbeat_times = beat_times[mejor_fase::4]
    return mejor_fase, downbeat_times


_PERFIL_MAYOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_PERFIL_MENOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOMBRES_NOTA = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]

_CAMELOT_MAYOR = {
    0: "8B", 1: "3B", 2: "10B", 3: "5B", 4: "12B", 5: "7B",
    6: "2B", 7: "9B", 8: "4B", 9: "11B", 10: "6B", 11: "1B",
}
_CAMELOT_MENOR = {
    0: "5A", 1: "12A", 2: "7A", 3: "2A", 4: "9A", 5: "4A",
    6: "11A", 7: "6A", 8: "1A", 9: "8A", 10: "3A", 11: "10A",
}


def detectar_tono(y, sr, hop_length=2048) -> dict:
    if y.size == 0:
        return {"tono": None, "tono_nombre": None}
    # El tono sale del PROMEDIO del chroma a lo largo de todo el tema, así
    # que no hace falta una resolución temporal fina: con hop_length=2048
    # (en vez del 512 por defecto de librosa) hay 4 veces menos cuadros
    # para calcular y el promedio da prácticamente lo mismo -- es la parte
    # más pesada del análisis de la lista.
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    perfil_tema = chroma.mean(axis=1)
    if perfil_tema.std() < 1e-9:
        return {"tono": None, "tono_nombre": None}
    mejor_score = None
    mejor_tonica = 0
    mejor_modo = "mayor"
    for tonica in range(12):
        score_mayor = np.corrcoef(perfil_tema, np.roll(_PERFIL_MAYOR, tonica))[0, 1]
        score_menor = np.corrcoef(perfil_tema, np.roll(_PERFIL_MENOR, tonica))[0, 1]
        if mejor_score is None or score_mayor > mejor_score:
            mejor_score, mejor_tonica, mejor_modo = score_mayor, tonica, "mayor"
        if score_menor > mejor_score:
            mejor_score, mejor_tonica, mejor_modo = score_menor, tonica, "menor"
    if mejor_modo == "mayor":
        return {"tono": _CAMELOT_MAYOR[mejor_tonica], "tono_nombre": f"{_NOMBRES_NOTA[mejor_tonica]} mayor"}
    return {"tono": _CAMELOT_MENOR[mejor_tonica], "tono_nombre": f"{_NOMBRES_NOTA[mejor_tonica]} menor"}


def _corregir_media_o_doble_tempo(bpm: float) -> float:
    if bpm <= 0:
        return bpm
    while bpm < 90 and bpm * 2 <= 180:
        bpm *= 2
    while bpm > 180 and bpm / 2 >= 90:
        bpm /= 2
    return bpm


def analizar_archivo(ruta_abs: str) -> dict:
    """Analiza UN tema y devuelve solo datos simples (se transmiten entre procesos).

    Este análisis es SOLO metadata de la lista (BPM, tono, energía para ordenar/mostrar):
    no toca el audio que suena (eso lo decodifica aparte, a calidad completa, la preparación
    del deck). Por eso alcanza con un fragmento del medio del tema a 22.05 kHz en vez del tema
    entero a 44.1 kHz: beat_track y sobre todo chroma_cqt (lo más pesado) bajan varias veces el
    tiempo, y el BPM/tono se detectan igual de bien con ~90 s de música. (fase_downbeat queda
    relativa al fragmento.)"""
    t0 = time.monotonic()
    offset_frag = 0.0
    try:
        dur_total = float(sf.info(ruta_abs).duration)
        if dur_total > ANALISIS_LISTA_SEG_FRAGMENTO * 1.3:
            offset_frag = (dur_total - ANALISIS_LISTA_SEG_FRAGMENTO) / 2.0
    except Exception:
        pass
    y, sr = librosa.load(ruta_abs, sr=ANALISIS_LISTA_SR, mono=True,
                         offset=offset_frag, duration=ANALISIS_LISTA_SEG_FRAGMENTO)
    t_load = time.monotonic() - t0
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
    bpm = _corregir_media_o_doble_tempo(bpm)
    t_beat = time.monotonic() - t0 - t_load
    resultado_tono = detectar_tono(y, sr)
    t_tono = time.monotonic() - t0 - t_load - t_beat
    if len(beat_frames) > 0:
        beat_times_full = librosa.frames_to_time(beat_frames, sr=sr)
        fase_downbeat, _ = detectar_downbeat(y, sr, beat_times_full)
    else:
        fase_downbeat = 0
    energia = float(np.sqrt(np.mean(np.square(y)))) if y.size else 0.0
    return {
        "bpm": bpm,
        "energia": energia,
        "tono": resultado_tono["tono"],
        "tono_nombre": resultado_tono["tono_nombre"],
        "fase_downbeat": int(fase_downbeat),
        "log": (f"total={time.monotonic() - t0:.2f}s (carga={t_load:.2f}s "
                f"beat={t_beat:.2f}s tono={t_tono:.2f}s) bpm={bpm:.1f}"),
    }


def _vigilar_proceso_padre(pid_padre: int) -> None:
    """Cierra este proceso de análisis si el programa principal desaparece (se cerró mal, se
    colgó, lo mataron): así nunca quedan procesos huérfanos gastando CPU."""
    import threading

    def _padre_vivo() -> bool:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k32.OpenProcess(0x00100000, 0, pid_padre)       # SYNCHRONIZE
            if not h:
                return False
            try:
                return k32.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT: sigue vivo
            finally:
                k32.CloseHandle(h)
        return os.getppid() == pid_padre

    def _bucle():
        while True:
            time.sleep(2.0)
            try:
                if not _padre_vivo():
                    os._exit(0)
            except Exception:
                pass

    threading.Thread(target=_bucle, daemon=True, name="VigilaPadre").start()


def iniciar_trabajador(pid_padre=None) -> None:
    """Se ejecuta una vez al arrancar cada proceso de análisis: lo pone en prioridad baja y
    (en Windows con 4+ núcleos) le deja libres los dos últimos núcleos al audio y la interfaz."""
    if pid_padre:
        try:
            _vigilar_proceso_padre(int(pid_padre))
        except Exception:
            pass
    try:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            h = k32.GetCurrentProcess()
            k32.SetPriorityClass(h, 0x00004000)            # BELOW_NORMAL_PRIORITY_CLASS
            n = os.cpu_count() or 1
            if 4 <= n <= 64:
                k32.SetProcessAffinityMask(h, (1 << (n - 2)) - 1)
        else:
            os.nice(8)
    except Exception:
        pass

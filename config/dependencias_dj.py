"""
config/dependencias_dj.py
--------------------------
Lista ÚNICA de las dependencias de Fusion Play_Mix (reproductor DJ + Ecualizador DJ
integrado), que usa dj_player_Mixer.py para la verificación/instalación automática al
arrancar la app (ver _cargar_lista_dependencias). Si por lo que sea este archivo no está
o no se puede leer, dj_player_Mixer.py tiene su propia copia de respaldo -- pero esta es
la que manda apenas existe.

Cada entrada es (nombre_para_pip, nombre_para_import).

Notas de esta versión fusionada:
  * Ya NO se usa pygame: el audio sale por setup/bus_audio.py (sounddevice).
  * pedalboard es el motor de efectos del ecualizador integrado.
  * No hace falta VB-Cable ni ningún driver de cable virtual.
"""

DEPENDENCIAS = [
    ("PySide6", "PySide6"),
    ("numpy", "numpy"),
    ("soundfile", "soundfile"),
    ("librosa", "librosa"),
    ("pedalboard", "pedalboard"),
    ("scipy", "scipy"),
    ("sounddevice", "sounddevice"),
    ("audiotsm", "audiotsm"),
]

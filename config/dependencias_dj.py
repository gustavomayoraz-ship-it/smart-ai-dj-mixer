"""
config/dependencias_dj.py
--------------------------
Lista ÚNICA de las dependencias del Smart AI DJ Player, que usa
dj_player_Mixer.py para la verificación/instalación automática al
arrancar la app (ver _cargar_lista_dependencias). Si por lo que sea
este archivo no está o no se puede leer, dj_player_Mixer.py tiene su
propia copia de respaldo -- pero esta es la que manda apenas existe,
así no hay que tocar dos lugares por separado cada vez que cambia una
dependencia.

Cada entrada es (nombre_para_pip, nombre_para_import): el nombre que se le
pasa a pip para instalar puede no ser igual al nombre con el que se
importa en el código -- caso de pygame-ce, que se instala como
"pygame-ce" pero se sigue importando como "import pygame" tal cual (es un
fork 100% compatible de pygame, mantenido por la comunidad, que sí trae
instaladores para versiones de Python nuevas como la 3.14, algo que el
pygame "oficial" todavía no tiene -- ver:
https://github.com/pygame/pygame/issues/4627). Si en algún momento el
pygame oficial vuelve a tener soporte al día con las versiones de Python
más nuevas, alcanza con volver a poner ("pygame", "pygame") acá, en un
solo lugar.
"""

DEPENDENCIAS = [
    ("PySide6", "PySide6"),
    ("numpy", "numpy"),
    ("soundfile", "soundfile"),
    ("librosa", "librosa"),
    ("pygame-ce", "pygame"),
    ("scipy", "scipy"),
    ("sounddevice", "sounddevice"),
    ("audiotsm", "audiotsm"),
]

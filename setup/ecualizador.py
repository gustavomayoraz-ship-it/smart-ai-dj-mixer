"""
setup/ecualizador.py
--------------------
Ecualizador DJ integrado en el reproductor (ya no hace falta ningún cable virtual).

    canales A/B del reproductor -> bus interno (setup/bus_audio.py)
        -> MotorEfectos (este archivo) -> parlantes

Qué hace:
  * Ecualizador automático de 9 bandas, ajuste manual de graves / presencia / agudos,
    compresor suave, multibanda automática de 3 bandas y limitador final.
  * Realce: golpe de bombo a 43 Hz, nitidez y nivelador de volumen.
  * Modo "Inteligente": perfil objetivo (se puede capturar de un tema que guste) y
    corrección gradual y con tope; el cambio de tema se detecta solo.
  * Espectro en vivo: entrada, salida y objetivo.

Uso desde el reproductor:
    ecualizador.preparar(bus)                      # al arrancar: crea el motor y lo engancha al bus
    ecualizador.crear_ventana(bus, config, app, padre)   # ventana (botón del reproductor)

Basado en Ecualizador-DJ (ecualizador_dj.py); acá se quitaron el instalador de
librerías, VB-Cable y la captura de audio (todo eso ya no hace falta).
"""
import os
import sys
import json
import math
import threading
import time

if getattr(sys, "frozen", False):
    CARPETA_BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    CARPETA_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA_CONFIG = os.path.join(CARPETA_BASE, "config")
try:
    os.makedirs(CARPETA_CONFIG, exist_ok=True)
except OSError:
    pass

import numpy as np

BLOQUE = 1024
CANALES = 2
LATENCIA_SEG = 0.05
COLCHON_MAX_SEG = 0.40
MAX_COLA_BLOQUES = 25
PREBUFFER_SEG = 0.12
CONFIG_ARCHIVO = os.path.join(CARPETA_CONFIG, "ecualizador_dj_config.json")

CONFIG_DEFECTO = {
    "dispositivo_entrada": "",
    "dispositivo_salida": "",
    "bypass": False,
    "inteligente": True,
    "idioma": "es",
    "fuerza_inteligente": 60,
    "objetivo_bandas": [0.0, 1.0, 3.0, 3.0, 1.5, 0.0, -2.0, -4.0, -7.0],
    "limite_inteligente_db": 6,
    "velocidad_db_s": 10,
    "reinicio_auto": True,
    "anti_saturacion": False,
    "graves_db": 0.0,
    "presencia_db": 0.0,
    "agudos_db": 0.0,
    "compresion": 1,
    "mb_activo": True,
    "mb_auto": True,
    "mb_intensidad": 60,
    "mb_corte_bajos": 200,
    "mb_corte_altos": 2000,
    "mb_graves": [-18.0, 2.5, 20.0, 150.0, 0.0],
    "mb_medios": [-16.0, 2.0, 10.0, 100.0, 0.0],
    "mb_agudos": [-14.0, 1.8,  5.0,  80.0, 0.0],
    "volumen": 100,
    "golpe_43": 70,
    "nitidez": 50,
    "nivelar_temas": False,
    "igualar_bypass": True,
    "eq_activo": True,
}

COMPENSA_LIMITADOR_DB = -4.75
BANDAS_HZ = [43, 63, 125, 250, 500, 1000, 2000, 4000, 8000]
NOMBRES_BANDAS = ["43", "63", "125", "250", "500", "1k", "2k", "4k", "8k"]
PERFILES = {
    "Natural": [0.0, 1.0, 3.0, 3.0, 1.5, 0.0, -2.0, -4.0, -7.0],
    "Brillante": [-0.5, 0.0, 1.5, 1.5, 0.5, 0.0, -1.0, -2.0, -3.0],
    "Cálido": [1.0, 2.0, 4.0, 4.0, 2.0, 0.0, -3.0, -6.0, -10.0],
}

IDIOMA_POR_DEFECTO = "es"
IDIOMAS_DISPONIBLES = {"es": "Español", "en": "English", "pt": "Português"}

_FILAS = [
    ("ventana_titulo", "Ecualizador DJ", "DJ Equalizer", "Equalizador DJ"),
    ("btn_conectar", "Conectar Cable", "Connect Cable", "Conectar Cabo"),
    ("btn_desconectar", "Desconectar Cable", "Disconnect Cable", "Desconectar Cabo"),
    ("tt_reposicionar",
     "Reposicionar: lleva esta ventana a la esquina inferior izquierda, encima de la barra de tareas, esté donde esté.",
     "Reposition: moves this window to the bottom-left corner, just above the taskbar, wherever it is.",
     "Reposicionar: leva esta janela para o canto inferior esquerdo, logo acima da barra de tarefas, esteja onde estiver."),
    ("tt_ajustes", "Ajustes (idioma)", "Settings (language)", "Configurações (idioma)"),
    ("cable_info",
     "Pone el cable virtual como salida de Windows y manda el sonido a tus parlantes.",
     "Sets the virtual cable as the Windows output and sends the sound to your speakers.",
     "Define o cabo virtual como saída do Windows e envia o som para as suas caixas de som."),
    ("cable_solo_windows",
     "La conexión automática solo funciona en Windows.",
     "Automatic connection only works on Windows.",
     "A conexão automática só funciona no Windows."),
    ("espectro_sin_audio", "Sin audio todavía", "No audio yet", "Sem áudio ainda"),
    ("espectro_desconectado", "Desconectado", "Disconnected", "Desconectado"),
    ("leg_salida", "SALIDA", "OUTPUT", "SAÍDA"),
    ("leg_entrada", "ENTRADA", "INPUT", "ENTRADA"),
    ("leg_objetivo", "OBJETIVO", "TARGET", "ALVO"),
    ("chk_inteligente", "Inteligente", "Smart", "Inteligente"),
    ("chk_bypass", "Bypass", "Bypass", "Bypass"),
    ("chk_reinicio", "Reiniciar análisis en cada tema", "Restart analysis on every track",
     "Reiniciar análise a cada faixa"),
    ("vol", "vol", "vol", "vol"),
    ("tab_objetivo", "Objetivo", "Target", "Alvo"),
    ("tab_manual", "Manual", "Manual", "Manual"),
    ("tab_multibanda", "Multibanda", "Multiband", "Multibanda"),
    ("tab_avanzado", "Avanzado", "Advanced", "Avançado"),
    ("tab_realce", "Realce", "Boost", "Realce"),
    ("re_golpe", "Golpe 43 Hz", "43 Hz punch", "Impacto 43 Hz"),
    ("tt_re_golpe",
     "Hace que el bombo y el bajo (zona de 43 Hz) sobresalgan: suma un empujón fijo, un empujón extra en el ataque de cada golpe y armónicos suaves del bajo para que también se sienta en parlantes chicos. 0 % = apagado.",
     "Makes the kick and bass (around 43 Hz) stand out: adds a fixed boost, an extra boost on the attack of every hit and soft bass harmonics so it is also felt on small speakers. 0 % = off.",
     "Faz o bumbo e o baixo (região de 43 Hz) se destacarem: soma um reforço fixo, um reforço extra no ataque de cada batida e harmônicos suaves do baixo para que também se sinta em caixas pequenas. 0 % = desligado."),
    ("re_nitidez", "Nitidez", "Clarity", "Nitidez"),
    ("tt_re_nitidez",
     "Da aire y definición: agrega armónicos finos en los agudos y limpia el 'barro' de unos 300 Hz. 0 % = apagado.",
     "Adds air and definition: adds fine harmonics to the treble and cleans the 'mud' around 300 Hz. 0 % = off.",
     "Dá ar e definição: adiciona harmônicos finos nos agudos e limpa o 'barro' em torno de 300 Hz. 0 % = desligado."),
    ("chk_nivelador", "Nivelar volumen entre temas", "Level volume between tracks", "Nivelar volume entre faixas"),
    ("chk_igualar", "Igualar volumen con Bypass", "Match volume with Bypass", "Igualar volume com Bypass"),
    ("tt_igualar",
     "Mantiene el volumen del sonido procesado igual al original, así al activar o desactivar Bypass solo cambia el timbre, no el volumen. Mide la sonoridad (casi sin contar el subgrave) y ajusta la ganancia despacio, hasta ±12 dB.",
     "Keeps the processed sound at the same loudness as the original, so toggling Bypass only changes the tone, not the volume. It measures loudness (almost ignoring sub-bass) and adjusts the gain slowly, up to ±12 dB.",
     "Mantém o som processado com a mesma sonoridade do original, assim ao ativar ou desativar o Bypass só muda o timbre, não o volume. Mede a sonoridade (quase sem contar o subgrave) e ajusta o ganho devagar, até ±12 dB."),
    ("tt_nivelador",
     "Mide la sonoridad en unos segundos y mueve la ganancia despacio (hasta +4 dB / -6 dB) hacia un volumen fijo para que los temas suenen parejos entre sí. Ojo: cambia el volumen respecto de Bypass; si querés comparar con y sin efecto, dejalo apagado.",
     "Measures loudness over a few seconds and moves the gain slowly (up to +4 dB / -6 dB) so tracks sound even. Does not touch silence. Note: it changes the volume compared with Bypass; to compare with and without effects, leave it off.",
     "Mede a sonoridade em alguns segundos e move o ganho devagar (até +4 dB / -6 dB) para que as faixas soem parelhas. Não mexe no silêncio. Atenção: muda o volume em relação ao Bypass; para comparar com e sem efeito, deixe desligado."),
    ("re_nota",
     "Se aplica al final de la cadena, antes del limitador final · el bajo se refuerza en paralelo (28-100 Hz)",
     "Applied at the end of the chain, before the final limiter · bass is reinforced in parallel (28-100 Hz)",
     "Aplicado no fim da cadeia, antes do limitador final · o baixo é reforçado em paralelo (28-100 Hz)"),
    ("obj_ayuda",
     "Cómo querés que suene cada franja. El programa corrige cada tema para acercarlo a este perfil (línea ámbar del gráfico).",
     "How you want each band to sound. The program corrects each track to bring it closer to this profile (amber line on the chart).",
     "Como você quer que cada faixa de frequência soe. O programa corrige cada faixa para aproximá-la deste perfil (linha âmbar do gráfico)."),
    ("perfil_Natural", "Natural", "Natural", "Natural"),
    ("perfil_Brillante", "Brillante", "Bright", "Brilhante"),
    ("perfil_Cálido", "Cálido", "Warm", "Quente"),
    ("btn_capturar", "Capturar", "Capture", "Capturar"),
    ("tt_capturar",
     "Toma el sonido del tema que está sonando como perfil objetivo",
     "Takes the sound of the track that is playing as the target profile",
     "Usa o som da faixa que está tocando como perfil alvo"),
    ("tt_43hz",
     "43 Hz: rango ±20 dB, para darle golpe / sub-grave al tema",
     "43 Hz: ±20 dB range, to give the track punch / sub-bass",
     "43 Hz: faixa de ±20 dB, para dar impacto / sub-grave à faixa"),
    ("cap_hace_falta",
     "Para capturar tiene que estar sonando música (unos 2 segundos).",
     "To capture, music has to be playing (about 2 seconds).",
     "Para capturar, a música precisa estar tocando (cerca de 2 segundos)."),
    ("cap_ok",
     "Perfil capturado del tema que suena. Se ve en la línea ámbar.",
     "Profile captured from the current track. It shows as the amber line.",
     "Perfil capturado da faixa que está tocando. Aparece na linha âmbar."),
    ("man_graves", "Graves", "Bass", "Graves"),
    ("man_presencia", "Presencia", "Presence", "Presença"),
    ("man_agudos", "Agudos", "Treble", "Agudos"),
    ("man_compresion", "Compresión", "Compression", "Compressão"),
    ("comp_no", "no", "off", "não"),
    ("chk_anti", "Anti-saturación inteligente", "Smart anti-saturation", "Anti-saturação inteligente"),
    ("tt_anti",
     "Graves / presencia / agudos pasan a ser el ajuste que querés, pero el programa lo modera solo: baja las subidas si la salida se pega al limitador o si el tema ya trae de sobra esa zona.",
     "Bass / presence / treble become the adjustment you want, but the program moderates it by itself: it lowers the boosts if the output hits the limiter or if the track already has plenty in that range.",
     "Graves / presença / agudos passam a ser o ajuste que você quer, mas o programa o modera sozinho: reduz os aumentos se a saída encosta no limitador ou se a faixa já tem de sobra essa região."),
    ("anti_aplicado", "Ajuste aplicado: {p} %", "Applied adjustment: {p} %", "Ajuste aplicado: {p} %"),
    ("anti_moderado", " (moderado para no saturar)", " (moderated to avoid saturation)",
     " (moderado para não saturar)"),
    ("anti_sin_limites", " (sin límites)", " (no limits)", " (sem limites)"),
    ("man_nota",
     "Se suma a la corrección automática · 120 Hz / 3 kHz / 6,5 kHz",
     "Added to the automatic correction · 120 Hz / 3 kHz / 6.5 kHz",
     "Somado à correção automática · 120 Hz / 3 kHz / 6,5 kHz"),
    ("mb_activar", "Activar multibanda (después de la de banda ancha)",
     "Enable multiband (after wideband)", "Ativar multibanda (após a de banda larga)"),
    ("tt_mb_activar",
     "Divide el sonido en 3 bandas (graves / medios / agudos) y comprime cada una por "
     "separado, después de la compresión de banda ancha. Sirve para controlar mejor los "
     "golpes de graves sin que apaguen los agudos, o al revés. La anti-saturación "
     "inteligente ajusta los umbrales sola según el tema.",
     "Splits the sound into 3 bands (bass / mids / treble) and compresses each one "
     "separately, after the wideband compression. Useful to control bass hits without "
     "dulling the treble, or the other way around. Smart anti-saturation adjusts the "
     "thresholds by itself depending on the track.",
     "Divide o som em 3 bandas (graves / médios / agudos) e comprime cada uma "
     "separadamente, depois da compressão de banda larga. Serve para controlar melhor "
     "os golpes de graves sem apagar os agudos, ou o contrário. A anti-saturação "
     "inteligente ajusta os limiares sozinha conforme a faixa."),
    ("mb_corte_bajos", "Corte graves/medios", "Bass/mid split", "Corte graves/médios"),
    ("mb_corte_altos", "Corte medios/agudos", "Mid/treble split", "Corte médios/agudos"),
    ("tt_mb_corte_bajos",
     "Frecuencia que divide graves y medios.\n\n"
     "• 80–120 Hz: la banda de graves queda angosta (solo el sub-grave más profundo). Ideal para "
     "controlar solo el \"pum\" del bombo sin tocar el cuerpo del bajo.\n"
     "• 150–250 Hz: valor normal. Separa bien el golpe del bombo (abajo) del cuerpo del bajo y "
     "la voz masculina (arriba).\n"
     "• 300–500 Hz: la banda de graves se hace ancha. Útil en música con mucho sub-bajo "
     "(electrónica, hip-hop), pero cuidado: la voz del locutor y algunos instrumentos caen acá.\n\n"
     "Regla: si el bajo suena \"gordo pero sin golpe\" → bajá el corte. Si la voz suena \"apagada\" "
     "→ subilo.",
     "Frequency that splits bass and mids.\n\n"
     "• 80–120 Hz: narrow bass band (only the deepest sub-bass). Ideal to control just the kick "
     "\"thump\" without touching the body of the bass.\n"
     "• 150–250 Hz: normal value. Cleanly separates the kick (below) from the bass body and "
     "male vocals (above).\n"
     "• 300–500 Hz: wide bass band. Useful in bass-heavy music (electronic, hip-hop), but "
     "careful: vocals and some instruments fall here.\n\n"
     "Rule: if the bass sounds \"fat but without punch\" → lower the split. If vocals sound "
     "\"dull\" → raise it.",
     "Frequência que divide graves e médios.\n\n"
     "• 80–120 Hz: banda de graves estreita (só o sub-grave mais profundo). Ideal para "
     "controlar só o \"pum\" do bumbo sem tocar o corpo do baixo.\n"
     "• 150–250 Hz: valor normal. Separa bem o golpe do bumbo (abaixo) do corpo do baixo e "
     "da voz masculina (acima).\n"
     "• 300–500 Hz: a banda de graves fica larga. Útil em música com muito sub-baixo "
     "(eletrônica, hip-hop), mas cuidado: a voz do locutor e alguns instrumentos caem aqui.\n\n"
     "Regra: se o baixo soar \"gordo mas sem golpe\" → abaixe o corte. Se a voz soar "
     "\"abafada\" → suba."),
    ("tt_mb_corte_altos",
     "Frecuencia que divide medios y agudos.\n\n"
     "• 1000–1500 Hz: la banda de agudos queda muy ancha. Útil si querés comprimir casi todo lo "
     "que no es graves (voces, guitarras, platos).\n"
     "• 1800–2500 Hz: valor normal. Separa la zona de presencia de la voz y guitarras (abajo) "
     "del aire y brillo de los platos (arriba).\n"
     "• 3000–5000 Hz: la banda de agudos se hace angosta (solo el \"aire\" y el brillo). Útil "
     "para domar solo las \"eses\" y los platos sin tocar la voz.\n\n"
     "Regla: si querés domar las \"eses\" y platos → subí el corte. Si querés comprimir la voz "
     "y guitarras enteras → bajalo.",
     "Frequency that splits mids and treble.\n\n"
     "• 1000–1500 Hz: wide treble band. Useful if you want to compress almost everything that "
     "isn't bass (vocals, guitars, cymbals).\n"
     "• 1800–2500 Hz: normal value. Separates the vocal and guitar presence range (below) from "
     "the cymbal air and brightness (above).\n"
     "• 3000–5000 Hz: narrow treble band (only \"air\" and brightness). Useful to tame only the "
     "\"s\" sounds and cymbals without touching the vocals.\n\n"
     "Rule: to tame \"s\" sounds and cymbals → raise the split. To compress vocals and guitars "
     "entirely → lower it.",
     "Frequência que divide médios e agudos.\n\n"
     "• 1000–1500 Hz: a banda de agudos fica muito larga. Útil se quiser comprimir quase tudo o "
     "que não é graves (vozes, guitarras, pratos).\n"
     "• 1800–2500 Hz: valor normal. Separa a zona de presença da voz e guitarras (abaixo) do ar "
     "e brilho dos pratos (acima).\n"
     "• 3000–5000 Hz: a banda de agudos fica estreita (só o \"ar\" e o brilho). Útil para domar "
     "só os \"esses\" e pratos sem tocar a voz.\n\n"
     "Regra: para domar os \"esses\" e pratos → suba o corte. Para comprimir a voz e guitarras "
     "inteiras → abaixe."),
    ("mb_banda_graves", "Graves", "Bass", "Graves"),
    ("mb_banda_medios", "Medios", "Mids", "Médios"),
    ("mb_banda_agudos", "Agudos", "Treble", "Agudos"),
    ("mb_umbral", "Umbral", "Threshold", "Limiar"),
    ("mb_ratio", "Ratio", "Ratio", "Ratio"),
    ("mb_attack", "Ataque", "Attack", "Ataque"),
    ("mb_release", "Liberación", "Release", "Liberação"),
    ("mb_makeup", "Makeup", "Makeup", "Makeup"),
    ("tt_mb_umbral",
     "Nivel a partir del cual la banda empieza a comprimirse. Por encima de este valor, "
     "el compresor baja la señal.\n\n"
     "• −10 a −14 dB: comprime casi todo (sonido más parejo y \"pegado\", pero menos vivo).\n"
     "• −16 a −20 dB: equilibrio (valor normal, deja pasar los golpes fuertes).\n"
     "• −22 a −30 dB: solo actúa en los picos (más dinámica, ideal para graves con mucha pegada).\n\n"
     "Con la anti-saturación inteligente activada, el programa lo modula solo según el tema.",
     "Level at which the band starts to compress. Above this value, the compressor "
     "reduces the signal.\n\n"
     "• −10 to −14 dB: compresses almost everything (more even, \"glued\" sound, but less lively).\n"
     "• −16 to −20 dB: balanced (normal value, lets strong hits through).\n"
     "• −22 to −30 dB: acts only on peaks (more dynamics, ideal for punchy bass).\n\n"
     "With smart anti-saturation on, the program modulates it by itself depending on the track.",
     "Nível a partir do qual a banda começa a comprimir. Acima deste valor, o compressor "
     "reduz o sinal.\n\n"
     "• −10 a −14 dB: comprime quase tudo (som mais uniforme e \"colado\", mas menos vivo).\n"
     "• −16 a −20 dB: equilíbrio (valor normal, deixa passar os golpes fortes).\n"
     "• −22 a −30 dB: atua só nos picos (mais dinâmica, ideal para graves com muita pegada).\n\n"
     "Com a anti-saturação inteligente ativada, o programa o modula sozinho conforme a faixa."),
    ("tt_mb_ratio",
     "Cuánto se reduce la señal por encima del umbral.\n\n"
     "• 1:1: no comprime (la banda suena igual).\n"
     "• 1,5:1 a 2:1: compresión suave, mantiene la vida del tema (valor normal).\n"
     "• 2,5:1 a 3,5:1: compresión marcada, muy útil en graves para que no tapen el resto.\n"
     "• 4:1 a 6:1: compresión fuerte, controla mucho los picos pero puede sonar aplastado.\n\n"
     "Regla rápida: graves 2,5–3,5:1 · medios 1,8–2,5:1 · agudos 1,5–2:1.",
     "How much the signal is reduced above the threshold.\n\n"
     "• 1:1: no compression (band sounds the same).\n"
     "• 1.5:1 to 2:1: gentle compression, keeps the track alive (normal value).\n"
     "• 2.5:1 to 3.5:1: marked compression, very useful on bass to keep it from masking the rest.\n"
     "• 4:1 to 6:1: strong compression, controls peaks but may sound squashed.\n\n"
     "Quick rule: bass 2.5–3.5:1 · mids 1.8–2.5:1 · treble 1.5–2:1.",
     "Quanto o sinal é reduzido acima do limiar.\n\n"
     "• 1:1: sem compressão (a banda soa igual).\n"
     "• 1,5:1 a 2:1: compressão suave, mantém a vida da faixa (valor normal).\n"
     "• 2,5:1 a 3,5:1: compressão marcada, muito útil nos graves para não cobrir o resto.\n"
     "• 4:1 a 6:1: compressão forte, controla os picos mas pode soar esmagado.\n\n"
     "Regra rápida: graves 2,5–3,5:1 · médios 1,8–2,5:1 · agudos 1,5–2:1."),
    ("tt_mb_attack",
     "Cuánto tarda el compresor en reaccionar cuando el sonido pasa el umbral.\n\n"
     "• 1–5 ms: ataca muy rápido, ideal en agudos para domar \"eses\" y platos brillantes.\n"
     "• 10–20 ms: valor normal en medios, deja pasar el ataque del instrumento.\n"
     "• 20–50 ms: deja pasar el \"pum\" del bombo y el golpe del bajo (valor típico en graves).\n"
     "• 50–200 ms: solo limita sonidos sostenidos, deja pasar todo el golpe (más natural).\n\n"
     "Si suena \"flojo\" o sin pegada → subí el attack. Si suena \"duro\" → bajalo.",
     "How long the compressor takes to react when the sound goes above the threshold.\n\n"
     "• 1–5 ms: very fast attack, ideal on treble to tame harsh \"s\" sounds and bright cymbals.\n"
     "• 10–20 ms: normal value on mids, lets the instrument attack through.\n"
     "• 20–50 ms: lets the kick and bass punch through (typical value on bass).\n"
     "• 50–200 ms: only limits sustained sounds, lets the whole hit through (more natural).\n\n"
     "If it sounds \"weak\" or without punch → raise the attack. If it sounds \"hard\" → lower it.",
     "Quanto o compressor demora para reagir quando o som passa o limiar.\n\n"
     "• 1–5 ms: ataque muito rápido, ideal nos agudos para domar \"esses\" e pratos brilhantes.\n"
     "• 10–20 ms: valor normal nos médios, deixa passar o ataque do instrumento.\n"
     "• 20–50 ms: deixa passar o \"pum\" do bumbo e o golpe do baixo (valor típico nos graves).\n"
     "• 50–200 ms: só limita sons sustentados, deixa passar todo o golpe (mais natural).\n\n"
     "Se soar \"fraco\" ou sem pegada → aumente o ataque. Se soar \"duro\" → diminua."),
    ("tt_mb_release",
     "Cuánto tarda el compresor en \"soltar\" la señal cuando ya bajó del umbral.\n\n"
     "• 30–80 ms: recupera rápido, sonido compacto y \"pegado\" (ideal en agudos y medios).\n"
     "• 80–150 ms: valor normal, natural y sin bombeo.\n"
     "• 150–300 ms: recupera lento, muy natural en graves (deja respirar el bajo).\n"
     "• 300–800 ms: recuperación muy lenta, sonido \"flotante\". Útil solo en graves con poca dinámica.\n\n"
     "Si escuchás que el volumen \"respira\" o \"bombea\" → subí el release. "
     "Si el sonido queda aplastado mucho tiempo → bajalo.",
     "How long the compressor takes to \"release\" the signal once it drops below the threshold.\n\n"
     "• 30–80 ms: recovers fast, tight and \"glued\" sound (ideal on treble and mids).\n"
     "• 80–150 ms: normal value, natural and without pumping.\n"
     "• 150–300 ms: recovers slowly, very natural on bass (lets the bass breathe).\n"
     "• 300–800 ms: very slow release, \"floating\" sound. Only useful on low-dynamic bass.\n\n"
     "If you hear the volume \"breathing\" or \"pumping\" → raise the release. "
     "If the sound stays squashed too long → lower it.",
     "Quanto o compressor demora para \"soltar\" o sinal quando ele cai abaixo do limiar.\n\n"
     "• 30–80 ms: recupera rápido, som compacto e \"colado\" (ideal nos agudos e médios).\n"
     "• 80–150 ms: valor normal, natural e sem bombeamento.\n"
     "• 150–300 ms: recupera devagar, muito natural nos graves (deixa o baixo respirar).\n"
     "• 300–800 ms: recuperação muito lenta, som \"flutuante\". Útil só em graves com pouca dinâmica.\n\n"
     "Se você ouvir o volume \"respirando\" ou \"bombeando\" → aumente o release. "
     "Se o som ficar esmagado por muito tempo → diminua."),
    ("tt_mb_makeup",
     "Ganancia que se suma al final de la banda para compensar lo que el compresor quitó.\n\n"
     "• 0 dB: no compensa nada (la salida queda más baja que la entrada).\n"
     "• +2 a +4 dB: compensación típica si el umbral es −16 a −20 dB y el ratio 2:1 a 3:1.\n"
     "• +5 a +8 dB: para umbrales bajos (−22 a −30 dB) o ratios altos (4:1 o más).\n"
     "• Negativo (−2 a −6 dB): si activaste la multibanda y notás que la salida sube de más.\n\n"
     "Regla: subí el makeup hasta que el nivel (out XX dB en el diagnóstico) sea casi igual "
     "con la multibanda activada y desactivada. Si no lo igualás, el limitador final va a "
     "trabajar de más y sonará aplastado.",
     "Gain added at the end of the band to compensate what the compressor removed.\n\n"
     "• 0 dB: no compensation (output ends up lower than input).\n"
     "• +2 to +4 dB: typical compensation if the threshold is −16 to −20 dB and ratio 2:1 to 3:1.\n"
     "• +5 to +8 dB: for low thresholds (−22 to −30 dB) or high ratios (4:1 or more).\n"
     "• Negative (−2 to −6 dB): if you enabled multiband and notice the output rises too much.\n\n"
     "Rule: raise the makeup until the level (out XX dB in the diagnostics) is almost the same "
     "with multiband on and off. If you don't match it, the final limiter will work too hard "
     "and sound squashed.",
     "Ganho somado no fim da banda para compensar o que o compressor tirou.\n\n"
     "• 0 dB: sem compensação (a saída fica mais baixa que a entrada).\n"
     "• +2 a +4 dB: compensação típica se o limiar é −16 a −20 dB e o ratio 2:1 a 3:1.\n"
     "• +5 a +8 dB: para limiares baixos (−22 a −30 dB) ou ratios altos (4:1 ou mais).\n"
     "• Negativo (−2 a −6 dB): se ativou a multibanda e nota que a saída sobe demais.\n\n"
     "Regra: aumente o makeup até o nível (out XX dB no diagnóstico) ficar quase igual "
     "com a multibanda ativada e desativada. Se não igualar, o limitador final vai "
     "trabalhar demais e soar esmagado."),
    ("mb_reset", "Restablecer valores por defecto", "Reset to defaults",
     "Restaurar valores padrão"),
    ("tt_mb_reset",
     "Vuelve las 3 bandas (umbral, ratio, ataque, liberación, makeup) y las frecuencias "
     "de corte a los valores de fábrica. No apaga la multibanda ni la saca: solo deja todo "
     "como al principio para que puedas empezar de nuevo.",
     "Returns the 3 bands (threshold, ratio, attack, release, makeup) and the split "
     "frequencies to the factory values. It does not turn off multiband or remove it: it "
     "just leaves everything as at the beginning so you can start over.",
     "Devolve as 3 bandas (limiar, ratio, ataque, liberação, makeup) e as frequências de "
     "corte aos valores de fábrica. Não desliga a multibanda nem a remove: só deixa tudo "
     "como no começo para você recomeçar."),
    ("mb_auto", "Automática (recomendado)", "Automatic (recommended)", "Automática (recomendado)"),
    ("tt_mb_auto",
     "El programa mide cuánta energía trae cada banda (graves / medios / agudos) y ajusta solo umbral, ratio, ataque, liberación y ganancia de compensación. Los graves se tratan con más suavidad y ataque lento para no apagar el golpe del bombo. Si lo desactivás, aparecen los controles manuales.",
     "The program measures how much energy each band (bass / mids / highs) carries and sets threshold, ratio, attack, release and make-up gain by itself. Bass is treated more gently with a slow attack so the kick's punch is not dulled. If you turn it off, the manual controls appear.",
     "O programa mede quanta energia cada banda (graves / médios / agudos) traz e ajusta sozinho limiar, ratio, ataque, liberação e ganho de compensação. Os graves são tratados com mais suavidade e ataque lento para não apagar o impacto do bumbo. Se você desativar, os controles manuais aparecem."),
    ("mb_intensidad", "Intensidad", "Amount", "Intensidade"),
    ("tt_mb_intensidad",
     "Cuánto comprime la multibanda automática. 0 % no comprime nada; 100 % controla más fuerte los excesos de cada banda.",
     "How much the automatic multiband compresses. 0 % compresses nothing; 100 % controls the excesses of each band more strongly.",
     "Quanto a multibanda automática comprime. 0 % não comprime nada; 100 % controla com mais força os excessos de cada banda."),
    ("mb_auto_info",
     "Cruces automáticos: 150 Hz / 2,5 kHz · los valores de abajo se mueven solos, en vivo",
     "Automatic crossovers: 150 Hz / 2.5 kHz · the values below move by themselves, live",
     "Cruzamentos automáticos: 150 Hz / 2,5 kHz · os valores abaixo se movem sozinhos, ao vivo"),
    ("pa_nivel", "Nivel", "Level", "Nível"),
    ("pa_umbral", "Umbral", "Threshold", "Limiar"),
    ("pa_ratio", "Ratio", "Ratio", "Ratio"),
    ("pa_reduccion", "Reducción", "Reduction", "Redução"),
    ("pa_compensa", "Compensación", "Make-up", "Compensação"),
    ("tt_pa_nivel", "Cuánta energía trae esta banda ahora (nivel medio de unos segundos).",
     "How much energy this band carries now (average level over a few seconds).",
     "Quanta energia esta banda traz agora (nível médio de alguns segundos)."),
    ("tt_pa_umbral", "Umbral que eligió el programa para esta banda. Se mueve con el nivel del tema.",
     "Threshold the program chose for this band. It follows the track's level.",
     "Limiar que o programa escolheu para esta banda. Acompanha o nível da faixa."),
    ("tt_pa_ratio", "Cuánto comprime esta banda cuando pasa el umbral (depende de Intensidad).",
     "How much this band compresses above the threshold (depends on Amount).",
     "Quanto esta banda comprime acima do limiar (depende da Intensidade)."),
    ("tt_pa_reduccion", "Cuánto está bajando el volumen de esta banda en este instante (reducción de ganancia real).",
     "How much this band's volume is being turned down right now (actual gain reduction).",
     "Quanto o volume desta banda está sendo reduzido neste instante (redução de ganho real)."),
    ("tt_pa_compensa", "Ganancia que se suma para compensar lo que comprime.",
     "Gain added to compensate for what is compressed.",
     "Ganho somado para compensar o que é comprimido."),
    ("mb_nota",
     "La anti-saturación inteligente modula sola los umbrales. Los valores de arriba "
     "son el punto de partida.",
     "Smart anti-saturation modulates the thresholds by itself. The values above are "
     "the starting point.",
     "A anti-saturação inteligente modula sozinha os limiares. Os valores acima são "
     "o ponto de partida."),
    ("av_max_corr", "Máx. corrección", "Max. correction", "Correção máx."),
    ("av_velocidad", "Velocidad", "Speed", "Velocidade"),
    ("av_entrada", "Entrada", "Input", "Entrada"),
    ("av_salida", "Salida", "Output", "Saída"),
    ("btn_iniciar", "Iniciar (manual)", "Start (manual)", "Iniciar (manual)"),
    ("btn_detener", "Detener", "Stop", "Parar"),
    ("est_detenido", "Detenido", "Stopped", "Parado"),
    ("est_activo", "Activo a {sr} Hz", "Active at {sr} Hz", "Ativo a {sr} Hz"),
    ("est_elegi", "Elegí entrada y salida", "Choose input and output", "Escolha entrada e saída"),
    ("est_mismo",
     "La entrada y la salida no pueden ser el mismo dispositivo",
     "Input and output cannot be the same device",
     "A entrada e a saída não podem ser o mesmo dispositivo"),
    ("est_error_iniciar", "Error al iniciar: {e}", "Error starting: {e}", "Erro ao iniciar: {e}"),
    ("est_no_audio", "No se pudo leer el audio: {e}", "Could not read the audio devices: {e}",
     "Não foi possível ler o áudio: {e}"),
    ("cab_no_restaurar",
     "No pude restaurar la salida automáticamente ({e}). Elegila en Configuración > Sistema > Sonido.",
     "I couldn't restore the output automatically ({e}). Choose it in Settings > System > Sound.",
     "Não consegui restaurar a saída automaticamente ({e}). Escolha-a em Configurações > Sistema > Som."),
    ("cab_desconectado_a",
     "Desconectado. Tu salida de audio volvió a «{destino}».",
     "Disconnected. Your audio output went back to «{destino}».",
     "Desconectado. Sua saída de áudio voltou para «{destino}»."),
    ("cab_desconectado_antes",
     "Desconectado. Tu salida de audio volvió a la de antes.",
     "Disconnected. Your audio output went back to the previous one.",
     "Desconectado. Sua saída de áudio voltou para a anterior."),
    ("cab_desconectado_revisar",
     "Desconectado. Revisá que la salida de Windows sea la que usás.",
     "Disconnected. Check that the Windows output is the one you use.",
     "Desconectado. Verifique se a saída do Windows é a que você usa."),
    ("vb_titulo", "Instalar VB-Cable", "Install VB-Cable", "Instalar o VB-Cable"),
    ("vb_pregunta",
     "No encuentro el cable virtual (VB-Cable).\n\nVB-Cable es un driver gratuito (donationware) de VB-Audio Software: www.vb-cable.com. Si te resulta útil, podés donar a su autor en esa página.\n\nEl instalador original está en la carpeta del programa. ¿Lo instalo ahora?\nWindows te va a pedir permiso de administrador.",
     "I can't find the virtual cable (VB-Cable).\n\nVB-Cable is a free driver (donationware) by VB-Audio Software: www.vb-cable.com. If you find it useful, you can donate to its author on that page.\n\nThe original installer is in the program folder. Install it now?\nWindows will ask for administrator permission.",
     "Não encontro o cabo virtual (VB-Cable).\n\nO VB-Cable é um driver gratuito (donationware) da VB-Audio Software: www.vb-cable.com. Se for útil para você, pode doar ao autor nessa página.\n\nO instalador original está na pasta do programa. Instalar agora?\nO Windows vai pedir permissão de administrador."),
    ("vb_instalando",
     "Instalando VB-Cable… aceptá el permiso de Windows.",
     "Installing VB-Cable… accept the Windows permission prompt.",
     "Instalando o VB-Cable… aceite a permissão do Windows."),
    ("vb_instalado_sin_ver",
     "VB-Cable instalado, pero Windows todavía no lo muestra.",
     "VB-Cable installed, but Windows doesn't show it yet.",
     "VB-Cable instalado, mas o Windows ainda não o mostra."),
    ("vb_reiniciar",
     " Reiniciá la PC y volvé a apretar este botón.",
     " Restart the PC and press this button again.",
     " Reinicie o PC e aperte este botão de novo."),
    ("vb_listo_conectando", "VB-Cable instalado. Conectando…", "VB-Cable installed. Connecting…",
     "VB-Cable instalado. Conectando…"),
    ("vb_sin",
     "Sin VB-Cable no se puede conectar. Apretá de nuevo para instalarlo.",
     "Can't connect without VB-Cable. Press again to install it.",
     "Sem o VB-Cable não é possível conectar. Aperte de novo para instalá-lo."),
    ("vb_no_encuentro",
     "No encuentro VB-Cable. Abrí la página de descarga: instalalo como administrador, reiniciá la PC y volvé a apretar este botón.",
     "I can't find VB-Cable. Open the download page: install it as administrator, restart the PC and press this button again.",
     "Não encontro o VB-Cable. Abra a página de download: instale como administrador, reinicie o PC e aperte este botão de novo."),
    ("vb_sin_permiso",
     "No se dio el permiso de administrador (o Windows bloqueó el instalador).",
     "Administrator permission was not granted (or Windows blocked the installer).",
     "A permissão de administrador não foi concedida (ou o Windows bloqueou o instalador)."),
    ("vb_terminada", "Instalación terminada.", "Installation finished.", "Instalação concluída."),
    ("vb_tardo", "El instalador tardó demasiado.", "The installer took too long.",
     "O instalador demorou demais."),
    ("cab_sin_parlantes",
     "Cable conectado, pero no pude elegir tus parlantes: elegilos en Avanzado y apretá Iniciar.",
     "Cable connected, but I couldn't pick your speakers: choose them in Advanced and press Start.",
     "Cabo conectado, mas não consegui escolher suas caixas de som: escolha-as em Avançado e aperte Iniciar."),
    ("cab_conectado",
     "Conectado · sale por {destino}. Si el reproductor no suena, reabrilo.",
     "Connected · output goes to {destino}. If the player has no sound, reopen it.",
     "Conectado · saída por {destino}. Se o reprodutor não tocar, reabra-o."),
    ("cab_error", "No pude conectar el cable: {e}", "Couldn't connect the cable: {e}",
     "Não consegui conectar o cabo: {e}"),
    ("cab_sin_input",
     "Windows no muestra el dispositivo 'CABLE Input'. ¿Reiniciaste la PC después de instalar VB-Cable?",
     "Windows doesn't show the 'CABLE Input' device. Did you restart the PC after installing VB-Cable?",
     "O Windows não mostra o dispositivo 'CABLE Input'. Você reiniciou o PC depois de instalar o VB-Cable?"),
    ("vig_cable_no", "El cable virtual ya no está disponible.", "The virtual cable is no longer available.",
     "O cabo virtual não está mais disponível."),
    ("vig_audio",
     "El audio se detuvo (¿se desconectó la salida?).",
     "The audio stopped (was the output disconnected?).",
     "O áudio parou (a saída foi desconectada?)."),
    ("cab_restaurada_previa",
     "Se restauró tu salida de audio anterior (el programa se había cerrado sin desconectar).",
     "Your previous audio output was restored (the program had closed without disconnecting).",
     "Sua saída de áudio anterior foi restaurada (o programa tinha fechado sem desconectar)."),
    ("diag_colchon", "colchón", "buffer", "colchão"),
    ("diag_vac", "vac", "empty", "vaz"),
    ("diag_desb", "desb", "over", "exc"),
    ("diag_temas", "temas", "tracks", "faixas"),
    ("diag_tt",
     "in/out: nivel de pico · colchón: audio guardado entre entrada y salida\n"
     "vac/desb: veces que el colchón se vació o se desbordó (deben ser 0). Si hay cortes, el colchón (segundo número) crece solo\n"
     "proc: lo máximo que tardó en procesar un bloque (el límite es ~21 ms)\n"
     "xr: fallos que reporta Windows en entrada/salida (deben ser 0)\n"
     "temas: cambios de tema detectados",
     "in/out: peak level · buffer: audio stored between input and output\n"
     "empty/over: times the buffer ran empty or overflowed (should be 0). If there are dropouts, the buffer (second number) grows by itself\n"
     "proc: the longest it took to process a block (the limit is ~21 ms)\n"
     "xr: failures reported by Windows on input/output (should be 0)\n"
     "tracks: track changes detected",
     "in/out: nível de pico · colchão: áudio guardado entre entrada e saída\n"
     "vaz/exc: vezes que o colchão esvaziou ou transbordou (devem ser 0). Se houver cortes, o colchão (segundo número) cresce sozinho\n"
     "proc: o máximo que levou para processar um bloco (o limite é ~21 ms)\n"
     "xr: falhas informadas pelo Windows na entrada/saída (devem ser 0)\n"
     "faixas: mudanças de faixa detectadas"),
    ("aj_titulo", "Ajustes", "Settings", "Configurações"),
    ("aj_idioma_titulo", "🌐 Idioma", "🌐 Language", "🌐 Idioma"),
    ("aj_idioma_etiqueta", "Idioma:", "Language:", "Idioma:"),
    ("aj_idioma_nota", "El cambio se aplica al instante.", "The change applies immediately.",
     "A mudança é aplicada na hora."),
    ("aj_cerrar", "Cerrar", "Close", "Fechar"),
    ("ay_que", "Qué hace:", "What it does:", "O que faz:"),
    ("ay_cfg", "Cómo configurarlo:", "How to set it:", "Como configurar:"),
    ("ay_afecta", "Cómo afecta:", "How it affects:", "Como afeta:"),
    ("ay_inteligente_t", "Inteligente", "Smart", "Inteligente"),
    ("ay_inteligente_q",
     "mide el sonido del tema que suena y lo corrige solo, banda por banda, para acercarlo al perfil de la pestaña Objetivo.",
     "measures the sound of the playing track and corrects it automatically, band by band, to bring it closer to the profile in the Target tab.",
     "mede o som da faixa que está tocando e o corrige sozinho, banda por banda, para aproximá-lo do perfil da aba Alvo."),
    ("ay_inteligente_c",
     "dejalo tildado y elegí un perfil (Natural, Brillante o Cálido) o usá Capturar con un tema que te guste. El deslizador de al lado es la Fuerza: 0 % no corrige nada y 100 % corrige todo lo que pueda. Lo normal es entre 50 y 70 %.",
     "leave it checked and pick a profile (Natural, Bright or Warm) or use Capture with a track you like. The slider next to it is the Strength: 0 % corrects nothing and 100 % corrects as much as it can. Normal is between 50 and 70 %.",
     "deixe marcado e escolha um perfil (Natural, Brilhante ou Quente) ou use Capturar com uma faixa de que você goste. O controle ao lado é a Força: 0 % não corrige nada e 100 % corrige o máximo possível. O normal é entre 50 e 70 %."),
    ("ay_inteligente_a",
     "sin el tilde, el programa solo aplica lo de la pestaña Manual y la compresión; el perfil Objetivo no hace nada. Con más Fuerza el sonido se parece más al perfil, pero puede sonar menos natural.",
     "without the check, the program only applies what's in the Manual tab and the compression; the Target profile does nothing. With more Strength the sound gets closer to the profile, but it may sound less natural.",
     "sem a marcação, o programa só aplica o que está na aba Manual e a compressão; o perfil Alvo não faz nada. Com mais Força o som fica mais parecido com o perfil, mas pode soar menos natural."),
    ("ay_bypass_t", "Bypass", "Bypass", "Bypass"),
    ("ay_bypass_q",
     "el sonido pasa sin ningún efecto (ni ecualizador, ni compresor, ni limitador).",
     "the sound passes through with no effect at all (no equalizer, no compressor, no limiter).",
     "o som passa sem nenhum efeito (sem equalizador, sem compressor e sem limitador)."),
    ("ay_bypass_c",
     "tildalo un momento para comparar cómo suena el tema original contra el procesado, y destildalo para volver a escuchar el ecualizador.",
     "check it for a moment to compare the original track against the processed one, and uncheck it to hear the equalizer again.",
     "marque por um momento para comparar o som original da faixa com o processado, e desmarque para voltar a ouvir o equalizador."),
    ("ay_bypass_a",
     "el volumen es casi el mismo con y sin Bypass, así que lo que oís es solo el cambio de sonido. El gráfico sigue funcionando con el Bypass puesto.",
     "the volume is nearly the same with and without Bypass, so what you hear is only the change in sound. The chart keeps working while Bypass is on.",
     "o volume é quase o mesmo com e sem Bypass, então o que você ouve é só a mudança no som. O gráfico continua funcionando com o Bypass ligado."),
    ("ay_reinicio_t", "Reiniciar análisis en cada tema", "Restart analysis on every track",
     "Reiniciar análise a cada faixa"),
    ("ay_reinicio_q",
     "el programa nota cuándo cambia el tema por el sonido; esta opción decide qué hace con lo que ya había medido.",
     "the program notices when the track changes by the sound; this option decides what it does with what it had already measured.",
     "o programa percebe quando a faixa muda pelo som; esta opção decide o que fazer com o que já tinha medido."),
    ("ay_reinicio_c",
     "tildado: olvida lo medido y empieza de cero con el tema nuevo (se acomoda en unos 5 segundos). Destildado: se readapta de forma suave sin olvidar. Dejalo tildado si mezclás temas muy distintos entre sí.",
     "checked: it forgets what it measured and starts from scratch with the new track (it settles in about 5 seconds). Unchecked: it adapts smoothly without forgetting. Leave it checked if you mix tracks that are very different from each other.",
     "marcado: esquece o que mediu e começa do zero com a faixa nova (se ajusta em uns 5 segundos). Desmarcado: se readapta de forma suave sem esquecer. Deixe marcado se você mistura faixas muito diferentes entre si."),
    ("ay_reinicio_a",
     "tildado corrige más rápido al empezar cada tema, pero al principio puede sonar un instante sin corregir. Destildado hay menos cambios bruscos, pero un tema muy distinto tarda más en quedar bien.",
     "checked corrects faster at the start of each track, but at first it may sound uncorrected for a moment. Unchecked there are fewer abrupt changes, but a very different track takes longer to sound right.",
     "marcado corrige mais rápido no início de cada faixa, mas no começo pode soar um instante sem correção. Desmarcado há menos mudanças bruscas, mas uma faixa muito diferente demora mais para ficar boa."),
    ("ay_maxcorr_t", "Máx. corrección", "Max. correction", "Correção máx."),
    ("ay_maxcorr_q",
     "cuántos dB como máximo puede mover el modo Inteligente cada banda del ecualizador.",
     "the maximum number of dB the Smart mode can move each equalizer band.",
     "quantos dB, no máximo, o modo Inteligente pode mover cada banda do equalizador."),
    ("ay_maxcorr_c",
     "de 2 a 4 dB: cambios sutiles y seguros. 5 a 6 dB: equilibrado (el valor normal). 8 a 10 dB: para temas muy apagados.",
     "2 to 4 dB: subtle, safe changes. 5 to 6 dB: balanced (the normal value). 8 to 10 dB: for very dull tracks.",
     "de 2 a 4 dB: mudanças sutis e seguras. 5 a 6 dB: equilibrado (o valor normal). 8 a 10 dB: para faixas muito abafadas."),
    ("ay_maxcorr_a",
     "más alto corrige más a fondo pero puede sonar artificial o subir demasiado alguna zona. La banda de 43 Hz puede llegar hasta 3 veces este valor (tope 20 dB). Solo actúa con Inteligente tildado.",
     "higher corrects more deeply but may sound artificial or boost some range too much. The 43 Hz band can go up to 3 times this value (limit 20 dB). It only works with Smart checked.",
     "mais alto corrige mais a fundo, mas pode soar artificial ou aumentar demais alguma região. A banda de 43 Hz pode chegar a 3 vezes este valor (limite 20 dB). Só atua com Inteligente marcado."),
    ("ay_velocidad_t", "Velocidad", "Speed", "Velocidade"),
    ("ay_velocidad_q",
     "qué tan rápido mueve el modo Inteligente los filtros hacia la corrección, en dB por segundo.",
     "how fast the Smart mode moves the filters toward the correction, in dB per second.",
     "com que rapidez o modo Inteligente move os filtros em direção à correção, em dB por segundo."),
    ("ay_velocidad_c",
     "lenta (0,3 a 1,0 dB/s): cambios que casi no se notan. Media (1,0 a 2,0): el valor normal. Rápida (3,0 a 4,0): reacciona enseguida.",
     "slow (0.3 to 1.0 dB/s): changes you barely notice. Medium (1.0 to 2.0): the normal value. Fast (3.0 to 4.0): reacts right away.",
     "lenta (0,3 a 1,0 dB/s): mudanças quase imperceptíveis. Média (1,0 a 2,0): o valor normal. Rápida (3,0 a 4,0): reage na hora."),
    ("ay_velocidad_a",
     "más rápida acomoda antes un tema nuevo, pero los movimientos se pueden llegar a escuchar. Más lenta suena más estable pero tarda en llegar. En los primeros 8 segundos de cada tema va al doble. Solo actúa con Inteligente tildado.",
     "faster settles a new track sooner, but the movements may become audible. Slower sounds more stable but takes longer to get there. In the first 8 seconds of each track it goes twice as fast. It only works with Smart checked.",
     "mais rápida ajusta antes uma faixa nova, mas os movimentos podem chegar a ser ouvidos. Mais lenta soa mais estável, mas demora a chegar. Nos primeiros 8 segundos de cada faixa vai ao dobro. Só atua com Inteligente marcado."),
    # --- textos de la versión integrada (sin cable virtual) ---
    ("btn_conectar", "Activar ecualizador", "Enable equalizer", "Ativar equalizador"),
    ("btn_desconectar", "Desactivar ecualizador", "Disable equalizer", "Desativar equalizador"),
    ("cable_info",
     "Procesa directamente el sonido del reproductor (sin cable virtual).",
     "Processes the player's sound directly (no virtual cable).",
     "Processa diretamente o som do reprodutor (sem cabo virtual)."),
    ("eq_on",
     "Ecualizador activo: procesando el sonido del reproductor.",
     "Equalizer active: processing the player's sound.",
     "Equalizador ativo: processando o som do reprodutor."),
    ("eq_off",
     "Ecualizador desactivado: el sonido sale sin procesar.",
     "Equalizer disabled: the sound goes out unprocessed.",
     "Equalizador desativado: o som sai sem processamento."),
    ("espectro_desconectado", "Ecualizador desactivado", "Equalizer disabled", "Equalizador desativado"),
    ("btn_iniciar", "Aplicar salida", "Apply output", "Aplicar saída"),
    ("est_elegi", "Elegí una salida de audio", "Choose an audio output", "Escolha uma saída de áudio"),
]

TEXTOS = {"es": {}, "en": {}, "pt": {}}
for _fila in _FILAS:
    assert len(_fila) == 4, _fila[0]
    for _cod, _txt in zip(("es", "en", "pt"), _fila[1:]):
        TEXTOS[_cod][_fila[0]] = _txt

_idioma_actual = IDIOMA_POR_DEFECTO


def establecer_idioma(codigo):
    global _idioma_actual
    if codigo in TEXTOS:
        _idioma_actual = codigo


def idioma_actual():
    return _idioma_actual


def tr(clave, **datos):
    txt = TEXTOS.get(_idioma_actual, {}).get(clave) or TEXTOS["es"].get(clave) or clave
    if datos:
        try:
            return txt.format(**datos)
        except (KeyError, IndexError, ValueError):
            return txt
    return txt


def ayuda_de(clave):
    return ('<table width="300"><tr><td>'
            f'<b style="color:#f2a33a">{tr("ay_" + clave + "_t")}</b><br>'
            f'<b>{tr("ay_que")}</b> {tr("ay_" + clave + "_q")}<br><br>'
            f'<b>{tr("ay_cfg")}</b> {tr("ay_" + clave + "_c")}<br><br>'
            f'<b>{tr("ay_afecta")}</b> {tr("ay_" + clave + "_a")}'
            '</td></tr></table>')


# ------------------------------------------------------------------
# Anillo de audio
# ------------------------------------------------------------------
class Anillo:
    def __init__(self, segundos=2.0, sr=48000):
        self.sr = sr
        self.cap = int(segundos * sr)
        self.buf = np.zeros((self.cap, CANALES), dtype=np.float32)
        self.r = 0
        self.w = 0
        self.n = 0
        self.lock = threading.Lock()
        self.desbordes = 0
        self.vaciados = 0
        self.llenando = True
        self.fase = 0.0
        self.ratio = 1.0

    def llenado(self):
        return self.n

    def _copiar(self, desde, k):
        fin = desde + k
        if fin <= self.cap:
            return self.buf[desde:fin]
        return np.concatenate((self.buf[desde:], self.buf[:fin - self.cap]))

    def escribir(self, x):
        k = len(x)
        with self.lock:
            if k > self.cap:
                x = x[-self.cap:]
                k = self.cap
            libre = self.cap - self.n
            if k > libre:
                perdidas = k - libre
                self.r = (self.r + perdidas) % self.cap
                self.n -= perdidas
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

    def leer(self, k, minimo):
        out = np.zeros((k, CANALES), dtype=np.float32)
        with self.lock:
            if self.llenando:
                if self.n >= minimo:
                    self.llenando = False
                    self.fase = 0.0
                else:
                    return out
            if self.n > minimo * 4:
                sobra = self.n - minimo
                self.r = (self.r + sobra) % self.cap
                self.n -= sobra
                self.fase = 0.0
            error = self.n - minimo
            objetivo = 1.0 + float(np.clip(error / (12.0 * self.sr), -0.003, 0.003))
            self.ratio += 0.05 * (objetivo - self.ratio)
            paso = self.ratio
            necesarias = int(np.ceil(self.fase + (k - 1) * paso)) + 2
            if self.n < necesarias:
                self.vaciados += 1
                self.llenando = True
                self.fase = 0.0
                k2 = min(self.n, k)
                if k2:
                    out[:k2] = self._copiar(self.r, k2)
                    self.r = (self.r + k2) % self.cap
                    self.n -= k2
                return out
            x = self._copiar(self.r, necesarias)
            pos = self.fase + np.arange(k) * paso
            i0 = pos.astype(np.int64)
            f = (pos - i0).astype(np.float32)[:, None]
            out = x[i0] * (1.0 - f) + x[i0 + 1] * f
            avance = self.fase + k * paso
            consumidas = int(avance)
            self.fase = avance - consumidas
            self.r = (self.r + consumidas) % self.cap
            self.n -= consumidas
        return out.astype(np.float32, copy=False)

    def vaciar(self):
        with self.lock:
            self.r = self.w = self.n = 0
            self.llenando = True
            self.fase = 0.0


# ------------------------------------------------------------------
# Análisis espectral
# ------------------------------------------------------------------
class AnalizadorEspectro:
    NFFT = 8192
    HOP = 4096
    UMBRAL_CAMBIO_DB = 2.5
    TIEMPO_CAMBIO_SEG = 1.0
    SILENCIO_DB = -55.0
    reinicio_auto = True

    def __init__(self, sr, control=False, tau_lento=8.0, tau_rapido=1.5):
        self.sr = sr
        self.control = control
        self.tau_lento = tau_lento
        self.tau_rapido = tau_rapido
        self.vent = np.hanning(self.NFFT).astype(np.float32)
        self.norma = float((self.vent.sum() / 2.0) ** 2)
        self.hist = np.zeros(self.NFFT, dtype=np.float32)
        self._acum = 0
        f = np.fft.rfftfreq(self.NFFT, 1.0 / sr)
        self.M_ctrl = self._matriz_contigua(f, BANDAS_HZ)
        cent = 50.0 * 2.0 ** (np.arange(0, 40) / 3.0)
        self.cent_disp = cent[cent <= 16000.0]
        self.M_disp = self._matriz(f, self.cent_disp, 2.0 ** (1.0 / 6.0))
        self.disp_db = np.full(len(self.cent_disp), -120.0)
        self.nivel_db = -120.0
        self.dt = self.HOP / float(sr)
        self.reiniciar()
        self.cambios = 0
        self.t_cambio = -1e9

    @staticmethod
    def _matriz(frecs, centros, razon):
        M = np.zeros((len(centros), len(frecs)), dtype=np.float32)
        for i, c in enumerate(centros):
            idx = np.where((frecs >= c / razon) & (frecs < c * razon))[0]
            if len(idx) == 0:
                idx = [int(np.argmin(np.abs(frecs - c)))]
            M[i, idx] = 1.0
        return M

    @staticmethod
    def _matriz_contigua(frecs, centros):
        c = np.asarray(centros, dtype=np.float64)
        medios = np.sqrt(c[:-1] * c[1:])
        bordes = np.concatenate(([c[0] ** 2 / medios[0]], medios, [c[-1] ** 2 / medios[-1]]))
        M = np.zeros((len(c), len(frecs)), dtype=np.float32)
        for i in range(len(c)):
            idx = np.where((frecs >= bordes[i]) & (frecs < bordes[i + 1]))[0]
            if len(idx) == 0:
                idx = [int(np.argmin(np.abs(frecs - c[i])))]
            M[i, idx] = 1.0 / max(np.log2(bordes[i + 1] / bordes[i]), 0.2)
        return M

    @staticmethod
    def forma(P):
        db = 10.0 * np.log10(np.asarray(P, dtype=np.float64) + 1e-12)
        return db - db.mean()

    def reiniciar(self):
        self.Pl = None
        self.Pr = None
        self.validas = 0.0
        self.desvio = 0.0
        self._exceso = 0.0
        self._silencio = 0.0

    def alimentar(self, mono):
        n = len(mono)
        if n >= self.NFFT:
            self.hist[:] = mono[-self.NFFT:]
        else:
            self.hist = np.roll(self.hist, -n)
            self.hist[-n:] = mono
        self._acum += n
        if self._acum < self.HOP:
            return False
        dt = self._acum / float(self.sr)
        self._acum = 0
        self.dt = dt

        rms = float(np.sqrt(np.mean(self.hist.astype(np.float64) ** 2) + 1e-20))
        self.nivel_db = 20.0 * np.log10(rms + 1e-12)
        esp = (np.abs(np.fft.rfft(self.hist * self.vent)) ** 2 / self.norma)
        esp = esp.astype(np.float64)
        self.disp_db = 10.0 * np.log10(self.M_disp @ esp + 1e-12)

        if not self.control:
            return True
        if self.nivel_db < self.SILENCIO_DB:
            self._silencio += dt
            if self._silencio > 1.5:
                self.reiniciar()
            return True
        self._silencio = 0.0

        e = self.M_ctrl @ esp + 1e-12
        if self.Pl is None:
            self.Pl = e.copy()
            self.Pr = e.copy()
        else:
            al = float(np.exp(-dt / self.tau_lento))
            ar = float(np.exp(-dt / self.tau_rapido))
            self.Pl = al * self.Pl + (1.0 - al) * e
            self.Pr = ar * self.Pr + (1.0 - ar) * e
        self.validas += dt

        if self.validas > 3.0:
            self.desvio = float(np.sqrt(np.mean(
                (self.forma(self.Pr) - self.forma(self.Pl)) ** 2)))
            if self.desvio > self.UMBRAL_CAMBIO_DB:
                self._exceso += dt
            else:
                self._exceso = max(0.0, self._exceso - dt)
            if self._exceso > self.TIEMPO_CAMBIO_SEG:
                self._exceso = 0.0
                self.cambios += 1
                self.t_cambio = time.monotonic()
                if self.reinicio_auto:
                    self.reiniciar()
                else:
                    self.Pl = self.Pr.copy()
        return True

    def forma_lenta(self):
        Pl = self.Pl                      # copia local: el hilo de audio puede reiniciar
        if Pl is None or self.validas < 2.0:
            return None
        return self.forma(Pl)

    def nivel_bandas_db(self):
        Pl = self.Pl
        if Pl is None:
            return None
        return 10.0 * np.log10(Pl + 1e-12)


# ------------------------------------------------------------------
# Compresión multibanda
# ------------------------------------------------------------------
class CompresorMultibanda:
    COMPENSACION_CRUCE_DB = 3.0

    def __init__(self, sr, f_bajos=200.0, f_altos=2000.0):
        from pedalboard import (LowpassFilter, HighpassFilter, Compressor,
                                Gain, Pedalboard)
        self.sr = sr
        self.f_bajos = float(f_bajos)
        self.f_altos = float(f_altos)

        self.lp_bajos = LowpassFilter(cutoff_frequency_hz=self.f_bajos)
        self.hp_bajos = HighpassFilter(cutoff_frequency_hz=self.f_bajos)
        self.lp_medios = LowpassFilter(cutoff_frequency_hz=self.f_altos)
        self.hp_altos = HighpassFilter(cutoff_frequency_hz=self.f_altos)

        self.comp_bajos = Compressor(threshold_db=-18.0, ratio=1.0,
                                     attack_ms=20.0, release_ms=150.0)
        self.comp_medios = Compressor(threshold_db=-16.0, ratio=1.0,
                                      attack_ms=10.0, release_ms=100.0)
        self.comp_agudos = Compressor(threshold_db=-14.0, ratio=1.0,
                                      attack_ms=5.0, release_ms=80.0)

        self.makeup_bajos = Gain(gain_db=0.0)
        self.makeup_medios = Gain(gain_db=0.0)
        self.makeup_agudos = Gain(gain_db=0.0)

        self.compensa_cruce = Gain(gain_db=self.COMPENSACION_CRUCE_DB)

        self.cadena_bajos = Pedalboard([self.comp_bajos, self.makeup_bajos])
        self.cadena_medios = Pedalboard([self.comp_medios, self.makeup_medios])
        self.cadena_agudos = Pedalboard([self.comp_agudos, self.makeup_agudos])

        self._aplicados = None
        self.energia = (0.0, 0.0, 0.0)
        self.energia_post = (0.0, 0.0, 0.0)

    def reconfigurar_cruces(self, f_bajos, f_altos):
        if float(f_bajos) == self.f_bajos and float(f_altos) == self.f_altos:
            return
        from pedalboard import LowpassFilter, HighpassFilter
        self.f_bajos = float(f_bajos)
        self.f_altos = float(f_altos)
        self.lp_bajos = LowpassFilter(cutoff_frequency_hz=self.f_bajos)
        self.hp_bajos = HighpassFilter(cutoff_frequency_hz=self.f_bajos)
        self.lp_medios = LowpassFilter(cutoff_frequency_hz=self.f_altos)
        self.hp_altos = HighpassFilter(cutoff_frequency_hz=self.f_altos)

    def _aplicar_parametros(self, params):
        clave = tuple(tuple(params[b]) for b in ("graves", "medios", "agudos"))
        if clave == self._aplicados:
            return
        self._aplicados = clave
        for comp, gain, banda in (
            (self.comp_bajos, self.makeup_bajos, "graves"),
            (self.comp_medios, self.makeup_medios, "medios"),
            (self.comp_agudos, self.makeup_agudos, "agudos"),
        ):
            p = params[banda]
            comp.threshold_db = float(p[0])
            comp.ratio = max(1.0, float(p[1]))
            comp.attack_ms = float(p[2])
            comp.release_ms = float(p[3])
            gain.gain_db = float(p[4])

    def procesar(self, bloque, params):
        self._aplicar_parametros(params)
        sr = self.sr
        x = np.ascontiguousarray(bloque.T)

        bajos = self.compensa_cruce(self.lp_bajos(x, sr, reset=False), sr, reset=False)
        medios = self.hp_bajos(x, sr, reset=False)
        medios = self.compensa_cruce(self.lp_medios(medios, sr, reset=False), sr, reset=False)
        agudos = self.compensa_cruce(self.hp_altos(x, sr, reset=False), sr, reset=False)

        self.energia = (float(np.mean(bajos.astype(np.float64) ** 2)),
                        float(np.mean(medios.astype(np.float64) ** 2)),
                        float(np.mean(agudos.astype(np.float64) ** 2)))

        bajos = self.cadena_bajos(bajos, sr, reset=False)
        medios = self.cadena_medios(medios, sr, reset=False)
        agudos = self.cadena_agudos(agudos, sr, reset=False)
        self.energia_post = (float(np.mean(bajos.astype(np.float64) ** 2)),
                             float(np.mean(medios.astype(np.float64) ** 2)),
                             float(np.mean(agudos.astype(np.float64) ** 2)))

        return (bajos + medios + agudos).T


# ------------------------------------------------------------------
# Realce de graves (golpe de bombo a 43 Hz), nitidez y nivelador
# ------------------------------------------------------------------
class _Seguidor:
    """Envolvente con ataque y relajación distintos, sobre valores ya reducidos
    (un valor cada `paso` muestras). Mantiene el estado entre bloques."""

    def __init__(self, t_ataque, t_relaj, tasa):
        self.a_at = math.exp(-1.0 / max(t_ataque * tasa, 1e-6))
        self.a_re = math.exp(-1.0 / max(t_relaj * tasa, 1e-6))
        self.v = 0.0

    def seguir(self, valores):
        v, a_at, a_re = self.v, self.a_at, self.a_re
        out = np.empty(len(valores), dtype=np.float64)
        for i, x in enumerate(valores):
            a = a_at if x > v else a_re
            v = a * v + (1.0 - a) * x
            out[i] = v
        self.v = v
        return out


class RealceGrave:
    """Hace que el bombo / bajo se sienta y sobresalga.

    Trabaja en paralelo sobre la banda 28-100 Hz y suma tres cosas:
      * un empujón fijo en la zona de 43 Hz,
      * un empujón extra solo en el ATAQUE de cada golpe (el "punch"): compara una
        envolvente rápida con una lenta y sube la ganancia mientras el golpe sube,
      * armónicos suaves del bajo (86 y 130 Hz aprox.) para que el golpe se perciba
        también en parlantes chicos que no llegan a 43 Hz.
    """
    PASO = 16                    # muestras por valor de envolvente
    EMPUJE_FIJO_DB = 6.0         # a cantidad 1.0
    EMPUJE_GOLPE_DB = 7.0        # tope extra durante el ataque, a cantidad 1.0
    MEZCLA_ARMONICOS = 0.55
    PISO = 10.0 ** (-60.0 / 20.0)

    def __init__(self, sr):
        from pedalboard import Pedalboard, HighpassFilter, LowpassFilter
        self.sr = sr
        self.cantidad = 0.0
        self.banda = Pedalboard([HighpassFilter(28.0), HighpassFilter(28.0),
                                 LowpassFilter(100.0), LowpassFilter(100.0),
                                 LowpassFilter(100.0)])
        self.arm = Pedalboard([HighpassFilter(70.0), HighpassFilter(70.0),
                               LowpassFilter(260.0), LowpassFilter(260.0)])
        tasa = sr / float(self.PASO)
        self.rapida = _Seguidor(0.002, 0.040, tasa)
        self.lenta = _Seguidor(0.030, 0.300, tasa)
        self.g_prev = 0.0
        self.golpe_db = 0.0     # para mostrar en la interfaz (0..1)

    def procesar(self, x):
        """x: (canales, n) float32. Devuelve la señal con el realce sumado."""
        if self.cantidad <= 0.001:
            self.golpe_db = 0.0
            return x
        n = x.shape[1]
        sr = self.sr
        sub = np.ascontiguousarray(self.banda(x, sr, reset=False), dtype=np.float32)

        # Envolvente (pico por tramo) del sub mono.
        mono = np.abs(sub).max(axis=0)
        k = -(-n // self.PASO)
        if k * self.PASO != n:
            mono = np.pad(mono, (0, k * self.PASO - n), mode="edge")
        picos = mono.reshape(k, self.PASO).max(axis=1).astype(np.float64)
        rapida = self.rapida.seguir(picos)
        lenta = self.lenta.seguir(picos)

        # Cuánto sube el golpe respecto del nivel medio reciente (dB).
        sube_db = 20.0 * np.log10((rapida + 1e-5) / (lenta + 1e-5))
        sube_db = np.clip(sube_db, 0.0, 12.0)
        activo = np.clip((lenta - self.PISO) / self.PISO, 0.0, 1.0)  # silencio -> 0
        golpe = np.minimum(sube_db * 0.9, self.EMPUJE_GOLPE_DB) * activo * self.cantidad
        fijo = self.EMPUJE_FIJO_DB * self.cantidad
        extra_lin = 10.0 ** ((fijo + golpe) / 20.0) - 1.0
        self.golpe_db = float(golpe.max()) if len(golpe) else 0.0

        centros = (np.arange(k) + 0.5) * self.PASO
        g = np.interp(np.arange(n), centros, extra_lin).astype(np.float32)

        # Armónicos del bajo: rectificación de onda completa (2.º armónico) filtrada.
        rect = np.abs(sub)
        arm = self.arm(np.ascontiguousarray(rect), sr, reset=False)
        mezcla = self.MEZCLA_ARMONICOS * self.cantidad

        # Hacemos lugar: bajamos un poco el resto para que el bajo gane protagonismo
        # sin disparar el limitador.
        resto = 10.0 ** (-1.0 * self.cantidad / 20.0)
        return (x * resto + sub * g + arm * mezcla).astype(np.float32, copy=False)


class Nitidez:
    """Aire y definición: agrega armónicos finos en agudos (exciter) y limpia el
    'barro' de 300 Hz para que todo se oiga más abierto."""

    def __init__(self, sr):
        from pedalboard import (Pedalboard, HighpassFilter, PeakFilter)
        self.sr = sr
        self.cantidad = 0.0
        self.barro = PeakFilter(cutoff_frequency_hz=300.0, gain_db=0.0, q=1.0)
        self.banda = Pedalboard([HighpassFilter(2500.0), HighpassFilter(2500.0)])
        self.aire = Pedalboard([HighpassFilter(5500.0), HighpassFilter(5500.0)])
        self._barro_db = None

    def procesar(self, x):
        c = self.cantidad
        if c <= 0.001:
            return x
        sr = self.sr
        db = round(-2.5 * c, 1)
        if db != self._barro_db:
            self.barro.gain_db = db
            self._barro_db = db
        y = self.barro(np.ascontiguousarray(x), sr, reset=False)
        hi = self.banda(np.ascontiguousarray(x), sr, reset=False)
        # Parte no lineal de una saturación suave = armónicos nuevos.
        resid = np.tanh(8.0 * hi) / 8.0 - hi
        aire = self.aire(np.ascontiguousarray(resid, dtype=np.float32), sr, reset=False)
        # Tope suave: con agudos fuertes el realce no se vuelve áspero.
        aire = 0.10 * np.tanh(aire * (3.5 * c) / 0.10)
        return (y - aire).astype(np.float32, copy=False)


class IgualadorBypass:
    """Mantiene la sonoridad del sonido procesado igual a la del original, así al
    activar / desactivar Bypass el volumen no cambia (solo cambia el timbre).

    Mide la sonoridad ponderada (casi no cuenta el subgrave, como el oído) de la
    entrada y de la salida ya procesada, y mueve una ganancia despacio para que
    coincidan. Al arrancar (o al volver de Bypass) converge rápido."""
    LIMITE_DB = 12.0
    GATE_DB = -60.0
    TAU_SEG = 2.0

    def __init__(self, sr):
        from pedalboard import Pedalboard, HighpassFilter, HighShelfFilter

        def _peso():
            return Pedalboard([HighpassFilter(90.0), HighpassFilter(90.0),
                               HighShelfFilter(cutoff_frequency_hz=2000.0,
                                               gain_db=3.0, q=0.7)])
        self.sr = sr
        self.peso_in = _peso()
        self.peso_out = _peso()
        from pedalboard import LowpassFilter
        # Separa el subgrave: la ganancia de igualación NO lo toca (el golpe del bajo manda).
        self.sub = Pedalboard([LowpassFilter(90.0), LowpassFilter(90.0), LowpassFilter(90.0)])
        self.reiniciar()
        self.diferencia_db = 0.0     # para mostrar en la interfaz

    def reiniciar(self):
        self.e_in = None
        self.e_out = None
        self.g_db = 0.0
        self.t = 0.0

    def _ms(self, peso, x):
        p = peso(np.ascontiguousarray(x.mean(axis=0, keepdims=True), dtype=np.float32),
                 self.sr, reset=False)
        return float(np.mean(p.astype(np.float64) ** 2)) + 1e-14

    def procesar(self, entrada, salida):
        """entrada y salida: (canales, n). Devuelve la salida con la ganancia."""
        n = salida.shape[1]
        dt = n / float(self.sr)
        ms_i = self._ms(self.peso_in, entrada)
        ms_o = self._ms(self.peso_out, salida)
        self.t += dt
        if self.e_in is None:
            self.e_in, self.e_out = ms_i, ms_o
        else:
            # Al principio promedia poco para converger rápido; después tau fijo.
            tau = min(self.TAU_SEG, 0.25 + self.t * 0.5)
            a = math.exp(-dt / tau)
            self.e_in = a * self.e_in + (1.0 - a) * ms_i
            self.e_out = a * self.e_out + (1.0 - a) * ms_o
        nivel_in = 10.0 * math.log10(self.e_in)
        if nivel_in > self.GATE_DB:
            meta = float(np.clip(10.0 * math.log10(self.e_in / self.e_out) - 0.0,
                                 -self.LIMITE_DB, self.LIMITE_DB))
            vel = 12.0 if self.t < 3.0 else 3.0      # dB por segundo
            paso = vel * dt
            nuevo = self.g_db + float(np.clip(meta - self.g_db, -paso, paso))
        else:
            nuevo = self.g_db
        self.diferencia_db = nuevo
        rampa = np.linspace(10.0 ** (self.g_db / 20.0), 10.0 ** (nuevo / 20.0),
                            n, dtype=np.float32)
        self.g_db = nuevo
        sub = np.ascontiguousarray(self.sub(np.ascontiguousarray(salida, dtype=np.float32),
                                            self.sr, reset=False), dtype=np.float32)
        return (sub + (salida - sub) * rampa).astype(np.float32, copy=False)


class Nivelador:
    """Iguala el volumen entre temas: mide la sonoridad en unos segundos y mueve
    la ganancia despacio (sube poco, baja más rápido). No toca el silencio."""
    OBJETIVO_DB = -15.0
    SUBE_MAX_DB = 4.0
    BAJA_MAX_DB = 6.0
    GATE_DB = -52.0

    def __init__(self, sr):
        from pedalboard import Pedalboard, HighpassFilter, HighShelfFilter
        self.sr = sr
        self.peso = Pedalboard([HighpassFilter(90.0), HighpassFilter(90.0),
                                HighShelfFilter(cutoff_frequency_hz=2000.0,
                                                gain_db=3.0, q=0.7)])
        self.energia = None
        self.g_db = 0.0
        self.sonoridad_db = -120.0

    def reiniciar(self):
        self.energia = None
        self.g_db = 0.0

    def procesar(self, x):
        n = x.shape[1]
        dt = n / float(self.sr)
        ponderada = self.peso(np.ascontiguousarray(x.mean(axis=0, keepdims=True)),
                              self.sr, reset=False)
        ms = float(np.mean(ponderada.astype(np.float64) ** 2)) + 1e-14
        if self.energia is None:
            self.energia = ms
        else:
            a = math.exp(-dt / 3.0)
            self.energia = a * self.energia + (1.0 - a) * ms
        self.sonoridad_db = 10.0 * math.log10(self.energia)
        if self.sonoridad_db > self.GATE_DB:
            meta = float(np.clip(self.OBJETIVO_DB - self.sonoridad_db,
                                 -self.BAJA_MAX_DB, self.SUBE_MAX_DB))
            paso = (1.2 if meta > self.g_db else 4.0) * dt
            nuevo = self.g_db + float(np.clip(meta - self.g_db, -paso, paso))
        else:
            nuevo = self.g_db            # en silencio se queda donde estaba
        rampa = np.linspace(10.0 ** (self.g_db / 20.0), 10.0 ** (nuevo / 20.0),
                            n, dtype=np.float32)
        self.g_db = nuevo
        return (x * rampa).astype(np.float32, copy=False)


# ------------------------------------------------------------------
# Motor de efectos
# ------------------------------------------------------------------
class MotorEfectos:
    def __init__(self, sr):
        from pedalboard import (Pedalboard, Gain, LowShelfFilter, PeakFilter,
                                HighShelfFilter, Compressor, Limiter)
        self.sr = sr
        self.pre = Gain(gain_db=0.0)

        self.f_bandas = [PeakFilter(cutoff_frequency_hz=float(BANDAS_HZ[0]),
                                    gain_db=0.0, q=0.9)]
        for fc in BANDAS_HZ[1:-1]:
            self.f_bandas.append(PeakFilter(cutoff_frequency_hz=float(fc),
                                            gain_db=0.0, q=1.1))
        self.f_bandas.append(HighShelfFilter(cutoff_frequency_hz=5700.0,
                                             gain_db=0.0, q=0.7))
        self.f_graves = LowShelfFilter(cutoff_frequency_hz=120.0, gain_db=0.0)
        self.f_pres = PeakFilter(cutoff_frequency_hz=3000.0, gain_db=0.0, q=0.9)
        self.f_agudos = HighShelfFilter(cutoff_frequency_hz=6500.0, gain_db=0.0)
        self.comp = Compressor(threshold_db=-16.0, ratio=1.0,
                               attack_ms=15.0, release_ms=120.0)
        self.compensa = Gain(gain_db=COMPENSA_LIMITADOR_DB)
        self.limite = Limiter(threshold_db=-1.0)
        self.board = Pedalboard([self.pre] + self.f_bandas +
                                [self.f_graves, self.f_pres, self.f_agudos,
                                 self.comp, self.compensa, self.limite])

        self.mb_activo = False
        self.mb = CompresorMultibanda(sr, f_bajos=200.0, f_altos=2000.0)
        self.mb_params = {
            "graves": [-18.0, 2.5, 20.0, 150.0, 0.0],
            "medios": [-16.0, 2.0, 10.0, 100.0, 0.0],
            "agudos": [-14.0, 1.8,  5.0,  80.0, 0.0],
        }
        self.mb_params_base = {k: list(v) for k, v in self.mb_params.items()}

        self.man_graves = 0.0
        self.man_presencia = 0.0
        self.man_agudos = 0.0
        self.anti_sat = False
        self.k_pico = 1.0
        self.k_banda = np.ones(3)
        self._sat = 0.0
        self.ratio = 1.0
        self.volumen = 1.0
        self.bypass = False

        self.inteligente = True
        self.fuerza = 0.6
        self.limite_auto = 6.0
        self.velocidad = 1.0
        self.objetivo = np.array(PERFILES["Natural"], dtype=np.float64)
        self.auto = np.zeros(len(BANDAS_HZ))
        self.forma_medida = None

        self.an_in = AnalizadorEspectro(sr, control=True)
        self.an_out = AnalizadorEspectro(sr, control=False)

        self.pico_in = 0.0
        self.pico_out = 0.0
        self._aplicados = None

        self.mb_auto = True
        self.mb_int = 0.6
        self._mb_e = None
        self.mb_niveles = np.full(3, -60.0)
        self.mb_gr = np.zeros(3)
        self.golpe = 0.7
        self.nitidez_c = 0.5
        self.nivela = False
        self.iguala = True
        self.realce = RealceGrave(sr)
        self.nitidez = Nitidez(sr)
        self.nivelador = Nivelador(sr)
        self.igualador = IgualadorBypass(sr)
        # El Limiter de pedalboard suma una ganancia fija (~+4,5 dB) aunque no limite:
        # la medimos acá y la compensamos para que el limitador final sea neutro.
        _prueba = (np.random.default_rng(0).standard_normal((2, sr)) * 0.02).astype(np.float32)
        _salida = Pedalboard([Limiter(threshold_db=-0.8)])(_prueba, sr)
        _a, _b = _prueba[:, sr // 4:], _salida[:, sr // 4:]
        _g = 20.0 * math.log10((float(np.sqrt(np.mean(_b ** 2))) + 1e-12) /
                               (float(np.sqrt(np.mean(_a ** 2))) + 1e-12))
        self.limite_final = Pedalboard([Gain(gain_db=-float(np.clip(_g, 0.0, 8.0))),
                                        Limiter(threshold_db=-0.8)])
        self.error_dsp = ""

    MULT_LIMITE = np.array([3.0] + [1.0] * (len(BANDAS_HZ) - 1))
    TOPE_BANDA = np.array([20.0] + [15.0] * (len(BANDAS_HZ) - 1))
    PESO_PICO = np.array([0.35] + [1.0] * (len(BANDAS_HZ) - 1))

    def _controlar(self, dt):
        forma = self.an_in.forma_lenta()
        self.forma_medida = forma
        if forma is None:
            return
        meta = self.objetivo - self.objetivo.mean()
        lim = float(self.limite_auto) * self.MULT_LIMITE
        lim = np.minimum(lim, self.TOPE_BANDA)
        g = np.clip(self.fuerza * (meta - forma), -lim, lim)
        g[1:] -= g[1:].mean()
        g = np.clip(g, -lim, lim)
        vel = self.velocidad
        if time.monotonic() - self.an_in.t_cambio < 8.0:
            vel *= 2.0
        paso = vel * dt
        self.auto += np.clip(g - self.auto, -paso, paso)

        if self.mb_activo and not self.mb_auto:
            exceso_bajos = float(np.mean(forma[:3] - meta[:3]))
            exceso_medios = float(np.mean(forma[3:7] - meta[3:7]))
            exceso_agudos = float(np.mean(forma[7:] - meta[7:]))
            self._modular_mb("graves", exceso_bajos, -30.0, -6.0, 0.8)
            self._modular_mb("medios", exceso_medios, -28.0, -6.0, 0.6)
            self._modular_mb("agudos", exceso_agudos, -26.0, -6.0, 0.5)

    def _modular_mb(self, banda, exceso, umbral_min, umbral_max, factor):
        base = self.mb_params_base[banda][0]
        objetivo = base - factor * max(0.0, exceso)
        objetivo = float(np.clip(objetivo, umbral_min, umbral_max))
        actual = self.mb_params[banda][0]
        self.mb_params[banda][0] = actual + 0.3 * (objetivo - actual)

    # Perfil de la multibanda automática, por banda:
    # (umbral sobre el nivel medio en dB, ratio máx., ataque ms, liberación ms,
    #  cuánto baja el umbral por cada dB que la banda se pasa del perfil objetivo)
    MB_AUTO = {
        "graves": (6.0, 1.8, 30.0, 200.0, 0.5),
        "medios": (2.5, 2.5, 12.0, 120.0, 0.5),
        "agudos": (3.5, 2.8, 4.0, 70.0, 0.4),
    }
    MB_TAU_SEG = 4.0
    MB_GATE_DB = -70.0

    def _auto_mb(self, dt):
        """Multibanda automática: mide cuánta energía trae cada banda y fija sola
        umbral, ratio, ataque, liberación y ganancia de compensación."""
        e = np.asarray(self.mb.energia, dtype=np.float64)
        if self._mb_e is None:
            self._mb_e = e + 1e-14
        else:
            a = math.exp(-dt / self.MB_TAU_SEG)
            self._mb_e = a * self._mb_e + (1.0 - a) * (e + 1e-14)
        niveles = 10.0 * np.log10(self._mb_e)
        self.mb_niveles = niveles

        # Reducción de ganancia real (dB) de cada banda, para mostrarla en vivo.
        pre = np.asarray(self.mb.energia, dtype=np.float64) + 1e-14
        post = np.asarray(self.mb.energia_post, dtype=np.float64) + 1e-14
        mk = np.array([self.mb_params[b][4] for b in ("graves", "medios", "agudos")])
        gr = np.clip(10.0 * np.log10(pre / post) + mk, 0.0, 12.0)
        gr = np.where(10.0 * np.log10(pre) < self.MB_GATE_DB, 0.0, gr)
        a_gr = math.exp(-dt / 0.15)
        self.mb_gr = a_gr * self.mb_gr + (1.0 - a_gr) * gr

        exceso = None
        if self.inteligente:
            forma = self.an_in.forma_lenta()
            if forma is not None:
                meta = self.objetivo - self.objetivo.mean()
                d = forma - meta
                exceso = (float(np.mean(d[:3])), float(np.mean(d[3:7])), float(np.mean(d[7:])))

        suave = 0.12
        for i, banda in enumerate(("graves", "medios", "agudos")):
            offset, rmax, ataque, relaj, k_exc = self.MB_AUTO[banda]
            p = self.mb_params[banda]
            if niveles[i] < self.MB_GATE_DB:
                continue                      # banda sin señal: no tocar
            ratio = 1.0 + (rmax - 1.0) * float(np.clip(self.mb_int, 0.0, 1.0))
            off = offset
            if exceso is not None:
                off -= k_exc * float(np.clip(exceso[i], 0.0, 8.0))
            if self.anti_sat:
                off -= 4.0 * self._sat
            thr = float(np.clip(niveles[i] + off, -60.0, -3.0))
            # Estimación de cuánto baja el nivel en los picos -> compensación a medias.
            sobre = max(0.0, 6.0 - off)
            makeup = float(np.clip(0.5 * sobre * (1.0 - 1.0 / ratio), 0.0, 3.0))
            p[0] += suave * (thr - p[0])
            p[1] += suave * (ratio - p[1])
            p[2] = ataque
            p[3] = relaj
            p[4] += suave * (makeup - p[4])

    _BANDAS_MANUAL = ([0, 1, 2], [6, 7], [8])

    def _guardia_espectral(self, dt):
        forma = self.an_in.forma_lenta()
        if forma is None:
            return
        meta = self.objetivo - self.objetivo.mean()
        exceso = forma - meta
        for i, bandas in enumerate(self._BANDAS_MANUAL):
            e = float(max(exceso[b] for b in bandas))
            destino = float(np.clip(1.0 - max(0.0, e - 2.0) / 8.0, 0.5, 1.0))
            self.k_banda[i] += float(np.clip(destino - self.k_banda[i], -0.3 * dt, 0.3 * dt))

    def _vigilar_saturacion(self, pico, pico_entrada, dt):
        caliente = 1.0 if (pico > 0.86 and pico > 1.1 * pico_entrada) else 0.0
        self._sat += (caliente - self._sat) * (1.0 - math.exp(-dt / 0.5))
        if self._sat > 0.15:
            self.k_pico -= 0.6 * dt * min(1.0, self._sat * 2.0)
        elif self._sat < 0.03:
            self.k_pico += 0.2 * dt
        self.k_pico = float(np.clip(self.k_pico, 0.3, 1.0))

        if self.mb_activo and not self.mb_auto and self._sat > 0.1:
            for banda in ("graves", "medios", "agudos"):
                actual = self.mb_params[banda][0]
                objetivo = max(-30.0, actual - 2.0 * self._sat)
                self.mb_params[banda][0] = actual + 0.4 * (objetivo - actual)

    def _manual_efectivo(self):
        m = np.array([self.man_graves, self.man_presencia, self.man_agudos], dtype=np.float64)
        if self.anti_sat:
            k = self.k_pico * self.k_banda
            m = np.where(m > 0, m * k, m)
        return m

    @property
    def porcentaje_manual(self):
        return 100.0 * (self.k_pico * float(self.k_banda.min()) if self.anti_sat else 1.0)

    def _aplicar_parametros(self):
        auto = self.auto if self.inteligente else np.zeros(len(BANDAS_HZ))
        man = np.round(self._manual_efectivo(), 1)
        valores = (tuple(np.round(auto, 2)), tuple(man), self.ratio)
        if valores == self._aplicados:
            return
        self._aplicados = valores
        for f, g in zip(self.f_bandas, auto):
            f.gain_db = float(np.clip(g, -20 if f is self.f_bandas[0] else -15,
                                      20 if f is self.f_bandas[0] else 15))
        self.f_graves.gain_db = float(np.clip(man[0], -15, 15))
        self.f_pres.gain_db = float(np.clip(man[1], -15, 15))
        self.f_agudos.gain_db = float(np.clip(man[2], -15, 15))
        pico = max(0.0, float(np.max(auto * self.PESO_PICO)), float(man.max()))
        self.pre.gain_db = -0.5 * pico
        self.comp.ratio = max(1.0, float(self.ratio))
        if self.mb_activo:
            self.mb._aplicar_parametros(self.mb_params)

    def procesar(self, bloque):
        n = len(bloque)
        if not np.isfinite(bloque).all():
            # Un solo NaN / inf envenenaría los filtros y el análisis para siempre.
            bloque = np.nan_to_num(bloque, nan=0.0, posinf=1.0, neginf=-1.0)
        self.pico_in = max(float(np.max(np.abs(bloque))) if n else 0.0,
                           self.pico_in * 0.92)
        if self.an_in.alimentar(bloque.mean(axis=1)):
            if self.inteligente and not self.bypass:
                self._controlar(self.an_in.dt)
            else:
                self.forma_medida = self.an_in.forma_lenta()
            if self.anti_sat and not self.bypass:
                self._guardia_espectral(self.an_in.dt)

        if self.bypass:
            y = bloque
            if self.volumen != 1.0:        # el volumen sigue funcionando en Bypass
                y = np.clip(bloque * self.volumen, -1.0, 1.0).astype(np.float32, copy=False)
            self.igualador.reiniciar()    # al volver, converge de nuevo rápido
        else:
            self._aplicar_parametros()
            y = self.board(np.ascontiguousarray(bloque.T), self.sr, reset=False).T
            if self.mb_activo:
                y = self.mb.procesar(y, self.mb_params)
                if self.mb_auto and n:
                    self._auto_mb(n / self.sr)
            if self.anti_sat and n:
                self._vigilar_saturacion(float(np.max(np.abs(y))),
                                         float(np.max(np.abs(bloque))), n / self.sr)
            try:
                yt = np.ascontiguousarray(y.T, dtype=np.float32)
                self.realce.cantidad = self.golpe
                yt = self.realce.procesar(yt)
                self.nitidez.cantidad = self.nitidez_c
                yt = self.nitidez.procesar(yt)
                if self.iguala:
                    yt = self.igualador.procesar(np.ascontiguousarray(bloque.T), yt)
                if self.nivela:
                    yt = self.nivelador.procesar(yt)
                yt = np.ascontiguousarray(yt * self.volumen, dtype=np.float32)
                y = self.limite_final(yt, self.sr, reset=False).T
            except Exception as e:
                # Si una etapa nueva falla, se sigue con el sonido que ya venía.
                self.error_dsp = f"realce: {e}"
                y = y * self.volumen
            y = np.clip(y, -1.0, 1.0).astype(np.float32, copy=False)
        self.an_out.alimentar(y.mean(axis=1))
        self.pico_out = max(float(np.max(np.abs(y))) if n else 0.0,
                            self.pico_out * 0.92)
        return y

    def capturar_objetivo(self):
        forma = self.an_in.forma_lenta()
        if forma is None:
            return None
        self.objetivo = np.clip(np.round(forma, 1), -12.0, 12.0)
        self.objetivo[0] = np.clip(np.round(forma[0], 1), -20.0, 20.0)
        return [float(v) for v in self.objetivo]

    def reiniciar_analisis(self):
        self.an_in.reiniciar()
        self.auto[:] = 0.0
        self.forma_medida = None
        self.k_pico = 1.0
        self.k_banda[:] = 1.0
        self._sat = 0.0


# ------------------------------------------------------------------
# Enlace entre la ventana / el motor y el bus de audio del reproductor
# ------------------------------------------------------------------
class TuberiaInterna:
    """Misma interfaz que tenía la ventana con la tubería de captura, pero sobre el
    bus interno del reproductor (setup/bus_audio.py)."""

    def __init__(self, bus):
        self.bus = bus

    @property
    def motor(self):
        return self.bus.motor

    @property
    def anillo(self):
        return self.bus.anillo

    @property
    def activa(self):
        return bool(self.bus.activa and self.bus.eq_activo and self.bus.motor is not None)

    @property
    def sr(self):
        return self.bus.sr

    @property
    def colchon_seg(self):
        return self.bus.colchon_seg

    @property
    def ultimo_error(self):
        return self.bus.ultimo_error

    @property
    def xr_entrada(self):
        return 0

    @property
    def xr_salida(self):
        return self.bus.xr_salida

    @property
    def descartes(self):
        return 0

    @property
    def t_proc_max(self):
        return self.bus.t_proc_max

    @t_proc_max.setter
    def t_proc_max(self, valor):
        self.bus.t_proc_max = valor

    def aplicar_config(self, c):
        m = self.motor
        if m is None:
            return
        m.bypass = bool(c["bypass"])
        m.inteligente = bool(c["inteligente"])
        m.fuerza = c["fuerza_inteligente"] / 100.0
        m.objetivo = np.array(c["objetivo_bandas"], dtype=np.float64)
        m.limite_auto = float(c["limite_inteligente_db"])
        m.velocidad = c["velocidad_db_s"] / 10.0
        m.an_in.reinicio_auto = bool(c["reinicio_auto"])
        m.anti_sat = bool(c["anti_saturacion"])
        m.man_graves = float(c["graves_db"])
        m.man_presencia = float(c["presencia_db"])
        m.man_agudos = float(c["agudos_db"])
        m.ratio = float(c["compresion"])
        m.volumen = c["volumen"] / 100.0
        m.golpe = float(c.get("golpe_43", 70)) / 100.0
        m.nitidez_c = float(c.get("nitidez", 50)) / 100.0
        m.nivela = bool(c.get("nivelar_temas", False))
        m.iguala = bool(c.get("igualar_bypass", True))
        m.mb_activo = bool(c["mb_activo"])
        m.mb_auto = bool(c.get("mb_auto", True))
        m.mb_int = float(c.get("mb_intensidad", 60)) / 100.0
        m.mb.compensa_cruce.gain_db = 0.0 if m.mb_auto else CompresorMultibanda.COMPENSACION_CRUCE_DB
        if m.mb_auto:
            # Cruces fijos pensados para no tocar el cuerpo del bombo (43 Hz).
            m.mb.reconfigurar_cruces(150.0, 2500.0)
        else:
            m.mb.reconfigurar_cruces(float(c["mb_corte_bajos"]), float(c["mb_corte_altos"]))
            m.mb_params["graves"] = [float(v) for v in c["mb_graves"]]
            m.mb_params["medios"] = [float(v) for v in c["mb_medios"]]
            m.mb_params["agudos"] = [float(v) for v in c["mb_agudos"]]
            m.mb_params_base = {k: list(v) for k, v in m.mb_params.items()}


# ------------------------------------------------------------------
# Interfaz
# ------------------------------------------------------------------
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF, QFont, QLinearGradient, QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QPushButton, QSlider, QCheckBox, QTabWidget, QMessageBox,
    QDialog, QGroupBox, QSizePolicy, QFrame,
)

COLOR_ACENTO = "#3ddc84"
ESTILO = """
QWidget { background: #15171c; color: #d5d9e0; font-size: 9pt; }
QLabel#suave { color: #8a93a3; font-size: 8pt; }
QPushButton { background: #262b34; border: 1px solid #343a46; border-radius: 5px;
              padding: 4px 8px; }
QPushButton:hover { background: #2f3541; }
QPushButton:disabled { color: #6b7280; }
QTabWidget::pane { border: 1px solid #2a2f39; border-radius: 5px; top: -1px; }
QTabBar::tab { background: #b56f17; color: #ffffff; font-weight: bold; padding: 5px 10px;
               border: 1px solid #d98a1f; border-bottom: none; margin-right: 2px;
               border-top-left-radius: 5px; border-top-right-radius: 5px; }
QTabBar::tab:hover:!selected { background: #d98a1f; }
QTabBar::tab:selected { background: #f5b041; color: #1a1205; border-color: #f5b041; }
QSlider::groove:horizontal { height: 4px; background: #2f3541; border-radius: 2px; }
QSlider::handle:horizontal { background: #3ddc84; width: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::groove:vertical { width: 4px; background: #2f3541; border-radius: 2px; }
QSlider::handle:vertical { background: #3ddc84; height: 12px; margin: 0 -5px; border-radius: 6px; }
QComboBox { background: #20242c; border: 1px solid #343a46; border-radius: 4px; padding: 2px 6px; }
QComboBox QAbstractItemView { background: #20242c; selection-background-color: #2f3541; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #4a5160;
                       border-radius: 3px; background: #20242c; }
QCheckBox::indicator:checked { background: #3ddc84; border-color: #3ddc84; }
QToolTip { background: #20242c; color: #e6e9ef; border: 1px solid #f2a33a; padding: 6px; }
QGroupBox { border: 1px solid #2a2f39; border-radius: 5px; margin-top: 8px;
            padding-top: 6px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px;
                   color: #f2a33a; }
"""


class EspectroWidget(QWidget):
    DB_MIN, DB_MAX = -77.0, -2.0
    SEGMENTOS = 22
    PARADAS = [(0.00, "#18e0a0"), (0.45, "#7be83a"), (0.62, "#ffd400"),
               (0.82, "#ff8a00"), (1.00, "#ff2d6f")]
    IZQ, DER, ARRIBA, ABAJO = 8.0, 8.0, 20.0, 16.0
    MARGEN_BARRA = 2.0

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(90)
        self.setMaximumHeight(110)
        self.cent = None
        self.t_ent = self.t_sal = None
        self.v_ent = self.v_sal = None
        self.pico = None
        self.meta = None
        self.mensaje = tr("espectro_sin_audio")
        self._cache_clave = None
        self._fondo = None
        self._barra = None
        self.colores = [self._color_en(i / (self.SEGMENTOS - 1)) for i in range(self.SEGMENTOS)]
        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._cuadro)
        self._reloj.start(40)

    def _color_en(self, f):
        for (a, ca), (b, cb) in zip(self.PARADAS, self.PARADAS[1:]):
            if f <= b:
                t = 0.0 if b == a else (f - a) / (b - a)
                c1, c2 = QColor(ca), QColor(cb)
                return QColor(int(c1.red() + (c2.red() - c1.red()) * t),
                              int(c1.green() + (c2.green() - c1.green()) * t),
                              int(c1.blue() + (c2.blue() - c1.blue()) * t))
        return QColor(self.PARADAS[-1][1])

    def poner(self, cent, ent, sal, meta):
        self.cent = cent
        self.t_ent = np.asarray(ent, dtype=np.float64)
        self.t_sal = np.asarray(sal, dtype=np.float64)
        self.meta = meta
        self.mensaje = ""
        if self.v_sal is None or len(self.v_sal) != len(self.t_sal):
            self.v_ent = self.t_ent.copy()
            self.v_sal = self.t_sal.copy()
            self.pico = self.t_sal.copy()

    def vaciar(self, mensaje):
        self.t_ent = self.t_sal = self.v_ent = self.v_sal = self.pico = self.meta = None
        self.mensaje = mensaje
        self.update()

    def _cuadro(self):
        if self.t_sal is None or self.v_sal is None or not self.isVisible():
            return
        for v, t in ((self.v_sal, self.t_sal), (self.v_ent, self.t_ent)):
            dif = t - v
            v += np.where(dif > 0, dif * 0.55, dif * 0.18)
        self.pico = np.maximum(self.v_sal, self.pico - 0.7)
        self.update()

    def _nivel01(self, db):
        return np.clip((np.asarray(db) - self.DB_MIN) / (self.DB_MAX - self.DB_MIN), 0.0, 1.0)

    def _medidas(self):
        n = len(self.cent)
        ancho = self.width() - self.IZQ - self.DER
        alto = self.height() - self.ARRIBA - self.ABAJO
        slot = ancho / n
        return n, ancho, alto, slot, slot * 0.74

    def _x_frec(self, f, ancho, slot):
        a, b = np.log10(self.cent[0]), np.log10(self.cent[-1])
        return slot * 0.5 + (np.log10(max(f, 1.0)) - a) / (b - a) * (ancho - slot)

    def _nueva_imagen(self, w, h):
        dpr = self.devicePixelRatioF()
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        return pm

    def _preparar_imagenes(self):
        clave = (self.width(), self.height(), len(self.cent), self.devicePixelRatioF(), idioma_actual())
        if clave == self._cache_clave:
            return
        self._cache_clave = clave
        w, h = self.width(), self.height()
        n, ancho, alto, slot, bw = self._medidas()
        seg_h = alto / self.SEGMENTOS
        hueco = max(1.0, seg_h * 0.22)

        fondo = self._nueva_imagen(w, h)
        p = QPainter(fondo)
        p.setRenderHint(QPainter.Antialiasing, True)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor("#0a0c14"))
        grad.setColorAt(1.0, QColor("#1a1130"))
        p.setPen(QPen(QColor("#2b2f48"), 1))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 8, 8)
        p.translate(self.IZQ, self.ARRIBA)
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for k in range(1, 4):
            y = alto * k / 4.0
            p.drawLine(QPointF(0, y), QPointF(ancho, y))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 12))
        for i in range(n):
            x0 = slot * (i + 0.5) - bw / 2
            for s in range(self.SEGMENTOS):
                y0 = alto - (s + 1) * seg_h + hueco / 2
                p.drawRoundedRect(QRectF(x0, y0, bw, seg_h - hueco), 1.5, 1.5)
        fuente = QFont(self.font())
        fuente.setPointSize(7)
        p.setFont(fuente)
        p.setPen(QColor("#8f97b8"))
        for f, txt in ((63, "63"), (125, "125"), (250, "250"), (500, "500"), (1000, "1k"),
                       (2000, "2k"), (4000, "4k"), (8000, "8k"), (16000, "16k")):
            x = float(self._x_frec(f, ancho, slot))
            xr = min(max(x - 14, 0.0), ancho - 28)
            p.drawText(QRectF(xr, alto + 3, 28, 11), Qt.AlignCenter, txt)
        p.resetTransform()
        x = 12.0
        for txt, color in ((tr("leg_salida"), "#7be83a"), (tr("leg_entrada"), "#b79bff"), (tr("leg_objetivo"), "#ffb347")):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, 7, 8, 8), 2, 2)
            p.setPen(QColor("#c9cfe6"))
            ancho_txt = p.fontMetrics().horizontalAdvance(txt)
            p.drawText(QPointF(x + 12, 14.5), txt)
            x += 12 + ancho_txt + 14
        p.end()
        self._fondo = fondo

        m = self.MARGEN_BARRA
        barra = self._nueva_imagen(bw + 2 * m, alto)
        p = QPainter(barra)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        for s in range(self.SEGMENTOS):
            y0 = alto - (s + 1) * seg_h + hueco / 2
            r = QRectF(m, y0, bw, seg_h - hueco)
            c = self.colores[s]
            halo = QColor(c)
            halo.setAlpha(38)
            p.setBrush(halo)
            p.drawRoundedRect(r.adjusted(-1.6, -1.2, 1.6, 1.2), 2, 2)
            p.setBrush(c)
            p.drawRoundedRect(r, 1.5, 1.5)
        p.end()
        self._barra = barra

    def paintEvent(self, _):
        p = QPainter(self)
        if self.v_sal is None or self.cent is None:
            self._cache_clave = None if self.cent is None else self._cache_clave
            p.setRenderHint(QPainter.Antialiasing, True)
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0.0, QColor("#0a0c14"))
            grad.setColorAt(1.0, QColor("#1a1130"))
            p.setPen(QPen(QColor("#2b2f48"), 1))
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 8, 8)
            p.setPen(QColor("#6b7280"))
            p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignCenter, self.mensaje)
            return

        self._preparar_imagenes()
        n, ancho, alto, slot, bw = self._medidas()
        seg_h = alto / self.SEGMENTOS
        m = self.MARGEN_BARRA
        p.drawPixmap(0, 0, self._fondo)

        niv = self._nivel01(self.v_sal)
        picos = self._nivel01(self.pico)
        p.setClipping(True)
        for i in range(n):
            x0 = self.IZQ + slot * (i + 0.5) - bw / 2
            enc = int(niv[i] * self.SEGMENTOS + 0.5)
            if enc > 0:
                y_top = self.ARRIBA + alto - enc * seg_h
                p.setClipRect(QRectF(x0 - m, y_top - 2, bw + 2 * m, enc * seg_h + 4))
                p.drawPixmap(QPointF(x0 - m, self.ARRIBA), self._barra)
        p.setClipping(False)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#eaf7ff"))
        for i in range(n):
            x0 = self.IZQ + slot * (i + 0.5) - bw / 2
            yp = self.ARRIBA + alto - picos[i] * alto
            p.drawRect(QRectF(x0, max(self.ARRIBA, yp - 2.2), bw, 2.0))

        p.setRenderHint(QPainter.Antialiasing, True)
        p.translate(self.IZQ, self.ARRIBA)

        ent = self._nivel01(self.v_ent)
        puntos = [QPointF(slot * (i + 0.5), alto - ent[i] * alto) for i in range(n)]
        area = QPolygonF([QPointF(slot * 0.5, alto)] + puntos + [QPointF(slot * (n - 0.5), alto)])
        sombra = QLinearGradient(0, 0, 0, alto)
        sombra.setColorAt(0.0, QColor(150, 110, 255, 70))
        sombra.setColorAt(1.0, QColor(150, 110, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(sombra)
        p.drawPolygon(area)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(170, 135, 255, 90), 4))
        p.drawPolyline(QPolygonF(puntos))
        p.setPen(QPen(QColor("#b79bff"), 1.6))
        p.drawPolyline(QPolygonF(puntos))

        if self.meta is not None:
            fr, dbs = self.meta
            pts = [QPointF(float(self._x_frec(f, ancho, slot)),
                           float(alto - self._nivel01(d) * alto)) for f, d in zip(fr, dbs)]
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#ffb347"), 1.6, Qt.DashLine))
            p.drawPolyline(QPolygonF(pts))
            p.setPen(Qt.NoPen)
            for q in pts:
                p.setBrush(QColor(255, 179, 71, 60))
                p.drawEllipse(q, 5.0, 5.0)
                p.setBrush(QColor("#ffcf80"))
                p.drawEllipse(q, 2.6, 2.6)


COLORES_BOTON = {
    "cable": "#17a673",
    "cable_activo": "#d64545",
    "Natural": "#2aa198",
    "Brillante": "#1e90ff",
    "Cálido": "#e0533d",
    "capturar": "#8b5cf6",
    "inicio": "#5b6cf0",
    "inicio_activo": "#d64545",
    "reset": "#7f8c8d",
}

def estilo_boton(color):
    c = QColor(color)
    claro, oscuro = c.lighter(118).name(), c.darker(125).name()
    return (f"QPushButton {{ background: {color}; color: #ffffff; font-weight: bold; "
            f"border: 1px solid {claro}; border-radius: 5px; padding: 4px 8px; }}"
            f"QPushButton:hover {{ background: {claro}; }}"
            f"QPushButton:pressed {{ background: {oscuro}; }}"
            f"QPushButton:disabled {{ background: #2a2e38; color: #6b7280; "
            f"border-color: #343a46; }}")


def cargar_config():
    c = dict(CONFIG_DEFECTO)
    try:
        with open(CONFIG_ARCHIVO, "r", encoding="utf-8") as f:
            c.update(json.load(f))
    except Exception:
        pass
    for vieja in ("nivelador", "objetivo_brillo"):      # claves de versiones anteriores
        c.pop(vieja, None)
    for clave in ("golpe_43", "nitidez", "mb_intensidad"):
        try:
            c[clave] = int(min(100, max(0, float(c[clave]))))
        except (TypeError, ValueError):
            c[clave] = CONFIG_DEFECTO[clave]
    for clave in ("mb_auto", "nivelar_temas", "igualar_bypass"):
        c[clave] = bool(c.get(clave, CONFIG_DEFECTO[clave]))
    ob = c.get("objetivo_bandas")
    if isinstance(ob, list) and len(ob) == len(BANDAS_HZ) - 1:
        try:
            c["objetivo_bandas"] = [round(float(ob[0]) - 1.0, 1)] + [float(v) for v in ob]
        except (TypeError, ValueError):
            pass
    if not (isinstance(c.get("objetivo_bandas"), list) and
            len(c["objetivo_bandas"]) == len(BANDAS_HZ)):
        c["objetivo_bandas"] = list(PERFILES["Natural"])
    for clave, valor in (("mb_activo", CONFIG_DEFECTO["mb_activo"]),
                         ("mb_corte_bajos", CONFIG_DEFECTO["mb_corte_bajos"]),
                         ("mb_corte_altos", CONFIG_DEFECTO["mb_corte_altos"]),
                         ("mb_graves", CONFIG_DEFECTO["mb_graves"]),
                         ("mb_medios", CONFIG_DEFECTO["mb_medios"]),
                         ("mb_agudos", CONFIG_DEFECTO["mb_agudos"])):
        if clave not in c:
            c[clave] = list(valor) if isinstance(valor, list) else valor
        elif isinstance(valor, list):
            try:
                c[clave] = [float(x) for x in c[clave]][:len(valor)]
                while len(c[clave]) < len(valor):
                    c[clave].append(float(valor[len(c[clave])]))
            except (TypeError, ValueError):
                c[clave] = list(valor)
    return c


def guardar_config(c):
    tmp = CONFIG_ARCHIVO + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_ARCHIVO)   # escritura atómica: no deja el JSON a medias
    except Exception:
        pass


RADIO_ESQUINAS = 10
COLOR_BORDE_VENTANA = "#4a4a4a"


def aplicar_esquinas_redondeadas(widget, radio=RADIO_ESQUINAS):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = int(widget.winId())
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        ancho, alto = rect.right - rect.left, rect.bottom - rect.top
        if ancho <= 0 or alto <= 0:
            return
        d = radio * 2
        hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, ancho + 1, alto + 1, d, d)
        ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass


def desactivar_transiciones_dwm(widget):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        valor = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(widget.winId())), ctypes.c_uint(3),
            ctypes.byref(valor), ctypes.sizeof(valor))
    except Exception:
        pass


class VentanaEcualizador(QWidget):
    def __init__(self, padre=None):
        super().__init__(padre)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint
                            | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint
                            | Qt.WindowCloseButtonHint)

    def showEvent(self, e):
        super().showEvent(e)
        aplicar_esquinas_redondeadas(self)
        desactivar_transiciones_dwm(self)
        tm = getattr(self, "_timer_refresco", None)
        if tm is not None:
            tm.start(80)

    def hideEvent(self, e):
        super().hideEvent(e)
        tm = getattr(self, "_timer_refresco", None)
        if tm is not None:
            tm.stop()

    def closeEvent(self, e):
        cb = getattr(self, "_al_cerrar", None)
        if cb is not None:
            cb()
        super().closeEvent(e)

    def _ajustar_alto_minimo(self):
        """Arranca con la altura mínima en la que entra todo (así no hace falta achicarla
        a mano). Qt sube el 1 hasta el mínimo real que piden los controles."""
        try:
            lay = self.layout()
            if lay is not None:
                lay.activate()
            self.resize(self.width(), 1)
        except Exception:
            pass

    def mostrar_primera_vez(self):
        self.show()
        self._ajustar_alto_minimo()
        poner_abajo_izquierda(self)
        self.anclar_abajo_izquierda()

    def anclar_abajo_izquierda(self, ms=2500):
        self._anclar = True
        QTimer.singleShot(ms, lambda: setattr(self, "_anclar", False))
        for t in (0, 150, 600, 1500):
            QTimer.singleShot(t, lambda: poner_abajo_izquierda(self) if self._anclar else None)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        aplicar_esquinas_redondeadas(self)
        if getattr(self, "_anclar", False):
            poner_abajo_izquierda(self)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange and self.isMaximized():
            self.setWindowState(self.windowState() & ~Qt.WindowMaximized)

    def paintEvent(self, e):
        from PySide6.QtWidgets import QStyleOption, QStyle
        from PySide6.QtGui import QPainterPath
        p = QPainter(self)
        op = QStyleOption()
        op.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, op, p, self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(COLOR_BORDE_VENTANA), 1))
        p.setBrush(Qt.NoBrush)
        w, h, r = self.width() - 1.0, self.height() - 1.0, float(RADIO_ESQUINAS)
        ruta = QPainterPath()
        ruta.moveTo(0.5, 0.5)
        ruta.lineTo(w, 0.5)
        ruta.lineTo(w, h - r)
        ruta.arcTo(w - 2 * r, h - 2 * r, 2 * r, 2 * r, 0, -90)
        ruta.lineTo(r + 0.5, h)
        ruta.arcTo(0.5, h - 2 * r, 2 * r, 2 * r, 270, -90)
        ruta.lineTo(0.5, 0.5)
        p.drawPath(ruta)
        p.end()


MARGEN_IZQ_PX = 8
MARGEN_ABAJO_PX = 6


def poner_abajo_izquierda(ventana):
    try:
        pantalla = ventana.screen() or QApplication.primaryScreen()
        area = pantalla.availableGeometry()
        extra_alto = ventana.frameGeometry().height() - ventana.geometry().height()
        alto_total = ventana.height() + max(0, extra_alto)
        x = area.left() + MARGEN_IZQ_PX
        y = area.bottom() + 1 - MARGEN_ABAJO_PX - alto_total
        ventana.move(x, max(area.top(), y))
        aplicar_esquinas_redondeadas(ventana)
    except Exception:
        pass


def crear_ventana(bus, config, app=None, padre=None):
    app = app or QApplication.instance()
    establecer_idioma(config.get("idioma", IDIOMA_POR_DEFECTO))
    AYUDA_INTELIGENTE, AYUDA_BYPASS, AYUDA_REINICIO, AYUDA_MAX_CORRECCION, AYUDA_VELOCIDAD = (
        ayuda_de(k) for k in ("inteligente", "bypass", "reinicio", "maxcorr", "velocidad"))
    tuberia = TuberiaInterna(bus)
    cable = None          # ya no hay cable virtual

    w = VentanaEcualizador(padre)
    w.setStyleSheet(ESTILO)
    w.setWindowTitle(tr("ventana_titulo"))
    w.setMinimumWidth(400)
    w.setMaximumHeight(590)
    w.resize(780, 590)
    raiz = QVBoxLayout(w)
    raiz.setContentsMargins(6, 6, 6, 6)
    raiz.setSpacing(4)

    def suave(texto=""):
        e = QLabel(texto)
        e.setObjectName("suave")
        e.setWordWrap(True)
        return e

    def deslizador(grilla, fila, texto, minimo, maximo, valor, formato, ayuda_html=""):
        etiqueta = QLabel(texto)
        grilla.addWidget(etiqueta, fila, 0)
        s = QSlider(Qt.Horizontal)
        s.setRange(minimo, maximo)
        s.setValue(int(valor))
        lbl = QLabel(formato(s.value()))
        lbl.setMinimumWidth(62)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        s.valueChanged.connect(lambda v, l=lbl: l.setText(formato(v)))
        grilla.addWidget(s, fila, 1)
        grilla.addWidget(lbl, fila, 2)
        s._etq, s._lbl = etiqueta, lbl
        if ayuda_html:
            for wdg in (etiqueta, s, lbl):
                wdg.setToolTip(ayuda_html)
        return s

    # ---- conexión + gráfico ----
    btn_cable = QPushButton(tr("btn_conectar"))
    btn_cable.setFixedHeight(24)
    btn_cable.setMinimumWidth(150)
    btn_cable.setMaximumWidth(240)
    btn_cable.setStyleSheet(estilo_boton(COLORES_BOTON["cable"]))
    btn_pos = QPushButton("\u2199")
    btn_pos.setFixedSize(28, 24)
    btn_pos.setStyleSheet("QPushButton { padding: 0px; font-size: 11pt; }")
    btn_pos.setToolTip(tr("tt_reposicionar"))
    btn_pos.clicked.connect(lambda: poner_abajo_izquierda(w))
    fila_cable = QHBoxLayout()
    fila_cable.setSpacing(4)
    fila_cable.addWidget(btn_cable)
    fila_cable.addWidget(btn_pos)
    btn_aj = QPushButton("\u2699")
    btn_aj.setFixedSize(28, 24)
    btn_aj.setStyleSheet("QPushButton { padding: 0px; font-size: 11pt; }")
    btn_aj.setToolTip(tr("tt_ajustes"))
    fila_cable.addWidget(btn_aj)
    fila_cable.addStretch(1)
    raiz.addLayout(fila_cable)
    lbl_cable = suave(tr("cable_info"))
    raiz.addWidget(lbl_cable)

    grafico = EspectroWidget()
    raiz.addWidget(grafico)

    # ---- controles principales ----
    fila = QGridLayout()
    chk_ia = QCheckBox(tr("chk_inteligente"))
    chk_ia.setChecked(config["inteligente"])
    chk_ia.setToolTip(AYUDA_INTELIGENTE)
    fila.addWidget(chk_ia, 0, 0)
    s_fuerza = QSlider(Qt.Horizontal)
    s_fuerza.setRange(0, 100)
    s_fuerza.setValue(config["fuerza_inteligente"])
    lbl_fuerza = QLabel(f"{s_fuerza.value()} %")
    lbl_fuerza.setMinimumWidth(40)
    lbl_fuerza.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s_fuerza.valueChanged.connect(lambda v: lbl_fuerza.setText(f"{v} %"))
    s_fuerza.setToolTip(AYUDA_INTELIGENTE)
    lbl_fuerza.setToolTip(AYUDA_INTELIGENTE)
    fila.addWidget(s_fuerza, 0, 1)
    fila.addWidget(lbl_fuerza, 0, 2)
    chk_bypass = QCheckBox(tr("chk_bypass"))
    chk_bypass.setChecked(config["bypass"])
    chk_bypass.setToolTip(AYUDA_BYPASS)
    fila.addWidget(chk_bypass, 1, 0)
    s_vol = QSlider(Qt.Horizontal)
    s_vol.setRange(0, 150)
    s_vol.setValue(config["volumen"])
    lbl_vol = QLabel(f"{tr('vol')} {s_vol.value()} %")
    lbl_vol.setMinimumWidth(52)
    lbl_vol.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s_vol.valueChanged.connect(lambda v: lbl_vol.setText(f"{tr('vol')} {v} %"))
    fila.addWidget(s_vol, 1, 1)
    fila.addWidget(lbl_vol, 1, 2)
    chk_reinicio = QCheckBox(tr("chk_reinicio"))
    chk_reinicio.setChecked(config["reinicio_auto"])
    chk_reinicio.setToolTip(AYUDA_REINICIO)
    fila.addWidget(chk_reinicio, 2, 0, 1, 3)
    fila.setColumnStretch(1, 1)
    raiz.addLayout(fila)

    # ---- pestañas ----
    pestanas = QTabWidget()
    pestanas.setMaximumHeight(345)
    raiz.addWidget(pestanas)

    # Pestaña Objetivo
    t_obj = QWidget()
    lo = QVBoxLayout(t_obj)
    lo.setContentsMargins(6, 6, 6, 6)
    lo.setSpacing(4)
    lbl_obj_ayuda = suave(tr("obj_ayuda"))
    lo.addWidget(lbl_obj_ayuda)
    fb = QHBoxLayout()
    botones_perfil = {}
    for nombre in list(PERFILES.keys()):
        b = QPushButton(tr("perfil_" + nombre))
        b.setMinimumHeight(26)
        b.setStyleSheet(estilo_boton(COLORES_BOTON[nombre]))
        botones_perfil[nombre] = b
        fb.addWidget(b)
    btn_capturar = QPushButton(tr("btn_capturar"))
    btn_capturar.setMinimumHeight(26)
    btn_capturar.setStyleSheet(estilo_boton(COLORES_BOTON["capturar"]))
    btn_capturar.setToolTip(tr("tt_capturar"))
    fb.addWidget(btn_capturar)
    lo.addLayout(fb)
    gb = QGridLayout()
    gb.setHorizontalSpacing(6)
    s_obj, lbl_obj = [], []
    for i, nombre in enumerate(NOMBRES_BANDAS):
        lv = QLabel(f"{config['objetivo_bandas'][i]:+.1f}")
        lv.setAlignment(Qt.AlignCenter)
        lv.setObjectName("suave")
        sv = QSlider(Qt.Vertical)
        sv.setRange(-200, 200) if i == 0 else sv.setRange(-120, 120)
        if i == 0:
            sv.setToolTip(tr("tt_43hz"))
        sv.setValue(int(round(config["objetivo_bandas"][i] * 10)))
        sv.setMinimumHeight(80)
        sv.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        sv.valueChanged.connect(lambda v, l=lv: l.setText(f"{v / 10:+.1f}"))
        ln = QLabel(nombre)
        ln.setAlignment(Qt.AlignCenter)
        ln.setObjectName("suave")
        gb.addWidget(lv, 0, i, Qt.AlignHCenter)
        gb.addWidget(sv, 1, i, Qt.AlignHCenter)
        gb.addWidget(ln, 2, i, Qt.AlignHCenter)
        s_obj.append(sv)
        lbl_obj.append(lv)
    gb.setRowStretch(1, 1)
    lo.addLayout(gb, 1)
    pestanas.addTab(t_obj, tr("tab_objetivo"))

    # Pestaña Manual
    t_man = QWidget()
    gm = QGridLayout(t_man)
    gm.setContentsMargins(6, 8, 6, 6)
    s_graves = deslizador(gm, 0, tr("man_graves"), -100, 100, config["graves_db"] * 10,
                          lambda v: f"{v / 10:+.1f} dB")
    s_pres = deslizador(gm, 1, tr("man_presencia"), -100, 100, config["presencia_db"] * 10,
                        lambda v: f"{v / 10:+.1f} dB")
    s_agudos = deslizador(gm, 2, tr("man_agudos"), -100, 100, config["agudos_db"] * 10,
                          lambda v: f"{v / 10:+.1f} dB")
    s_comp = deslizador(gm, 3, tr("man_compresion"), 1, 8, config["compresion"],
                        lambda v: tr("comp_no") if v == 1 else f"{v}:1")
    chk_anti = QCheckBox(tr("chk_anti"))
    chk_anti.setChecked(config["anti_saturacion"])
    chk_anti.setToolTip(tr("tt_anti"))
    gm.addWidget(chk_anti, 4, 0, 1, 3)
    lbl_anti = suave("")
    gm.addWidget(lbl_anti, 5, 0, 1, 3)
    lbl_man_nota = suave(tr("man_nota"))
    gm.addWidget(lbl_man_nota, 6, 0, 1, 3)
    gm.setRowStretch(7, 1)
    pestanas.addTab(t_man, tr("tab_manual"))

    # Pestaña Multibanda
    t_mb = QWidget()
    gmb = QGridLayout(t_mb)
    gmb.setContentsMargins(6, 3, 6, 2)
    gmb.setVerticalSpacing(3)
    gmb.setHorizontalSpacing(8)

    fila_top = QHBoxLayout()
    chk_mb = QCheckBox(tr("mb_activar"))
    chk_mb.setChecked(config["mb_activo"])
    chk_mb.setToolTip(tr("tt_mb_activar"))
    fila_top.addWidget(chk_mb)
    fila_top.addSpacing(12)
    lbl_cb = QLabel(tr("mb_corte_bajos"))
    lbl_cb.setToolTip(tr("tt_mb_corte_bajos"))
    s_cb = QSlider(Qt.Horizontal)
    s_cb.setRange(60, 500)
    s_cb.setValue(int(config["mb_corte_bajos"]))
    s_cb.setMinimumWidth(90)
    lbl_cb_v = QLabel(f"{s_cb.value()} Hz")
    lbl_cb_v.setMinimumWidth(52)
    lbl_cb_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s_cb.valueChanged.connect(lambda v, l=lbl_cb_v: l.setText(f"{v} Hz"))
    fila_top.addWidget(lbl_cb)
    fila_top.addWidget(s_cb)
    fila_top.addWidget(lbl_cb_v)
    fila_top.addSpacing(12)
    lbl_ca = QLabel(tr("mb_corte_altos"))
    lbl_ca.setToolTip(tr("tt_mb_corte_altos"))
    s_ca = QSlider(Qt.Horizontal)
    s_ca.setRange(1000, 6000)
    s_ca.setValue(int(config["mb_corte_altos"]))
    s_ca.setMinimumWidth(90)
    lbl_ca_v = QLabel(f"{s_ca.value()} Hz")
    lbl_ca_v.setMinimumWidth(52)
    lbl_ca_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s_ca.valueChanged.connect(lambda v, l=lbl_ca_v: l.setText(f"{v} Hz"))
    fila_top.addWidget(lbl_ca)
    fila_top.addWidget(s_ca)
    fila_top.addWidget(lbl_ca_v)
    fila_top.addStretch(1)
    # Fila de modo automático (lo normal) + intensidad + estado en vivo.
    chk_mb_auto = QCheckBox(tr("mb_auto"))
    chk_mb_auto.setChecked(bool(config["mb_auto"]))
    chk_mb_auto.setToolTip(tr("tt_mb_auto"))
    lbl_mb_int = QLabel(tr("mb_intensidad"))
    lbl_mb_int.setToolTip(tr("tt_mb_intensidad"))
    s_mb_int = QSlider(Qt.Horizontal)
    s_mb_int.setRange(0, 100)
    s_mb_int.setValue(int(config["mb_intensidad"]))
    s_mb_int.setMinimumWidth(120)
    s_mb_int.setToolTip(tr("tt_mb_intensidad"))
    lbl_mb_int_v = QLabel(f"{s_mb_int.value()} %")
    lbl_mb_int_v.setMinimumWidth(44)
    lbl_mb_int_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s_mb_int.valueChanged.connect(lambda v, l=lbl_mb_int_v: l.setText(f"{v} %"))
    fila_auto = QHBoxLayout()
    fila_auto.addWidget(chk_mb_auto)
    fila_auto.addSpacing(12)
    fila_auto.addWidget(lbl_mb_int)
    fila_auto.addWidget(s_mb_int, 1)
    fila_auto.addWidget(lbl_mb_int_v)
    lbl_mb_auto_info = suave(tr("mb_auto_info"))
    col_top = QVBoxLayout()
    col_top.setSpacing(4)
    col_top.addLayout(fila_top)
    col_top.addLayout(fila_auto)
    col_top.addWidget(lbl_mb_auto_info)
    gmb.addLayout(col_top, 0, 0, 1, 10)

    # Refs para poder actualizar los tooltips al cambiar de idioma en vivo.
    # Cada elemento: (etiqueta_de_columna, slider, label_valor, clave_tt)
    refs_mb = []
    cabs_mb = []

    def fila_banda(fila, etiqueta_banda, valores):
        cab = QLabel(etiqueta_banda)
        cab.setStyleSheet("font-weight: bold; color: #f2a33a;")
        cab.setMinimumWidth(60)
        gmb.addWidget(cab, fila, 0)
        cabs_mb.append(cab)

        s_u = QSlider(Qt.Horizontal); s_u.setRange(-40, 0);    s_u.setValue(int(valores[0]))
        s_r = QSlider(Qt.Horizontal); s_r.setRange(10, 80);    s_r.setValue(int(valores[1] * 10))
        s_a = QSlider(Qt.Horizontal); s_a.setRange(1, 200);    s_a.setValue(int(valores[2]))
        s_l = QSlider(Qt.Horizontal); s_l.setRange(10, 800);   s_l.setValue(int(valores[3]))
        s_m = QSlider(Qt.Horizontal); s_m.setRange(-120, 120); s_m.setValue(int(valores[4] * 10))

        def _celda(col, sl, fmt, tt_clave):
            sl.setMinimumWidth(80)
            gmb.addWidget(sl, fila, col)
            lbl = QLabel(fmt(sl.value()))
            lbl.setObjectName("suave")
            lbl.setMinimumWidth(56)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            sl.valueChanged.connect(lambda v, l=lbl, f=fmt: l.setText(f(v)))
            gmb.addWidget(lbl, fila, col + 1)
            sl.setToolTip(tr(tt_clave))    # tooltip sobre el slider
            refs_mb.append((None, sl, lbl, tt_clave))

        _celda(1, s_u, lambda v: f"{v} dB",           "tt_mb_umbral")
        _celda(3, s_r, lambda v: "1:1" if v <= 10 else f"{v / 10:.1f}:1",
                                                       "tt_mb_ratio")
        _celda(5, s_a, lambda v: f"{v} ms",           "tt_mb_attack")
        _celda(7, s_l, lambda v: f"{v} ms",           "tt_mb_release")
        _celda(9, s_m, lambda v: f"{v / 10:+.1f} dB", "tt_mb_makeup")
        return s_u, s_r, s_a, s_l, s_m

    # Cabeceras de columna, con tooltip. Las guardamos en refs_mb para poder
    # traducirlas en vivo sin tener que reconstruir toda la pestaña.
    cabeceras_mb = []
    for col, (txt, tt_clave) in zip(
            (1, 3, 5, 7, 9),
            ((tr("mb_umbral"),  "tt_mb_umbral"),
             (tr("mb_ratio"),   "tt_mb_ratio"),
             (tr("mb_attack"),  "tt_mb_attack"),
             (tr("mb_release"), "tt_mb_release"),
             (tr("mb_makeup"),  "tt_mb_makeup"))):
        e = QLabel(txt)
        e.setObjectName("suave")
        e.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        e.setToolTip(tr(tt_clave))
        gmb.addWidget(e, 1, col, 1, 2)
        # Guardamos la etiqueta junto con su clave de texto y de tooltip.
        # (idx_texto apunta a TEXTOS: mb_umbral, mb_ratio, mb_attack, mb_release, mb_makeup)
        cabeceras_mb.append((e, tt_clave,
                             "mb_" + {"tt_mb_umbral": "umbral",
                                      "tt_mb_ratio": "ratio",
                                      "tt_mb_attack": "attack",
                                      "tt_mb_release": "release",
                                      "tt_mb_makeup": "makeup"}[tt_clave]))

    mb_graves = fila_banda(2, tr("mb_banda_graves"), config["mb_graves"])
    mb_medios = fila_banda(3, tr("mb_banda_medios"), config["mb_medios"])
    mb_agudos = fila_banda(4, tr("mb_banda_agudos"), config["mb_agudos"])

    lbl_mb_nota = suave(tr("mb_nota"))
    gmb.addWidget(lbl_mb_nota, 5, 0, 1, 10)
    btn_mb_reset = QPushButton(tr("mb_reset"))
    btn_mb_reset.setMinimumHeight(26)
    btn_mb_reset.setStyleSheet(estilo_boton(COLORES_BOTON["reset"]))
    btn_mb_reset.setToolTip(tr("tt_mb_reset"))
    gmb.addWidget(btn_mb_reset, 6, 0, 1, 10)
    gmb.setRowStretch(7, 1)
    pestanas.addTab(t_mb, tr("tab_multibanda"))

    # Panel en vivo (solo en automático): medidores que se mueven solos.
    panel_auto = QWidget()
    gpa = QGridLayout(panel_auto)
    gpa.setContentsMargins(0, 2, 0, 0)
    gpa.setHorizontalSpacing(8)
    gpa.setVerticalSpacing(5)
    columnas_pa = (
        ("pa_nivel", -60, 0, lambda v: f"{v} dB"),
        ("pa_umbral", -60, 0, lambda v: f"{v} dB"),
        ("pa_ratio", 10, 30, lambda v: "1:1" if v <= 10 else f"{v / 10:.1f}:1"),
        ("pa_reduccion", 0, 120, lambda v: "0.0 dB" if v == 0 else f"-{v / 10:.1f} dB"),
        ("pa_compensa", 0, 40, lambda v: f"+{v / 10:.1f} dB"),
    )
    cabeceras_pa = []
    for j, (clave, _mn, _mx, _f) in enumerate(columnas_pa):
        e = QLabel(tr(clave))
        e.setObjectName("suave")
        e.setToolTip(tr("tt_" + clave))
        gpa.addWidget(e, 0, 1 + 2 * j, 1, 2)
        cabeceras_pa.append((e, clave))
    medidores_mb = {}
    etiquetas_banda_pa = []
    for i, (banda, clave_b) in enumerate((("graves", "mb_banda_graves"),
                                          ("medios", "mb_banda_medios"),
                                          ("agudos", "mb_banda_agudos"))):
        cab = QLabel(tr(clave_b))
        cab.setStyleSheet("font-weight: bold; color: #f2a33a;")
        cab.setMinimumWidth(60)
        gpa.addWidget(cab, 1 + i, 0)
        etiquetas_banda_pa.append((cab, clave_b))
        medidores_mb[banda] = {}
        for j, (clave, mn, mx, fmt) in enumerate(columnas_pa):
            sl = QSlider(Qt.Horizontal)
            sl.setRange(mn, mx)
            sl.setValue(mn)
            sl.setMinimumWidth(80)
            sl.setAttribute(Qt.WA_TransparentForMouseEvents, True)   # solo lectura
            sl.setFocusPolicy(Qt.NoFocus)
            sl.setToolTip(tr("tt_" + clave))
            lbl = QLabel(fmt(sl.value()))
            lbl.setObjectName("suave")
            lbl.setMinimumWidth(56)
            sl.valueChanged.connect(lambda v, l=lbl, f=fmt: l.setText(f(v)))
            gpa.addWidget(sl, 1 + i, 1 + 2 * j)
            gpa.addWidget(lbl, 1 + i, 2 + 2 * j)
            medidores_mb[banda][clave] = sl
    gpa.setRowStretch(4, 1)
    gmb.addWidget(panel_auto, 1, 0, 6, 10)

    def actualizar_panel_auto(m):
        for i, banda in enumerate(("graves", "medios", "agudos")):
            p = m.mb_params[banda]
            valores = {"pa_nivel": m.mb_niveles[i], "pa_umbral": p[0], "pa_ratio": p[1] * 10.0,
                       "pa_reduccion": m.mb_gr[i] * 10.0, "pa_compensa": p[4] * 10.0}
            for clave, sl in medidores_mb[banda].items():
                sl.setValue(int(round(float(valores[clave]))))

    # En automático se ocultan los controles manuales (corte, umbral, ratio...).
    def aplicar_modo_mb(*_):
        auto = chk_mb_auto.isChecked()
        manuales = [lbl_cb, s_cb, lbl_cb_v, lbl_ca, s_ca, lbl_ca_v, lbl_mb_nota, btn_mb_reset]
        manuales += [e for e, _tt, _tx in cabeceras_mb] + cabs_mb
        for _e, sl, lbl, _tt in refs_mb:
            manuales += [sl, lbl]
        for wdg in manuales:
            wdg.setVisible(not auto)
        for wdg in (lbl_mb_int, s_mb_int, lbl_mb_int_v, lbl_mb_auto_info, panel_auto):
            wdg.setVisible(auto)

    chk_mb_auto.toggled.connect(aplicar_modo_mb)
    aplicar_modo_mb()

    # Bloque "Avanzado": ya no es una solapa aparte, va al pie de Multibanda (siempre visible,
    # tanto en modo manual como automático).
    t_av = QWidget()
    ga = QGridLayout(t_av)
    ga.setContentsMargins(0, 0, 0, 0)
    ga.setVerticalSpacing(3)
    s_lim = deslizador(ga, 0, tr("av_max_corr"), 1, 10, config["limite_inteligente_db"],
                       lambda v: f"±{v} dB", AYUDA_MAX_CORRECCION)
    s_vel = deslizador(ga, 1, tr("av_velocidad"), 1, 40, config["velocidad_db_s"],
                       lambda v: f"{v / 10:.1f} dB/s", AYUDA_VELOCIDAD)
    cb_ent, cb_sal = QComboBox(), QComboBox()
    cb_ent.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    cb_sal.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    cb_ent.setMinimumContentsLength(10)
    cb_sal.setMinimumContentsLength(10)
    lbl_av_ent, lbl_av_sal = QLabel(tr("av_entrada")), QLabel(tr("av_salida"))
    ga.addWidget(lbl_av_ent, 3, 0)
    ga.addWidget(cb_ent, 3, 1, 1, 2)
    ga.addWidget(lbl_av_sal, 4, 0)
    ga.addWidget(cb_sal, 4, 1, 1, 2)
    for _x in (lbl_av_ent, cb_ent):
        _x.setVisible(False)       # sin cable no hay "entrada"
    btn_inicio = QPushButton(tr("btn_iniciar"))
    btn_inicio.setMinimumHeight(26)
    btn_inicio.setStyleSheet(estilo_boton(COLORES_BOTON["inicio"]))
    ga.addWidget(btn_inicio, 5, 0, 1, 3)
    lbl_estado = suave(tr("est_detenido"))
    ga.addWidget(lbl_estado, 6, 0, 1, 3)
    cont_av = QWidget()
    v_av = QVBoxLayout(cont_av)
    v_av.setContentsMargins(0, 0, 0, 0)
    v_av.setSpacing(2)
    linea_av = QFrame()
    linea_av.setFrameShape(QFrame.HLine)
    linea_av.setStyleSheet("color: #2a2f39;")
    lbl_tit_av = QLabel(tr("tab_avanzado"))
    lbl_tit_av.setStyleSheet("font-weight: bold; color: #f2a33a;")
    v_av.addWidget(linea_av)
    v_av.addWidget(lbl_tit_av)
    v_av.addWidget(t_av)
    gmb.addWidget(cont_av, 8, 0, 1, 10)

    # Pestaña Realce
    t_re = QWidget()
    gre = QGridLayout(t_re)
    gre.setContentsMargins(6, 8, 6, 6)
    s_golpe = deslizador(gre, 0, tr("re_golpe"), 0, 100, config["golpe_43"],
                         lambda v: f"{v} %", tr("tt_re_golpe"))
    s_nit = deslizador(gre, 1, tr("re_nitidez"), 0, 100, config["nitidez"],
                       lambda v: f"{v} %", tr("tt_re_nitidez"))
    chk_igu = QCheckBox(tr("chk_igualar"))
    chk_igu.setChecked(bool(config["igualar_bypass"]))
    chk_igu.setToolTip(tr("tt_igualar"))
    gre.addWidget(chk_igu, 2, 0, 1, 3)
    chk_niv = QCheckBox(tr("chk_nivelador"))
    chk_niv.setChecked(bool(config["nivelar_temas"]))
    chk_niv.setToolTip(tr("tt_nivelador"))
    gre.addWidget(chk_niv, 3, 0, 1, 3)
    lbl_re_nota = suave(tr("re_nota"))
    gre.addWidget(lbl_re_nota, 4, 0, 1, 3)
    gre.setRowStretch(5, 1)
    pestanas.addTab(t_re, tr("tab_realce"))

    lbl_diag = suave("")
    lbl_diag.setMinimumHeight(28)
    lbl_diag.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    raiz.addWidget(lbl_diag)

    # ---- dispositivo de salida ----
    def llenar_dispositivos():
        try:
            sal = bus.listar_salidas()
        except Exception as e:
            lbl_estado.setText(tr("est_no_audio", e=e))
            return
        cb_sal.clear()
        for i, texto in sal:
            cb_sal.addItem(texto, i)
        guardado = config.get("dispositivo_salida", "")
        elegido = -1
        for k in range(cb_sal.count()):
            if cb_sal.itemData(k) == bus.dispositivo_actual:
                elegido = k
                break
            if guardado and cb_sal.itemText(k) == guardado and elegido < 0:
                elegido = k
        if elegido >= 0:
            cb_sal.setCurrentIndex(elegido)

    llenar_dispositivos()

    def leer_config():
        config.update({
            "dispositivo_entrada": "",
            "dispositivo_salida": cb_sal.currentText(),
            "bypass": chk_bypass.isChecked(),
            "inteligente": chk_ia.isChecked(),
            "reinicio_auto": chk_reinicio.isChecked(),
            "fuerza_inteligente": s_fuerza.value(),
            "objetivo_bandas": [s.value() / 10.0 for s in s_obj],
            "limite_inteligente_db": s_lim.value(),
            "velocidad_db_s": s_vel.value(),
            "anti_saturacion": chk_anti.isChecked(),
            "graves_db": s_graves.value() / 10.0,
            "presencia_db": s_pres.value() / 10.0,
            "agudos_db": s_agudos.value() / 10.0,
            "compresion": s_comp.value(),
            "volumen": s_vol.value(),
            "golpe_43": s_golpe.value(),
            "nitidez": s_nit.value(),
            "nivelar_temas": chk_niv.isChecked(),
            "igualar_bypass": chk_igu.isChecked(),
            "mb_activo": chk_mb.isChecked(),
            "mb_auto": chk_mb_auto.isChecked(),
            "mb_intensidad": s_mb_int.value(),
            "mb_corte_bajos": s_cb.value(),
            "mb_corte_altos": s_ca.value(),
            "mb_graves": [mb_graves[0].value(),
                          mb_graves[1].value() / 10.0,
                          mb_graves[2].value(),
                          mb_graves[3].value(),
                          mb_graves[4].value() / 10.0],
            "mb_medios": [mb_medios[0].value(),
                          mb_medios[1].value() / 10.0,
                          mb_medios[2].value(),
                          mb_medios[3].value(),
                          mb_medios[4].value() / 10.0],
            "mb_agudos": [mb_agudos[0].value(),
                          mb_agudos[1].value() / 10.0,
                          mb_agudos[2].value(),
                          mb_agudos[3].value(),
                          mb_agudos[4].value() / 10.0],
        })

    def al_cambiar(*_):
        leer_config()
        tuberia.aplicar_config(config)

    for s in [s_fuerza, s_vol, s_graves, s_pres, s_agudos, s_comp, s_lim, s_vel,
              s_golpe, s_nit] + s_obj:
        s.valueChanged.connect(al_cambiar)
    chk_bypass.toggled.connect(al_cambiar)
    chk_ia.toggled.connect(al_cambiar)
    chk_reinicio.toggled.connect(al_cambiar)
    chk_anti.toggled.connect(al_cambiar)
    chk_niv.toggled.connect(al_cambiar)
    chk_igu.toggled.connect(al_cambiar)
    chk_mb.toggled.connect(al_cambiar)
    chk_mb_auto.toggled.connect(al_cambiar)
    s_mb_int.valueChanged.connect(al_cambiar)
    s_cb.valueChanged.connect(al_cambiar)
    s_ca.valueChanged.connect(al_cambiar)
    for trio in (mb_graves, mb_medios, mb_agudos):
        for s in trio:
            s.valueChanged.connect(al_cambiar)

    def reset_multibanda():
        s_cb.setValue(int(CONFIG_DEFECTO["mb_corte_bajos"]))
        s_ca.setValue(int(CONFIG_DEFECTO["mb_corte_altos"]))
        for trio, valores_def in ((mb_graves, CONFIG_DEFECTO["mb_graves"]),
                                  (mb_medios, CONFIG_DEFECTO["mb_medios"]),
                                  (mb_agudos, CONFIG_DEFECTO["mb_agudos"])):
            s_u, s_r, s_a, s_l, s_m = trio
            s_u.setValue(int(valores_def[0]))
            s_r.setValue(int(round(valores_def[1] * 10)))
            s_a.setValue(int(valores_def[2]))
            s_l.setValue(int(valores_def[3]))
            s_m.setValue(int(round(valores_def[4] * 10)))
        al_cambiar()

    btn_mb_reset.clicked.connect(reset_multibanda)

    def poner_perfil(valores):
        for s, v in zip(s_obj, valores):
            s.blockSignals(True)
            s.setValue(int(round(float(v) * 10)))
            s.blockSignals(False)
        for s, lv in zip(s_obj, lbl_obj):
            lv.setText(f"{s.value() / 10:+.1f}")
        al_cambiar()

    for nombre, b in botones_perfil.items():
        b.clicked.connect(lambda _=False, n=nombre: poner_perfil(PERFILES[n]))

    def capturar():
        m = tuberia.motor
        r = m.capturar_objetivo() if (m is not None and tuberia.activa) else None
        if r is None:
            lbl_cable.setText(tr("cap_hace_falta"))
            return
        poner_perfil(r)
        lbl_cable.setText(tr("cap_ok"))

    btn_capturar.clicked.connect(capturar)

    def pintar_inicio():
        btn_inicio.setText(tr("btn_iniciar"))
        btn_inicio.setStyleSheet(estilo_boton(COLORES_BOTON["inicio"]))

    def alternar():
        leer_config()
        if cb_sal.currentIndex() < 0:
            lbl_estado.setText(tr("est_elegi"))
            return
        try:
            bus.cambiar_salida(cb_sal.currentData())
            guardar_config(config)
            lbl_estado.setText(tr("est_activo", sr=bus.sr))
        except Exception as e:
            lbl_estado.setText(tr("est_error_iniciar", e=e))

    btn_inicio.clicked.connect(alternar)
    lbl_estado.setText(tr("est_activo", sr=bus.sr) if bus.activa else tr("est_detenido"))

    estado_cable = {"conectado": bool(bus.eq_activo)}

    def pintar_cable(activo):
        btn_cable.setText(tr("btn_desconectar") if activo else tr("btn_conectar"))
        btn_cable.setStyleSheet(estilo_boton(
            COLORES_BOTON["cable_activo" if activo else "cable"]))

    def poner_estado_cable(activo, texto):
        estado_cable["conectado"] = activo
        pintar_cable(activo)
        lbl_cable.setText(texto)

    def alternar_ecualizador():
        activar = not estado_cable["conectado"]
        if activar:
            leer_config()
            tuberia.aplicar_config(config)
            if bus.motor is not None:
                bus.motor.reiniciar_analisis()
            bus.eq_activo = True
        else:
            bus.eq_activo = False
            grafico.vaciar(tr("espectro_desconectado"))
        config["eq_activo"] = activar
        poner_estado_cable(activar, tr("eq_on") if activar else tr("eq_off"))

    btn_cable.clicked.connect(alternar_ecualizador)
    pintar_cable(estado_cable["conectado"])
    lbl_cable.setText(tr("eq_on") if estado_cable["conectado"] else tr("eq_off"))

    def aplicar_idioma():
        w.setWindowTitle(tr("ventana_titulo"))
        btn_pos.setToolTip(tr("tt_reposicionar"))
        btn_aj.setToolTip(tr("tt_ajustes"))
        pintar_cable(estado_cable["conectado"])
        ay = {k: ayuda_de(k) for k in ("inteligente", "bypass", "reinicio", "maxcorr", "velocidad")}
        chk_ia.setText(tr("chk_inteligente"))
        for x in (chk_ia, s_fuerza, lbl_fuerza):
            x.setToolTip(ay["inteligente"])
        chk_bypass.setText(tr("chk_bypass"))
        chk_bypass.setToolTip(ay["bypass"])
        chk_reinicio.setText(tr("chk_reinicio"))
        chk_reinicio.setToolTip(ay["reinicio"])
        lbl_vol.setText(f"{tr('vol')} {s_vol.value()} %")
        pestanas.setTabText(0, tr("tab_objetivo"))
        pestanas.setTabText(1, tr("tab_manual"))
        pestanas.setTabText(2, tr("tab_multibanda"))
        lbl_tit_av.setText(tr("tab_avanzado"))
        pestanas.setTabText(3, tr("tab_realce"))
        for sl, clave, tt in ((s_golpe, "re_golpe", "tt_re_golpe"), (s_nit, "re_nitidez", "tt_re_nitidez")):
            sl._etq.setText(tr(clave))
            for x in (sl._etq, sl, sl._lbl):
                x.setToolTip(tr(tt))
        chk_igu.setText(tr("chk_igualar"))
        chk_igu.setToolTip(tr("tt_igualar"))
        chk_niv.setText(tr("chk_nivelador"))
        chk_niv.setToolTip(tr("tt_nivelador"))
        lbl_re_nota.setText(tr("re_nota"))
        lbl_obj_ayuda.setText(tr("obj_ayuda"))
        for nombre, b in botones_perfil.items():
            b.setText(tr("perfil_" + nombre))
        btn_capturar.setText(tr("btn_capturar"))
        btn_capturar.setToolTip(tr("tt_capturar"))
        s_obj[0].setToolTip(tr("tt_43hz"))
        for sl, clave in ((s_graves, "man_graves"), (s_pres, "man_presencia"),
                          (s_agudos, "man_agudos"), (s_comp, "man_compresion")):
            sl._etq.setText(tr(clave))
        s_comp._lbl.setText(tr("comp_no") if s_comp.value() == 1 else f"{s_comp.value()}:1")
        chk_anti.setText(tr("chk_anti"))
        chk_anti.setToolTip(tr("tt_anti"))
        lbl_man_nota.setText(tr("man_nota"))
        chk_mb.setText(tr("mb_activar"))
        chk_mb_auto.setText(tr("mb_auto"))
        chk_mb_auto.setToolTip(tr("tt_mb_auto"))
        lbl_mb_int.setText(tr("mb_intensidad"))
        lbl_mb_auto_info.setText(tr("mb_auto_info"))
        for e, clave in cabeceras_pa:
            e.setText(tr(clave))
            e.setToolTip(tr("tt_" + clave))
        for cab, clave_b in etiquetas_banda_pa:
            cab.setText(tr(clave_b))
        for banda_d in medidores_mb.values():
            for clave, sl in banda_d.items():
                sl.setToolTip(tr("tt_" + clave))
        lbl_mb_int.setToolTip(tr("tt_mb_intensidad"))
        s_mb_int.setToolTip(tr("tt_mb_intensidad"))
        chk_mb.setToolTip(tr("tt_mb_activar"))
        lbl_cb.setText(tr("mb_corte_bajos"))
        lbl_ca.setText(tr("mb_corte_altos"))
        lbl_cb.setToolTip(tr("tt_mb_corte_bajos"))
        lbl_ca.setToolTip(tr("tt_mb_corte_altos"))
        btn_mb_reset.setText(tr("mb_reset"))
        btn_mb_reset.setToolTip(tr("tt_mb_reset"))
        lbl_mb_nota.setText(tr("mb_nota"))
        for e, tt_clave, clave_txt in cabeceras_mb:
            e.setText(tr(clave_txt))
            e.setToolTip(tr(tt_clave))
        for cab, clave_b in zip(cabs_mb, ("mb_banda_graves", "mb_banda_medios", "mb_banda_agudos")):
            cab.setText(tr(clave_b))
        for _e, sl, _lbl, tt_clave in refs_mb:
            sl.setToolTip(tr(tt_clave))
        for sl, clave, k in ((s_lim, "av_max_corr", "maxcorr"), (s_vel, "av_velocidad", "velocidad")):
            sl._etq.setText(tr(clave))
            for x in (sl._etq, sl, sl._lbl):
                x.setToolTip(ay[k])
        lbl_av_ent.setText(tr("av_entrada"))
        lbl_av_sal.setText(tr("av_salida"))
        pintar_inicio()
        lbl_estado.setText(tr("est_activo", sr=tuberia.sr) if tuberia.activa else tr("est_detenido"))
        for clave in ("espectro_sin_audio", "espectro_desconectado"):
            if grafico.mensaje in [TEXTOS[c][clave] for c in TEXTOS]:
                grafico.mensaje = tr(clave)
        grafico._cache_clave = None
        grafico.update()
        lbl_cable.setText(tr("eq_on") if estado_cable["conectado"] else tr("eq_off"))

    def abrir_ajustes():
        d = QDialog(w)
        d.setWindowTitle(tr("aj_titulo"))
        d.setMinimumWidth(300)
        lv = QVBoxLayout(d)
        grupo = QGroupBox(tr("aj_idioma_titulo"))
        lg = QGridLayout(grupo)
        et = QLabel(tr("aj_idioma_etiqueta"))
        combo = QComboBox()
        for cod, nombre in IDIOMAS_DISPONIBLES.items():
            combo.addItem(nombre, cod)
        combo.setCurrentIndex(max(0, combo.findData(idioma_actual())))
        nota = suave(tr("aj_idioma_nota"))
        lg.addWidget(et, 0, 0)
        lg.addWidget(combo, 0, 1)
        lg.addWidget(nota, 1, 0, 1, 2)
        lv.addWidget(grupo)
        cerrar = QPushButton(tr("aj_cerrar"))

        def repintar():
            d.setWindowTitle(tr("aj_titulo"))
            grupo.setTitle(tr("aj_idioma_titulo"))
            et.setText(tr("aj_idioma_etiqueta"))
            nota.setText(tr("aj_idioma_nota"))
            cerrar.setText(tr("aj_cerrar"))

        def cambio(_=None):
            cod = combo.currentData()
            if cod and cod != idioma_actual():
                config["idioma"] = cod
                guardar_config(config)
                establecer_idioma(cod)
                repintar()
                aplicar_idioma()

        combo.currentIndexChanged.connect(cambio)
        cerrar.clicked.connect(d.accept)
        lv.addWidget(cerrar, 0, Qt.AlignRight)
        d.exec()

    btn_aj.clicked.connect(abrir_ajustes)

    # Integrado al reproductor: el idioma se cambia desde Ajustes del reproductor,
    # así que el botón de ajustes propio sobra. Se deja disponible para quien use
    # el ecualizador suelto (ecualizador.py solo).
    def cambiar_idioma_externo(cod):
        """Lo llama el reproductor: cambia el idioma en vivo y lo guarda."""
        if not cod:
            return
        config["idioma"] = cod
        establecer_idioma(cod)
        aplicar_idioma()

    w.cambiar_idioma_externo = cambiar_idioma_externo
    if padre is not None or getattr(sys, "_fusion_integrado", False):
        btn_aj.hide()

    ciclo = {"n": 0, "maximos": []}

    def refrescar():
        m = tuberia.motor
        if m is None or not tuberia.activa:
            return
        niv = m.an_in.nivel_bandas_db()
        meta = None
        if niv is not None:
            o = m.objetivo - m.objetivo.mean()
            meta = (BANDAS_HZ, float(niv.mean()) + o - 4.77)
        grafico.poner(m.an_in.cent_disp, m.an_in.disp_db, m.an_out.disp_db, meta)
        if chk_mb_auto.isChecked() and chk_mb.isChecked() and t_mb.isVisible():
            actualizar_panel_auto(m)

        ciclo["n"] += 1
        if ciclo["n"] % 6:
            return
        ciclo["maximos"].append(tuberia.t_proc_max)
        tuberia.t_proc_max = 0.0
        del ciclo["maximos"][:-20]
        if chk_anti.isChecked():
            p = m.porcentaje_manual
            lbl_anti.setText(tr("anti_aplicado", p=f"{p:.0f}") +
                             (tr("anti_moderado") if p < 95 else tr("anti_sin_limites")))
        else:
            lbl_anti.setText("")
        if chk_mb_auto.isChecked():
            if chk_mb.isChecked():
                pm = m.mb_params
                lbl_mb_auto_info.setText(tr("mb_auto_info", g=f"{pm['graves'][0]:.0f}",
                                            m=f"{pm['medios'][0]:.0f}", a=f"{pm['agudos'][0]:.0f}"))
            else:
                lbl_mb_auto_info.setText("")
        a = tuberia.anillo
        db_in = 20.0 * np.log10(max(m.pico_in, 1e-5))
        db_out = 20.0 * np.log10(max(m.pico_out, 1e-5))
        lbl_diag.setText(
            f"in {db_in:.0f} · out {db_out:.0f} dB · {tr('diag_colchon')} "
            f"{a.llenado() * 1000 // max(tuberia.sr, 1)}/{int(tuberia.colchon_seg * 1000)} ms · "
            f"{tr('diag_vac')} {a.vaciados} · {tr('diag_desb')} {a.desbordes + tuberia.descartes} · proc {max(ciclo['maximos']) * 1000:.1f} ms · "
            f"xr {tuberia.xr_entrada}/{tuberia.xr_salida} · {tr('diag_temas')} {m.an_in.cambios}"
            + (f" · {tuberia.ultimo_error}" if tuberia.ultimo_error else "")
            + (f" · {m.error_dsp}" if m.error_dsp else ""))
        lbl_diag.setToolTip(tr("diag_tt"))

    t = QTimer()
    t.timeout.connect(refrescar)
    t.start(80)

    def guardar_todo():
        try:
            leer_config()
            config["eq_activo"] = bool(bus.eq_activo)
            guardar_config(config)
        except Exception:
            pass

    w._al_cerrar = guardar_todo
    w._timer_refresco = t
    if app is not None:
        app.aboutToQuit.connect(guardar_todo)
    return w


# ------------------------------------------------------------------
# API para el reproductor
# ------------------------------------------------------------------
_ESTADO = {"config": None}


def config_actual():
    """Config del ecualizador (un único dict compartido con la ventana)."""
    if _ESTADO["config"] is None:
        _ESTADO["config"] = cargar_config()
    return _ESTADO["config"]


def preparar(bus):
    """Crea el motor del ecualizador, le aplica la config guardada y lo engancha al bus."""
    c = config_actual()
    establecer_idioma(c.get("idioma", IDIOMA_POR_DEFECTO))
    bus.motor = MotorEfectos(bus.sr)
    TuberiaInterna(bus).aplicar_config(c)
    bus.eq_activo = bool(c.get("eq_activo", True))
    return c


def guardar_config_actual(bus=None):
    c = _ESTADO["config"]
    if c is None:
        return
    if bus is not None:
        c["eq_activo"] = bool(bus.eq_activo)
    guardar_config(c)

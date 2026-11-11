"""Configuracion compartida por el receptor y el monitor.

IMPORTANTE: GRUPO debe ser igual al GRUPO de wokwi/main.py. Como el broker es
publico, usen un nombre unico para no mezclar sus mensajes con los de otros equipos.
"""
import os

BROKER = os.environ.get("SAFESTEP_BROKER", "broker.hivemq.com")   # SAFESTEP_BROKER=localhost para pruebas locales
PUERTO = int(os.environ.get("SAFESTEP_PUERTO", "1883"))

GRUPO = "safestep_dgp27"            # iniciales del equipo (David, Gabriel, Paul)
PROYECTO = "safestep"
BASE = f"utp/ds8/{GRUPO}/{PROYECTO}"

T_TELEMETRIA = f"{BASE}/telemetria"   # dispositivo -> broker, no retenido
T_ESTADO = f"{BASE}/estado"           # dispositivo -> broker, RETENIDO (online/offline)
T_ORDENES = f"{BASE}/ordenes"         # broker -> dispositivo, no retenido
T_RESPUESTA = f"{BASE}/respuesta"     # dispositivo -> broker, no retenido
T_TODOS = f"{BASE}/#"                 # comodin para el receptor y el monitor

INTERVALO_PUBLICACION_S = 2.0         # debe coincidir con PUBLICAR_CADA_MS / 1000 del ESP32
SILENCIO_MAX_S = 10.0                 # configurable; minimo 3 x INTERVALO_PUBLICACION_S

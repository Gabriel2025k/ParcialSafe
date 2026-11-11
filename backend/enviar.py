"""Herramienta de pruebas: publica un mensaje en un subtema del proyecto.

Ejemplos:
  python enviar.py ordenes '{"cmd":"sistema","valor":"on"}'
  python enviar.py ordenes '{"cmd":"umbral_peligro","valor":500}'     # orden invalida
  python enviar.py telemetria 'esto no es json'                        # dato invalido al receptor
  python enviar.py ordenes '{"cmd":"simular_caida","valor":20}'        # caida del broker 20 s
"""
import sys

import paho.mqtt.publish as publish

import config

if len(sys.argv) != 3:
    raise SystemExit(__doc__)
tema = f"{config.BASE}/{sys.argv[1]}"
publish.single(tema, sys.argv[2], hostname=config.BROKER, port=config.PUERTO,
               client_id=f"safestep-pruebas-{config.GRUPO}")
print(f"Publicado en {tema}: {sys.argv[2]}")

"""Receptor MQTT de SafeStep.

Se suscribe a utp/ds8/<GRUPO>/safestep/# , valida la telemetria y guarda las
lecturas validas en salida/telemetria.csv. Los datos invalidos se descartan con
un aviso, sin detener el programa.

Uso:  python receptor.py
"""
import csv
import json
import math
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

import config

SALIDA = Path(__file__).parent / "salida"
CSV_TELEMETRIA = SALIDA / "telemetria.csv"

CABECERA = ["fecha_recepcion", "seq", "frontal_cm", "piso_cm", "nivel_frontal",
            "estado_piso", "vibracion_pct", "bocina", "activo"]

NIVELES = ("LIBRE", "PRECAUCION", "PELIGRO")
ESTADOS_PISO = ("NORMAL", "DESNIVEL")
BOCINAS = ("SILENCIO", "OBSTACULO", "DESNIVEL")
DIST_MIN_CM, DIST_MAX_CM = 2.0, 400.0   # rango del HC-SR04


def _es_entero(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _es_numero(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validar_telemetria(payload):
    """Devuelve (datos, None) si es valido o (None, motivo) si hay que descartarlo."""
    try:
        datos = json.loads(payload.decode("utf-8"))
    except ValueError:  # incluye UnicodeDecodeError y JSONDecodeError
        return None, "JSON ilegible"
    if not isinstance(datos, dict):
        return None, "el JSON no es un objeto"

    faltan = [c for c in CABECERA[1:] if c not in datos]
    if faltan:
        return None, "faltan campos: " + ", ".join(faltan)

    # distancias: numero en el rango del sensor, o null = "sin eco"
    for campo in ("frontal_cm", "piso_cm"):
        v = datos[campo]
        if v is not None and not (_es_numero(v) and DIST_MIN_CM <= v <= DIST_MAX_CM):
            return None, f"{campo} fuera de rango ({v!r})"

    if datos["nivel_frontal"] not in NIVELES:
        return None, f"nivel_frontal invalido ({datos['nivel_frontal']!r})"
    if datos["estado_piso"] not in ESTADOS_PISO:
        return None, f"estado_piso invalido ({datos['estado_piso']!r})"
    if datos["bocina"] not in BOCINAS:
        return None, f"bocina invalida ({datos['bocina']!r})"
    if not (_es_entero(datos["vibracion_pct"]) and 0 <= datos["vibracion_pct"] <= 100):
        return None, f"vibracion_pct fuera de rango ({datos['vibracion_pct']!r})"
    if not (_es_entero(datos["activo"]) and datos["activo"] in (0, 1)):
        return None, f"activo invalido ({datos['activo']!r})"
    if not (_es_entero(datos["seq"]) and datos["seq"] >= 0):
        return None, f"seq invalido ({datos['seq']!r})"
    return datos, None


class Receptor:
    def __init__(self, ruta_csv=CSV_TELEMETRIA):
        self.ruta = Path(ruta_csv)
        self.ultimo_seq = None
        self.validos = 0
        self.descartados = 0

    def _guardar(self, d):
        """Agrega una fila al CSV. Un error de archivo no detiene el programa."""
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            nuevo = not self.ruta.exists() or self.ruta.stat().st_size == 0
            with open(self.ruta, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if nuevo:
                    w.writerow(CABECERA)
                w.writerow([datetime.now().isoformat(timespec="seconds"), d["seq"],
                            "" if d["frontal_cm"] is None else d["frontal_cm"],
                            "" if d["piso_cm"] is None else d["piso_cm"],
                            d["nivel_frontal"], d["estado_piso"], d["vibracion_pct"],
                            d["bocina"], d["activo"]])
            return True
        except OSError as e:
            print(f"[AVISO] No se pudo escribir el CSV ({e}). Se continua.")
            return False

    def procesar_telemetria(self, payload):
        datos, motivo = validar_telemetria(payload)
        if datos is None:
            self.descartados += 1
            print(f"[DESCARTADO] {motivo}: {payload[:80]!r}")
            return False
        seq = datos["seq"]
        if self.ultimo_seq is not None:
            if seq < self.ultimo_seq:
                print("[INFO] seq reinicio: el dispositivo se reinicio")
            elif seq > self.ultimo_seq + 1:
                print(f"[AVISO] faltan {seq - self.ultimo_seq - 1} muestras (hubo desconexion)")
        self.ultimo_seq = seq
        if self._guardar(datos):
            self.validos += 1
            print(f"[OK] seq={seq} F={datos['frontal_cm']} P={datos['piso_cm']} "
                  f"{datos['nivel_frontal']}/{datos['estado_piso']} vib={datos['vibracion_pct']}%")
        return True


def main():
    receptor = Receptor()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[ERROR] Conexion rechazada: {reason_code}")
            return
        print(f"[RECEPTOR] Conectado a {config.BROKER}. Suscrito a {config.T_TODOS}")
        client.subscribe(config.T_TODOS)  # se repite en cada reconexion

    def on_disconnect(client, userdata, flags, reason_code, properties):
        print(f"[RECEPTOR] Desconectado ({reason_code}). Reintentando...")

    def on_message(client, userdata, msg):
        try:
            if msg.topic == config.T_TELEMETRIA:
                receptor.procesar_telemetria(msg.payload)
            else:  # estado, ordenes, respuesta
                print(f"[INFO] {msg.topic}: {msg.payload.decode('utf-8', 'replace')}")
        except Exception as e:  # nada debe tumbar el receptor
            print(f"[ERROR] Mensaje no procesado: {e}")

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id=f"safestep-receptor-{config.GRUPO}")
    cliente.on_connect = on_connect
    cliente.on_disconnect = on_disconnect
    cliente.on_message = on_message
    cliente.reconnect_delay_set(min_delay=2, max_delay=60)
    cliente.connect_async(config.BROKER, config.PUERTO, keepalive=30)
    try:
        cliente.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        print(f"\n[RECEPTOR] Fin. Validos: {receptor.validos}, descartados: {receptor.descartados}")
        cliente.disconnect()


if __name__ == "__main__":
    main()
